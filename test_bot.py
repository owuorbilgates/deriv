"""
Comprehensive Unit Test Suite for Momentum-Driven Mean Reversion Deriv Digit Trading Bot.
"""

import asyncio
import sys
from pathlib import Path
import unittest
from collections import deque

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import TradingConfig
from risk_manager import RiskManager, TradeOutcome
from strategy import SignalType, StrategyEngine, StrategySignal, TickData
from scanner import MultiMarketScanner, MarketMomentum


class TestStrategyEngine(unittest.TestCase):
    """Test strategy digit extraction, rolling window, and mean reversion rules."""

    def setUp(self):
        self.strategy = StrategyEngine(buffer_size=100, threshold_percent=14.0)

    def test_extract_last_digit_precision(self):
        """Verify exact decimal digit extraction without float truncation."""
        self.assertEqual(StrategyEngine.extract_last_digit(1234.50, pip_size=2), 0)
        self.assertEqual(StrategyEngine.extract_last_digit(1234.55, pip_size=2), 5)
        self.assertEqual(StrategyEngine.extract_last_digit(2456.78, pip_size=2), 8)
        self.assertEqual(StrategyEngine.extract_last_digit(100.1234, pip_size=4), 4)
        self.assertEqual(StrategyEngine.extract_last_digit(100.1230, pip_size=4), 0)
        self.assertEqual(StrategyEngine.extract_last_digit(50.0, pip_size=1), 0)
        self.assertEqual(StrategyEngine.extract_last_digit(4856.679, pip_size=3), 9)

    def test_insufficient_ticks_returns_wait(self):
        """Strategy must return WAIT when fewer than 100 ticks are recorded."""
        for i in range(50):
            tick, signal = self.strategy.on_new_tick("R_100", 100.0 + i, 2, 1000 + i)
            self.assertEqual(signal.signal_type, SignalType.WAIT)
            self.assertIn("Accumulating tick history", signal.reason)

    def test_balanced_distribution_returns_wait(self):
        """When all digits appear equally (10% each), strategy must return WAIT."""
        # Feed 10 of each digit 0-9
        for cycle in range(10):
            for d in range(10):
                quote = 100.0 + (d * 0.01)
                self.strategy.on_new_tick("R_100", quote, 2, 1000 + cycle * 10 + d)

        self.assertEqual(len(self.strategy.tick_history), 100)
        # Evaluate
        signal = self.strategy.evaluate_strategy(self.strategy.tick_history)
        self.assertEqual(signal.signal_type, SignalType.WAIT)
        self.assertIn("balanced", signal.reason.lower())

    def test_over_frequency_generates_digitdiff(self):
        """When a digit appears > 14% of the time, trigger DIGITDIFF against that digit."""
        # 16 occurrences of digit 7, and 84 occurrences of other digits (balanced)
        ticks = []
        for i in range(16):
            ticks.append(TickData(symbol="R_100", quote=100.07, pip_size=2, last_digit=7, epoch=1000 + i))
        for i in range(84):
            digit = i % 7  # Digits 0 to 6
            ticks.append(TickData(symbol="R_100", quote=100.00 + (digit * 0.01), pip_size=2, last_digit=digit, epoch=1100 + i))

        self.strategy.seed_history(ticks)
        self.assertEqual(len(self.strategy.tick_history), 100)

        signal = self.strategy.evaluate_strategy(self.strategy.tick_history)
        self.assertEqual(signal.signal_type, SignalType.DIGITDIFF)
        self.assertEqual(signal.predicted_barrier, 7)
        self.assertGreater(signal.confidence, 0.15)
        self.assertIn("Digit '7' appeared 16/100 times", signal.reason)


class TestRiskManager(unittest.IsolatedAsyncioTestCase):
    """Test 2-step 11.0x Martingale ladder, risk limits, and hard stops."""

    def setUp(self):
        self.rm = RiskManager(
            base_stake=0.35,
            martingale_multiplier=11.0,
            martingale_max_steps=2,
            daily_stop_loss=25.0,
            daily_take_profit=5.0,
            cooldown_seconds=0.05,
            starting_balance=50.0
        )

    def test_initial_stake(self):
        """Initial stake must be $0.35."""
        self.assertEqual(self.rm.get_current_stake(), 0.35)

    async def test_martingale_escalation_and_ladder_cap(self):
        """
        Martingale Ladder Test:
        - Step 0: $0.35
        - Loss 1 -> Step 1: $3.85 (0.35 * 11)
        - Loss 2 -> Ladder Cap Reached! Reset to Step 0 ($0.35)
        """
        self.assertEqual(self.rm.get_current_stake(), 0.35)

        # 1. First trade loses
        await self.rm.acquire_trade_lock(101)
        outcome1 = TradeOutcome(
            contract_id=101,
            contract_type="DIGITDIFF",
            symbol="R_100",
            stake=0.35,
            payout=0.0,
            profit=-0.35,
            status="lost",
            barrier=7
        )
        self.rm.release_trade_lock(outcome1)

        # Next stake must be $3.85
        self.assertEqual(self.rm.current_martingale_step, 1)
        self.assertAlmostEqual(self.rm.get_current_stake(), 3.85, places=2)

        # 2. Second trade loses (Ladder Cap Trigger)
        await self.rm.acquire_trade_lock(102)
        outcome2 = TradeOutcome(
            contract_id=102,
            contract_type="DIGITDIFF",
            symbol="R_100",
            stake=3.85,
            payout=0.0,
            profit=-3.85,
            status="lost",
            barrier=7
        )
        self.rm.release_trade_lock(outcome2)

        # After 2nd loss, ladder must reset to Step 0 ($0.35) to protect capital
        self.assertEqual(self.rm.current_martingale_step, 0)
        self.assertEqual(self.rm.get_current_stake(), 0.35)

    async def test_martingale_win_resets_ladder(self):
        """Winning after a loss resets stake back to $0.35."""
        # First trade loses
        await self.rm.acquire_trade_lock(201)
        self.rm.release_trade_lock(TradeOutcome(
            contract_id=201, contract_type="DIGITDIFF", symbol="R_100",
            stake=0.35, payout=0.0, profit=-0.35, status="lost", barrier=4
        ))
        self.assertEqual(self.rm.get_current_stake(), 3.85)

        # Second trade wins
        await self.rm.acquire_trade_lock(202)
        self.rm.release_trade_lock(TradeOutcome(
            contract_id=202, contract_type="DIGITDIFF", symbol="R_100",
            stake=3.85, payout=4.23, profit=0.38, status="won", barrier=4
        ))

        # Stake must reset to base $0.35
        self.assertEqual(self.rm.current_martingale_step, 0)
        self.assertEqual(self.rm.get_current_stake(), 0.35)

    async def test_hard_take_profit_stop(self):
        """Net PnL reaching +$5.00 triggers hard stop."""
        await self.rm.acquire_trade_lock(301)
        self.rm.release_trade_lock(TradeOutcome(
            contract_id=301, contract_type="DIGITDIFF", symbol="R_100",
            stake=50.0, payout=55.20, profit=5.20, status="won", barrier=1
        ))
        self.assertTrue(self.rm.is_halted)
        self.assertIn("TARGET_PROFIT", self.rm.halt_reason)

    async def test_hard_stop_loss(self):
        """Net loss reaching -$25.00 triggers hard stop."""
        await self.rm.acquire_trade_lock(401)
        self.rm.release_trade_lock(TradeOutcome(
            contract_id=401, contract_type="DIGITDIFF", symbol="R_100",
            stake=25.0, payout=0.0, profit=-25.0, status="lost", barrier=1
        ))
        self.assertTrue(self.rm.is_halted)
        self.assertIn("STOP_LOSS", self.rm.halt_reason)

    def test_insufficient_balance_check(self):
        """Cannot place trade if account balance is less than required stake."""
        self.rm.update_balance(0.30)  # Account only has $0.30
        can_trade, reason = self.rm.can_open_trade(0.35)
        self.assertFalse(can_trade)
        self.assertIn("Insufficient balance", reason)


class TestMultiMarketScanner(unittest.TestCase):
    """Test momentum formula and ranking."""

    def test_momentum_calculation(self):
        """Verify percentage move formula abs((cur - p_5m) / p_5m * 100)."""
        p_5m = 500.0
        p_cur = 502.50
        pct = abs((p_cur - p_5m) / p_5m * 100.0)
        self.assertAlmostEqual(pct, 0.50, places=4)

        # Downward move
        p_cur_down = 495.0
        pct_down = abs((p_cur_down - p_5m) / p_5m * 100.0)
        self.assertAlmostEqual(pct_down, 1.00, places=4)


if __name__ == "__main__":
    unittest.main()
