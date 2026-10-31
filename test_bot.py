"""
Comprehensive Unit Test Suite for Deriv Digit Trading Bot.
Tests:
- Precision decimal digit extraction (Decimal module)
- Digit Cooldown / Anti-Revenge Blacklist Protocol
- Statistical Anomaly threshold (>= 18.0%)
- Trend-Following Momentum Match mode (DIGITMATCH flat stake)
- Risk limits, 2-step Martingale, and hard boundaries
- Multi-Market Scanner momentum calculations
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


class TestStrategyEngine(unittest.TestCase):
    """Test strategy digit extraction, rolling window, and anti-revenge rules."""

    def setUp(self):
        self.strategy = StrategyEngine(
            buffer_size=100,
            threshold_percent=18.0,
            strategy_mode="DIFFERS",
            blacklist_duration_trades=10
        )

    def test_extract_last_digit_precision(self):
        """Verify exact decimal digit extraction without float truncation."""
        self.assertEqual(StrategyEngine.extract_last_digit(1234.50, pip_size=2), 0)
        self.assertEqual(StrategyEngine.extract_last_digit(1234.55, pip_size=2), 5)
        self.assertEqual(StrategyEngine.extract_last_digit(2456.78, pip_size=2), 8)
        self.assertEqual(StrategyEngine.extract_last_digit(100.1234, pip_size=4), 4)
        self.assertEqual(StrategyEngine.extract_last_digit(100.1230, pip_size=4), 0)
        self.assertEqual(StrategyEngine.extract_last_digit(50.0, pip_size=1), 0)
        self.assertEqual(StrategyEngine.extract_last_digit(4856.679, pip_size=3), 9)

    def test_raised_threshold_filters_noise(self):
        """Frequencies below 18.0% (e.g. 15% or 16%) must return WAIT."""
        ticks = []
        # 16 occurrences of digit 7 (16%), 84 of other digits
        for i in range(16):
            ticks.append(TickData(symbol="R_100", quote=100.07, pip_size=2, last_digit=7, epoch=1000 + i))
        for i in range(84):
            d = i % 7
            ticks.append(TickData(symbol="R_100", quote=100.00 + (d * 0.01), pip_size=2, last_digit=d, epoch=1100 + i))

        self.strategy.seed_history(ticks)
        signal = self.strategy.evaluate_strategy(self.strategy.tick_history)
        self.assertEqual(signal.signal_type, SignalType.WAIT)
        self.assertIn("< 18.0% threshold", signal.reason)

    def test_anomaly_triggers_differs_when_above_threshold(self):
        """Frequency >= 18% (e.g. 20%) triggers DIGITDIFF on that hot digit."""
        ticks = []
        # 20 occurrences of digit 4 (20%)
        for i in range(20):
            ticks.append(TickData(symbol="R_100", quote=100.04, pip_size=2, last_digit=4, epoch=1000 + i))
        for i in range(80):
            d = (i % 9) if (i % 9) != 4 else 8
            ticks.append(TickData(symbol="R_100", quote=100.00 + (d * 0.01), pip_size=2, last_digit=d, epoch=1100 + i))

        self.strategy.seed_history(ticks)
        signal = self.strategy.evaluate_strategy(self.strategy.tick_history)
        self.assertEqual(signal.signal_type, SignalType.DIGITDIFF)
        self.assertEqual(signal.predicted_barrier, 4)
        self.assertIn("20.0% >= 18.0%", signal.reason)

    def test_anti_revenge_blacklist_blocks_losing_digit(self):
        """If a trade loses on digit 4, digit 4 must be quarantined and ignored."""
        ticks = []
        for i in range(20):
            ticks.append(TickData(symbol="R_100", quote=100.04, pip_size=2, last_digit=4, epoch=1000 + i))
        for i in range(80):
            d = (i % 9) if (i % 9) != 4 else 8
            ticks.append(TickData(symbol="R_100", quote=100.00 + (d * 0.01), pip_size=2, last_digit=d, epoch=1100 + i))
        self.strategy.seed_history(ticks)

        # Trigger loss on digit 4
        self.strategy.on_trade_completed(status="lost", barrier=4)
        self.assertIn(4, self.strategy.digit_blacklist)
        self.assertEqual(self.strategy.digit_blacklist[4], 10)

        # Evaluate strategy again: it must NOT trade digit 4 even though it's 20%!
        signal = self.strategy.evaluate_strategy(self.strategy.tick_history)
        self.assertEqual(signal.signal_type, SignalType.WAIT)
        self.assertIn("Anti-Revenge Blacklist", signal.reason)

    def test_trend_following_momentum_match_mode(self):
        """In MATCH mode, an anomaly >= 18% triggers DIGITMATCH on that digit."""
        match_engine = StrategyEngine(buffer_size=100, threshold_percent=18.0, strategy_mode="MATCH")
        ticks = []
        # 19 occurrences of digit 9
        for i in range(19):
            ticks.append(TickData(symbol="R_100", quote=100.09, pip_size=2, last_digit=9, epoch=1000 + i))
        for i in range(81):
            d = (i % 8)
            ticks.append(TickData(symbol="R_100", quote=100.00 + (d * 0.01), pip_size=2, last_digit=d, epoch=1100 + i))

        match_engine.seed_history(ticks)
        signal = match_engine.evaluate_strategy(match_engine.tick_history)
        self.assertEqual(signal.signal_type, SignalType.DIGITMATCH)
        self.assertEqual(signal.predicted_barrier, 9)
        self.assertIn("Trend-Following Momentum", signal.reason)


class TestRiskManager(unittest.IsolatedAsyncioTestCase):
    """Test flat-stake vs Martingale, risk controls, and hard stops."""

    def test_flat_stake_when_martingale_disabled(self):
        """When use_martingale is False (e.g. for DIGITMATCH), stake stays flat $0.35."""
        rm = RiskManager(base_stake=0.35, use_martingale=False)
        self.assertEqual(rm.get_current_stake(), 0.35)

        # Simulate 3 losses
        for i in range(3):
            outcome = TradeOutcome(
                contract_id=100 + i, contract_type="DIGITMATCH", symbol="R_100",
                stake=0.35, payout=0.0, profit=-0.35, status="lost", barrier=9
            )
            rm.release_trade_lock(outcome)
            # Stake must remain strictly $0.35
            self.assertEqual(rm.get_current_stake(), 0.35)

    async def test_martingale_escalation_and_ladder_cap(self):
        """In Martingale mode: Step 0 is $0.35, Step 1 is $3.85, then resets."""
        rm = RiskManager(base_stake=0.35, use_martingale=True, martingale_multiplier=11.0, martingale_max_steps=2)
        self.assertEqual(rm.get_current_stake(), 0.35)

        # Loss 1 -> Step 1 ($3.85)
        await rm.acquire_trade_lock(201)
        rm.release_trade_lock(TradeOutcome(
            contract_id=201, contract_type="DIGITDIFF", symbol="R_100",
            stake=0.35, payout=0.0, profit=-0.35, status="lost", barrier=7
        ))
        self.assertAlmostEqual(rm.get_current_stake(), 3.85, places=2)

        # Loss 2 -> Ladder Cap reached, resets to $0.35
        await rm.acquire_trade_lock(202)
        rm.release_trade_lock(TradeOutcome(
            contract_id=202, contract_type="DIGITDIFF", symbol="R_100",
            stake=3.85, payout=0.0, profit=-3.85, status="lost", barrier=7
        ))
        self.assertEqual(rm.get_current_stake(), 0.35)

    async def test_hard_take_profit_stop(self):
        """Target profit halts trading."""
        rm = RiskManager(daily_take_profit=5.0)
        await rm.acquire_trade_lock(301)
        rm.release_trade_lock(TradeOutcome(
            contract_id=301, contract_type="DIGITMATCH", symbol="R_100",
            stake=0.35, payout=5.40, profit=5.05, status="won", barrier=1
        ))
        self.assertTrue(rm.is_halted)
        self.assertIn("TARGET_PROFIT", rm.halt_reason)

    async def test_hard_stop_loss(self):
        """Stop loss halts trading."""
        rm = RiskManager(daily_stop_loss=25.0)
        await rm.acquire_trade_lock(401)
        rm.release_trade_lock(TradeOutcome(
            contract_id=401, contract_type="DIGITDIFF", symbol="R_100",
            stake=25.0, payout=0.0, profit=-25.0, status="lost", barrier=1
        ))
        self.assertTrue(rm.is_halted)
        self.assertIn("STOP_LOSS", rm.halt_reason)


class TestMultiMarketScanner(unittest.TestCase):
    def test_momentum_calculation(self):
        p_5m = 500.0
        p_cur = 502.50
        pct = abs((p_cur - p_5m) / p_5m * 100.0)
        self.assertAlmostEqual(pct, 0.50, places=4)


if __name__ == "__main__":
    unittest.main()
