"""
Main Application Orchestrator for Deriv Digit Trading Bot.

Executes the "Momentum-Driven Mean Reversion for Digit Differs (DIGITDIFF)" Strategy:
1. Multi-Market Scanner (The 5-Minute Rule):
   - Monitors candidate Volatility Indices (R_10, R_25, R_50, R_75, R_100).
   - Recalculates 5-minute percentage price moves every 60 seconds.
   - Dynamically selects and switches to the index with the highest momentum.
2. Digit Distribution & Selection Engine:
   - Maintains a 100-tick rolling window using Python's Decimal module for exact digit precision.
   - If a digit appears > 14% of the time, selects that overrepresented digit as the barrier.
3. Trade Execution:
   - Dispatches DIGITDIFF contracts with duration 1 tick.
   - Locks execution and awaits settlement via proposal_open_contract.
4. Risk Management:
   - 2-Step 11.0x Martingale ladder ($0.35 -> $3.85 -> reset to $0.35).
   - Hard stops: TARGET_PROFIT ($5.00) and STOP_LOSS ($25.00).
"""

import argparse
import asyncio
import logging
import os
import random
import signal
import sys
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional

from config import CONFIG, TradingConfig
from deriv_client import DerivClient
from risk_manager import RiskManager
from scanner import MultiMarketScanner
from strategy import SignalType, StrategyEngine, TickData
from trade_executor import TradeExecutor


def setup_logging(log_file: str, log_level_str: str) -> logging.Logger:
    """
    Configure dual-destination logging: colorful console stream and structured .log file.
    Ensures safe Unicode / UTF-8 encoding across Windows and POSIX shells.
    """
    if sys.platform == "win32":
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

    log_level = getattr(logging, log_level_str.upper(), logging.INFO)
    logger = logging.getLogger()
    logger.setLevel(log_level)
    logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d [%(levelname)-8s] %(name)-14s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


class DerivTradingBot:
    """
    Central Asynchronous Bot Controller for Momentum Digit Differs.
    """

    def __init__(self, config: TradingConfig):
        self.config = config
        self.logger = logging.getLogger("DerivBot")
        
        # 1. Strategy Engine (100 ticks, > 14% threshold)
        self.strategy = StrategyEngine(
            buffer_size=config.DIGIT_WINDOW_SIZE,
            threshold_percent=config.DIGIT_THRESHOLD_PERCENT
        )
        
        # 2. Risk Manager (2-step 11.0x Martingale, hard profit/loss boundaries)
        self.risk_manager = RiskManager(
            base_stake=config.STAKE_AMOUNT,
            martingale_multiplier=config.MARTINGALE_MULTIPLIER,
            martingale_max_steps=config.MARTINGALE_MAX_STEPS,
            daily_stop_loss=config.DAILY_STOP_LOSS,
            daily_take_profit=config.DAILY_TAKE_PROFIT,
            cooldown_seconds=config.COOLDOWN_SECONDS,
            max_consecutive_losses=config.MAX_CONSECUTIVE_LOSSES
        )
        
        # 3. Deriv Client
        self.client = DerivClient(
            app_id=config.APP_ID,
            api_token=config.API_TOKEN,
            ws_url_template=config.WS_URL_TEMPLATE,
            ping_interval=config.PING_INTERVAL_SECONDS,
            reconnect_initial_delay=config.RECONNECT_INITIAL_DELAY,
            reconnect_max_delay=config.RECONNECT_MAX_DELAY,
            reconnect_factor=config.RECONNECT_BACKOFF_FACTOR,
            max_reconnect_attempts=config.MAX_RECONNECT_ATTEMPTS
        )
        
        # 4. Multi-Market Scanner (The 5-Minute Rule)
        self.scanner = MultiMarketScanner(
            symbols=list(config.SCANNER_SYMBOLS),
            public_ws_url=config.WS_URL_TEMPLATE
        )
        self.current_symbol: str = config.SYMBOL
        
        # 5. Trade Executor
        self.executor = TradeExecutor(
            client=self.client,
            risk_manager=self.risk_manager,
            config=self.config
        )

        self._shutdown_event = asyncio.Event()
        self._scanner_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """
        Start the bot lifecycle: connection, auth, initial scan, subscription, and main loops.
        """
        self.logger.info("=" * 72)
        self.logger.info("🚀 DERIV ALGORITHMIC TRADING BOT - MOMENTUM DIGIT DIFFERS")
        self.logger.info(f"Candidate Markets: {list(self.config.SCANNER_SYMBOLS)}")
        self.logger.info(f"Scan Interval:     Every {self.config.SCANNER_INTERVAL_SECONDS} seconds")
        self.logger.info(f"Contract Strategy: DIGITDIFF (Differs)")
        self.logger.info(f"Contract Duration: {self.config.DURATION} tick ({self.config.DURATION_UNIT})")
        self.logger.info(f"Base Stake:        ${self.config.STAKE_AMOUNT:.2f} {self.config.CURRENCY}")
        self.logger.info(f"Martingale Ladder: {self.config.MARTINGALE_MULTIPLIER}x multiplier, max {self.config.MARTINGALE_MAX_STEPS} steps")
        self.logger.info(f"Mean Reversion:    Trigger when max digit > {self.config.DIGIT_THRESHOLD_PERCENT:.1f}% in {self.config.DIGIT_WINDOW_SIZE} ticks")
        self.logger.info(f"Hard Stops:        Target Profit: +${self.config.DAILY_TAKE_PROFIT:.2f} | Stop Loss: -${self.config.DAILY_STOP_LOSS:.2f}")
        self.logger.info(f"Simulation Mode:   {self.config.SIMULATION_MODE}")
        self.logger.info("=" * 72)

        # Wire client callbacks
        self.client.on_tick = self._on_tick_received
        self.client.on_contract_update = self.executor.handle_contract_update

        if self.config.SIMULATION_MODE:
            await self._run_simulation_mode()
        else:
            await self._run_live_mode()

    async def _run_live_mode(self) -> None:
        """Execute live trading session connected to Deriv WebSocket gateway."""
        try:
            self.config.validate()
        except ValueError as err:
            self.logger.critical(f"Configuration Validation Error: {err}")
            return

        while not self._shutdown_event.is_set() and not self.risk_manager.is_halted:
            try:
                # 1. Connect & Authorize
                await self.client.connect()
                auth_info = await self.client.authorize()
                initial_balance = float(auth_info.get("balance", 0.0))
                self.risk_manager.update_balance(initial_balance)

                # 2. Initial Multi-Market 5-Minute Momentum Scan
                top_symbol, top_pct, _ = await self.scanner.scan_highest_momentum(self.client)
                self.current_symbol = top_symbol

                # 3. Seed Strategy with 100 historical ticks for active market
                await self._seed_market_history(self.current_symbol)

                # 4. Subscribe to live market ticks
                await self.client.subscribe_ticks(self.current_symbol)

                # 5. Start background multi-market scanner periodic loop
                if not self._scanner_task or self._scanner_task.done():
                    self._scanner_task = asyncio.create_task(self._scanner_loop(), name="MarketScannerLoop")

                # 6. Main monitoring loop
                while not self._shutdown_event.is_set():
                    if self.risk_manager.is_halted:
                        self.logger.warning(f"🛑 Trading halted by Risk Manager: {self.risk_manager.halt_reason}")
                        return

                    if not self.client.is_connected:
                        self.logger.warning("WebSocket dropped! Reconnecting...")
                        break

                    await asyncio.sleep(1.0)

                if self._shutdown_event.is_set():
                    break

                # Connection dropped - attempt reconnection
                reconnected = await self.client.reconnect_loop()
                if not reconnected:
                    self.logger.critical("Failed to reconnect after maximum retries. Halting bot.")
                    break

            except (KeyboardInterrupt, asyncio.CancelledError):
                break
            except Exception as e:
                self.logger.error(f"Live engine error: {e}", exc_info=True)
                reconnected = await self.client.reconnect_loop()
                if not reconnected:
                    break

    async def _seed_market_history(self, symbol: str) -> None:
        """Fetch historical ticks to pre-fill the 100-tick window immediately."""
        try:
            self.logger.info(f"📥 Loading historical tick window ({self.config.DIGIT_WINDOW_SIZE} ticks) for [{symbol}]...")
            res = await self.client.fetch_ticks_history(symbol, count=self.config.DIGIT_WINDOW_SIZE)
            history = res.get("history", {})
            prices = history.get("prices", [])
            times = history.get("times", [])
            pip_size = int(res.get("pip_size", 2))

            ticks: List[TickData] = []
            for p, t in zip(prices, times):
                d = Decimal(str(p))
                formatted = f"{d:.{pip_size}f}"
                last_digit = int(formatted[-1])
                ticks.append(TickData(
                    symbol=symbol,
                    quote=float(p),
                    pip_size=pip_size,
                    last_digit=last_digit,
                    epoch=int(t)
                ))

            self.strategy.seed_history(ticks)
            self.logger.info(
                f"✅ Pre-seeded {len(ticks)} ticks for [{symbol}]. "
                f"{self.strategy.get_distribution_summary()}"
            )
        except Exception as e:
            self.logger.warning(f"Failed to pre-seed tick history for {symbol}: {e}")

    async def _scanner_loop(self) -> None:
        """
        Background loop executing every SCANNER_INTERVAL_SECONDS:
        Compares 5-minute price moves across R_10, R_25, R_50, R_75, R_100.
        Dynamically switches active symbol if a different index has higher momentum.
        """
        while not self._shutdown_event.is_set() and not self.risk_manager.is_halted:
            try:
                await asyncio.sleep(self.config.SCANNER_INTERVAL_SECONDS)
                
                # Only switch symbols when no trade is active
                if self.risk_manager.is_trade_active:
                    self.logger.debug("Trade in progress; deferring momentum market switch.")
                    continue

                top_symbol, top_pct, _ = await self.scanner.scan_highest_momentum(self.client)

                if top_symbol != self.current_symbol:
                    self.logger.info(
                        f"🔄 [DYNAMIC SWITCH] Market [{top_symbol}] took the momentum lead ({top_pct:.4f}% move)! "
                        f"Switching active instrument from [{self.current_symbol}] to [{top_symbol}]..."
                    )
                    
                    old_symbol = self.current_symbol
                    self.current_symbol = top_symbol

                    # Unsubscribe old symbol
                    await self.client.unsubscribe_ticks(old_symbol)

                    # Pre-seed new symbol's 100-tick window
                    await self._seed_market_history(self.current_symbol)

                    # Subscribe to new symbol
                    await self.client.subscribe_ticks(self.current_symbol)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.warning(f"Notice in scanner loop: {e}")

    async def _on_tick_received(self, msg: Dict[str, Any]) -> None:
        """
        Handle incoming tick data from WebSocket stream.
        """
        tick_info = msg.get("tick") if isinstance(msg, dict) and "tick" in msg else msg
        if not tick_info:
            return

        symbol = tick_info.get("symbol", self.current_symbol)
        if symbol != self.current_symbol:
            # Ignore ticks from previous symbols during switchover
            return

        quote = float(tick_info.get("quote", 0.0))
        pip_size = int(tick_info.get("pip_size", 2))
        epoch = int(tick_info.get("epoch", int(time.time())))

        # Process tick through strategy engine (high precision Decimal extraction)
        tick, signal = self.strategy.on_new_tick(symbol, quote, pip_size, epoch)

        # Log formatted tick and distribution
        formatted_quote = f"{Decimal(str(quote)):.{pip_size}f}"
        self.logger.info(
            f"📈 [TICK] {symbol} | Spot: {formatted_quote} | Digit: [{tick.last_digit}] | "
            f"{self.strategy.get_distribution_summary()}"
        )

        # Evaluate and dispatch order if signal generated
        if signal.signal_type == SignalType.DIGITDIFF:
            await self.executor.execute_signal(signal, tick, self.current_symbol)

    async def _run_simulation_mode(self) -> None:
        """
        Paper-trading simulation loop generating synthetic ticks and momentum scans.
        """
        self.logger.info("🧪 RUNNING IN PAPER-TRADING SIMULATION MODE")
        sim_spot = 1250.50
        pip_size = 2
        epoch = int(time.time())

        # Pre-seed 100 ticks with natural random walk
        initial_ticks = []
        for i in range(100):
            sim_spot += round(random.uniform(-0.35, 0.35), 2)
            epoch += 1
            last_digit = int(f"{Decimal(str(sim_spot)):.{pip_size}f}"[-1])
            initial_ticks.append(TickData(
                symbol=self.current_symbol,
                quote=sim_spot,
                pip_size=pip_size,
                last_digit=last_digit,
                epoch=epoch
            ))
        self.strategy.seed_history(initial_ticks)

        while not self._shutdown_event.is_set() and not self.risk_manager.is_halted:
            sim_spot += round(random.uniform(-0.40, 0.40), 2)
            epoch += 1
            last_digit = int(f"{Decimal(str(sim_spot)):.{pip_size}f}"[-1])
            tick = TickData(
                symbol=self.current_symbol,
                quote=sim_spot,
                pip_size=pip_size,
                last_digit=last_digit,
                epoch=epoch
            )

            # Evaluate strategy
            tick, signal = self.strategy.on_new_tick(self.current_symbol, sim_spot, pip_size, epoch)

            self.logger.info(
                f"📈 [SIM TICK] {self.current_symbol} | Spot: {sim_spot:.{pip_size}f} | "
                f"Digit: [{tick.last_digit}] | {self.strategy.get_distribution_summary()}"
            )

            # Progress simulated open contracts
            await self.executor.on_simulated_tick(tick)

            # Dispatch new signals
            if signal.signal_type == SignalType.DIGITDIFF:
                await self.executor.execute_signal(signal, tick, self.current_symbol)

            await asyncio.sleep(1.0)

        if self.risk_manager.is_halted:
            self.logger.warning(f"🛑 Simulation halted by Risk Manager: {self.risk_manager.halt_reason}")

    async def stop(self) -> None:
        """
        Gracefully stop the bot, unsubscribe, cancel tasks, and print final session metrics.
        """
        self.logger.info("Initiating graceful shutdown...")
        self._shutdown_event.set()

        if self._scanner_task and not self._scanner_task.done():
            self._scanner_task.cancel()
        
        await self.client.disconnect()

        # Print Final Session Summary
        self.logger.info("=" * 72)
        self.logger.info("📊 FINAL TRADING SESSION SUMMARY")
        self.logger.info(f"Total Trades Executed: {self.risk_manager.total_trades}")
        self.logger.info(f"Winning Trades:        {self.risk_manager.winning_trades}")
        self.logger.info(f"Losing Trades:         {self.risk_manager.losing_trades}")
        win_rate = (
            (self.risk_manager.winning_trades / self.risk_manager.total_trades * 100)
            if self.risk_manager.total_trades > 0 else 0.0
        )
        self.logger.info(f"Win Rate:              {win_rate:.2f}%")
        self.logger.info(f"Net Realized PnL:      ${self.risk_manager.net_pnl:+.2f}")
        self.logger.info(f"Final Balance:         ${self.risk_manager.current_balance:.2f}")
        self.logger.info("=" * 72)


def parse_arguments() -> argparse.Namespace:
    """Parse CLI arguments for runtime overrides."""
    parser = argparse.ArgumentParser(
        description="Momentum-Driven Mean Reversion Deriv Digit Trading Bot (DIGITDIFF)"
    )
    parser.add_argument("--sim", action="store_true", help="Run in paper-trading simulation mode")
    parser.add_argument("--symbol", type=str, help="Initial Volatility Index (e.g. R_100)")
    parser.add_argument("--stake", type=float, help="Base stake amount in USD (default: 0.35)")
    parser.add_argument("--duration", type=int, help="Contract duration in ticks (default: 1)")
    parser.add_argument("--threshold", type=float, help="Digit occurrence threshold percentage (default: 14.0)")
    parser.add_argument("--stop-loss", type=float, help="Session Stop-Loss limit in USD (default: 25.00)")
    parser.add_argument("--take-profit", type=float, help="Session Take-Profit limit in USD (default: 5.00)")
    parser.add_argument("--scanner-interval", type=float, help="Seconds between momentum scans (default: 60)")
    return parser.parse_args()


async def main() -> None:
    args = parse_arguments()

    overrides: Dict[str, Any] = {}
    if args.sim:
        overrides["SIMULATION_MODE"] = True
    if args.symbol:
        overrides["SYMBOL"] = args.symbol
    if args.stake:
        overrides["STAKE_AMOUNT"] = args.stake
    if args.duration:
        overrides["DURATION"] = args.duration
    if args.threshold:
        overrides["DIGIT_THRESHOLD_PERCENT"] = args.threshold
    if args.stop_loss:
        overrides["DAILY_STOP_LOSS"] = args.stop_loss
    if args.take_profit:
        overrides["DAILY_TAKE_PROFIT"] = args.take_profit
    if args.scanner_interval:
        overrides["SCANNER_INTERVAL_SECONDS"] = args.scanner_interval

    active_config = TradingConfig(**{**CONFIG.__dict__, **overrides})
    setup_logging(active_config.LOG_FILE, active_config.LOG_LEVEL)

    bot = DerivTradingBot(active_config)

    loop = asyncio.get_running_loop()
    def handle_exit():
        asyncio.create_task(bot.stop())

    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, handle_exit)

    try:
        await bot.start()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await bot.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown completed by user.")
