"""
settings.py — Todas las variables leídas desde entorno (.env / Railway)
"""
import os

def _b(k, d='false'): return os.getenv(k, str(d)).lower() in ('1', 'true', 'yes')
def _f(k, d):         return float(os.getenv(k, str(d)))
def _i(k, d):         return int(os.getenv(k, str(d)))
def _s(k, d=''):      return os.getenv(k, str(d))

# ── API ───────────────────────────────────────────────────────────────────────
BINGX_API_KEY    = _s('BINGX_API_KEY')
BINGX_API_SECRET = _s('BINGX_API_SECRET')
TG_TOKEN         = _s('TG_TOKEN')
TG_CHAT          = _s('TG_CHAT')

# ── Modo ──────────────────────────────────────────────────────────────────────
PAPER_TRADING = _b('PAPER_TRADING', 'true')
AUTO          = not PAPER_TRADING           # retrocompatibilidad
LOG_LEVEL     = _s('LOG_LEVEL', 'INFO')

# ── Capital ───────────────────────────────────────────────────────────────────
EQUITY         = _f('EQUITY', 100.0)        # USDT inicial (paper o real)
RISK_PER_TRADE = _f('RISK_PER_TRADE', 1.0) # % del equity por trade
LEVERAGE       = _i('LEVERAGE', 3)

# ── Límites de operativa ──────────────────────────────────────────────────────
MAX_OPEN_TRADES  = _i('MAX_OPEN_TRADES', 3)
MAX_DAILY        = _i('MAX_DAILY', 10)
MAX_DRAWDOWN_PCT = _f('MAX_DRAWDOWN_PCT', 8.0)
DAILY_LOSS       = _f('DAILY_LOSS', 5.0)    # % equity → circuit breaker
CB_H             = _i('CB_H', 4)            # horas pausa tras CB

# ── SL / TP dinámicos por ATR ─────────────────────────────────────────────────
ATR_SL_MULT = _f('ATR_SL_MULT', 1.5)       # SL = entry - ATR * mult
ATR_TP_MULT = _f('ATR_TP_MULT', 3.0)       # TP = entry + ATR * mult
SL_MAX      = _f('SL_MAX', 3.0)            # % máximo de SL
SL_MIN      = _f('SL_MIN', 0.3)            # % mínimo de SL
MIN_RR      = _f('MIN_RR', 1.8)            # R:R mínimo para entrar
TP1_R       = _f('TP1_R', 1.5)
TP2_R       = _f('TP2_R', 3.0)
TP1_PCT     = _f('TP1_PCT', 0.5)           # cierra 50% en TP1
TP2_PCT     = _f('TP2_PCT', 0.3)           # cierra 30% en TP2

# ── Trailing stop ─────────────────────────────────────────────────────────────
USE_TRAIL  = _b('USE_TRAIL', 'true')
TRAIL_RATE = _f('TRAIL_RATE', 0.5)         # % para trailing
TRAIL_ACT  = _f('TRAIL_ACT', 1.5)         # activa trail cuando ganancia >= sl_pct * TRAIL_ACT

# ── Señales ───────────────────────────────────────────────────────────────────
MIN_SCORE        = _i('MIN_SCORE', 30)
KLINE_INTERVAL   = _s('KLINE_INTERVAL', '15m')
SIGNAL_LOOKBACK  = _i('SIGNAL_LOOKBACK', 5)
VOL_CONFIRM_MULT = _f('VOL_CONFIRM_MULT', 1.0)
USE_VWAP         = _b('USE_VWAP', 'false')
USE_MTF          = _b('USE_MTF', 'false')
USE_BB           = _b('USE_BB', 'true')
USE_RSI          = _b('USE_RSI', 'true')
VOL_R_MIN        = _f('VOL_R_MIN', VOL_CONFIRM_MULT)
AUROLO_MIN       = _i('AUROLO_MIN', 1)

# Umbrales RSI
RSI_OVERSOLD    = _f('RSI_OVERSOLD', 45.0)  # por encima → alcista
RSI_OVERBOUGHT  = _f('RSI_OVERBOUGHT', 75.0) # por encima → evitar

# ── Scanner ───────────────────────────────────────────────────────────────────
TOP_SYMBOLS       = _i('TOP_SYMBOLS', 50)
MAX_SYMS          = TOP_SYMBOLS
MIN_VOL           = _f('MIN_VOLUME_USDT', 200_000)
MIN_VOLUME_USDT   = MIN_VOL
SCAN_INTERVAL_SEC = _i('SCAN_INTERVAL_SEC', 60)
HOT_CONF          = _i('HOT_CONF', 65)
CHECK_INT         = max(_i('CHECK_INT', 30), 20)

# ── Aprendizaje ───────────────────────────────────────────────────────────────
MIN_MEAN_PNL     = _f('MIN_MEAN_PNL', 0.001)
MIN_SIGNALS_HIST = _i('MIN_SIGNALS_HIST', 2)
SCORE_BULL       = _i('SCORE_BULL', MIN_SCORE)
SCORE_NEUTRAL    = _i('SCORE_NEUTRAL', int(MIN_SCORE * 1.1))

# ── Cooldowns ─────────────────────────────────────────────────────────────────
CD_TP = _i('CD_TP', 30)   # minutos tras TP
CD_SL = _i('CD_SL', 60)   # minutos tras SL

# ── Fees BingX ────────────────────────────────────────────────────────────────
FEE_TAKER = 0.0005
FEE_COST  = 0.10

# ── ML / RL (desactivados por defecto) ───────────────────────────────────────
ML_ENABLED    = _b('ML_ENABLED', 'false')
RL_ENABLED    = _b('RL_ENABLED', 'false')
ML_THRESHOLD  = _f('ML_THRESHOLD', 0.60)
ML_MODEL_PATH = _s('ML_MODEL_PATH', 'models/ml_model.pkl')
RL_MODEL_PATH = _s('RL_MODEL_PATH', 'models/rl_model.pkl')

# ── Macro / régimen ───────────────────────────────────────────────────────────
BTC_CRASH    = _f('BTC_CRASH', 3.0)
BREADTH_BEAR = _f('BREADTH_BEAR', 0.25)
BREADTH_COINS = [
    'ETH-USDT', 'BNB-USDT', 'SOL-USDT', 'ADA-USDT', 'AVAX-USDT',
    'DOT-USDT', 'MATIC-USDT', 'LINK-USDT', 'UNI-USDT', 'ATOM-USDT',
]

# ── Exclusiones ───────────────────────────────────────────────────────────────
EXCL     = {'USDC', 'BUSD', 'TUSD', 'USDP', 'DAI', 'FDUSD', 'USDT', 'UST'}
EXCL_PFX = {'UP', 'DOWN', 'BULL', 'BEAR', '1000', 'HALF', 'DEFI'}
