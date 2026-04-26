"""
Configuration central del bot - carga variables de entorno
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── BINGX ────────────────────────────────────────────────────────────────────
BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
BINGX_BASE_URL   = "https://open-api.bingx.com"
BINGX_WS_URL     = "wss://open-api.bingx.com/market"

# ─── TELEGRAM ─────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ─── SÍMBOLOS ─────────────────────────────────────────────────────────────────
SYMBOLS = os.getenv("SYMBOLS", "BTC-USDT,ETH-USDT").split(",")

# ─── TIMEFRAMES ───────────────────────────────────────────────────────────────
TF_HTF   = "1h"    # Sesgo HTF
TF_ENTRY = "15m"   # Entrada (EMA10 × 15m × 8)
TF_EXEC  = "1m"    # Confirmación rápida

# ─── ESTRATEGIA ───────────────────────────────────────────────────────────────
EMA_FAST       = 10       # EMA10 principal (tu método)
EMA_SLOW_HTF   = 50       # HTF trend filter
EMA_TREND_HTF  = 200      # HTF macro trend
BOS_LOOKBACK   = 20       # Velas para detectar swing H/L
CVD_PERIOD     = 20       # Período acumulación CVD
MIN_CVD_DELTA  = 0.15     # CVD mínimo para confirmar presión (15%)
MIN_VOLUME_MULT= 1.3      # Volumen mínimo vs media (30% superior)
EMA_MULTIPLIER = 8        # Tu multiplicador EMA10×15m×8

# ─── GESTIÓN DE RIESGO ────────────────────────────────────────────────────────
RISK_PER_TRADE  = float(os.getenv("RISK_PER_TRADE", "1.0"))   # % capital por trade
MAX_DAILY_LOSS  = float(os.getenv("MAX_DAILY_LOSS", "5.0"))   # % pérdida diaria máxima
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "2"))       # Trades simultáneos
RISK_REWARD     = float(os.getenv("RISK_REWARD", "2.5"))       # RR mínimo
SL_ATR_MULT     = 1.5     # SL = 1.5 × ATR
LEVERAGE        = int(os.getenv("LEVERAGE", "10"))             # Apalancamiento

# ─── FILTROS TEMPORALES ───────────────────────────────────────────────────────
TRADE_HOURS_UTC = os.getenv("TRADE_HOURS_UTC", "7-23")  # "HH-HH" formato
AVOID_NEWS      = os.getenv("AVOID_NEWS", "true").lower() == "true"

# ─── SISTEMA ──────────────────────────────────────────────────────────────────
LOG_LEVEL    = os.getenv("LOG_LEVEL", "INFO")
DRY_RUN      = os.getenv("DRY_RUN", "true").lower() == "true"  # Paper trading por defecto
POLL_INTERVAL= int(os.getenv("POLL_INTERVAL", "15"))           # Segundos entre ciclos

# ─── CANDLE BUFFER ────────────────────────────────────────────────────────────
CANDLES_REQUIRED = max(EMA_TREND_HTF, BOS_LOOKBACK) + 50  # Buffer mínimo de velas
