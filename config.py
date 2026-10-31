"""
Configuration Module for Deriv Digit Trading Bot.

Loads credentials, risk management parameters, and trading variables
from environment variables (.env file) or defaults.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple
from dotenv import load_dotenv

# Automatically locate and load the .env file from the current directory or parent
env_path = Path(__file__).resolve().parent / ".env"
if not env_path.exists():
    env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)


@dataclass(frozen=True)
class TradingConfig:
    """Immutable trading bot configuration parameters."""

    # -------------------------------------------------------------------------
    # Deriv API Credentials & Connection
    # -------------------------------------------------------------------------
    API_TOKEN: str = os.getenv("DERIV_API_TOKEN", "")
    APP_ID: str = os.getenv("DERIV_APP_ID", "1089")
    ACCOUNT_ID: str = os.getenv("DERIV_ACCOUNT_ID", "")
    
    # WebSocket endpoint
    WS_URL_TEMPLATE: str = os.getenv(
        "DERIV_WS_URL", 
        "wss://api.derivws.com/trading/v1/options/ws/public"
    )

    # -------------------------------------------------------------------------
    # Execution Modes
    # -------------------------------------------------------------------------
    # 1. LIVE MODE: Default (python main.py) -> Real Deriv ticks + Real Deriv orders
    # 2. REAL DERIV SIMULATION / PAPER TRADING (python main.py --real-sim or --paper):
    #    Connects with API token & App ID, checks REAL Deriv charts & live ticks, but orders are LOCAL on machine.
    # 3. OFFLINE SIMULATION (python main.py --sim): Completely offline synthetic random walk.
    PAPER_TRADING_MODE: bool = os.getenv("DERIV_PAPER_TRADING", "false").lower() in ("1", "true", "yes")
    SIMULATION_MODE: bool = os.getenv("DERIV_SIMULATION_MODE", "false").lower() in ("1", "true", "yes")

    # -------------------------------------------------------------------------
    # Multi-Market Momentum Scanner Settings (The 5-Minute Rule)
    # -------------------------------------------------------------------------
    SCANNER_SYMBOLS: Tuple[str, ...] = tuple(
        os.getenv("DERIV_SCANNER_SYMBOLS", "R_10,R_25,R_50,R_75,R_100").split(",")
    )
    SCANNER_INTERVAL_SECONDS: float = float(os.getenv("DERIV_SCANNER_INTERVAL", "60.0"))
    SYMBOL: str = os.getenv("DERIV_SYMBOL", "R_100")

    # -------------------------------------------------------------------------
    # Strategy Mode & Dynamic Match/Differs State Machine
    # -------------------------------------------------------------------------
    STRATEGY_MODE: str = os.getenv("DERIV_STRATEGY_MODE", "DIFFERS").upper()
    DYNAMIC_MODE_SWITCH: bool = os.getenv("DERIV_DYNAMIC_MODE_SWITCH", "true").lower() in ("1", "true", "yes")
    
    # Rolling tick history window size
    DIGIT_WINDOW_SIZE: int = int(os.getenv("DERIV_DIGIT_WINDOW_SIZE", "100"))
    
    # Statistical Anomaly Threshold: >= 18.0% (+2.67 sigma above 10% mean)
    DIGIT_THRESHOLD_PERCENT: float = float(os.getenv("DERIV_DIGIT_THRESHOLD", "18.0"))
    
    # Anti-Revenge Blacklist: Number of trades to quarantine a losing digit
    DIGIT_BLACKLIST_TRADES: int = int(os.getenv("DERIV_DIGIT_BLACKLIST_TRADES", "10"))

    # Contract Configuration
    DEFAULT_CONTRACT_TYPE: str = os.getenv("DERIV_CONTRACT_TYPE", "DIGITDIFF")
    STAKE_AMOUNT: float = float(os.getenv("DERIV_STAKE_AMOUNT", "0.35"))
    CURRENCY: str = os.getenv("DERIV_CURRENCY", "USD")
    DURATION: int = int(os.getenv("DERIV_DURATION", "1"))
    DURATION_UNIT: str = "t"
    DEFAULT_BARRIER: int = int(os.getenv("DERIV_DEFAULT_BARRIER", "5"))

    # -------------------------------------------------------------------------
    # Risk Management
    # -------------------------------------------------------------------------
    USE_MARTINGALE: bool = os.getenv("DERIV_USE_MARTINGALE", "false").lower() in ("1", "true", "yes")
    MARTINGALE_MULTIPLIER: float = float(os.getenv("DERIV_MARTINGALE_MULTIPLIER", "11.0"))
    MARTINGALE_MAX_STEPS: int = int(os.getenv("DERIV_MARTINGALE_MAX_STEPS", "2"))

    DAILY_STOP_LOSS: float = float(os.getenv("DERIV_DAILY_STOP_LOSS", "25.00"))
    DAILY_TAKE_PROFIT: float = float(os.getenv("DERIV_DAILY_TAKE_PROFIT", "5.00"))
    COOLDOWN_SECONDS: float = float(os.getenv("DERIV_COOLDOWN_SECONDS", "3.0"))
    MAX_CONSECUTIVE_LOSSES: int = int(os.getenv("DERIV_MAX_CONSECUTIVE_LOSSES", "6"))

    # Virtual starting bankroll for Paper Trading Mode
    PAPER_STARTING_BALANCE: float = float(os.getenv("DERIV_PAPER_BALANCE", "100.00"))

    # -------------------------------------------------------------------------
    # Network Controls & Logging
    # -------------------------------------------------------------------------
    PING_INTERVAL_SECONDS: float = 30.0
    RECONNECT_INITIAL_DELAY: float = 2.0
    RECONNECT_MAX_DELAY: float = 30.0
    RECONNECT_BACKOFF_FACTOR: float = 2.0
    MAX_RECONNECT_ATTEMPTS: int = 15

    LOG_FILE: str = os.getenv("DERIV_LOG_FILE", "deriv_trading_bot.log")
    LOG_LEVEL: str = os.getenv("DERIV_LOG_LEVEL", "INFO")

    def get_ws_url(self) -> str:
        return self.WS_URL_TEMPLATE

    def validate(self) -> None:
        if not self.SIMULATION_MODE and not self.API_TOKEN:
            raise ValueError(
                "DERIV_API_TOKEN is missing! Please set your token in .env file "
                "or enable offline simulation with --sim."
            )
        if self.STAKE_AMOUNT <= 0:
            raise ValueError("DERIV_STAKE_AMOUNT must be greater than 0.")
        if self.DURATION < 1 or self.DURATION > 10:
            raise ValueError("DERIV_DURATION must be between 1 and 10 ticks for digit contracts.")
        if self.DAILY_STOP_LOSS <= 0 or self.DAILY_TAKE_PROFIT <= 0:
            raise ValueError("Stop loss and take profit limits must be positive numbers.")
        if self.DIGIT_WINDOW_SIZE < 10:
            raise ValueError("DIGIT_WINDOW_SIZE must be at least 10 ticks.")
        if self.DIGIT_THRESHOLD_PERCENT < 11.0:
            raise ValueError("DIGIT_THRESHOLD_PERCENT must be at least 11.0%.")


CONFIG = TradingConfig()
