"""
Comprehensive Unit Test Suite for Dynamic Match/Differs Deriv Bot.
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
from scanner import MultiMarketScanner


class TestDynamicMatchDiffersStrategy(unittest.TestCase):
    """Test dynamic switching: Differs -> on loss -> Match -> on win -> Differs."""

    def setUp(self):
        self.strategy = StrategyEngine(
            buffer_size=100,
            threshold_percent=18.0,
            strategy_mode="DIFFERS",
            blacklist_duration_trades=10,
            dynamic_mode_switch=True
        )

    def test_initial_state_is_differs(self):
        """Must start in DIFFERS mode."""
        self.assertEqual(self.strategy.strategy_mode, "DIFFERS")

    def test_loss_in_differs_switches_to_match(self):
        """When a Differs trade loses, state must switch to MATCH (No Martingale)."""
        self.assertEqual(self.strategy.strategy_mode, "DIFFERS")
        
        # Simulate loss on digit 3
        self.strategy.on_trade_completed(status="lost", barrier=3)

        self.assertEqual(self.strategy.strategy_mode, "MATCH")
        self.assertIn(3, self.strategy.digit_blacklist)

    def test_win_in_match_switches_back_to_differs(self):
        """When a Match trade wins, state must switch back to DIFFERS."""
        # Force into MATCH mode
        self.strategy.strategy_mode = "MATCH"

        # Simulate winning Match trade
        self.strategy.on_trade_completed(status="won", barrier=7)

        self.assertEqual(self.strategy.strategy_mode, "DIFFERS")

    def test_full_cycle_state_transitions(self):
        """
        Full Lifecycle Cycle Test:
        1. DIFFERS win -> remains DIFFERS
        2. DIFFERS loss -> switches to MATCH
        3. MATCH loss -> remains MATCH
        4. MATCH win -> switches back to DIFFERS
        """
        # 1. Win in DIFFERS
        self.strategy.on_trade_completed(status="won", barrier=1)
        self.assertEqual(self.strategy.strategy_mode, "DIFFERS")

        # 2. Loss in DIFFERS -> triggers pivot to MATCH
        self.strategy.on_trade_completed(status="lost", barrier=1)
        self.assertEqual(self.strategy.strategy_mode, "MATCH")

        # 3. Another loss in MATCH -> remains in MATCH
        self.strategy.on_trade_completed(status="lost", barrier=4)
        self.assertEqual(self.strategy.strategy_mode, "MATCH")

        # 4. Win in MATCH -> triggers restoration to DIFFERS!
        self.strategy.on_trade_completed(status="won", barrier=8)
        self.assertEqual(self.strategy.strategy_mode, "DIFFERS")

    def test_signal_generation_matches_active_state(self):
        """Verify StrategySignal output type matches the current state."""
        ticks = []
        for i in range(20):
            ticks.append(TickData(symbol="R_100", quote=100.05, pip_size=2, last_digit=5, epoch=1000 + i))
        for i in range(80):
            d = (i % 9) if (i % 9) != 5 else 8
            ticks.append(TickData(symbol="R_100", quote=100.00 + (d * 0.01), pip_size=2, last_digit=d, epoch=1100 + i))
        self.strategy.seed_history(ticks)

        # In DIFFERS state: produces DIGITDIFF
        sig_diff = self.strategy.evaluate_strategy(self.strategy.tick_history)
        self.assertEqual(sig_diff.signal_type, SignalType.DIGITDIFF)
        self.assertEqual(sig_diff.predicted_barrier, 5)

        # Simulate loss -> pivots to MATCH
        self.strategy.on_trade_completed(status="lost", barrier=2)
        self.assertEqual(self.strategy.strategy_mode, "MATCH")

        # In MATCH state: produces DIGITMATCH on hot digit 5!
        sig_match = self.strategy.evaluate_strategy(self.strategy.tick_history)
        self.assertEqual(sig_match.signal_type, SignalType.DIGITMATCH)
        self.assertEqual(sig_match.predicted_barrier, 5)


class TestRiskManagerFlatStaking(unittest.IsolatedAsyncioTestCase):
    """Verify flat $0.35 stake without 11x Martingale multiplier."""

    def test_flat_stake_throughout_drawdown(self):
        rm = RiskManager(base_stake=0.35, use_martingale=False)
        self.assertEqual(rm.get_current_stake(), 0.35)

        # 3 consecutive losses
        for i in range(3):
            outcome = TradeOutcome(
                contract_id=100 + i, contract_type="DIGITDIFF", symbol="R_100",
                stake=0.35, payout=0.0, profit=-0.35, status="lost", barrier=7
            )
            rm.release_trade_lock(outcome)
            # Must remain flat $0.35
            self.assertEqual(rm.get_current_stake(), 0.35)


class TestDigitExtraction(unittest.TestCase):
    def test_precision(self):
        self.assertEqual(StrategyEngine.extract_last_digit(1234.50, pip_size=2), 0)
        self.assertEqual(StrategyEngine.extract_last_digit(4856.679, pip_size=3), 9)


if __name__ == "__main__":
    unittest.main()
