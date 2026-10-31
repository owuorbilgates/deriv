"""
Risk Management Module for Deriv Digit Trading Bot.

Supports:
1. Dynamic Staking Modes:
   - Flat Staking (Zero Martingale): For Trend-Following DIGITMATCH and Dynamic State Machine.
   - 2-Step 11.0x Martingale Recovery: Optional legacy setting.
2. Execution Modes:
   - Live Trading (Real Balance)
   - Real Deriv Simulation / Paper Trading (Local Execution, Virtual Bankroll)
   - Offline Simulation
3. Hard Session Stops:
   - TARGET_PROFIT (e.g. $5.00) -> immediately halts
   - STOP_LOSS (e.g. $25.00) -> immediately halts
4. Concurrency Lock: Strictly 1 open contract at a time.
5. Win-Rate and Realized PnL tracking.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger("RiskManager")


@dataclass
class TradeOutcome:
    """Detailed summary of a completed contract."""
    contract_id: int
    contract_type: str
    symbol: str
    stake: float
    payout: float
    profit: float
    status: str  # "won" or "lost"
    barrier: int
    exit_digit: Optional[int] = None
    duration_ticks: int = 1


class RiskManager:
    """
    Automated risk manager and trade lifecycle guard.
    """

    def __init__(
        self,
        base_stake: float = 0.35,
        use_martingale: bool = False,
        martingale_multiplier: float = 11.0,
        martingale_max_steps: int = 2,
        daily_stop_loss: float = 25.0,
        daily_take_profit: float = 5.0,
        cooldown_seconds: float = 3.0,
        max_consecutive_losses: int = 6,
        starting_balance: float = 0.0,
        paper_trading: bool = False
    ):
        """
        Initialize Risk Manager.
        
        Args:
            base_stake: Starting stake per trade (e.g. $0.35 USD).
            use_martingale: Whether Martingale escalation is enabled.
            martingale_multiplier: Multiplier upon loss (default: 11.0x).
            martingale_max_steps: Maximum consecutive Martingale steps before resetting (default: 2).
            daily_stop_loss: Max acceptable session loss in USD (e.g. $25.00).
            daily_take_profit: Target session profit in USD (e.g. $5.00).
            cooldown_seconds: Enforced quiet period after trade settlement.
            max_consecutive_losses: Emergency stop after N successive losses.
            starting_balance: Initial account balance.
            paper_trading: If True, tracks virtual balance locally without requiring real funds.
        """
        self.base_stake = base_stake
        self.use_martingale = use_martingale
        self.martingale_multiplier = martingale_multiplier
        self.martingale_max_steps = martingale_max_steps
        self.daily_stop_loss = daily_stop_loss
        self.daily_take_profit = daily_take_profit
        self.cooldown_seconds = cooldown_seconds
        self.max_consecutive_losses = max_consecutive_losses
        self.paper_trading = paper_trading
        
        # Virtual starting balance if paper trading and no real balance provided
        default_bal = 100.0 if paper_trading else 0.0
        self.starting_balance = starting_balance if starting_balance > 0 else default_bal
        self.current_balance = self.starting_balance
        
        # Martingale Ladder Tracking
        self.current_martingale_step: int = 0
        
        # PnL & Trade Counters
        self.total_trades: int = 0
        self.winning_trades: int = 0
        self.losing_trades: int = 0
        self.net_pnl: float = 0.0
        self.consecutive_losses: int = 0
        
        # Concurrency & Cooldown Controls
        self._trade_lock: asyncio.Lock = asyncio.Lock()
        self._active_trade_id: Optional[int] = None
        self._last_trade_closed_time: float = 0.0
        self._is_halted: bool = False
        self._halt_reason: str = ""

    @property
    def is_trade_active(self) -> bool:
        """Check if a contract is currently open."""
        return self._active_trade_id is not None

    @property
    def is_halted(self) -> bool:
        """Check if trading has been halted by risk limits."""
        return self._is_halted

    @property
    def halt_reason(self) -> str:
        """Return the reason trading was halted."""
        return self._halt_reason

    def get_current_stake(self) -> float:
        """Calculate current trade stake."""
        if not self.use_martingale or self.current_martingale_step == 0:
            return round(self.base_stake, 2)
        else:
            stake = self.base_stake * (self.martingale_multiplier ** self.current_martingale_step)
            return round(stake, 2)

    def update_balance(self, new_balance: float) -> None:
        """Set or update current account balance."""
        if not self.paper_trading:
            if self.starting_balance == 0.0:
                self.starting_balance = new_balance
            self.current_balance = new_balance
        else:
            # In paper trading, keep virtual balance if account balance is 0 or low
            if self.starting_balance == 0.0:
                self.starting_balance = new_balance if new_balance > 0 else 100.0
                self.current_balance = self.starting_balance

    def can_open_trade(self, stake: Optional[float] = None) -> Tuple[bool, str]:
        """Evaluate risk checks before opening a new position."""
        if stake is None:
            stake = self.get_current_stake()

        if self._is_halted:
            return False, f"Trading halted: {self._halt_reason}"

        if self.is_trade_active:
            return False, f"Active trade in progress (Contract ID: #{self._active_trade_id})"

        now = time.time()
        elapsed_since_last = now - self._last_trade_closed_time
        if elapsed_since_last < self.cooldown_seconds:
            remaining = round(self.cooldown_seconds - elapsed_since_last, 1)
            return False, f"In post-trade cooldown ({remaining}s remaining)"

        if self.net_pnl <= -self.daily_stop_loss:
            self._is_halted = True
            self._halt_reason = f"STOP_LOSS limit hit: Net PnL is -${abs(self.net_pnl):.2f}"
            logger.critical(f"🛑 [HARD STOP] {self._halt_reason}")
            return False, self._halt_reason

        if self.net_pnl >= self.daily_take_profit:
            self._is_halted = True
            self._halt_reason = f"TARGET_PROFIT achieved: Net PnL is +${self.net_pnl:.2f}"
            logger.info(f"🏆 [TARGET ACHIEVED] {self._halt_reason}")
            return False, self._halt_reason

        if self.consecutive_losses >= self.max_consecutive_losses:
            self._is_halted = True
            self._halt_reason = f"Circuit breaker: {self.consecutive_losses} consecutive losses."
            logger.critical(f"🛑 [CIRCUIT BREAKER] {self._halt_reason}")
            return False, self._halt_reason

        if self.current_balance > 0 and stake > self.current_balance:
            return False, (
                f"Insufficient balance (${self.current_balance:.2f} USD) for required stake (${stake:.2f} USD)."
            )

        return True, "All risk checks passed"

    async def acquire_trade_lock(self, contract_id: int) -> bool:
        """Acquire lock for active contract."""
        await self._trade_lock.acquire()
        self._active_trade_id = contract_id
        return True

    def release_trade_lock(self, outcome: TradeOutcome) -> None:
        """Record trade results, advance/reset stake, release lock, and trigger cooldown."""
        self.total_trades += 1
        self.net_pnl += outcome.profit

        # In paper trading, track virtual balance locally
        if self.paper_trading:
            self.current_balance += outcome.profit

        if outcome.status == "won":
            self.winning_trades += 1
            self.consecutive_losses = 0
            self.current_martingale_step = 0
            
            logger.info(
                f"✅ [WIN] Contract #{outcome.contract_id} | Stake: ${outcome.stake:.2f} | "
                f"Profit: +${outcome.profit:.2f} | Session Net: ${self.net_pnl:+.2f} | "
                f"Next Stake: ${self.get_current_stake():.2f}"
            )
        else:
            self.losing_trades += 1
            self.consecutive_losses += 1

            if self.use_martingale:
                self.current_martingale_step += 1
                if self.current_martingale_step >= self.martingale_max_steps:
                    logger.warning(
                        f"⚠️ [LADDER CAP] Martingale maximum steps ({self.martingale_max_steps}) reached! "
                        f"Resetting ladder to Step 0 (${self.base_stake:.2f}) to protect account."
                    )
                    self.current_martingale_step = 0
                else:
                    logger.warning(
                        f"❌ [LOSS] Contract #{outcome.contract_id} | Stake: ${outcome.stake:.2f} | "
                        f"Loss: -${abs(outcome.profit):.2f} | Martingale Step {self.current_martingale_step} "
                        f"(Next Stake: ${self.get_current_stake():.2f})"
                    )
            else:
                logger.warning(
                    f"❌ [LOSS] Contract #{outcome.contract_id} | Flat Stake: ${outcome.stake:.2f} | "
                    f"Loss: -${abs(outcome.profit):.2f} | Session Net: ${self.net_pnl:+.2f}"
                )

        self._last_trade_closed_time = time.time()
        self._active_trade_id = None
        
        if self._trade_lock.locked():
            self._trade_lock.release()

        # Check stops
        if self.net_pnl <= -self.daily_stop_loss:
            self._is_halted = True
            self._halt_reason = f"STOP_LOSS limit reached (-${self.daily_stop_loss:.2f})"
        elif self.net_pnl >= self.daily_take_profit:
            self._is_halted = True
            self._halt_reason = f"TARGET_PROFIT achieved (+${self.daily_take_profit:.2f})"

    def get_summary(self) -> str:
        """Return performance summary string."""
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0.0
        bal_label = "Paper Balance" if self.paper_trading else "Balance"
        return (
            f"Trades: {self.total_trades} (W: {self.winning_trades} | L: {self.losing_trades}) | "
            f"Win Rate: {win_rate:.1f}% | Net PnL: ${self.net_pnl:+.2f} | "
            f"{bal_label}: ${self.current_balance:.2f} | Next Stake: ${self.get_current_stake():.2f}"
        )
