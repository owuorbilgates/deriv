"""
Trade Execution Module for Deriv Digit Trading Bot.

Constructs Deriv API trade contracts, dispatches orders, enforces locks,
monitors live contract lifecycles, and reports settlement outcomes to Risk Manager.
"""

import asyncio
import logging
import random
from typing import Any, Dict, Optional

from config import TradingConfig
from deriv_client import DerivClient
from risk_manager import RiskManager, TradeOutcome
from strategy import SignalType, StrategySignal, TickData

logger = logging.getLogger("TradeExecutor")


class TradeExecutor:
    """
    Coordinates contract purchase, lifecycle monitoring, and settlement.
    """

    def __init__(self, client: DerivClient, risk_manager: RiskManager, config: TradingConfig):
        """
        Initialize Trade Executor.
        
        Args:
            client: Connected Deriv WebSocket client.
            risk_manager: RiskManager instance.
            config: TradingConfig instance.
        """
        self.client = client
        self.risk_manager = risk_manager
        self.config = config

        # Pending contract tracking: {contract_id: contract_context_dict}
        self._pending_contracts: Dict[int, Dict[str, Any]] = {}
        
        # Simulated contract tracking for paper-trading mode
        self._simulated_trades: Dict[str, Dict[str, Any]] = {}

    async def execute_signal(self, signal: StrategySignal, current_tick: TickData) -> bool:
        """
        Evaluate and execute a trading signal.
        
        Args:
            signal: Generated StrategySignal.
            current_tick: Latest TickData context.
            
        Returns:
            bool: True if order was dispatched, False otherwise.
        """
        if signal.signal_type == SignalType.WAIT:
            return False

        # 1. Check Risk Controls
        can_trade, reason = self.risk_manager.can_open_trade(self.config.STAKE_AMOUNT)
        if not can_trade:
            logger.info(f"⏸️ Trade skipped: {reason}")
            return False

        logger.info(
            f"⚡ [SIGNAL TRIGGERED] Action={signal.signal_type.value} | "
            f"Barrier={signal.predicted_barrier} | Confidence={signal.confidence:.0%} | "
            f"Reason={signal.reason}"
        )

        # 2. Simulation Mode Execution
        if self.config.SIMULATION_MODE:
            return await self._execute_simulated_trade(signal, current_tick)

        # 3. Live Trade Execution via Deriv WebSocket API
        try:
            response = await self.client.buy_contract(
                symbol=self.config.SYMBOL,
                contract_type=signal.signal_type.value,
                stake=self.config.STAKE_AMOUNT,
                duration=self.config.DURATION,
                barrier=signal.predicted_barrier,
                duration_unit=self.config.DURATION_UNIT,
                currency=self.config.CURRENCY
            )

            if "error" in response:
                logger.error(f"Trade execution failed: {response['error'].get('message')}")
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
                "symbol": self.config.SYMBOL,
                "stake": self.config.STAKE_AMOUNT,
                "barrier": signal.predicted_barrier,
                "duration": self.config.DURATION,
                "start_tick": current_tick.last_digit,
                "ticks_seen": 0
            }

            # 5. Subscribe to Contract Updates
            await self.client.subscribe_open_contract(contract_id)
            return True

        except Exception as e:
            logger.error(f"Exception during trade execution: {e}", exc_info=True)
            return False

    async def handle_contract_update(self, msg: Dict[str, Any]) -> None:
        """
        Process live contract status updates from Deriv server (proposal_open_contract).
        
        Args:
            msg: JSON payload from Deriv containing contract state.
        """
        contract_data = msg.get("proposal_open_contract")
        if not contract_data:
            return

        contract_id = contract_data.get("contract_id")
        if not contract_id or contract_id not in self._pending_contracts:
            return

        is_sold = bool(contract_data.get("is_sold", 0))
        status = contract_data.get("status")  # "open", "won", "lost"
        current_spot = contract_data.get("current_spot")
        tick_count = contract_data.get("tick_count", 0)

        logger.debug(
            f"Contract #{contract_id} update: Status={status} | "
            f"Tick {tick_count}/{self.config.DURATION} | Spot={current_spot}"
        )

        # Check for settlement
        if is_sold or status in ("won", "lost"):
            await self._settle_contract(contract_id, contract_data)

    async def _settle_contract(self, contract_id: int, contract_data: Dict[str, Any]) -> None:
        """
        Parse final contract results, release risk lock, and update metrics.
        """
        ctx = self._pending_contracts.pop(contract_id, {})
        status = contract_data.get("status", "lost")
        profit = float(contract_data.get("profit", 0.0))
        payout = float(contract_data.get("payout", 0.0))
        stake = float(contract_data.get("buy_price", self.config.STAKE_AMOUNT))
        barrier = ctx.get("barrier", self.config.DEFAULT_BARRIER)
        
        # Extract exit digit if available
        exit_tick_str = str(contract_data.get("exit_tick_display_value", ""))
        exit_digit = int(exit_tick_str[-1]) if exit_tick_str and exit_tick_str[-1].isdigit() else None

        outcome = TradeOutcome(
            contract_id=contract_id,
            contract_type=ctx.get("contract_type", self.config.DEFAULT_CONTRACT_TYPE),
            symbol=ctx.get("symbol", self.config.SYMBOL),
            stake=stake,
            payout=payout,
            profit=profit,
            status=status,
            barrier=barrier,
            exit_digit=exit_digit,
            duration_ticks=self.config.DURATION
        )

        # Release trade lock in RiskManager
        self.risk_manager.release_trade_lock(outcome)

        # Update balance if present
        balance_after = contract_data.get("balance_after")
        if balance_after is not None:
            self.risk_manager.update_balance(float(balance_after))

        # Log formatted result
        result_icon = "🎉 WIN" if status == "won" else "💥 LOSS"
        logger.info(
            f"{result_icon} Contract #{contract_id} Settled! | "
            f"Result: {status.upper()} | Profit/Loss: ${profit:+.2f} | "
            f"Exit Digit: {exit_digit} (Predicted Barrier: {barrier}) | "
            f"{self.risk_manager.get_summary()}"
        )

    # -------------------------------------------------------------------------
    # Simulation / Paper-Trading Engine
    # -------------------------------------------------------------------------
    async def _execute_simulated_trade(self, signal: StrategySignal, current_tick: TickData) -> bool:
        """Execute a simulated paper-trading position."""
        sim_id = random.randint(10000000, 99999999)
        logger.info(
            f"📝 [SIMULATED TRADE PLACED] ID: #{sim_id} | Type: {signal.signal_type.value} | "
            f"Barrier: {signal.predicted_barrier} | Stake: ${self.config.STAKE_AMOUNT:.2f}"
        )

        await self.risk_manager.acquire_trade_lock(sim_id)
        
        self._simulated_trades[str(sim_id)] = {
            "contract_id": sim_id,
            "contract_type": signal.signal_type.value,
            "barrier": signal.predicted_barrier,
            "stake": self.config.STAKE_AMOUNT,
            "start_epoch": current_tick.epoch,
            "ticks_remaining": self.config.DURATION
        }
        return True

    async def on_simulated_tick(self, tick: TickData) -> None:
        """Progress simulated trades on incoming ticks."""
        for sim_id, trade in list(self._simulated_trades.items()):
            trade["ticks_remaining"] -= 1
            if trade["ticks_remaining"] <= 0:
                # Settle simulated trade
                barrier = trade["barrier"]
                contract_type = trade["contract_type"]
                exit_digit = tick.last_digit
                stake = trade["stake"]

                if contract_type == SignalType.DIGITDIFF.value:
                    # Differs wins if exit digit != barrier
                    won = (exit_digit != barrier)
                    # Deriv DIGITDIFF typical return is ~9.5%
                    profit = round(stake * 0.095, 2) if won else -stake
                    payout = round(stake * 1.095, 2) if won else 0.0
                else:
                    # Matches wins if exit digit == barrier
                    won = (exit_digit == barrier)
                    # Deriv DIGITMATCH typical return is ~810%
                    profit = round(stake * 8.10, 2) if won else -stake
                    payout = round(stake * 9.10, 2) if won else 0.0

                status = "won" if won else "lost"
                outcome = TradeOutcome(
                    contract_id=trade["contract_id"],
                    contract_type=contract_type,
                    symbol=tick.symbol,
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

                result_icon = "🎉 SIM WIN" if won else "💥 SIM LOSS"
                logger.info(
                    f"{result_icon} [SIM #{trade['contract_id']}] {status.upper()} | "
                    f"Profit: ${profit:+.2f} | Exit: {exit_digit} vs Barrier: {barrier} | "
                    f"{self.risk_manager.get_summary()}"
                )
