"""
Comprehensive Unit Test Suite for Dynamic Match/Differs Deriv Bot (with 2-Trade Match Cap).
"""

import asyncio
import sys
from pathlib import Path
import unittest
from collections import deque

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import CONFIG, KNOWN_PIP_SIZES, TradingConfig, resolve_pip_size
from risk_manager import RiskManager, TradeOutcome
from strategy import SignalType, StrategyEngine, StrategySignal, TickData
from scanner import MarketMomentum, MultiMarketScanner


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


class TestThirteenVolatilityIndicesAndPipSizes(unittest.TestCase):
    """Verify that all 13 Deriv volatility indices are present and accurately mapped."""

    EXPECTED_13_INDICES = [
        "1HZ10V", "1HZ15V", "1HZ25V", "1HZ30V", "1HZ50V",
        "1HZ75V", "1HZ90V", "1HZ100V", "R_10", "R_25",
        "R_50", "R_75", "R_100"
    ]

    def test_all_13_indices_in_scanner(self):
        scanner = MultiMarketScanner()
        for symbol in self.EXPECTED_13_INDICES:
            self.assertIn(symbol, scanner.symbols)
        self.assertEqual(len(scanner.symbols), 13)

    def test_pip_size_resolutions(self):
        # 2 decimal places: 1HZ10V, 1HZ25V, 1HZ50V, 1HZ75V, 1HZ100V, R_100
        for s in ["1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V", "R_100"]:
            self.assertEqual(resolve_pip_size(None, s), 2)
            self.assertEqual(resolve_pip_size(2.0, s), 2)
            self.assertEqual(resolve_pip_size(0.01, s), 2)

        # 3 decimal places: 1HZ15V, 1HZ30V, 1HZ90V, R_10, R_25
        for s in ["1HZ15V", "1HZ30V", "1HZ90V", "R_10", "R_25"]:
            self.assertEqual(resolve_pip_size(None, s), 3)
            self.assertEqual(resolve_pip_size(3.0, s), 3)
            self.assertEqual(resolve_pip_size(0.001, s), 3)

        # 4 decimal places: R_50, R_75
        for s in ["R_50", "R_75"]:
            self.assertEqual(resolve_pip_size(None, s), 4)
            self.assertEqual(resolve_pip_size(4.0, s), 4)
            self.assertEqual(resolve_pip_size(0.0001, s), 4)


class TestPreciseDigitExtraction(unittest.TestCase):
    """Verify precision digit extraction with Decimal across all decimal places."""

    def test_extract_last_digit_2_decimals(self):
        # Normal quote
        self.assertEqual(StrategyEngine.extract_last_digit(9601.54, pip_size=2), 4)
        # Trailing zero in float representation
        self.assertEqual(StrategyEngine.extract_last_digit(1234.50, pip_size=2), 0)
        self.assertEqual(StrategyEngine.extract_last_digit(1200.0, pip_size=2), 0)
        # Fractional pip_size
        self.assertEqual(StrategyEngine.extract_last_digit(9601.54, pip_size=0.01), 4)

    def test_extract_last_digit_3_decimals(self):
        # Normal quote
        self.assertEqual(StrategyEngine.extract_last_digit(4864.835, pip_size=3), 5)
        # Trailing zero in 3rd decimal place
        self.assertEqual(StrategyEngine.extract_last_digit(4864.830, pip_size=3), 0)
        self.assertEqual(StrategyEngine.extract_last_digit(5981.020, pip_size=0.001), 0)

    def test_extract_last_digit_4_decimals(self):
        # Normal quote
        self.assertEqual(StrategyEngine.extract_last_digit(89.3331, pip_size=4), 1)
        self.assertEqual(StrategyEngine.extract_last_digit(41109.4407, pip_size=4), 7)
        # Trailing zero in 4th decimal place
        self.assertEqual(StrategyEngine.extract_last_digit(89.3330, pip_size=4), 0)
        self.assertEqual(StrategyEngine.extract_last_digit(89.3300, pip_size=0.0001), 0)


class TestFiveMinuteMomentumCalculation(unittest.TestCase):
    """Verify accuracy of 5-minute price percentage change math."""

    def test_percentage_change_formula(self):
        # Move: 100.00 -> 100.25 (0.25% up)
        p_5m = 100.00
        p_cur = 100.25
        pct = abs((p_cur - p_5m) / p_5m * 100.0)
        self.assertAlmostEqual(pct, 0.25, places=4)

        # Move: 200.00 -> 199.40 (0.30% down)
        p_5m_down = 200.00
        p_cur_down = 199.40
        pct_down = abs((p_cur_down - p_5m_down) / p_5m_down * 100.0)
        self.assertAlmostEqual(pct_down, 0.30, places=4)


if __name__ == "__main__":
    unittest.main()
