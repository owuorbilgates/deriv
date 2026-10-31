"""
Main Application Orchestrator for Deriv Digit Trading Bot.

Supports Three Execution Modes with 2-Trade Capped Match/Differs Strategy:
1. Live Trading (python main.py):
   - Real Deriv market stream (symbols, momentum, ticks).
   - Real contracts placed on Deriv account with real funds.
2. Real Deriv Simulation / Paper Trading (python main.py --real-sim or --paper):
   - Connects with API token & App ID to REAL Deriv market gateway.
   - Monitors REAL Deriv charts, live 5-minute momentum, and streams REAL live ticks.
   - Executes contracts LOCALLY on your machine with ZERO real money at risk!
3. Offline Simulation (python main.py --sim):
   - Completely offline synthetic random walk for software tests without internet.
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

from config import CONFIG, TradingConfig, resolve_pip_size
from deriv_client import DerivClient
from risk_manager import RiskManager
from scanner import MultiMarketScanner
from strategy import SignalType, StrategyEngine, TickData
from trade_executor import TradeExecutor


def setup_logging(log_file: str, log_level_str: str) -> logging.Logger:
    """Configure dual console and file logging."""
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
    """Central Asynchronous Bot Controller."""

    def __init__(self, config: TradingConfig):
        self.config = config
        self.logger = logging.getLogger("DerivBot")
        
        # 1. Strategy Engine (Decoupled Dynamic Match/Differs State Machine with 2-Trade Cap)
        self.strategy = StrategyEngine(
            buffer_size=config.DIGIT_WINDOW_SIZE,
            differs_cold_threshold=config.DIFFERS_COLD_THRESHOLD_PERCENT,
            match_hot_threshold=config.MATCH_HOT_THRESHOLD_PERCENT,
            strategy_mode=config.STRATEGY_MODE,
            blacklist_duration_trades=config.DIGIT_BLACKLIST_TRADES,
            dynamic_mode_switch=config.DYNAMIC_MODE_SWITCH,
            max_match_attempts=config.MATCH_MAX_ATTEMPTS
        )
        
        # 2. Risk Manager
        self.risk_manager = RiskManager(
            base_stake=config.STAKE_AMOUNT,
            use_martingale=config.USE_MARTINGALE,
            martingale_multiplier=config.MARTINGALE_MULTIPLIER,
            martingale_max_steps=config.MARTINGALE_MAX_STEPS,
            daily_stop_loss=config.DAILY_STOP_LOSS,
            daily_take_profit=config.DAILY_TAKE_PROFIT,
            cooldown_seconds=config.COOLDOWN_SECONDS,
            max_consecutive_losses=config.MAX_CONSECUTIVE_LOSSES,
            paper_trading=config.PAPER_TRADING_MODE,
            starting_balance=config.PAPER_STARTING_BALANCE if config.PAPER_TRADING_MODE else 0.0
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
            config=self.config,
            strategy=self.strategy
        )

        self._shutdown_event = asyncio.Event()
        self._scanner_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start bot execution."""
        if self.config.SIMULATION_MODE:
            exec_mode = "OFFLINE SIMULATION (Synthetic Random Walk)"
        elif self.config.PAPER_TRADING_MODE:
            exec_mode = "REAL DERIV SIMULATION (Real Live Deriv Ticks, Local Paper Orders - ZERO RISK)"
        else:
            exec_mode = "LIVE TRADING (Real Deriv Ticks, Real Money Contracts)"

        self.logger.info("=" * 80)
        self.logger.info("🚀 DERIV QUANTITATIVE DIGIT TRADING BOT")
        self.logger.info(f"Execution Mode:     [{exec_mode}]")
        self.logger.info(f"Strategy Pipeline:  DIFFERS primary -> on 1 loss: MATCH recovery (Max {self.config.MATCH_MAX_ATTEMPTS} trades cap)")
        self.logger.info(f"Recovery Pivot:     MATCH recovery -> on WIN or {self.config.MATCH_MAX_ATTEMPTS} LOSSES: switches back to DIFFERS")
        self.logger.info(f"Initial State:      [{self.strategy.strategy_mode}]")
        self.logger.info(f"Candidate Markets:  {list(self.config.SCANNER_SYMBOLS)}")
        self.logger.info(f"Differs Trigger:    Cold Digits <= {self.config.DIFFERS_COLD_THRESHOLD_PERCENT:.1f}% in {self.config.DIGIT_WINDOW_SIZE} ticks")
        self.logger.info(f"Match Trigger:      Hot Digits >= {self.config.MATCH_HOT_THRESHOLD_PERCENT:.1f}% in {self.config.DIGIT_WINDOW_SIZE} ticks")
        self.logger.info(f"Anti-Revenge Guard: {self.config.DIGIT_BLACKLIST_TRADES} trades quarantine on losing digits")
        self.logger.info(f"Stake Management:   Flat ${self.config.STAKE_AMOUNT:.2f} {self.config.CURRENCY} (Zero Martingale)")
        self.logger.info(f"Hard Boundaries:    Take Profit: +${self.config.DAILY_TAKE_PROFIT:.2f} | Stop Loss: -${self.config.DAILY_STOP_LOSS:.2f}")
        self.logger.info("=" * 80)

        # Wire client callbacks
        self.client.on_tick = self._on_tick_received
        self.client.on_contract_update = self.executor.handle_contract_update

        if self.config.SIMULATION_MODE:
            await self._run_offline_simulation_mode()
        else:
            await self._run_live_mode()

    async def _run_live_mode(self) -> None:
        """Execute live gateway connection (used by both Live Mode and Real Deriv Simulation)."""
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
                
                if not self.config.PAPER_TRADING_MODE:
                    self.risk_manager.update_balance(initial_balance)
                else:
                    self.logger.info(
                        f"🛡️ [REAL SIMULATION ACTIVE] Connected to Deriv as {self.client.login_id}. "
                        f"Real Balance: ${initial_balance:.2f} (UNTOUCHED). Starting Paper Bankroll: ${self.risk_manager.current_balance:.2f}."
                    )

                # 2. Initial Multi-Market 5-Minute Momentum Scan on real Deriv charts
                top_symbol, top_pct, _ = await self.scanner.scan_highest_momentum(self.client)
                self.current_symbol = top_symbol

                # 3. Seed Strategy with 100 REAL historical ticks
                await self._seed_market_history(self.current_symbol)

                # 4. Subscribe to REAL live market ticks
                await self.client.subscribe_ticks(self.current_symbol)

                # 5. Start background scanner
                if not self._scanner_task or self._scanner_task.done():
                    self._scanner_task = asyncio.create_task(self._scanner_loop(), name="MarketScannerLoop")

                # 6. Main monitoring loop
                while not self._shutdown_event.is_set():
                    if self.risk_manager.is_halted:
                        self.logger.warning(f"🛑 Session halted by Risk Manager: {self.risk_manager.halt_reason}")
                        return

                    if not self.client.is_connected:
                        self.logger.warning("WebSocket dropped! Reconnecting...")
                        break

                    await asyncio.sleep(1.0)

                if self._shutdown_event.is_set():
                    break

                reconnected = await self.client.reconnect_loop()
                if not reconnected:
                    self.logger.critical("Failed to reconnect after maximum retries. Halting bot.")
                    break

            except (KeyboardInterrupt, asyncio.CancelledError):
                break
            except Exception as e:
                self.logger.error(f"Engine error: {e}", exc_info=True)
                reconnected = await self.client.reconnect_loop()
                if not reconnected:
                    break

    async def _seed_market_history(self, symbol: str) -> None:
        """Pre-fill 100-tick window immediately using real Deriv historical ticks."""
        try:
            self.logger.info(f"📥 Loading real Deriv historical ticks ({self.config.DIGIT_WINDOW_SIZE} ticks) for [{symbol}]...")
            res = await self.client.fetch_ticks_history(symbol, count=self.config.DIGIT_WINDOW_SIZE)
            history = res.get("history", {})
            prices = history.get("prices", [])
            times = history.get("times", [])
            pip_size = resolve_pip_size(res.get("pip_size"), symbol)

            ticks: List[TickData] = []
            for p, t in zip(prices, times):
                last_digit = StrategyEngine.extract_last_digit(p, pip_size)
                ticks.append(TickData(
                    symbol=symbol,
                    quote=float(p),
                    pip_size=pip_size,
                    last_digit=last_digit,
                    epoch=int(t)
                ))

            self.strategy.seed_history(ticks)
            self.logger.info(
                f"✅ Pre-seeded {len(ticks)} real ticks for [{symbol}] (pip_size={pip_size}). "
                f"{self.strategy.get_distribution_summary()}"
            )
        except Exception as e:
            self.logger.warning(f"Failed to pre-seed tick history for {symbol}: {e}")

    async def _scanner_loop(self) -> None:
        """Background loop querying real 5-minute moves every SCANNER_INTERVAL_SECONDS."""
        while not self._shutdown_event.is_set() and not self.risk_manager.is_halted:
            try:
                await asyncio.sleep(self.config.SCANNER_INTERVAL_SECONDS)
                if self.risk_manager.is_trade_active:
                    continue

                top_symbol, top_pct, _ = await self.scanner.scan_highest_momentum(self.client)

                if top_symbol != self.current_symbol:
                    self.logger.info(
                        f"🔄 [DYNAMIC SWITCH] Market [{top_symbol}] took momentum lead ({top_pct:.4f}% move)! "
                        f"Switching active instrument from [{self.current_symbol}] to [{top_symbol}]..."
                    )
                    old_symbol = self.current_symbol
                    self.current_symbol = top_symbol

                    await self.client.unsubscribe_ticks(old_symbol)
                    await self._seed_market_history(self.current_symbol)
                    await self.client.subscribe_ticks(self.current_symbol)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.warning(f"Notice in scanner loop: {e}")

    async def _on_tick_received(self, msg: Dict[str, Any]) -> None:
        """Handle incoming real market ticks streamed from Deriv."""
        tick_info = msg.get("tick") if isinstance(msg, dict) and "tick" in msg else msg
        if not tick_info:
            return

        symbol = tick_info.get("symbol", self.current_symbol)
        if symbol != self.current_symbol:
            return

        quote = float(tick_info.get("quote", 0.0))
        raw_pip = tick_info.get("pip_size")
        pip_size = resolve_pip_size(raw_pip, symbol)
        epoch = int(tick_info.get("epoch", int(time.time())))

        # Process real tick through strategy engine (high precision Decimal extraction)
        tick, signal = self.strategy.on_new_tick(symbol, quote, pip_size, epoch)

        # Progress any open paper-trading contracts on incoming live tick
        if self.config.PAPER_TRADING_MODE:
            await self.executor.on_simulated_tick(tick)

        formatted_quote = f"{Decimal(str(quote)):.{pip_size}f}"
        mode_tag = " [REAL-SIM]" if self.config.PAPER_TRADING_MODE else ""
        self.logger.info(
            f"📈 [TICK{mode_tag}] {symbol} | Spot: {formatted_quote} | Digit: [{tick.last_digit}] | "
            f"{self.strategy.get_distribution_summary()}"
        )

        if signal.signal_type in (SignalType.DIGITDIFF, SignalType.DIGITMATCH):
            await self.executor.execute_signal(signal, tick, self.current_symbol)

    async def _run_offline_simulation_mode(self) -> None:
        """Offline simulation loop using synthetic ticks."""
        self.logger.info("🧪 RUNNING IN OFFLINE SIMULATION MODE (Synthetic Random Walk)")
        pip_size = resolve_pip_size(None, self.current_symbol)
        sim_spot = 1250.50 if pip_size == 2 else (4864.835 if pip_size == 3 else 89.3331)
        epoch = int(time.time())

        initial_ticks = []
        for i in range(100):
            sim_spot += round(random.uniform(-0.35, 0.35), pip_size)
            epoch += 1
            last_digit = StrategyEngine.extract_last_digit(sim_spot, pip_size)
            initial_ticks.append(TickData(
                symbol=self.current_symbol,
                quote=sim_spot,
                pip_size=pip_size,
                last_digit=last_digit,
                epoch=epoch
            ))
        self.strategy.seed_history(initial_ticks)

        while not self._shutdown_event.is_set() and not self.risk_manager.is_halted:
            sim_spot += round(random.uniform(-0.40, 0.40), pip_size)
            epoch += 1
            last_digit = StrategyEngine.extract_last_digit(sim_spot, pip_size)
            tick = TickData(
                symbol=self.current_symbol,
                quote=sim_spot,
                pip_size=pip_size,
                last_digit=last_digit,
                epoch=epoch
            )

            tick, signal = self.strategy.on_new_tick(self.current_symbol, sim_spot, pip_size, epoch)

            self.logger.info(
                f"📈 [OFFLINE TICK] {self.current_symbol} | Spot: {sim_spot:.{pip_size}f} | "
                f"Digit: [{tick.last_digit}] | {self.strategy.get_distribution_summary()}"
            )

            await self.executor.on_simulated_tick(tick)

            if signal.signal_type in (SignalType.DIGITDIFF, SignalType.DIGITMATCH):
                await self.executor.execute_signal(signal, tick, self.current_symbol)

            await asyncio.sleep(1.0)

        if self.risk_manager.is_halted:
            self.logger.warning(f"🛑 Offline simulation halted by Risk Manager: {self.risk_manager.halt_reason}")

    async def stop(self) -> None:
        """Gracefully shutdown."""
        self.logger.info("Initiating graceful shutdown...")
        self._shutdown_event.set()

        if self._scanner_task and not self._scanner_task.done():
            self._scanner_task.cancel()
        
        await self.client.disconnect()

        self.logger.info("=" * 80)
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
        bal_label = "Final Paper Balance" if self.config.PAPER_TRADING_MODE else "Final Balance"
        self.logger.info(f"{bal_label}:         ${self.risk_manager.current_balance:.2f}")
        self.logger.info("=" * 80)


def parse_arguments() -> argparse.Namespace:
    """CLI Argument parser."""
    parser = argparse.ArgumentParser(
        description="Deriv Dynamic Match/Differs Quantitative Trading Bot"
    )
    # Execution modes
    parser.add_argument("--real-sim", action="store_true", help="Run Real Deriv Simulation (live Deriv ticks & momentum, local paper orders)")
    parser.add_argument("--paper", action="store_true", help="Alias for --real-sim")
    parser.add_argument("--sim", action="store_true", help="Run offline synthetic simulation (no internet required)")
    
    # Strategy & parameters
    parser.add_argument("--mode", choices=["DIFFERS", "MATCH"], help="Initial Mode (default: DIFFERS)")
    parser.add_argument("--match-cap", type=int, default=2, help="Max Match trades before reverting to Differs (default: 2)")
    parser.add_argument("--symbol", type=str, help="Initial Volatility Index (e.g. R_100)")
    parser.add_argument("--stake", type=float, help="Base stake amount in USD (default: 0.35)")
    parser.add_argument("--differs-threshold", type=float, help="Cold digit % threshold for DIFFERS (default: 2.0)")
    parser.add_argument("--match-threshold", type=float, help="Hot digit % threshold for MATCH (default: 19.0)")
    parser.add_argument("--threshold", type=float, help="Legacy alias for --match-threshold")
    parser.add_argument("--blacklist-trades", type=int, help="Quarantine duration on losing digit (default: 10)")
    parser.add_argument("--static-mode", action="store_true", help="Disable dynamic mode switching")
    parser.add_argument("--stop-loss", type=float, help="Session Stop-Loss in USD (default: 25.00)")
    parser.add_argument("--take-profit", type=float, help="Session Take-Profit in USD (default: 5.00)")
    parser.add_argument("--scanner-interval", type=float, help="Seconds between momentum scans (default: 60)")
    return parser.parse_args()


async def main() -> None:
    args = parse_arguments()

    overrides: Dict[str, Any] = {}
    if args.real_sim or args.paper:
        overrides["PAPER_TRADING_MODE"] = True
        overrides["SIMULATION_MODE"] = False
    elif args.sim:
        overrides["SIMULATION_MODE"] = True
        overrides["PAPER_TRADING_MODE"] = False

    if args.mode:
        overrides["STRATEGY_MODE"] = args.mode
    if args.match_cap:
        overrides["MATCH_MAX_ATTEMPTS"] = args.match_cap
    if args.symbol:
        overrides["SYMBOL"] = args.symbol
    if args.stake:
        overrides["STAKE_AMOUNT"] = args.stake
    if args.differs_threshold is not None:
        overrides["DIFFERS_COLD_THRESHOLD_PERCENT"] = args.differs_threshold
    if args.match_threshold is not None:
        overrides["MATCH_HOT_THRESHOLD_PERCENT"] = args.match_threshold
    elif args.threshold:
        overrides["MATCH_HOT_THRESHOLD_PERCENT"] = args.threshold
        overrides["DIGIT_THRESHOLD_PERCENT"] = args.threshold
    if args.blacklist_trades is not None:
        overrides["DIGIT_BLACKLIST_TRADES"] = args.blacklist_trades
    if args.static_mode:
        overrides["DYNAMIC_MODE_SWITCH"] = False
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
