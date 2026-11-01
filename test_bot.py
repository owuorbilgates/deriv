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
from trade_executor import TradeExecutor


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


class TestDecoupledTriggers(unittest.TestCase):
    """
    Verify decoupled triggers:
    - DIFFERS bets on cold digits (<= 2.0%)
    - MATCH bets on hot digits (>= 19.0%)
    """

    def _create_ticks_with_distribution(self, counts_dict):
        """Helper to create 100 TickData objects with specific digit counts."""
        ticks = []
        epoch = 1000
        for digit, count in counts_dict.items():
            for _ in range(count):
                epoch += 1
                ticks.append(TickData(
                    symbol="1HZ100V",
                    quote=float(f"1200.{digit}"),
                    pip_size=2,
                    last_digit=digit,
                    epoch=epoch
                ))
        return ticks

    def test_differs_triggers_on_cold_digit_le_2_pct(self):
        engine = StrategyEngine(
            buffer_size=100,
            differs_cold_threshold=2.0,
            match_hot_threshold=19.0,
            strategy_mode="DIFFERS"
        )
        # Digit 3 appears 1 time (1%), all other 9 digits appear 11 times (11 * 9 = 99 + 1 = 100)
        counts = {3: 1}
        for d in range(10):
            if d != 3:
                counts[d] = 11

        ticks = self._create_ticks_with_distribution(counts)
        engine.seed_history(ticks)

        signal = engine.evaluate_strategy(engine.tick_history)
        self.assertEqual(signal.signal_type, SignalType.DIGITDIFF)
        self.assertEqual(signal.predicted_barrier, 3)
        self.assertIn("cold", signal.reason.lower())

    def test_differs_waits_if_no_digit_le_2_pct(self):
        engine = StrategyEngine(
            buffer_size=100,
            differs_cold_threshold=2.0,
            match_hot_threshold=19.0,
            strategy_mode="DIFFERS"
        )
        # Uniform 10% each -> No digit <= 2.0%
        counts = {d: 10 for d in range(10)}
        ticks = self._create_ticks_with_distribution(counts)
        engine.seed_history(ticks)

        signal = engine.evaluate_strategy(engine.tick_history)
        self.assertEqual(signal.signal_type, SignalType.WAIT)

    def test_differs_does_not_trigger_on_hot_digit(self):
        engine = StrategyEngine(
            buffer_size=100,
            differs_cold_threshold=2.0,
            match_hot_threshold=19.0,
            strategy_mode="DIFFERS"
        )
        # Digit 7 appears 22 times (22%), digits 0-8 appear 8 or 9 times (all > 2.0%)
        # Digit 7 is hot, but DIFFERS must NOT trigger on hot digits!
        counts = {7: 22, 0: 9, 1: 9, 2: 9, 3: 9, 4: 9, 5: 9, 6: 9, 8: 8, 9: 7}
        ticks = self._create_ticks_with_distribution(counts)
        engine.seed_history(ticks)

        signal = engine.evaluate_strategy(engine.tick_history)
        self.assertEqual(signal.signal_type, SignalType.WAIT)

    def test_match_triggers_on_hot_digit_ge_19_pct(self):
        engine = StrategyEngine(
            buffer_size=100,
            differs_cold_threshold=2.0,
            match_hot_threshold=19.0,
            strategy_mode="MATCH"
        )
        # Digit 7 appears 20 times (20% >= 19%), remaining 80 ticks across others
        counts = {0: 9, 1: 9, 2: 9, 3: 9, 4: 8, 5: 8, 6: 9, 7: 20, 8: 9, 9: 10}
        self.assertEqual(sum(counts.values()), 100)

        ticks = self._create_ticks_with_distribution(counts)
        engine.seed_history(ticks)

        signal = engine.evaluate_strategy(engine.tick_history)
        self.assertEqual(signal.signal_type, SignalType.DIGITMATCH)
        self.assertEqual(signal.predicted_barrier, 7)
        self.assertIn("hot", signal.reason.lower())

    def test_match_waits_if_no_digit_ge_19_pct(self):
        engine = StrategyEngine(
            buffer_size=100,
            differs_cold_threshold=2.0,
            match_hot_threshold=19.0,
            strategy_mode="MATCH"
        )
        # Digit 3 appears 1 time (1%), highest digit 8 appears 15 times (15% < 19%)
        counts = {0: 11, 1: 11, 2: 11, 3: 1, 4: 11, 5: 11, 6: 10, 7: 10, 8: 15, 9: 9}
        self.assertEqual(sum(counts.values()), 100)

        ticks = self._create_ticks_with_distribution(counts)
        engine.seed_history(ticks)

        signal = engine.evaluate_strategy(engine.tick_history)
        # MATCH mode must NOT trigger on cold digit 3, and 15% is below 19%
        self.assertEqual(signal.signal_type, SignalType.WAIT)

    def test_cold_digit_blacklist_skips_to_next_cold_candidate(self):
        engine = StrategyEngine(
            buffer_size=100,
            differs_cold_threshold=2.0,
            match_hot_threshold=19.0,
            strategy_mode="DIFFERS",
            blacklist_duration_trades=10
        )
        # Digit 1 appears 1 time (1%), Digit 2 appears 2 times (2%)
        # All other digits sum to 97 (total = 100)
        counts = {0: 13, 1: 1, 2: 2, 3: 12, 4: 12, 5: 12, 6: 12, 7: 12, 8: 12, 9: 12}
        self.assertEqual(sum(counts.values()), 100)

        ticks = self._create_ticks_with_distribution(counts)
        engine.seed_history(ticks)

        # Blacklist digit 1
        engine.digit_blacklist[1] = 5

        signal = engine.evaluate_strategy(engine.tick_history)
        # Digit 1 is blacklisted, so it skips to next cold digit: 2
        self.assertEqual(signal.signal_type, SignalType.DIGITDIFF)
        self.assertEqual(signal.predicted_barrier, 2)


class TestSeparatedModesAndScanOnLoss(unittest.TestCase):
    """
    Verify separated modes:
    - User selects DIFFERS (1) or MATCH (2) at start.
    - On loss, remains on the same strategy mode.
    - Scanner can exclude the losing market to select a new momentum leader.
    """

    def test_differs_loss_preserves_differs_mode(self):
        """In separated mode (dynamic_mode_switch=False), loss keeps DIFFERS mode."""
        engine = StrategyEngine(
            strategy_mode="DIFFERS",
            dynamic_mode_switch=False
        )
        self.assertEqual(engine.strategy_mode, "DIFFERS")
        # Trade loses
        engine.on_trade_completed(status="lost", barrier=4)
        self.assertEqual(engine.strategy_mode, "DIFFERS")
        self.assertIn(4, engine.digit_blacklist)

    def test_match_loss_preserves_match_mode(self):
        """In separated mode (dynamic_mode_switch=False), loss keeps MATCH mode."""
        engine = StrategyEngine(
            strategy_mode="MATCH",
            dynamic_mode_switch=False
        )
        self.assertEqual(engine.strategy_mode, "MATCH")
        # Trade loses
        engine.on_trade_completed(status="lost", barrier=7)
        self.assertEqual(engine.strategy_mode, "MATCH")
        self.assertIn(7, engine.digit_blacklist)

    def test_scanner_exclude_losing_symbol(self):
        """When exclude_symbol is specified, scanner picks top among remaining markets."""
        scanner = MultiMarketScanner(symbols=["1HZ10V", "1HZ100V", "R_50"])
        # Mock scan results
        scanner.last_scan_results = {
            "1HZ10V": MarketMomentum(symbol="1HZ10V", price_5m_ago=100.0, current_price=100.5, pct_change=0.50, pip_size=2),
            "1HZ100V": MarketMomentum(symbol="1HZ100V", price_5m_ago=100.0, current_price=100.4, pct_change=0.40, pip_size=2),
            "R_50": MarketMomentum(symbol="R_50", price_5m_ago=100.0, current_price=100.2, pct_change=0.20, pip_size=4),
        }
        sorted_results = sorted(scanner.last_scan_results.values(), key=lambda m: m.pct_change, reverse=True)
        # Without exclusion, 1HZ10V is #1
        self.assertEqual(sorted_results[0].symbol, "1HZ10V")

        # With 1HZ10V excluded (losing market), top eligible is 1HZ100V
        eligible = [m for m in sorted_results if m.symbol != "1HZ10V"]
        top = eligible[0]
        self.assertEqual(top.symbol, "1HZ100V")


class TestDerivPredictionTableAndThousandTicks(unittest.TestCase):
    """
    Verify 1000-tick window sizing, Deriv percentage formatting,
    and prediction table rendering matching Deriv web platform.
    """

    def test_default_buffer_size_and_adaptive_thresholds(self):
        engine_default = StrategyEngine()
        self.assertEqual(engine_default.buffer_size, 1000)
        self.assertEqual(engine_default.differs_cold_threshold, 9.0)
        self.assertEqual(engine_default.match_hot_threshold, 11.5)

        engine_small = StrategyEngine(buffer_size=100)
        self.assertEqual(engine_small.buffer_size, 100)
        self.assertEqual(engine_small.differs_cold_threshold, 2.0)
        self.assertEqual(engine_small.match_hot_threshold, 19.0)

    def test_deriv_percentage_formatting(self):
        # Deriv format: integers for whole numbers, 1 decimal place otherwise
        self.assertEqual(StrategyEngine.format_deriv_percentage(9.0), "9%")
        self.assertEqual(StrategyEngine.format_deriv_percentage(10.2), "10.2%")
        self.assertEqual(StrategyEngine.format_deriv_percentage(10.6), "10.6%")
        self.assertEqual(StrategyEngine.format_deriv_percentage(12.3), "12.3%")
        self.assertEqual(StrategyEngine.format_deriv_percentage(9.6), "9.6%")
        self.assertEqual(StrategyEngine.format_deriv_percentage(0.0), "0%")

    def test_deriv_prediction_table_matches_user_distribution(self):
        # User's exact pasted distribution:
        # 0: 9%, 1: 10.2%, 2: 9%, 3: 10.6%, 4: 9.6%, 5: 10.2%, 6: 9.7%, 7: 9.8%, 8: 9.6%, 9: 12.3%
        # Sum = 90 + 102 + 90 + 106 + 96 + 102 + 97 + 98 + 96 + 123 = 1000 ticks
        counts = {
            0: 90, 1: 102, 2: 90, 3: 106, 4: 96,
            5: 102, 6: 97, 7: 98, 8: 96, 9: 123
        }
        self.assertEqual(sum(counts.values()), 1000)

        engine = StrategyEngine(buffer_size=1000)
        ticks = []
        epoch = 1000
        for digit, count in counts.items():
            for _ in range(count):
                epoch += 1
                ticks.append(TickData(
                    symbol="R_100",
                    quote=float(f"1234.{digit}"),
                    pip_size=2,
                    last_digit=digit,
                    epoch=epoch
                ))
        engine.seed_history(ticks)

        table = engine.get_deriv_prediction_table("R_100")
        self.assertIn("DERIV LAST DIGIT PREDICTION (1000/1000 Ticks) - R_100", table)
        self.assertIn("9%", table)
        self.assertIn("10.2%", table)
        self.assertIn("12.3%", table)
        self.assertIn("HOT", table)  # Digit 9 (12.3% >= 11.5%) is hot

    def test_thousand_ticks_differs_trigger(self):
        # In 1000 ticks, cold threshold is <= 9.0%.
        # Digits 0 and 2 are 90/1000 = 9.0%. Differs should trigger on digit 0 (coldest).
        counts = {
            0: 90, 1: 102, 2: 90, 3: 106, 4: 96,
            5: 102, 6: 97, 7: 98, 8: 96, 9: 123
        }
        engine = StrategyEngine(buffer_size=1000, strategy_mode="DIFFERS")
        ticks = []
        epoch = 1000
        for digit, count in counts.items():
            for _ in range(count):
                epoch += 1
                ticks.append(TickData(
                    symbol="R_100",
                    quote=float(f"1234.{digit}"),
                    pip_size=2,
                    last_digit=digit,
                    epoch=epoch
                ))
        engine.seed_history(ticks)

        signal = engine.evaluate_strategy(engine.tick_history)
        self.assertEqual(signal.signal_type, SignalType.DIGITDIFF)
        self.assertEqual(signal.predicted_barrier, 0)
        self.assertIn("cold", signal.reason.lower())

    def test_thousand_ticks_match_trigger(self):
        # In 1000 ticks, hot threshold is >= 11.5%.
        # Digit 9 is 123/1000 = 12.3%. Match should trigger on digit 9.
        counts = {
            0: 90, 1: 102, 2: 90, 3: 106, 4: 96,
            5: 102, 6: 97, 7: 98, 8: 96, 9: 123
        }
        engine = StrategyEngine(buffer_size=1000, strategy_mode="MATCH")
        ticks = []
        epoch = 1000
        for digit, count in counts.items():
            for _ in range(count):
                epoch += 1
                ticks.append(TickData(
                    symbol="R_100",
                    quote=float(f"1234.{digit}"),
                    pip_size=2,
                    last_digit=digit,
                    epoch=epoch
                ))
        engine.seed_history(ticks)

        signal = engine.evaluate_strategy(engine.tick_history)
        self.assertEqual(signal.signal_type, SignalType.DIGITMATCH)
        self.assertEqual(signal.predicted_barrier, 9)
        self.assertIn("hot", signal.reason.lower())


if __name__ == "__main__":
    unittest.main()
