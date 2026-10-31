"""
Main Application Orchestrator for Deriv Digit Trading Bot.

High-Frequency Asynchronous Trading System executing "Digit Match / Digit Differs"
strategies on Synthetic Volatility Indices via Deriv API (WebSocket v3).
"""

import argparse
import asyncio
import logging
import os
import random
import signal
import sys
import time
from typing import Any, Dict

from config import CONFIG, TradingConfig
from deriv_client import DerivClient
from risk_manager import RiskManager
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

    # Formatter with millisecond timestamps for HFT auditability
    formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d [%(levelname)-8s] %(name)-14s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 1. Console Stream Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 2. Local File Handler (explicit UTF-8 encoding)
    file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


class DerivTradingBot:
    """
    Central Asynchronous Bot Controller.
    """

    def __init__(self, config: TradingConfig):
        self.config = config
        self.logger = logging.getLogger("DerivBot")
        
        # Instantiate Core Components
        self.strategy = StrategyEngine(buffer_size=config.TICK_BUFFER_SIZE)
        self.risk_manager = RiskManager(
            daily_stop_loss=config.DAILY_STOP_LOSS,
            daily_take_profit=config.DAILY_TAKE_PROFIT,
            cooldown_seconds=config.COOLDOWN_SECONDS,
            max_consecutive_losses=config.MAX_CONSECUTIVE_LOSSES
        )
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
        self.executor = TradeExecutor(
            client=self.client,
            risk_manager=self.risk_manager,
            config=self.config
        )

        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        """
        Start the bot lifecycle: connection, auth, subscription, and event dispatch.
        """
        self.logger.info("=" * 70)
        self.logger.info("🚀 STARTING DERIV ALGORITHMIC DIGIT TRADING BOT")
        self.logger.info(f"Market Symbol:    [{self.config.SYMBOL}]")
        self.logger.info(f"Contract Strategy:[{self.config.DEFAULT_CONTRACT_TYPE}]")
        self.logger.info(f"Duration:         [{self.config.DURATION} {self.config.DURATION_UNIT}]")
        self.logger.info(f"Stake Amount:     [${self.config.STAKE_AMOUNT:.2f} {self.config.CURRENCY}]")
        self.logger.info(f"Daily Stop-Loss:  [${self.config.DAILY_STOP_LOSS:.2f}]")
        self.logger.info(f"Daily Take-Profit:[${self.config.DAILY_TAKE_PROFIT:.2f}]")
        self.logger.info(f"Simulation Mode:  [{self.config.SIMULATION_MODE}]")
        self.logger.info("=" * 70)

        # Wire client callbacks
        self.client.on_tick = self._on_tick_received
        self.client.on_contract_update = self.executor.handle_contract_update

        if self.config.SIMULATION_MODE:
            await self._run_simulation_mode()
        else:
            await self._run_live_mode()

    async def _run_live_mode(self) -> None:
        """Execute live trading session connected to Deriv WebSocket v3."""
        # Validate credentials before initiating connection
        try:
            self.config.validate()
        except ValueError as err:
            self.logger.critical(f"Configuration Validation Error: {err}")
            return

        while not self._shutdown_event.is_set() and not self.risk_manager.is_halted:
            try:
                # 1. Connect
                await self.client.connect()

                # 2. Authorize
                auth_info = await self.client.authorize()
                initial_balance = float(auth_info.get("balance", 0.0))
                self.risk_manager.update_balance(initial_balance)

                # 3. Subscribe to live market ticks
                await self.client.subscribe_ticks(self.config.SYMBOL)

                # 4. Wait for shutdown signal or risk limit breach
                while not self._shutdown_event.is_set():
                    if self.risk_manager.is_halted:
                        self.logger.warning(f"🛑 Trading halted by Risk Manager: {self.risk_manager.halt_reason}")
                        return

                    if not self.client.is_connected:
                        self.logger.warning("WebSocket connection dropped! Triggering reconnection protocol...")
                        break

                    await asyncio.sleep(1.0)

                if self._shutdown_event.is_set():
                    break

                # Connection dropped - attempt reconnection
                reconnected = await self.client.reconnect_loop()
                if not reconnected:
                    self.logger.critical("Failed to reconnect after maximum retry attempts. Halting bot.")
                    break

            except (KeyboardInterrupt, asyncio.CancelledError):
                break
            except Exception as e:
                self.logger.error(f"Live engine encountered an unhandled error: {e}", exc_info=True)
                reconnected = await self.client.reconnect_loop()
                if not reconnected:
                    break

    async def _on_tick_received(self, msg: Dict[str, Any]) -> None:
        """
        Handle incoming tick data from WebSocket stream.
        """
        tick_info = msg.get("tick")
        if not tick_info:
            return

        symbol = tick_info.get("symbol", self.config.SYMBOL)
        quote = float(tick_info.get("quote", 0.0))
        pip_size = int(tick_info.get("pip_size", 2))
        epoch = int(tick_info.get("epoch", int(time.time())))

        # Process tick through strategy engine
        tick, signal = self.strategy.on_new_tick(symbol, quote, pip_size, epoch)

        self.logger.info(
            f"📈 [TICK] {symbol} | Spot: {quote:.{pip_size}f} | Last Digit: [{tick.last_digit}] | "
            f"{self.strategy.get_digits_summary()}"
        )

        # Evaluate and dispatch order if signal generated
        if signal.signal_type != SignalType.WAIT:
            await self.executor.execute_signal(signal, tick)

    async def _run_simulation_mode(self) -> None:
        """
        Simulation loop generating synthetic ticks to test strategy and risk rules.
        """
        self.logger.info("🧪 RUNNING IN PAPER-TRADING SIMULATION MODE (Synthetic Live Ticks)")
        sim_spot = 2456.70
        pip_size = 2
        epoch = int(time.time())

        while not self._shutdown_event.is_set() and not self.risk_manager.is_halted:
            # Simulate natural random walk for Volatility Index
            sim_spot += round(random.uniform(-0.45, 0.45), 2)
            epoch += 1

            tick, signal = self.strategy.on_new_tick(self.config.SYMBOL, sim_spot, pip_size, epoch)

            self.logger.info(
                f"📈 [SIM TICK] {self.config.SYMBOL} | Spot: {sim_spot:.{pip_size}f} | "
                f"Last Digit: [{tick.last_digit}] | {self.strategy.get_digits_summary()}"
            )

            # Progress simulated open contracts
            await self.executor.on_simulated_tick(tick)

            # Dispatch new signals
            if signal.signal_type != SignalType.WAIT:
                await self.executor.execute_signal(signal, tick)

            await asyncio.sleep(1.0)  # 1 tick per second (Standard 1s Volatility Index)

        if self.risk_manager.is_halted:
            self.logger.warning(f"🛑 Simulation halted by Risk Manager: {self.risk_manager.halt_reason}")

    async def stop(self) -> None:
        """
        Gracefully stop the bot, unsubscribe, and print final session metrics.
        """
        self.logger.info("Initiating graceful shutdown...")
        self._shutdown_event.set()
        
        await self.client.disconnect()

        # Print Final Session Summary
        self.logger.info("=" * 70)
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
        self.logger.info("=" * 70)


def parse_arguments() -> argparse.Namespace:
    """Parse CLI arguments for runtime overrides."""
    parser = argparse.ArgumentParser(
        description="Asynchronous Deriv Digit Trading Bot (Digit Match / Differs)"
    )
    parser.add_argument("--sim", action="store_true", help="Run in paper-trading simulation mode")
    parser.add_argument("--symbol", type=str, help="Target Synthetic Volatility Index (e.g. R_100)")
    parser.add_argument("--stake", type=float, help="Stake amount in USD (e.g. 1.00)")
    parser.add_argument("--duration", type=int, help="Contract duration in ticks (1-10)")
    parser.add_argument("--type", choices=["DIGITDIFF", "DIGITMATCH"], help="Contract type")
    parser.add_argument("--stop-loss", type=float, help="Daily Stop-Loss limit in USD")
    parser.add_argument("--take-profit", type=float, help="Daily Take-Profit limit in USD")
    return parser.parse_args()


async def main() -> None:
    args = parse_arguments()

    # Apply CLI overrides to configuration if provided
    overrides: Dict[str, Any] = {}
    if args.sim:
        overrides["SIMULATION_MODE"] = True
    if args.symbol:
        overrides["SYMBOL"] = args.symbol
    if args.stake:
        overrides["STAKE_AMOUNT"] = args.stake
    if args.duration:
        overrides["DURATION"] = args.duration
    if args.type:
        overrides["DEFAULT_CONTRACT_TYPE"] = args.type
    if args.stop_loss:
        overrides["DAILY_STOP_LOSS"] = args.stop_loss
    if args.take_profit:
        overrides["DAILY_TAKE_PROFIT"] = args.take_profit

    # Create active config instance
    active_config = TradingConfig(**{**CONFIG.__dict__, **overrides})

    # Setup Dual Logging
    setup_logging(active_config.LOG_FILE, active_config.LOG_LEVEL)

    bot = DerivTradingBot(active_config)

    # Register OS signals for graceful termination (Windows & POSIX)
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
