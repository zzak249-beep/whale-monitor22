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
SYMBOLS = [s.strip() for s in os.getenv("SYMBOLS", "BTC-USDT,ETH-USDT").split(",")]

# ─── TIMEFRAMES ───────────────────────────────────────────────────────────────
TF_HTF   = "1h"    # Sesgo HTF
TF_ENTRY = "15m"   # Entrada EMA10×15m×8
TF_EXEC  = "1m"    # Confirmación rápida

# ─── ESTRATEGIA ───────────────────────────────────────────────────────────────
EMA_FAST       = 10
EMA_SLOW_HTF   = 50
EMA_TREND_HTF  = 200
EMA_MID_HTF    = 21      # EMA media para momentum HTF
BOS_LOOKBACK   = 20
CVD_PERIOD     = 20
MIN_CVD_DELTA  = 0.10    # CVD mínimo (10% presión neta)
MIN_VOLUME_MULT= 1.2     # Volumen mínimo vs media
EMA_MULTIPLIER = 8
RSI_PERIOD     = 14
ADX_PERIOD     = 14

# ─── UMBRALES DE SEÑAL ────────────────────────────────────────────────────────
MIN_SIGNAL_SCORE = float(os.getenv("MIN_SIGNAL_SCORE", "55"))  # Score mínimo para entrar
SCORE_HIGH       = 75.0  # Score para HIGH confidence
SCORE_MED        = 55.0  # Score para MEDIUM confidence

# ─── GESTIÓN DE RIESGO ────────────────────────────────────────────────────────
RISK_PER_TRADE  = float(os.getenv("RISK_PER_TRADE",  "1.0"))
MAX_DAILY_LOSS  = float(os.getenv("MAX_DAILY_LOSS",  "5.0"))
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES",   "2"))
RISK_REWARD     = float(os.getenv("RISK_REWARD",     "2.5"))
SL_ATR_MULT     = 1.5
TP_ATR_MULT     = SL_ATR_MULT * RISK_REWARD   # Automático según RR
LEVERAGE        = int(os.getenv("LEVERAGE",         "10"))

# ─── TRAILING STOP ────────────────────────────────────────────────────────────
TRAILING_ACTIVATION_PCT = 0.5   # % ganancia para activar trailing
TRAILING_DISTANCE_PCT   = 0.30  # % de distancia del trailing

# ─── FILTROS TEMPORALES ───────────────────────────────────────────────────────
TRADE_HOURS_UTC = os.getenv("TRADE_HOURS_UTC", "7-23")
AVOID_NEWS      = os.getenv("AVOID_NEWS", "true").lower() == "true"

# ─── SISTEMA ──────────────────────────────────────────────────────────────────
LOG_LEVEL     = os.getenv("LOG_LEVEL", "INFO")
DRY_RUN       = os.getenv("DRY_RUN", "true").lower() == "true"
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))
WS_ENABLED    = os.getenv("WS_ENABLED", "true").lower() == "true"

# ─── CANDLE BUFFER ────────────────────────────────────────────────────────────
CANDLES_REQUIRED = max(EMA_TREND_HTF, BOS_LOOKBACK) + 50
HTF_CANDLES_REQUIRED = EMA_TREND_HTF + 10

# ─── COOLDOWN ─────────────────────────────────────────────────────────────────
MIN_BARS_COOLDOWN = 3  # Barras mínimas entre señales del mismo símbolo
