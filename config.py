"""Configuration management for UltraBot v3."""
from dataclasses import dataclass, field
from typing import Optional
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    """Central configuration for the trading bot."""

    # Exchange API
    exchange_key: str = os.getenv("EXCHANGE_KEY", "")
    exchange_secret: str = os.getenv("EXCHANGE_SECRET", "")
    exchange_url: str = os.getenv("EXCHANGE_URL", "https://fapi.binance.com")

    # Trading parameters
    leverage: int = int(os.getenv("LEVERAGE", "10"))
    top_n_symbols: int = int(os.getenv("TOP_N_SYMBOLS", "50"))
    min_volume_usdt: float = float(os.getenv("MIN_VOLUME_USDT", "100000"))
    max_open_trades: int = int(os.getenv("MAX_OPEN_TRADES", "5"))
    scan_interval: float = float(os.getenv("SCAN_INTERVAL", "5"))

    # Risk management
    max_risk_per_trade: float = float(os.getenv("MAX_RISK_PER_TRADE", "1.0"))
    max_daily_loss: float = float(os.getenv("MAX_DAILY_LOSS", "500"))
    trailing_sl: bool = os.getenv("TRAILING_SL", "true").lower() == "true"

    # Signal parameters
    min_confidence: float = float(os.getenv("MIN_CONFIDENCE", "65"))
    adx_threshold: float = float(os.getenv("ADX_THRESHOLD", "25"))
    rsi_oversold: float = float(os.getenv("RSI_OVERSOLD", "30"))
    rsi_overbought: float = float(os.getenv("RSI_OVERBOUGHT", "70"))

    # Telegram
    telegram_token: str = os.getenv("TELEGRAM_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Dashboard
    dashboard_enabled: bool = os.getenv("DASHBOARD_ENABLED", "true").lower() == "true"
    dashboard_port: int = int(os.getenv("DASHBOARD_PORT", "8000"))

    # Database
    db_path: str = os.getenv("DB_PATH", "data/trades.db")

    # Blacklist — inicializado en __post_init__
    blacklist: set = field(default_factory=set)

    def __post_init__(self):
        """Initialize blacklist from environment."""
        blacklist_str = os.getenv("BLACKLIST", "")
        self.blacklist = set(s.strip() for s in blacklist_str.split(",") if s.strip())


# Global config instance
cfg = Config()
