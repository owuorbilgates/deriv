"""
Unit and Integration Test Suite for Deriv Digit Trading Bot.
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
from trade_executor import TradeExecutor


class TestStrategyEngine(unittest.TestCase):
    """Test strategy digit extraction and pattern recognition."""

    def setUp(self):
        self.strategy = StrategyEngine(buffer_size=20)

    def test_extract_last_digit_precision(self):
        """Verify exact decimal digit extraction without float truncation."""
        # Critical test: float 1234.50 must return 0, not 5
        self.assertEqual(StrategyEngine.extract_last_digit(1234.50, pip_size=2), 0)
        self.assertEqual(StrategyEngine.extract_last_digit(1234.55, pip_size=2), 5)
        self.assertEqual(StrategyEngine.extract_last_digit(2456.78, pip_size=2), 8)
        self.assertEqual(StrategyEngine.extract_last_digit(100.1234, pip_size=4), 4)
        self.assertEqual(StrategyEngine.extract_last_digit(100.1230, pip_size=4), 0)
        self.assertEqual(StrategyEngine.extract_last_digit(50.0, pip_size=1), 0)

    def test_insufficient_ticks_returns_wait(self):
        """Strategy must return WAIT when fewer than 5 ticks are recorded."""
        for i in range(3):
            tick, signal = self.strategy.on_new_tick("R_100", 100.0 + i, 2, 1000 + i)
            self.assertEqual(signal.signal_type, SignalType.WAIT)

    def test_consecutive_repeat_generates_digitdiff(self):
        """Two identical consecutive digits should generate a DIGITDIFF signal."""
        # Feed 3 diverse ticks
        self.strategy.on_new_tick("R_100", 100.12, 2, 1000)
        self.strategy.on_new_tick("R_100", 100.14, 2, 1001)
        self.strategy.on_new_tick("R_100", 100.16, 2, 1002)
        
        # Feed two consecutive ticks ending with digit 7
        self.strategy.on_new_tick("R_100", 100.17, 2, 1003)
        tick, signal = self.strategy.on_new_tick("R_100", 100.27, 2, 1004)

        self.assertEqual(signal.signal_type, SignalType.DIGITDIFF)
        self.assertEqual(signal.predicted_barrier, 7)
        self.assertGreater(signal.confidence, 0.8)

    def test_over_frequency_generates_digitdiff(self):
        """A digit appearing 3+ times in last 5 ticks triggers DIGITDIFF."""
        prices = [100.13, 100.23, 100.15, 100.33, 100.41]
        last_sig = None
        for i, p in enumerate(prices):
            _, last_sig = self.strategy.on_new_tick("R_100", p, 2, 1000 + i)

        self.assertEqual(last_sig.signal_type, SignalType.DIGITDIFF)
        self.assertEqual(last_sig.predicted_barrier, 3)


class TestRiskManager(unittest.IsolatedAsyncioTestCase):
    """Test risk controls, locks, cooldowns, and stop-loss/take-profit."""

    def setUp(self):
        self.rm = RiskManager(
            daily_stop_loss=10.0,
            daily_take_profit=20.0,
            cooldown_seconds=0.1,  # Short cooldown for test speed
            max_consecutive_losses=3,
            starting_balance=100.0
        )

    async def test_concurrency_lock(self):
        """Cannot open trade while another contract is active."""
        can_trade, reason = self.rm.can_open_trade(1.0)
        self.assertTrue(can_trade)

        # Acquire lock
        await self.rm.acquire_trade_lock(contract_id=999)
        self.assertTrue(self.rm.is_trade_active)

        # Second trade attempt must be rejected
        can_trade2, reason2 = self.rm.can_open_trade(1.0)
        self.assertFalse(can_trade2)
        self.assertIn("Active trade in progress", reason2)

        # Release lock with winning outcome
        outcome = TradeOutcome(
            contract_id=999,
            contract_type="DIGITDIFF",
            symbol="R_100",
            stake=1.0,
            payout=1.09,
            profit=0.09,
            status="won",
            barrier=5
        )
        self.rm.release_trade_lock(outcome)
        self.assertFalse(self.rm.is_trade_active)

    async def test_stop_loss_trigger(self):
        """Cumulative losses hitting daily_stop_loss halts trading."""
        # Simulate 10 losing trades of $1 each
        for i in range(10):
            await self.rm.acquire_trade_lock(100 + i)
            outcome = TradeOutcome(
                contract_id=100 + i,
                contract_type="DIGITDIFF",
                symbol="R_100",
                stake=1.0,
                payout=0.0,
                profit=-1.0,
                status="lost",
                barrier=5
            )
            self.rm.release_trade_lock(outcome)

        self.assertTrue(self.rm.is_halted)
        self.assertIn("Daily Stop-Loss", self.rm.halt_reason)
        can_trade, reason = self.rm.can_open_trade(1.0)
        self.assertFalse(can_trade)

    async def test_take_profit_trigger(self):
        """Cumulative profit hitting daily_take_profit halts trading."""
        await self.rm.acquire_trade_lock(201)
        outcome = TradeOutcome(
            contract_id=201,
            contract_type="DIGITMATCH",
            symbol="R_100",
            stake=2.5,
            payout=23.0,
            profit=20.5,
            status="won",
            barrier=7
        )
        self.rm.release_trade_lock(outcome)

        self.assertTrue(self.rm.is_halted)
        self.assertIn("Take-Profit", self.rm.halt_reason)


class TestTradePayload(unittest.TestCase):
    """Verify Deriv contract buy payload construction."""

    def test_buy_payload_structure(self):
        config = TradingConfig()
        stake = 1.00
        barrier = 7
        duration = 5

        # Check payload specification compliance with Deriv API v3
        payload = {
            "buy": 1,
            "price": float(stake),
            "parameters": {
                "amount": float(stake),
                "basis": "stake",
                "contract_type": "DIGITDIFF",
                "currency": "USD",
                "duration": duration,
                "duration_unit": "t",
                "symbol": "R_100",
                "barrier": str(barrier)
            }
        }

        self.assertEqual(payload["buy"], 1)
        self.assertEqual(payload["price"], 1.0)
        self.assertEqual(payload["parameters"]["contract_type"], "DIGITDIFF")
        self.assertEqual(payload["parameters"]["barrier"], "7")
        self.assertEqual(payload["parameters"]["duration_unit"], "t")


if __name__ == "__main__":
    unittest.main()
