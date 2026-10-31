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

# Multi-path .env loader ensuring .env is found whether run from root or deriv/
for env_file in [
    Path.cwd() / ".env",
    Path(__file__).resolve().parent / ".env",
    Path(__file__).resolve().parent.parent / ".env"
]:
    if env_file.exists():
        load_dotenv(dotenv_path=env_file, override=False)


@dataclass(frozen=True)
class TradingConfig:
    """Immutable trading bot configuration parameters."""

    # -------------------------------------------------------------------------
    # Deriv API Credentials & Connection
    # -------------------------------------------------------------------------
    API_TOKEN: str = os.getenv("DERIV_API_TOKEN", "")
    APP_ID: str = os.getenv("DERIV_APP_ID") or "34PsAhlWu7nJZr8gtqGGd"
    ACCOUNT_ID: str = os.getenv("DERIV_ACCOUNT_ID") or "ROT91835717"
    
    # Deriv WebSocket endpoint
    WS_URL_TEMPLATE: str = os.getenv(
        "DERIV_WS_URL", 
        "wss://api.derivws.com/trading/v1/options/ws/public"
    )

    # -------------------------------------------------------------------------
    # Multi-Market Momentum Scanner Settings (The 5-Minute Rule)
    # -------------------------------------------------------------------------
    # Candidate Volatility Indices to monitor simultaneously
    SCANNER_SYMBOLS: Tuple[str, ...] = tuple(
        os.getenv("DERIV_SCANNER_SYMBOLS", "R_10,R_25,R_50,R_75,R_100").split(",")
    )
    # Interval in seconds to recalculate 5-minute price momentum
    SCANNER_INTERVAL_SECONDS: float = float(os.getenv("DERIV_SCANNER_INTERVAL", "60.0"))
    
    # Active default symbol before first scan
    SYMBOL: str = os.getenv("DERIV_SYMBOL", "R_100")

    # -------------------------------------------------------------------------
    # Digit Distribution & Strategy Rules
    # -------------------------------------------------------------------------
    # Rolling tick history window size for digit frequency analysis
    DIGIT_WINDOW_SIZE: int = int(os.getenv("DERIV_DIGIT_WINDOW_SIZE", "100"))
    # Minimum occurrence percentage of the most frequent digit to trigger DIGITDIFF
    DIGIT_THRESHOLD_PERCENT: float = float(os.getenv("DERIV_DIGIT_THRESHOLD", "14.0"))

    # -------------------------------------------------------------------------
    # Contract Parameters for Digit Differs
    # -------------------------------------------------------------------------
    # Strategy strictly executes DIGITDIFF
    DEFAULT_CONTRACT_TYPE: str = os.getenv("DERIV_CONTRACT_TYPE", "DIGITDIFF")
    
    # Base stake amount in USD (or account currency)
    STAKE_AMOUNT: float = float(os.getenv("DERIV_STAKE_AMOUNT", "0.35"))
    CURRENCY: str = os.getenv("DERIV_CURRENCY", "USD")
    
    # Duration in ticks (1 tick as specified for Differs momentum strategy)
    DURATION: int = int(os.getenv("DERIV_DURATION", "1"))
    DURATION_UNIT: str = "t"  # "t" for ticks

    # Fallback barrier (digit 0-9)
    DEFAULT_BARRIER: int = int(os.getenv("DERIV_DEFAULT_BARRIER", "5"))

    # -------------------------------------------------------------------------
    # Risk Management & Martingale Recovery Protocol
    # -------------------------------------------------------------------------
    # 11.0x Martingale multiplier upon a loss (because Differs payouts are ~9.9%)
    MARTINGALE_MULTIPLIER: float = float(os.getenv("DERIV_MARTINGALE_MULTIPLIER", "11.0"))
    # Strictly cap Martingale at 2 steps (Trade 1: Base, Trade 2: Base*11; then reset)
    MARTINGALE_MAX_STEPS: int = int(os.getenv("DERIV_MARTINGALE_MAX_STEPS", "2"))

    # Hard Stops that immediately terminate the bot
    DAILY_STOP_LOSS: float = float(os.getenv("DERIV_DAILY_STOP_LOSS", "25.00"))
    DAILY_TAKE_PROFIT: float = float(os.getenv("DERIV_DAILY_TAKE_PROFIT", "5.00"))
    
    # Cooldown period in seconds after each trade closes before looking for new signals
    COOLDOWN_SECONDS: float = float(os.getenv("DERIV_COOLDOWN_SECONDS", "3.0"))

    # Circuit breaker: maximum consecutive losses before halting
    MAX_CONSECUTIVE_LOSSES: int = int(os.getenv("DERIV_MAX_CONSECUTIVE_LOSSES", "4"))

    # -------------------------------------------------------------------------
    # Network, Reconnection & Engine Controls
    # -------------------------------------------------------------------------
    PING_INTERVAL_SECONDS: float = 30.0
    RECONNECT_INITIAL_DELAY: float = 2.0
    RECONNECT_MAX_DELAY: float = 30.0
    RECONNECT_BACKOFF_FACTOR: float = 2.0
    MAX_RECONNECT_ATTEMPTS: int = 15

    # Simulation mode: when True, runs on simulated tick stream without placing real trades
    SIMULATION_MODE: bool = os.getenv("DERIV_SIMULATION_MODE", "false").lower() in ("1", "true", "yes")

    # Logging settings
    LOG_FILE: str = os.getenv("DERIV_LOG_FILE", "deriv_trading_bot.log")
    LOG_LEVEL: str = os.getenv("DERIV_LOG_LEVEL", "INFO")

    def get_ws_url(self) -> str:
        """Format the full WebSocket URL."""
        return self.WS_URL_TEMPLATE

    def validate(self) -> None:
        """Validate critical configuration fields."""
        if not self.SIMULATION_MODE and not self.API_TOKEN:
            raise ValueError(
                "DERIV_API_TOKEN is missing! Please set your token in .env file "
                "or enable DERIV_SIMULATION_MODE=true for testing."
            )
        if self.STAKE_AMOUNT <= 0:
            raise ValueError("DERIV_STAKE_AMOUNT must be greater than 0.")
        if self.DURATION < 1 or self.DURATION > 10:
            raise ValueError("DERIV_DURATION must be between 1 and 10 ticks for digit contracts.")
        if self.DAILY_STOP_LOSS <= 0 or self.DAILY_TAKE_PROFIT <= 0:
            raise ValueError("Stop loss and take profit limits must be positive numbers.")
        if self.DIGIT_WINDOW_SIZE < 10:
            raise ValueError("DIGIT_WINDOW_SIZE must be at least 10 ticks.")


# Global singleton configuration instance
CONFIG = TradingConfig()
