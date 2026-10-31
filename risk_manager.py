"""
Risk Management Module for Deriv Digit Trading Bot.

Enforces session risk rules:
1. Daily Stop-Loss and Take-Profit limits
2. Concurrency locks (prevents overlapping positions)
3. Post-trade cooldown timers
4. Consecutive loss circuit breaker
5. Comprehensive PnL and Win-Rate tracking
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
    duration_ticks: int = 5


class RiskManager:
    """
    Automated risk manager and trade lifecycle guard.
    """

    def __init__(
        self,
        daily_stop_loss: float = 10.0,
        daily_take_profit: float = 20.0,
        cooldown_seconds: float = 3.0,
        max_consecutive_losses: int = 5,
        starting_balance: float = 0.0
    ):
        """
        Initialize Risk Manager.
        
        Args:
            daily_stop_loss: Max acceptable loss in currency units (e.g., $10.00).
            daily_take_profit: Target profit in currency units (e.g., $20.00).
            cooldown_seconds: Enforced quiet period after trade settlement.
            max_consecutive_losses: Emergency stop after N successive losses.
            starting_balance: Initial account balance.
        """
        self.daily_stop_loss = daily_stop_loss
        self.daily_take_profit = daily_take_profit
        self.cooldown_seconds = cooldown_seconds
        self.max_consecutive_losses = max_consecutive_losses
        
        self.starting_balance = starting_balance
        self.current_balance = starting_balance
        
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

    def update_balance(self, new_balance: float) -> None:
        """Set or update current account balance."""
        if self.starting_balance == 0.0:
            self.starting_balance = new_balance
        self.current_balance = new_balance

    def can_open_trade(self, stake: float) -> Tuple[bool, str]:
        """
        Evaluate all risk checks to determine if a new trade can be initiated.
        
        Args:
            stake: Planned stake amount.
            
        Returns:
            Tuple of (can_trade: bool, reason: str)
        """
        # Check if already halted
        if self._is_halted:
            return False, f"Trading halted: {self._halt_reason}"

        # Check concurrency lock
        if self.is_trade_active:
            return False, f"Active trade in progress (Contract ID: {self._active_trade_id})"

        # Check post-trade cooldown
        now = time.time()
        elapsed_since_last = now - self._last_trade_closed_time
        if elapsed_since_last < self.cooldown_seconds:
            remaining = round(self.cooldown_seconds - elapsed_since_last, 1)
            return False, f"In post-trade cooldown ({remaining}s remaining)"

        # Check Stop-Loss
        if self.net_pnl <= -self.daily_stop_loss:
            self._is_halted = True
            self._halt_reason = (
                f"Daily Stop-Loss reached: Net PnL is ${self.net_pnl:.2f} "
                f"(Limit: -${self.daily_stop_loss:.2f})"
            )
            logger.critical(f"🛑 [RISK TRIGGER] {self._halt_reason}")
            return False, self._halt_reason

        # Check Take-Profit
        if self.net_pnl >= self.daily_take_profit:
            self._is_halted = True
            self._halt_reason = (
                f"Daily Take-Profit achieved: Net PnL is +${self.net_pnl:.2f} "
                f"(Target: +${self.daily_take_profit:.2f})"
            )
            logger.info(f"🏆 [TARGET ACHIEVED] {self._halt_reason}")
            return False, self._halt_reason

        # Check Consecutive Loss Limit
        if self.consecutive_losses >= self.max_consecutive_losses:
            self._is_halted = True
            self._halt_reason = (
                f"Consecutive loss safety circuit triggered: {self.consecutive_losses} losses in a row."
            )
            logger.critical(f"🛑 [CIRCUIT BREAKER] {self._halt_reason}")
            return False, self._halt_reason

        # Check account balance sufficiency
        if self.current_balance > 0 and stake > self.current_balance:
            return False, f"Insufficient balance (${self.current_balance:.2f}) for stake (${stake:.2f})"

        return True, "All risk checks passed"

    async def acquire_trade_lock(self, contract_id: int) -> bool:
        """
        Acquire lock for a newly opened contract.
        
        Args:
            contract_id: Deriv contract ID assigned by server.
        """
        await self._trade_lock.acquire()
        self._active_trade_id = contract_id
        logger.debug(f"Trade lock acquired for Contract #{contract_id}")
        return True

    def release_trade_lock(self, outcome: TradeOutcome) -> None:
        """
        Record completed trade outcome, update metrics, release lock, and trigger cooldown.
        
        Args:
            outcome: TradeOutcome details.
        """
        self.total_trades += 1
        self.net_pnl += outcome.profit

        if outcome.status == "won":
            self.winning_trades += 1
            self.consecutive_losses = 0
            logger.info(
                f"✅ [WIN] Contract #{outcome.contract_id} | Profit: +${outcome.profit:.2f} | "
                f"Session Net: ${self.net_pnl:+.2f}"
            )
        else:
            self.losing_trades += 1
            self.consecutive_losses += 1
            logger.warning(
                f"❌ [LOSS] Contract #{outcome.contract_id} | Loss: -${abs(outcome.profit):.2f} | "
                f"Session Net: ${self.net_pnl:+.2f} | Consecutive Losses: {self.consecutive_losses}"
            )

        # Update closed timestamp for cooldown
        self._last_trade_closed_time = time.time()
        self._active_trade_id = None
        
        if self._trade_lock.locked():
            self._trade_lock.release()
            logger.debug(f"Trade lock released for Contract #{outcome.contract_id}")

        # Post-settlement check on Stop-Loss & Take-Profit limits
        if self.net_pnl <= -self.daily_stop_loss:
            self._is_halted = True
            self._halt_reason = f"Daily Stop-Loss triggered (-${self.daily_stop_loss:.2f})"
        elif self.net_pnl >= self.daily_take_profit:
            self._is_halted = True
            self._halt_reason = f"Daily Take-Profit achieved (+${self.daily_take_profit:.2f})"

    def get_summary(self) -> str:
        """Return formatted performance summary string."""
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0.0
        return (
            f"Trades: {self.total_trades} (W: {self.winning_trades} | L: {self.losing_trades}) | "
            f"Win Rate: {win_rate:.1f}% | Net PnL: ${self.net_pnl:+.2f} | "
            f"Current Balance: ${self.current_balance:.2f}"
        )
