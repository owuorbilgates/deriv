"""
Trade Execution Module for Deriv Digit Trading Bot.

Supports:
1. Live Mode: Submits real buy contracts to Deriv WebSocket and monitors settlement.
2. Real Deriv Simulation (Paper Trading): Executes orders locally on your machine
   against REAL live ticks streamed from Deriv. Zero real money at risk!
3. Offline Simulation: Executes against synthetic tick streams.
"""

import asyncio
import logging
import random
from typing import Any, Dict, Optional

from config import TradingConfig
from deriv_client import DerivClient
from risk_manager import RiskManager, TradeOutcome
from strategy import SignalType, StrategyEngine, StrategySignal, TickData

logger = logging.getLogger("TradeExecutor")


class TradeExecutor:
    """
    Coordinates contract purchase, lifecycle monitoring, settlement, and strategy feedback.
    """

    def __init__(
        self,
        client: DerivClient,
        risk_manager: RiskManager,
        config: TradingConfig,
        strategy: Optional[StrategyEngine] = None
    ):
        self.client = client
        self.risk_manager = risk_manager
        self.config = config
        self.strategy = strategy

        # Pending live contract tracking: {contract_id: contract_context_dict}
        self._pending_contracts: Dict[int, Dict[str, Any]] = {}
        
        # Simulated/Paper contract tracking
        self._simulated_trades: Dict[str, Dict[str, Any]] = {}

    async def execute_signal(self, signal: StrategySignal, current_tick: TickData, target_symbol: Optional[str] = None) -> bool:
        """Evaluate and execute a trading signal."""
        if signal.signal_type == SignalType.WAIT:
            return False

        active_symbol = target_symbol or current_tick.symbol or self.config.SYMBOL
        current_stake = self.risk_manager.get_current_stake()

        # 1. Check Risk Controls
        can_trade, reason = self.risk_manager.can_open_trade(current_stake)
        if not can_trade:
            logger.info(f"⏸️ Trade skipped: {reason}")
            return False

        logger.info(
            f"⚡ [EXECUTION TRIGGER] Symbol={active_symbol} | Type={signal.signal_type.value} | "
            f"Barrier={signal.predicted_barrier} | Stake=${current_stake:.2f} {self.config.CURRENCY} | "
            f"Confidence={signal.confidence:.0%}"
        )
        logger.info(f"   Rationale: {signal.reason}")

        # 2. Local Simulation / Paper Trading Mode (Zero Real Money at Risk)
        if self.config.SIMULATION_MODE or self.config.PAPER_TRADING_MODE:
            return await self._execute_simulated_trade(signal, current_tick, current_stake, active_symbol)

        # 3. Live Trade Execution via Deriv WebSocket API (Real Money)
        try:
            response = await self.client.buy_contract(
                symbol=active_symbol,
                contract_type=signal.signal_type.value,
                stake=current_stake,
                duration=self.config.DURATION,
                barrier=signal.predicted_barrier,
                duration_unit=self.config.DURATION_UNIT,
                currency=self.config.CURRENCY
            )

            if "error" in response:
                logger.error(f"❌ Trade execution rejected: {response['error'].get('message')}")
                return False

            buy_data = response.get("buy", {})
            contract_id = buy_data.get("contract_id")
            if not contract_id:
                logger.error(f"Malformed buy response from Deriv: {response}")
                return False

            # 4. Acquire Lock & Track Lifecycle
            await self.risk_manager.acquire_trade_lock(contract_id)
            
            self._pending_contracts[contract_id] = {
                "contract_id": contract_id,
                "contract_type": signal.signal_type.value,
                "symbol": active_symbol,
                "stake": current_stake,
                "barrier": signal.predicted_barrier,
                "duration": self.config.DURATION,
                "start_tick": current_tick.last_digit,
                "ticks_seen": 0
            }

            return True

        except Exception as e:
            logger.error(f"Exception during trade execution: {e}", exc_info=True)
            return False

    async def handle_contract_update(self, msg: Dict[str, Any]) -> None:
        """Process live contract status updates from Deriv server."""
        poc = msg.get("proposal_open_contract") if isinstance(msg, dict) and "proposal_open_contract" in msg else msg
        if not poc or not isinstance(poc, dict):
            return

        contract_id = poc.get("contract_id")
        if not contract_id or contract_id not in self._pending_contracts:
            return

        is_sold = bool(poc.get("is_sold", 0))
        status = poc.get("status")

        if is_sold or status in ("won", "lost"):
            await self._settle_contract(contract_id, poc)

    async def _settle_contract(self, contract_id: int, poc: Dict[str, Any]) -> None:
        """Parse contract results, release risk lock, and notify strategy of outcome."""
        ctx = self._pending_contracts.pop(contract_id, {})
        status = poc.get("status", "lost")
        profit = float(poc.get("profit", 0.0))
        payout = float(poc.get("payout", 0.0))
        stake = float(poc.get("buy_price", ctx.get("stake", self.config.STAKE_AMOUNT)))
        barrier = ctx.get("barrier", self.config.DEFAULT_BARRIER)
        contract_type = ctx.get("contract_type", self.config.DEFAULT_CONTRACT_TYPE)
        
        exit_tick_str = str(poc.get("exit_tick_display_value", poc.get("exit_tick", "")))
        exit_digit = int(exit_tick_str[-1]) if exit_tick_str and exit_tick_str[-1].isdigit() else None

        outcome = TradeOutcome(
            contract_id=contract_id,
            contract_type=contract_type,
            symbol=ctx.get("symbol", self.config.SYMBOL),
            stake=stake,
            payout=payout,
            profit=profit,
            status=status,
            barrier=barrier,
            exit_digit=exit_digit,
            duration_ticks=self.config.DURATION
        )

        self.risk_manager.release_trade_lock(outcome)

        if self.strategy:
            self.strategy.on_trade_completed(status=status, barrier=barrier)

        balance_after = poc.get("balance_after")
        if balance_after is not None:
            self.risk_manager.update_balance(float(balance_after))

        result_icon = "🎉 WIN" if status == "won" else "💥 LOSS"
        logger.info(
            f"{result_icon} Contract #{contract_id} Settled! | "
            f"Type: {contract_type} | Result: {status.upper()} | Profit/Loss: ${profit:+.2f} | "
            f"Exit Digit: [{exit_digit}] (Barrier: [{barrier}]) | "
            f"{self.risk_manager.get_summary()}"
        )

    # -------------------------------------------------------------------------
    # Local Simulation / Paper-Trading Engine
    # -------------------------------------------------------------------------
    async def _execute_simulated_trade(
        self,
        signal: StrategySignal,
        current_tick: TickData,
        stake: float,
        symbol: str
    ) -> bool:
        """Execute a simulated paper-trading position locally on the machine."""
        sim_id = random.randint(10000000, 99999999)
        mode_label = "REAL-DERIV SIMULATION" if self.config.PAPER_TRADING_MODE else "OFFLINE SIM"
        logger.info(
            f"📝 [{mode_label} ORDER PLACED] ID: #{sim_id} | Symbol: {symbol} | Type: {signal.signal_type.value} | "
            f"Barrier: {signal.predicted_barrier} | Stake: ${stake:.2f} (Local Execution, Zero Risk)"
        )

        await self.risk_manager.acquire_trade_lock(sim_id)
        
        self._simulated_trades[str(sim_id)] = {
            "contract_id": sim_id,
            "contract_type": signal.signal_type.value,
            "barrier": signal.predicted_barrier,
            "stake": stake,
            "symbol": symbol,
            "start_epoch": current_tick.epoch,
            "ticks_remaining": self.config.DURATION
        }
        return True

    async def on_simulated_tick(self, tick: TickData) -> None:
        """
        Progress and settle simulated/paper trades on incoming live Deriv market ticks.
        Evaluates the contract against the REAL market tick's last digit.
        """
        for sim_id, trade in list(self._simulated_trades.items()):
            trade["ticks_remaining"] -= 1
            if trade["ticks_remaining"] <= 0:
                barrier = trade["barrier"]
                contract_type = trade["contract_type"]
                exit_digit = tick.last_digit
                stake = trade["stake"]

                if contract_type == SignalType.DIGITDIFF.value:
                    won = (exit_digit != barrier)
                    # Typical Deriv Differs payout ~9.9%
                    profit = round(stake * 0.099, 2) if won else -stake
                    payout = round(stake * 1.099, 2) if won else 0.0
                else:
                    # DIGITMATCH wins if exit digit equals barrier
                    won = (exit_digit == barrier)
                    # Typical Deriv Match payout ~810% profit (9.10x total return)
                    profit = round(stake * 8.10, 2) if won else -stake
                    payout = round(stake * 9.10, 2) if won else 0.0

                status = "won" if won else "lost"
                outcome = TradeOutcome(
                    contract_id=trade["contract_id"],
                    contract_type=contract_type,
                    symbol=trade.get("symbol", tick.symbol),
                    stake=stake,
                    payout=payout,
                    profit=profit,
                    status=status,
                    barrier=barrier,
                    exit_digit=exit_digit,
                    duration_ticks=self.config.DURATION
                )

                del self._simulated_trades[sim_id]
                self.risk_manager.release_trade_lock(outcome)

                if self.strategy:
                    self.strategy.on_trade_completed(status=status, barrier=barrier)

                mode_label = "REAL-DERIV SIM" if self.config.PAPER_TRADING_MODE else "OFFLINE SIM"
                result_icon = f"🎉 {mode_label} WIN" if won else f"💥 {mode_label} LOSS"
                logger.info(
                    f"{result_icon} [#{trade['contract_id']}] {status.upper()} | "
                    f"Type: {contract_type} | Profit: ${profit:+.2f} | Exit: [{exit_digit}] vs Barrier: [{barrier}] | "
                    f"{self.risk_manager.get_summary()}"
                )
