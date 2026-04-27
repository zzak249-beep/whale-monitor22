"""Configuration central del bot - carga variables de entorno"""
import os
from dotenv import load_dotenv

load_dotenv()

BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
BINGX_BASE_URL   = "https://open-api.bingx.com"

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SYMBOLS = os.getenv("SYMBOLS", "BTC-USDT,ETH-USDT").split(",")

TF_HTF   = "1h"
TF_ENTRY = "15m"
TF_EXEC  = "1m"

EMA_FAST       = 10
EMA_SLOW_HTF   = 50
EMA_TREND_HTF  = 200
BOS_LOOKBACK   = 20
CVD_PERIOD     = 20
MIN_CVD_DELTA  = 0.15
MIN_VOLUME_MULT= 1.3
EMA_MULTIPLIER = 8

RISK_PER_TRADE  = float(os.getenv("RISK_PER_TRADE", "1.0"))
MAX_DAILY_LOSS  = float(os.getenv("MAX_DAILY_LOSS", "5.0"))
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "2"))
RISK_REWARD     = float(os.getenv("RISK_REWARD", "2.5"))
SL_ATR_MULT     = 1.5
LEVERAGE        = int(os.getenv("LEVERAGE", "10"))

TRADE_HOURS_UTC = os.getenv("TRADE_HOURS_UTC", "7-23")
AVOID_NEWS      = os.getenv("AVOID_NEWS", "true").lower() == "true"

LOG_LEVEL     = os.getenv("LOG_LEVEL", "INFO")
DRY_RUN       = os.getenv("DRY_RUN", "true").lower() == "true"
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "10"))

CANDLES_REQUIRED = max(EMA_TREND_HTF, BOS_LOOKBACK) + 50
