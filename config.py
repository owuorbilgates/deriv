"""
Configuration Module for Deriv Digit Trading Bot.

Loads credentials, risk management parameters, and trading variables
from environment variables (.env file) or defaults.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from decimal import Decimal
from typing import Any, Dict, Tuple
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
    PAPER_TRADING_MODE: bool = os.getenv("DERIV_PAPER_TRADING", "false").lower() in ("1", "true", "yes")
    SIMULATION_MODE: bool = os.getenv("DERIV_SIMULATION_MODE", "false").lower() in ("1", "true", "yes")

    # -------------------------------------------------------------------------
    # Multi-Market Momentum Scanner Settings (The 5-Minute Rule)
    # -------------------------------------------------------------------------
    SCANNER_SYMBOLS: Tuple[str, ...] = tuple(
        os.getenv(
            "DERIV_SCANNER_SYMBOLS",
            "1HZ10V,1HZ15V,1HZ25V,1HZ30V,1HZ50V,1HZ75V,1HZ90V,1HZ100V,R_10,R_25,R_50,R_75,R_100"
        ).split(",")
    )
    SCANNER_INTERVAL_SECONDS: float = float(os.getenv("DERIV_SCANNER_INTERVAL", "60.0"))
    SYMBOL: str = os.getenv("DERIV_SYMBOL", "1HZ100V")

    # -------------------------------------------------------------------------
    # Strategy Mode: User selects DIFFERS (1) or MATCH (2) at startup
    # -------------------------------------------------------------------------
    STRATEGY_MODE: str = os.getenv("DERIV_STRATEGY_MODE", "DIFFERS").upper()
    DYNAMIC_MODE_SWITCH: bool = os.getenv("DERIV_DYNAMIC_MODE_SWITCH", "false").lower() in ("1", "true", "yes")
    SCAN_ON_LOSS: bool = os.getenv("DERIV_SCAN_ON_LOSS", "true").lower() in ("1", "true", "yes")
    
    # In Match recovery mode (if dynamic switch enabled), strictly cap at 2 trades
    MATCH_MAX_ATTEMPTS: int = int(os.getenv("DERIV_MATCH_MAX_ATTEMPTS", "2"))
    
    # Rolling tick history window size
    DIGIT_WINDOW_SIZE: int = int(os.getenv("DERIV_DIGIT_WINDOW_SIZE", "100"))
    
    # -------------------------------------------------------------------------
    # Decoupled Triggers for DIFFERS and MATCH
    # -------------------------------------------------------------------------
    # 1. DIFFERS: Cold Digit Trigger (Bet DIFFERS on cold digits appearing <= 2.0%)
    DIFFERS_COLD_THRESHOLD_PERCENT: float = float(os.getenv("DERIV_DIFFERS_COLD_THRESHOLD", "2.0"))
    
    # 2. MATCH: Hot Digit Trigger (Bet MATCH on hot digits appearing >= 19.0%)
    MATCH_HOT_THRESHOLD_PERCENT: float = float(os.getenv("DERIV_MATCH_HOT_THRESHOLD", "19.0"))
    
    # Legacy alias
    DIGIT_THRESHOLD_PERCENT: float = float(os.getenv("DERIV_DIGIT_THRESHOLD", "19.0"))
    
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
        if self.DIFFERS_COLD_THRESHOLD_PERCENT < 0.0 or self.DIFFERS_COLD_THRESHOLD_PERCENT > 10.0:
            raise ValueError("DIFFERS_COLD_THRESHOLD_PERCENT must be between 0.0% and 10.0%.")
        if self.MATCH_HOT_THRESHOLD_PERCENT < 10.0 or self.MATCH_HOT_THRESHOLD_PERCENT > 100.0:
            raise ValueError("MATCH_HOT_THRESHOLD_PERCENT must be between 10.0% and 100.0%.")
        if self.MATCH_MAX_ATTEMPTS < 1:
            raise ValueError("MATCH_MAX_ATTEMPTS must be at least 1.")


CONFIG = TradingConfig()


# -------------------------------------------------------------------------
# Pip Size & Decimal Precision Metadata for Volatility Indices
# -------------------------------------------------------------------------
# Deriv indices use specific decimal pip precisions:
# - 2 decimals (pip_size=0.01 / 2): 1HZ10V, 1HZ25V, 1HZ50V, 1HZ75V, 1HZ100V, R_100
# - 3 decimals (pip_size=0.001 / 3): 1HZ15V, 1HZ30V, 1HZ90V, R_10, R_25
# - 4 decimals (pip_size=0.0001 / 4): R_50, R_75
KNOWN_PIP_SIZES: Dict[str, int] = {
    "1HZ10V": 2,
    "1HZ15V": 3,
    "1HZ25V": 2,
    "1HZ30V": 3,
    "1HZ50V": 2,
    "1HZ75V": 2,
    "1HZ90V": 3,
    "1HZ100V": 2,
    "R_10": 3,
    "R_25": 3,
    "R_50": 4,
    "R_75": 4,
    "R_100": 2,
}


def resolve_pip_size(raw_pip_size: Any = None, symbol: str = "") -> int:
    """
    Safely resolves the exact decimal precision (number of decimal places)
    for Deriv synthetic volatility indices across all API response formats:
    - Exponent format (e.g. 2, 2.0, 3, 4) -> 2, 3, 4
    - Fractional step format (e.g. 0.01, 0.001, 0.0001) -> 2, 3, 4
    - None or invalid -> looked up from KNOWN_PIP_SIZES for the symbol (fallback: 2)
    """
    if raw_pip_size is not None:
        try:
            val = float(raw_pip_size)
            if val >= 1.0:
                return int(val)
            elif 0 < val < 1.0:
                d = Decimal(str(val))
                return abs(d.as_tuple().exponent)
        except (ValueError, TypeError):
            pass
    return KNOWN_PIP_SIZES.get(symbol.upper(), 2)
