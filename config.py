"""
Configuration Module for Deriv Digit Trading Bot.

Loads credentials, risk management parameters, and trading variables
from environment variables (.env file) or defaults.
"""

import os
from dataclasses import dataclass
from pathlib import Path
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
    APP_ID: str = os.getenv("DERIV_APP_ID", "1089")  # 1089 is Deriv's default public app_id
    
    # Official Deriv WebSocket endpoints:
    # Primary: wss://ws.binaryws.com/websockets/v3?app_id={app_id}
    # Secondary: wss://ws.derivws.com/websockets/v3?app_id={app_id}
    WS_URL_TEMPLATE: str = os.getenv(
        "DERIV_WS_URL", 
        "wss://ws.binaryws.com/websockets/v3?app_id={app_id}"
    )

    # -------------------------------------------------------------------------
    # Target Market & Instrument
    # -------------------------------------------------------------------------
    # Synthetic Volatility Indices symbols examples:
    # "R_10"   -> Volatility 10 Index
    # "R_25"   -> Volatility 25 Index
    # "R_50"   -> Volatility 50 Index
    # "R_75"   -> Volatility 75 Index
    # "R_100"  -> Volatility 100 Index
    # "1HZ100V" -> Volatility 100 (1s) Index
    SYMBOL: str = os.getenv("DERIV_SYMBOL", "1HZ100V")

    # -------------------------------------------------------------------------
    # Contract Parameters for Digit Match / Differs
    # -------------------------------------------------------------------------
    # Contract type: "DIGITMATCH" or "DIGITDIFF"
    DEFAULT_CONTRACT_TYPE: str = os.getenv("DERIV_CONTRACT_TYPE", "DIGITDIFF")
    
    # Stake amount in USD (or account currency)
    STAKE_AMOUNT: float = float(os.getenv("DERIV_STAKE_AMOUNT", "1.00"))
    CURRENCY: str = os.getenv("DERIV_CURRENCY", "USD")
    
    # Duration in ticks (Deriv digit contracts typically use 1 to 10 ticks)
    DURATION: int = int(os.getenv("DERIV_DURATION", "5"))
    DURATION_UNIT: str = "t"  # "t" for ticks

    # Default barrier (digit 0-9) when not dynamically calculated by strategy
    DEFAULT_BARRIER: int = int(os.getenv("DERIV_DEFAULT_BARRIER", "5"))

    # -------------------------------------------------------------------------
    # Risk Management & Limits
    # -------------------------------------------------------------------------
    # Maximum allowable loss per trading session/day (in USD)
    DAILY_STOP_LOSS: float = float(os.getenv("DERIV_DAILY_STOP_LOSS", "10.00"))
    
    # Target profit per trading session/day (in USD)
    DAILY_TAKE_PROFIT: float = float(os.getenv("DERIV_DAILY_TAKE_PROFIT", "20.00"))
    
    # Cooldown period in seconds after each trade closes before looking for new signals
    COOLDOWN_SECONDS: float = float(os.getenv("DERIV_COOLDOWN_SECONDS", "3.0"))

    # Maximum consecutive losses before halting trading as an emergency safeguard
    MAX_CONSECUTIVE_LOSSES: int = int(os.getenv("DERIV_MAX_CONSECUTIVE_LOSSES", "5"))

    # -------------------------------------------------------------------------
    # Network, Reconnection & Engine Controls
    # -------------------------------------------------------------------------
    # Ping interval in seconds to keep WebSocket connection alive
    PING_INTERVAL_SECONDS: float = 30.0
    
    # Reconnection backoff limits (seconds)
    RECONNECT_INITIAL_DELAY: float = 2.0
    RECONNECT_MAX_DELAY: float = 30.0
    RECONNECT_BACKOFF_FACTOR: float = 2.0
    MAX_RECONNECT_ATTEMPTS: int = 15

    # Rolling tick buffer length for strategy engine analysis
    TICK_BUFFER_SIZE: int = int(os.getenv("DERIV_TICK_BUFFER_SIZE", "50"))

    # Simulation mode: when True, runs on simulated tick stream without placing real trades
    SIMULATION_MODE: bool = os.getenv("DERIV_SIMULATION_MODE", "false").lower() in ("1", "true", "yes")

    # Logging settings
    LOG_FILE: str = os.getenv("DERIV_LOG_FILE", "deriv_trading_bot.log")
    LOG_LEVEL: str = os.getenv("DERIV_LOG_LEVEL", "INFO")

    def get_ws_url(self) -> str:
        """Format the full WebSocket URL with the configured App ID (must be numeric)."""
        app_id_clean = self.APP_ID if self.APP_ID and self.APP_ID.isdigit() else "1089"
        return self.WS_URL_TEMPLATE.format(app_id=app_id_clean)

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


# Global singleton configuration instance
CONFIG = TradingConfig()
