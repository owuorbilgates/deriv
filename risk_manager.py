"""
Risk Management Module for Deriv Digit Trading Bot.

Enforces:
1. 2-Step 11.0x Martingale Recovery Protocol for Digit Differs:
   - Step 0 (Base Stake): $0.35 USD
   - Step 1 (After 1 loss): $0.35 * 11.0 = $3.85 USD
   - If Step 1 wins -> Reset to Step 0 ($0.35)
   - If Step 1 loses -> Ladder Cap Reached (2 steps), accept loss and reset to Step 0 ($0.35)
2. Hard Session Stops:
   - TARGET_PROFIT (e.g. $5.00) -> immediately terminates
   - STOP_LOSS (e.g. $25.00) -> immediately terminates
3. Concurrency Lock: Strictly 1 open contract at a time.
4. Account Balance Verification: Prevents orders exceeding account balance.
5. Performance and Win-Rate tracking.
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
    Implements the 2-step 11.0x Martingale ladder and hard profit/loss boundaries.
    """

    def __init__(
        self,
        base_stake: float = 0.35,
        martingale_multiplier: float = 11.0,
        martingale_max_steps: int = 2,
        daily_stop_loss: float = 25.0,
        daily_take_profit: float = 5.0,
        cooldown_seconds: float = 3.0,
        max_consecutive_losses: int = 4,
        starting_balance: float = 0.0
    ):
        """
        Initialize Risk Manager.
        
        Args:
            base_stake: Starting stake per trade (e.g. $0.35 USD).
            martingale_multiplier: Multiplier upon loss (default: 11.0x).
            martingale_max_steps: Maximum consecutive Martingale steps before resetting (default: 2).
            daily_stop_loss: Max acceptable session loss in USD (e.g. $25.00).
            daily_take_profit: Target session profit in USD (e.g. $5.00).
            cooldown_seconds: Enforced quiet period after trade settlement.
            max_consecutive_losses: Emergency stop after N successive losses.
            starting_balance: Initial account balance.
        """
        self.base_stake = base_stake
        self.martingale_multiplier = martingale_multiplier
        self.martingale_max_steps = martingale_max_steps
        self.daily_stop_loss = daily_stop_loss
        self.daily_take_profit = daily_take_profit
        self.cooldown_seconds = cooldown_seconds
        self.max_consecutive_losses = max_consecutive_losses
        
        self.starting_balance = starting_balance
        self.current_balance = starting_balance
        
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
        """
        Calculate current trade stake according to Martingale ladder:
        - Step 0: base_stake (e.g. $0.35)
        - Step 1: round(base_stake * 11.0, 2) (e.g. $3.85)
        """
        if self.current_martingale_step == 0:
            return round(self.base_stake, 2)
        else:
            stake = self.base_stake * (self.martingale_multiplier ** self.current_martingale_step)
            return round(stake, 2)

    def update_balance(self, new_balance: float) -> None:
        """Set or update current account balance."""
        if self.starting_balance == 0.0:
            self.starting_balance = new_balance
        self.current_balance = new_balance

    def can_open_trade(self, stake: Optional[float] = None) -> Tuple[bool, str]:
        """
        Evaluate all risk checks to determine if a new trade can be initiated.
        
        Args:
            stake: Planned stake amount. If None, uses get_current_stake().
            
        Returns:
            Tuple of (can_trade: bool, reason: str)
        """
        if stake is None:
            stake = self.get_current_stake()

        # Check if already halted
        if self._is_halted:
            return False, f"Trading halted: {self._halt_reason}"

        # Check concurrency lock
        if self.is_trade_active:
            return False, f"Active trade in progress (Contract ID: #{self._active_trade_id})"

        # Check post-trade cooldown
        now = time.time()
        elapsed_since_last = now - self._last_trade_closed_time
        if elapsed_since_last < self.cooldown_seconds:
            remaining = round(self.cooldown_seconds - elapsed_since_last, 1)
            return False, f"In post-trade cooldown ({remaining}s remaining)"

        # Check Hard Stop-Loss
        if self.net_pnl <= -self.daily_stop_loss:
            self._is_halted = True
            self._halt_reason = (
                f"STOP_LOSS limit hit: Net PnL is -${abs(self.net_pnl):.2f} "
                f"(Limit: -${self.daily_stop_loss:.2f})"
            )
            logger.critical(f"🛑 [HARD STOP] {self._halt_reason}")
            return False, self._halt_reason

        # Check Hard Take-Profit
        if self.net_pnl >= self.daily_take_profit:
            self._is_halted = True
            self._halt_reason = (
                f"TARGET_PROFIT achieved: Net PnL is +${self.net_pnl:.2f} "
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
            return False, (
                f"Insufficient balance (${self.current_balance:.2f} USD) for required stake (${stake:.2f} USD). "
                f"Deposit funds or reset Martingale ladder."
            )

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
        Record completed trade outcome, update metrics, advance/reset Martingale ladder,
        release lock, and trigger cooldown.
        
        Args:
            outcome: TradeOutcome details.
        """
        self.total_trades += 1
        self.net_pnl += outcome.profit

        if outcome.status == "won":
            self.winning_trades += 1
            self.consecutive_losses = 0
            prev_step = self.current_martingale_step
            self.current_martingale_step = 0  # Reset to base stake on win
            
            logger.info(
                f"✅ [WIN] Contract #{outcome.contract_id} | Stake: ${outcome.stake:.2f} | "
                f"Profit: +${outcome.profit:.2f} | Session Net: ${self.net_pnl:+.2f} | "
                f"Martingale Step Reset ({prev_step} -> 0, Next Stake: ${self.get_current_stake():.2f})"
            )
        else:
            self.losing_trades += 1
            self.consecutive_losses += 1
            self.current_martingale_step += 1

            if self.current_martingale_step >= self.martingale_max_steps:
                # Ladder Cap Reached (2 steps: Trade 1 and Trade 2 lost)
                logger.warning(
                    f"⚠️ [LADDER CAP] Martingale maximum steps ({self.martingale_max_steps}) reached! "
                    f"Accepting loss to protect account balance. Resetting ladder to Step 0 (${self.base_stake:.2f})."
                )
                self.current_martingale_step = 0
            else:
                next_stake = self.get_current_stake()
                logger.warning(
                    f"❌ [LOSS] Contract #{outcome.contract_id} | Stake: ${outcome.stake:.2f} | "
                    f"Loss: -${abs(outcome.profit):.2f} | Session Net: ${self.net_pnl:+.2f} | "
                    f"Martingale Escalation: Step {self.current_martingale_step}/{self.martingale_max_steps} "
                    f"(Next Stake: ${next_stake:.2f})"
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
            self._halt_reason = f"STOP_LOSS limit reached (-${self.daily_stop_loss:.2f})"
        elif self.net_pnl >= self.daily_take_profit:
            self._is_halted = True
            self._halt_reason = f"TARGET_PROFIT achieved (+${self.daily_take_profit:.2f})"

    def get_summary(self) -> str:
        """Return formatted performance summary string."""
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0.0
        return (
            f"Trades: {self.total_trades} (W: {self.winning_trades} | L: {self.losing_trades}) | "
            f"Win Rate: {win_rate:.1f}% | Net PnL: ${self.net_pnl:+.2f} | "
            f"Current Balance: ${self.current_balance:.2f} | Next Stake: ${self.get_current_stake():.2f}"
        )
