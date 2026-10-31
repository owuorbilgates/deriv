"""
Comprehensive Unit Test Suite for Dynamic Match/Differs Deriv Bot (with 2-Trade Match Cap).
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


class TestMatchCapProtocol(unittest.TestCase):
    """Test 2-trade Match cap: exactly 2 trades in Match mode before returning to Differs."""

    def setUp(self):
        self.strategy = StrategyEngine(
            buffer_size=100,
            threshold_percent=18.0,
            strategy_mode="DIFFERS",
            blacklist_duration_trades=10,
            dynamic_mode_switch=True,
            max_match_attempts=2
        )

    def test_initial_state_is_differs(self):
        self.assertEqual(self.strategy.strategy_mode, "DIFFERS")

    def test_differs_loss_triggers_match_mode(self):
        # Differs loss
        self.strategy.on_trade_completed(status="lost", barrier=4)
        self.assertEqual(self.strategy.strategy_mode, "MATCH")
        self.assertEqual(self.strategy.match_attempts, 0)

    def test_match_win_on_trade_1_returns_to_differs(self):
        # Trigger switch to MATCH
        self.strategy.on_trade_completed(status="lost", barrier=4)
        self.assertEqual(self.strategy.strategy_mode, "MATCH")

        # Trade 1 in Match mode WINS
        self.strategy.on_trade_completed(status="won", barrier=7)
        self.assertEqual(self.strategy.strategy_mode, "DIFFERS")
        self.assertEqual(self.strategy.match_attempts, 0)

    def test_match_win_on_trade_2_returns_to_differs(self):
        # Trigger switch to MATCH
        self.strategy.on_trade_completed(status="lost", barrier=4)
        self.assertEqual(self.strategy.strategy_mode, "MATCH")

        # Trade 1 in Match mode LOSES -> stays in MATCH for 2nd attempt
        self.strategy.on_trade_completed(status="lost", barrier=2)
        self.assertEqual(self.strategy.strategy_mode, "MATCH")
        self.assertEqual(self.strategy.match_attempts, 1)

        # Trade 2 in Match mode WINS -> switches back to DIFFERS!
        self.strategy.on_trade_completed(status="won", barrier=8)
        self.assertEqual(self.strategy.strategy_mode, "DIFFERS")
        self.assertEqual(self.strategy.match_attempts, 0)

    def test_match_cap_at_2_losses_returns_to_differs(self):
        """
        Verify that 2 losses in Match mode strictly stops bleeding and reverts to DIFFERS!
        """
        # Trigger switch to MATCH
        self.strategy.on_trade_completed(status="lost", barrier=4)
        self.assertEqual(self.strategy.strategy_mode, "MATCH")

        # Trade 1 in Match mode LOSES
        self.strategy.on_trade_completed(status="lost", barrier=2)
        self.assertEqual(self.strategy.strategy_mode, "MATCH")
        self.assertEqual(self.strategy.match_attempts, 1)

        # Trade 2 in Match mode LOSES -> Must hit 2-trade limit and switch to DIFFERS!
        self.strategy.on_trade_completed(status="lost", barrier=9)
        self.assertEqual(self.strategy.strategy_mode, "DIFFERS")
        self.assertEqual(self.strategy.match_attempts, 0)

    def test_subsequent_cycle_operates_identically(self):
        """After reverting to Differs, a subsequent Differs loss can trigger Match again."""
        # Cycle 1: Differs loss -> 2 Match losses -> back to Differs
        self.strategy.on_trade_completed(status="lost", barrier=1)
        self.strategy.on_trade_completed(status="lost", barrier=2)
        self.strategy.on_trade_completed(status="lost", barrier=3)
        self.assertEqual(self.strategy.strategy_mode, "DIFFERS")

        # Next Differs trade wins
        self.strategy.on_trade_completed(status="won", barrier=5)
        self.assertEqual(self.strategy.strategy_mode, "DIFFERS")

        # Next Differs trade loses -> starts Cycle 2 in MATCH
        self.strategy.on_trade_completed(status="lost", barrier=6)
        self.assertEqual(self.strategy.strategy_mode, "MATCH")
        self.assertEqual(self.strategy.match_attempts, 0)


class TestRiskManagerFlatStaking(unittest.IsolatedAsyncioTestCase):
    def test_flat_stake(self):
        rm = RiskManager(base_stake=0.35, use_martingale=False)
        self.assertEqual(rm.get_current_stake(), 0.35)
        rm.release_trade_lock(TradeOutcome(
            contract_id=1, contract_type="DIGITDIFF", symbol="R_100",
            stake=0.35, payout=0.0, profit=-0.35, status="lost", barrier=7
        ))
        self.assertEqual(rm.get_current_stake(), 0.35)


class TestDigitExtraction(unittest.TestCase):
    def test_precision(self):
        self.assertEqual(StrategyEngine.extract_last_digit(1234.50, pip_size=2), 0)
        self.assertEqual(StrategyEngine.extract_last_digit(4856.679, pip_size=3), 9)


if __name__ == "__main__":
    unittest.main()
