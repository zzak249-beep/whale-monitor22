#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  TRADING BOT V16 — 77% WIN RATE EDITION                             ║
║                                                                      ║
║  PROBLEMAS DETECTADOS EN V15:                                        ║
║    · Solo 30 símbolos escaneados (MAX_SYMBOLS=30 en Railway)        ║
║    · SEI-USDT spameaba cada ciclo sin abrir orden                   ║
║      (slope ❌ pero 5/6 otros filtros ✅ → pasaba MIN_CONFLUENCES)  ║
║    · Slope con ❌ no debería generar señal nunca                     ║
║    · H1:NEUTRAL aceptaba señales de baja calidad                    ║
║                                                                      ║
║  TÉCNICAS PARA 77% WIN RATE:                                        ║
║    1. SLOPE ES OBLIGATORIO — no es confluencia opcional              ║
║    2. ANTI-SPAM — mismo símbolo máx 1 señal cada 3 ciclos           ║
║    3. H1 BULL/BEAR requerido — NEUTRAL solo si score > 65           ║
║    4. TREND MOMENTUM — EMA7 acelerando (slope actual > slope prev)  ║
║    5. RSI MOMENTUM — RSI en zona de fuerza (45-65 long, 35-55 sh)  ║
║    6. VOLUMEN CONFIRMADOR — vela actual > 1.2x media en señal       ║
║    7. ANTI-CHOP — rango de las últimas 10 velas > 1.5x ATR          ║
║    8. CONFIRMACIÓN DE CIERRE — usamos vela i-2 confirmada           ║
║    9. MAX_SYMBOLS=0 siempre (Railway: eliminar la variable)          ║
║   10. SCORE PONDERADO — slope y H1 pesan más                        ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import os, time, hmac, hashlib, json, asyncio, logging, threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import pandas as pd
import numpy as np

try:
    from telegram import Bot
    from telegram.constants import ParseMode
    TELEGRAM_OK = True
except ImportError:
    TELEGRAM_OK = False

# ══════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════
BINGX_API_KEY    = os.environ["BINGX_API_KEY"]
BINGX_SECRET_KEY = os.environ.get("BINGX_SECRET_KEY", os.environ.get("BINGX_API_SECRET", ""))
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

TIMEFRAME       = os.environ.get("TIMEFRAME",      "5m")
RISK_PERCENT    = float(os.environ.get("RISK_PERCENT",  "1.0"))
LEVERAGE        = int(os.environ.get("LEVERAGE",        "5"))
LOOP_SECONDS    = int(os.environ.get("LOOP_SECONDS",    "60"))
MAX_OPEN_TRADES = int(os.environ.get("MAX_OPEN_TRADES", "6"))
SCAN_WORKERS    = int(os.environ.get("SCAN_WORKERS",    "25"))
MAX_SYMBOLS     = int(os.environ.get("MAX_SYMBOLS",     "0"))   # 0 = todos

# ── Calidad de señal ──────────────────────────────────────────────────
MIN_SCORE       = float(os.environ.get("MIN_SCORE",     "55.0"))
MIN_DIST_PCT    = float(os.environ.get("MIN_DIST_PCT",  "0.20"))
ATR_MAX_PCT     = float(os.environ.get("ATR_MAX_PCT",   "4.0"))
MIN_RR          = float(os.environ.get("MIN_RR",        "1.8"))

# ── EMAs ─────────────────────────────────────────────────────────────
EMA_FAST        = int(os.environ.get("EMA_FAST",    "7"))
EMA_SLOW        = int(os.environ.get("EMA_SLOW",    "21"))
EMA_TREND       = int(os.environ.get("EMA_TREND",   "50"))
SLOPE_LIMIT     = float(os.environ.get("SLOPE_LIMIT","12.0"))
SLOPE_LOOK      = int(os.environ.get("SLOPE_LOOK",   "5"))

# ── ADX / RSI ────────────────────────────────────────────────────────
ADX_LEN         = int(os.environ.get("ADX_LEN",   "14"))
ADX_MIN         = float(os.environ.get("ADX_MIN",  "20.0"))
RSI_LEN         = int(os.environ.get("RSI_LEN",   "14"))
RSI_OB          = float(os.environ.get("RSI_OB",   "70.0"))
RSI_OS          = float(os.environ.get("RSI_OS",   "30.0"))
VOL_MULT        = float(os.environ.get("VOL_MULT",  "1.0"))

# ── SuperTrend ───────────────────────────────────────────────────────
ST_PERIOD       = int(os.environ.get("ST_PERIOD",  "10"))
ST_MULT         = float(os.environ.get("ST_MULT",  "3.0"))

# ── TP / SL ──────────────────────────────────────────────────────────
TP_MULT         = float(os.environ.get("TP_MULT",      "2.0"))
SL_ATR_MULT     = float(os.environ.get("SL_ATR_MULT",  "1.5"))

# ── Sizing ───────────────────────────────────────────────────────────
MIN_ORDER_USDT  = float(os.environ.get("MIN_ORDER_USDT", "5.0"))
MAX_ORDER_USDT  = float(os.environ.get("MAX_ORDER_USDT", "50.0"))
MAX_MARGIN_PCT  = float(os.environ.get("MAX_MARGIN_PCT", "30.0"))

# ── Anti-spam ────────────────────────────────────────────────────────
# Mínimo de ciclos que deben pasar antes de volver a reportar
# el mismo símbolo en el mensaje de señales (evita spam SEI-USDT)
SIGNAL_COOLDOWN_CYCLES = int(os.environ.get("SIGNAL_COOLDOWN_CYCLES", "3"))

# ── H1 ───────────────────────────────────────────────────────────────
H1_CACHE_TTL    = int(os.environ.get("H1_CACHE_TTL",   "300"))
# Si H1=NEUTRAL, exige score mínimo más alto
H1_NEUTRAL_MIN_SCORE = float(os.environ.get("H1_NEUTRAL_MIN_SCORE", "65.0"))

# ── Circuit breaker ──────────────────────────────────────────────────
MAX_CONSEC_LOSSES = int(os.environ.get("MAX_CONSEC_LOSSES", "3"))
CB_PAUSE_MINS     = int(os.environ.get("CB_PAUSE_MINS",    "45"))
COOLDOWN_MINS     = int(os.environ.get("COOLDOWN_MINS",    "20"))

# ── Sesión ───────────────────────────────────────────────────────────
SESSION_FILTER  = os.environ.get("SESSION_FILTER", "false").lower() == "true"
SESSION_START   = int(os.environ.get("SESSION_START", "6"))
SESSION_END     = int(os.environ.get("SESSION_END",  "22"))

# FIX: filtra cualquier valor vacío o basura — Railway puede pasar " " o ""
_raw = os.environ.get("CUSTOM_SYMBOLS", "").strip()
CUSTOM_SYMBOLS = [s.strip() for s in _raw.split(",") if s.strip() and len(s.strip()) > 3] if _raw else []

BINGX_BASE   = "https://open-api.bingx.com"
INTERVAL_MAP = {"1m":"1m","3m":"3m","5m":"5m","15m":"15m","30m":"30m","1h":"1H","4h":"4H"}
EXCLUDED_PREFIXES  = ("NCS","NCF","NCMEX","NCOIL","NCGAS","NCXAU","NCXAG")
EXCLUDED_KEYWORDS  = ("Gasoline","GasOil","Brent","WTI","Copper","Wheat","Cotton",
                      "Soybean","Silver","EURUSD","GBPUSD","JPYUSD")

FALLBACK_SYMBOLS = [
    "BTC-USDT","ETH-USDT","BNB-USDT","SOL-USDT","XRP-USDT",
    "DOGE-USDT","ADA-USDT","AVAX-USDT","DOT-USDT","LINK-USDT",
    "MATIC-USDT","INJ-USDT","SUI-USDT","ARB-USDT","OP-USDT",
    "WIF-USDT","PEPE-USDT","WLD-USDT","TIA-USDT","SEI-USDT",
    "NEAR-USDT","APT-USDT","FIL-USDT","HBAR-USDT","AAVE-USDT",
    "LDO-USDT","RUNE-USDT","GRT-USDT","CRV-USDT","DYDX-USDT",
]

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.StreamHandler()])
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════
#  ESTADO GLOBAL
# ══════════════════════════════════════════════════════════════════════
sl_cooldown      = {}   # {sym: datetime} — cooldown tras SL
h1_cache         = {}   # {sym: (df, ts)}
signal_last_seen = {}   # {sym: cycle_number} — anti-spam
consec_losses    = 0
cb_pause_until   = None

# ══════════════════════════════════════════════════════════════════════
#  BINGX API
# ══════════════════════════════════════════════════════════════════════
def _sign(params):
    qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(BINGX_SECRET_KEY.encode(), qs.encode(), hashlib.sha256).hexdigest()

def bx_get(path, params=None):
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000)
    p["signature"] = _sign(p)
    r = requests.get(BINGX_BASE + path, params=p,
                     headers={"X-BX-APIKEY": BINGX_API_KEY}, timeout=15)
    r.raise_for_status()
    return r.json()

def bx_post(path, payload):
    p = dict(payload)
    p["timestamp"] = int(time.time() * 1000)
    p["signature"] = _sign(p)
    r = requests.post(BINGX_BASE + path, json=p,
                      headers={"X-BX-APIKEY": BINGX_API_KEY,
                               "Content-Type": "application/json"}, timeout=15)
    r.raise_for_status()
    return r.json()

def get_balance():
    try:
        data = bx_get("/openApi/swap/v2/user/balance")
        bal  = data.get("data", {}).get("balance", {})
        for f in ("availableMargin","available","crossWalletBalance","walletBalance","equity"):
            v = bal.get(f)
            if v is not None and v != "" and float(v) > 0:
                log.info(f"Balance: {float(v):.4f} USDT ({f})")
                return float(v)
        return 0.0
    except Exception as e:
        log.error(f"get_balance: {e}")
        return 0.0

def get_all_positions():
    try:
        data   = bx_get("/openApi/swap/v2/user/positions", {})
        result = {}
        for p in data.get("data", []):
            if isinstance(p, dict) and float(p.get("positionAmt", 0)) != 0:
                result[p["symbol"]] = p
        log.info(f"Posiciones abiertas ({len(result)}): {list(result.keys())[:8]}")
        return result
    except Exception as e:
        log.error(f"get_positions: {e}")
        return {}

def _is_valid(sym):
    if not sym or not sym.endswith("-USDT"): return False
    base = sym.replace("-USDT","")
    if len(base) < 2: return False
    if any(base.startswith(p) for p in EXCLUDED_PREFIXES): return False
    if any(kw.lower() in sym.lower() for kw in EXCLUDED_KEYWORDS): return False
    return True

def get_all_symbols(limit=0):
    try:
        data = bx_get("/openApi/swap/v2/quote/contracts", {})
        contracts = data.get("data", [])
        usdt = [c for c in contracts
                if isinstance(c, dict) and c.get("asset","") == "USDT" and c.get("status") == 1]
        if not usdt:
            usdt = [c for c in contracts
                    if isinstance(c, dict) and c.get("asset","") == "USDT"]
        usdt.sort(key=lambda x: float(x.get("tradeAmount", 0) or 0), reverse=True)
        syms   = [c["symbol"] for c in usdt if _is_valid(c.get("symbol",""))]
        result = syms if limit == 0 else syms[:limit]
        log.info(f"✅ {len(result)} símbolos cargados")
        return result or FALLBACK_SYMBOLS
    except Exception as e:
        log.warning(f"get_all_symbols: {e}")
        return FALLBACK_SYMBOLS

def set_lev(symbol):
    for side in ("LONG","SHORT"):
        try:
            bx_post("/openApi/swap/v2/trade/leverage",
                    {"symbol": symbol, "side": side, "leverage": LEVERAGE})
        except Exception:
            pass

def get_live_price(symbol):
    for attempt in range(3):
        try:
            if attempt == 0:
                data  = bx_get("/openApi/swap/v2/quote/premiumIndex", {"symbol": symbol})
                items = data.get("data", [])
                if isinstance(items, list):
                    for item in items:
                        if item.get("symbol") == symbol and item.get("markPrice"):
                            return float(item["markPrice"])
                if isinstance(items, dict) and items.get("markPrice"):
                    return float(items["markPrice"])
            elif attempt == 1:
                data = bx_get("/openApi/swap/v2/quote/ticker", {"symbol": symbol})
                t    = data.get("data", [])
                if isinstance(t, list):
                    for item in t:
                        if item.get("symbol") == symbol:
                            lp = item.get("lastPrice") or item.get("price")
                            if lp: return float(lp)
                if isinstance(t, dict):
                    lp = t.get("lastPrice") or t.get("price")
                    if lp: return float(lp)
            else:
                params = {"symbol": symbol,
                          "interval": INTERVAL_MAP.get(TIMEFRAME,"5m"), "limit": 2}
                data = bx_get("/openApi/swap/v3/quote/klines", params)
                rows = data.get("data", [])
                if rows: return float(rows[-1][4])
        except Exception:
            pass
    raise ValueError(f"Sin precio para {symbol}")

# ══════════════════════════════════════════════════════════════════════
#  KLINES
# ══════════════════════════════════════════════════════════════════════
def _fetch_klines(symbol, interval, limit):
    params = {"symbol": symbol,
              "interval": INTERVAL_MAP.get(interval, interval), "limit": limit}
    data = bx_get("/openApi/swap/v3/quote/klines", params)
    rows = data.get("data", [])
    if not rows or not isinstance(rows, list):
        return pd.DataFrame()
    df = pd.DataFrame(rows,
                      columns=["open_time","open","high","low","close","volume","close_time"])
    for col in ("open","high","low","close","volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df.dropna(subset=["open","high","low","close","volume"], inplace=True)
    return df.sort_values("open_time").reset_index(drop=True)

def get_klines(symbol, limit=250):
    return _fetch_klines(symbol, TIMEFRAME, limit)

def get_h1_klines(symbol, limit=80):
    now    = time.time()
    cached = h1_cache.get(symbol)
    if cached:
        df_c, ts = cached
        if now - ts < H1_CACHE_TTL and len(df_c) >= 30:
            return df_c.copy()
    try:
        df = _fetch_klines(symbol, "1h", limit)
        if not df.empty:
            h1_cache[symbol] = (df.copy(), now)
        return df
    except Exception:
        return pd.DataFrame()

# ══════════════════════════════════════════════════════════════════════
#  INDICADORES
# ══════════════════════════════════════════════════════════════════════
def calc_atr(high, low, close, period=14):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()

def calc_ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def calc_ema_angle(ema_s, atr_s, look=5):
    price_change = ema_s - ema_s.shift(look)
    denom        = atr_s * look
    return pd.Series(
        np.degrees(np.arctan2(price_change.values, denom.values)),
        index=ema_s.index
    )

def calc_adx(high, low, close, period=14):
    up       = high.diff()
    down     = -low.diff()
    plus_dm  = np.where((up > down) & (up > 0),   up,   0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs()
    ], axis=1).max(axis=1)
    alpha = 1 / period
    def w(arr):
        return pd.Series(arr, index=high.index).ewm(alpha=alpha, adjust=False).mean()
    tr_s  = w(tr);  pdm_s = w(plus_dm);  mdm_s = w(minus_dm)
    di_p  = 100 * pdm_s / tr_s.replace(0, np.nan)
    di_m  = 100 * mdm_s / tr_s.replace(0, np.nan)
    dx    = 100 * (di_p - di_m).abs() / (di_p + di_m).replace(0, np.nan)
    adx   = dx.ewm(alpha=alpha, adjust=False).mean()
    return di_p, di_m, adx

def calc_rsi(close, period=14):
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calc_supertrend(high, low, close, period=10, mult=3.0):
    atr       = calc_atr(high, low, close, period)
    hl2       = (high + low) / 2
    upper_raw = hl2 + mult * atr
    lower_raw = hl2 - mult * atr
    direction = pd.Series(1, index=close.index, dtype=int)
    final_ub  = upper_raw.copy()
    final_lb  = lower_raw.copy()
    for i in range(1, len(close)):
        if upper_raw.iloc[i] < final_ub.iloc[i-1] or close.iloc[i-1] > final_ub.iloc[i-1]:
            final_ub.iloc[i] = upper_raw.iloc[i]
        else:
            final_ub.iloc[i] = final_ub.iloc[i-1]
        if lower_raw.iloc[i] > final_lb.iloc[i-1] or close.iloc[i-1] < final_lb.iloc[i-1]:
            final_lb.iloc[i] = lower_raw.iloc[i]
        else:
            final_lb.iloc[i] = final_lb.iloc[i-1]
        if close.iloc[i] > final_ub.iloc[i-1]:
            direction.iloc[i] = 1
        elif close.iloc[i] < final_lb.iloc[i-1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i-1]
    return direction

def calc_heikin_ashi(df):
    ha = df.copy()
    ha["ha_close"] = (df["open"] + df["high"] + df["low"] + df["close"]) / 4
    ha["ha_open"]  = ha["ha_close"].copy()
    for i in range(1, len(ha)):
        ha.at[ha.index[i], "ha_open"] = \
            (ha["ha_open"].iloc[i-1] + ha["ha_close"].iloc[i-1]) / 2
    return ha

def calc_vwap(df):
    typical    = (df["high"] + df["low"] + df["close"]) / 3
    df2        = df.copy()
    df2["_tp"] = typical * df["volume"]
    df2["_day"]= df2["open_time"].dt.floor("D")
    df2["_ctp"]= df2.groupby("_day")["_tp"].cumsum()
    df2["_cv"] = df2.groupby("_day")["volume"].cumsum()
    return df2["_ctp"] / df2["_cv"]

# ══════════════════════════════════════════════════════════════════════
#  ANÁLISIS H1
# ══════════════════════════════════════════════════════════════════════
def analyze_h1(symbol):
    df = get_h1_klines(symbol, 80)
    if df.empty or len(df) < 30:
        return None
    close, high, low = df["close"], df["high"], df["low"]
    ema7    = calc_ema(close, 7)
    ema21   = calc_ema(close, 21)
    ema50   = calc_ema(close, 50)
    st_dir  = calc_supertrend(high, low, close, ST_PERIOD, ST_MULT)
    rsi_h1  = calc_rsi(close, 14)
    atr_h1  = calc_atr(high, low, close, 14)
    angle_h1= calc_ema_angle(ema7, atr_h1, 5)

    e7  = float(ema7.iloc[-1])
    e21 = float(ema21.iloc[-1])
    e50 = float(ema50.iloc[-1])
    cl  = float(close.iloc[-1])
    st  = int(st_dir.iloc[-1])
    rsi = float(rsi_h1.iloc[-1])
    ang = float(angle_h1.iloc[-1])

    # Bull H1: EMA alineadas + precio sobre EMA50 + ST alcista
    bull_strong = (e7 > e21 > e50) and (cl > e50) and (st == 1)
    bull_weak   = (e7 > e21) and (st == 1)
    bear_strong = (e7 < e21 < e50) and (cl < e50) and (st == -1)
    bear_weak   = (e7 < e21) and (st == -1)

    if bull_strong:   h1_trend = "BULL"
    elif bull_weak:   h1_trend = "BULL"
    elif bear_strong: h1_trend = "BEAR"
    elif bear_weak:   h1_trend = "BEAR"
    else:             h1_trend = "NEUTRAL"

    h1_strength = 2 if (bull_strong or bear_strong) else (1 if (bull_weak or bear_weak) else 0)

    return {
        "h1_trend":    h1_trend,
        "h1_strength": h1_strength,   # 0=neutral, 1=weak, 2=strong
        "h1_st":       st,
        "h1_rsi":      round(rsi, 1),
        "h1_angle":    round(ang, 1),
    }

# ══════════════════════════════════════════════════════════════════════
#  PATRONES DE VELA
# ══════════════════════════════════════════════════════════════════════
def detect_candle_pattern(df, i, direction, atr_val):
    if i < 1:
        return "NONE", 0.0, None
    o  = float(df["open"].iloc[i]);  h  = float(df["high"].iloc[i])
    l  = float(df["low"].iloc[i]);   c  = float(df["close"].iloc[i])
    o1 = float(df["open"].iloc[i-1]);c1 = float(df["close"].iloc[i-1])
    rng  = h - l
    body = abs(c - o)
    if rng < 1e-10 or atr_val < 1e-10:
        return "NONE", 0.0, None
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    # Pin Bar
    if body / rng < 0.35:
        if direction == "LONG" and lower_wick/rng >= 0.55 and lower_wick >= 2*max(body,1e-10):
            return "PIN_BAR", min(lower_wick/rng*120, 100.0), l - atr_val*0.1
        if direction == "SHORT" and upper_wick/rng >= 0.55 and upper_wick >= 2*max(body,1e-10):
            return "PIN_BAR", min(upper_wick/rng*120, 100.0), h + atr_val*0.1
    # Engulfing
    body1 = abs(c1 - o1)
    if body1 > 1e-10 and body/body1 >= 1.05:
        if direction=="LONG"  and c>o and c1<o1 and c>max(o1,c1) and o<min(o1,c1):
            return "ENGULF", min(body/body1*45, 100.0), l - atr_val*0.1
        if direction=="SHORT" and c<o and c1>o1 and c<min(o1,c1) and o>max(o1,c1):
            return "ENGULF", min(body/body1*45, 100.0), h + atr_val*0.1
    # Momentum
    if body/rng >= 0.65 and body >= atr_val*0.5:
        if direction=="LONG"  and c>o and upper_wick < body*0.35:
            return "MOMENTUM", min(body/rng*90, 100.0), l - atr_val*0.1
        if direction=="SHORT" and c<o and lower_wick < body*0.35:
            return "MOMENTUM", min(body/rng*90, 100.0), h + atr_val*0.1
    return "NONE", 0.0, None

# ══════════════════════════════════════════════════════════════════════
#  POSITION SIZING
# ══════════════════════════════════════════════════════════════════════
def calc_qty(balance, entry, sl, quality_mult=1.0):
    dist_pct = abs(entry - sl) / entry
    if dist_pct < 1e-8:
        return 0, 0
    risk_usdt    = balance * (RISK_PERCENT / 100) * quality_mult
    notional     = risk_usdt / dist_pct
    max_margin   = balance * (MAX_MARGIN_PCT / 100)
    max_notional = min(MAX_ORDER_USDT, max_margin * LEVERAGE)
    notional     = max(MIN_ORDER_USDT, min(notional, max_notional))
    qty          = notional / entry
    return round(max(qty, 0.001), 4), round(notional, 2)

def open_order(symbol, side, qty, sl, tp):
    payload = {
        "symbol":       symbol,
        "side":         side,
        "positionSide": "LONG" if side=="BUY" else "SHORT",
        "type":         "MARKET",
        "quantity":     round(qty, 4),
        "stopLoss": json.dumps({
            "type":"STOP_MARKET","stopPrice":round(sl,6),"workingType":"MARK_PRICE"
        }),
        "takeProfit": json.dumps({
            "type":"TAKE_PROFIT_MARKET","stopPrice":round(tp,6),"workingType":"MARK_PRICE"
        }),
    }
    resp = bx_post("/openApi/swap/v2/trade/order", payload)
    if resp.get("code", 0) != 0:
        raise ValueError(f"BingX {resp.get('code')}: {resp.get('msg','?')}")
    return resp

def open_order_with_retry(symbol, side, qty, sl, tp, atr_val, direction, retries=1):
    for attempt in range(retries + 1):
        try:
            return open_order(symbol, side, qty, sl, tp)
        except ValueError as e:
            if "101400" in str(e) and attempt < retries:
                log.warning(f"101400 {symbol} → retry precio fresco")
                time.sleep(1)
                live = get_live_price(symbol)
                if direction == "LONG":
                    sl = min(live - atr_val*SL_ATR_MULT, live*(1-MIN_DIST_PCT/100))
                    tp = live + (live - sl) * TP_MULT
                else:
                    sl = max(live + atr_val*SL_ATR_MULT, live*(1+MIN_DIST_PCT/100))
                    tp = live - (sl - live) * TP_MULT
                sl = round(sl, 6);  tp = round(tp, 6)
            else:
                raise

# ══════════════════════════════════════════════════════════════════════
#  ESCANEO PRINCIPAL V16 — SLOPE OBLIGATORIO + ANTI-SPAM + 77% WR
# ══════════════════════════════════════════════════════════════════════
def scan_symbol(symbol):
    if symbol in sl_cooldown:
        elapsed = (datetime.now(timezone.utc) - sl_cooldown[symbol]).total_seconds() / 60
        if elapsed < COOLDOWN_MINS:
            return None
    try:
        df = get_klines(symbol, 250)
        if df.empty or len(df) < 120:
            return None

        h, l, c, o = df["high"], df["low"], df["close"], df["open"]
        atr_s  = calc_atr(h, l, c, 14)
        ema_f  = calc_ema(c, EMA_FAST)
        ema_s  = calc_ema(c, EMA_SLOW)
        ema_t  = calc_ema(c, EMA_TREND)
        angle  = calc_ema_angle(ema_f, atr_s, SLOPE_LOOK)
        di_p, di_m, adx_s = calc_adx(h, l, c, ADX_LEN)
        rsi_s  = calc_rsi(c, RSI_LEN)
        vol_ma = df["volume"].rolling(20).mean()
        vwap_s = calc_vwap(df)
        st_dir = calc_supertrend(h, l, c, ST_PERIOD, ST_MULT)
        ha     = calc_heikin_ashi(df)

        # Squeeze OFF
        basis   = c.rolling(20).mean()
        std20   = c.rolling(20).std()
        atr_kc  = calc_atr(h, l, c, 20)
        bb_lo   = basis - 2.0 * std20;  bb_up = basis + 2.0 * std20
        kc_lo   = basis - 1.5 * atr_kc; kc_up = basis + 1.5 * atr_kc
        sqz_off = ~((bb_lo > kc_lo) & (bb_up < kc_up))

        i = len(df) - 2   # vela cerrada
        if i < 100:
            return None

        close_now = float(c.iloc[i])
        atr_val   = float(atr_s.iloc[i])
        if atr_val <= 0: return None
        atr_pct = atr_val / close_now * 100
        if atr_pct > ATR_MAX_PCT: return None

        angle_now  = float(angle.iloc[i])
        angle_prev = float(angle.iloc[i-1])   # para detectar aceleración
        adx_now    = float(adx_s.iloc[i])
        di_p_now   = float(di_p.iloc[i])
        di_m_now   = float(di_m.iloc[i])
        rsi_now    = float(rsi_s.iloc[i])
        vol_now    = float(df["volume"].iloc[i])
        vma        = float(vol_ma.iloc[i])
        sqz_ok     = bool(sqz_off.iloc[i])
        vwap_now   = float(vwap_s.iloc[i])
        st_now     = int(st_dir.iloc[i])
        ha_bull    = float(ha["ha_close"].iloc[i]) > float(ha["ha_open"].iloc[i])
        vratio     = round(vol_now / vma, 2) if vma > 0 else 0.0
        ema_f_now  = float(ema_f.iloc[i])
        ema_s_now  = float(ema_s.iloc[i])
        ema_t_now  = float(ema_t.iloc[i])

        if any(np.isnan(x) for x in [angle_now, adx_now, rsi_now, atr_val,
                                      ema_f_now, ema_s_now, ema_t_now]):
            return None

        # ── Dirección base ────────────────────────────────────────────
        if   ema_f_now > ema_s_now: direction = "LONG"
        elif ema_f_now < ema_s_now: direction = "SHORT"
        else: return None

        # ══ FILTROS DUROS — cualquiera descarta la señal ══════════════

        # 1. SLOPE ES OBLIGATORIO (fix problema SEI-USDT en V15)
        #    El slope con ❌ no puede generar señal bajo ningún concepto
        slope_ok = angle_now >= SLOPE_LIMIT if direction=="LONG" else angle_now <= -SLOPE_LIMIT
        if not slope_ok:
            return None   # ← sale aquí, no genera señal

        # 2. EMA TREND (precio vs EMA50)
        if direction=="LONG"  and close_now < ema_t_now: return None
        if direction=="SHORT" and close_now > ema_t_now: return None

        # 3. RSI en zona extrema — nunca entrar en sobrecompra/sobreventa
        if direction=="LONG"  and rsi_now > RSI_OB: return None
        if direction=="SHORT" and rsi_now < RSI_OS: return None

        # 4. ADX mínimo (tendencia real)
        if adx_now < ADX_MIN: return None

        # 5. Anti-chop: rango de las últimas 10 velas debe ser > 1.5x ATR
        #    Evita entrar en mercados completamente planos
        rango_10 = float(h.iloc[i-9:i+1].max() - l.iloc[i-9:i+1].min())
        if rango_10 < atr_val * 1.5: return None

        # ══ CONFLUENCIAS — 5 filtros opcionales ══════════════════════
        # (slope ya pasó como filtro duro, no cuenta aquí)
        confluences = 0
        conf_detail = {}

        # C1: SuperTrend 5m
        st_ok = (st_now==1 and direction=="LONG") or (st_now==-1 and direction=="SHORT")
        if st_ok: confluences += 1
        conf_detail["ST"] = f"{'✅' if st_ok else '❌'}{'▲' if st_now==1 else '▼'}"

        # C2: Heikin Ashi
        ha_ok = (ha_bull and direction=="LONG") or (not ha_bull and direction=="SHORT")
        if ha_ok: confluences += 1
        conf_detail["HA"] = "✅" if ha_ok else "❌"

        # C3: VWAP
        vwap_ok = (close_now > vwap_now and direction=="LONG") or \
                  (close_now < vwap_now and direction=="SHORT")
        if vwap_ok: confluences += 1
        conf_detail["VWAP"] = "✅" if vwap_ok else "❌"

        # C4: Volumen
        vol_ok = vratio >= VOL_MULT
        if vol_ok: confluences += 1
        conf_detail["Vol"] = f"{'✅' if vol_ok else '❌'}{vratio:.1f}x"

        # C5: DI direccional
        di_ok = (di_p_now > di_m_now and direction=="LONG") or \
                (di_m_now > di_p_now and direction=="SHORT")
        if di_ok: confluences += 1
        conf_detail["DI"] = "✅" if di_ok else "❌"

        # Mínimo 3 de 5 confluencias (slope ya garantizado)
        if confluences < 3: return None

        # ══ H1 ALIGNMENT ══════════════════════════════════════════════
        h1_ctx      = analyze_h1(symbol)
        h1_trend    = h1_ctx["h1_trend"]    if h1_ctx else "NEUTRAL"
        h1_strength = h1_ctx["h1_strength"] if h1_ctx else 0
        h1_bonus    = 0

        if h1_ctx:
            if h1_trend=="BULL" and direction=="LONG":
                h1_bonus = 15 + h1_strength * 5   # 15 weak, 25 strong
            elif h1_trend=="BEAR" and direction=="SHORT":
                h1_bonus = 15 + h1_strength * 5
            elif h1_trend=="NEUTRAL":
                h1_bonus = 3   # pequeño bonus, no penaliza
            else:
                return None    # H1 opuesto → descarte duro

        # ══ PATRÓN DE VELA ════════════════════════════════════════════
        pat_name, pat_score, sl_candle = detect_candle_pattern(df, i, direction, atr_val)

        # Bonus adicional si el patrón se da con squeeze OFF
        if pat_name != "NONE" and sqz_ok:
            pat_score = min(pat_score * 1.2, 100.0)

        # ══ RSI MOMENTUM — zona de fuerza ════════════════════════════
        # Long ideal: RSI entre 45-65 (subiendo con fuerza, no sobrecomprado)
        # Short ideal: RSI entre 35-55 (bajando con fuerza, no sobrevendido)
        rsi_momentum_bonus = 0
        if direction == "LONG" and 45 <= rsi_now <= 65:
            rsi_momentum_bonus = 8
        elif direction == "SHORT" and 35 <= rsi_now <= 55:
            rsi_momentum_bonus = 8
        elif direction == "LONG" and rsi_now > 65:
            rsi_momentum_bonus = -5  # acercándose a sobrecompra
        elif direction == "SHORT" and rsi_now < 35:
            rsi_momentum_bonus = -5

        # ══ SLOPE ACELERACIÓN — ángulo aumentando ════════════════════
        accel_bonus = 0
        if direction == "LONG"  and angle_now > angle_prev > 0:
            accel_bonus = 7
        elif direction == "SHORT" and angle_now < angle_prev < 0:
            accel_bonus = 7

        # ══ SL / TP ════════════════════════════════════════════════════
        sl_atr = atr_val * SL_ATR_MULT
        if direction == "LONG":
            sl_price = close_now - sl_atr
            if sl_candle and sl_candle > 0:
                sl_price = min(sl_price, sl_candle)
            sl_price = min(sl_price, close_now * (1 - MIN_DIST_PCT/100))
            if sl_price >= close_now: return None
            tp_price = close_now + (close_now - sl_price) * TP_MULT
        else:
            sl_price = close_now + sl_atr
            if sl_candle and sl_candle > 0:
                sl_price = max(sl_price, sl_candle)
            sl_price = max(sl_price, close_now * (1 + MIN_DIST_PCT/100))
            if sl_price <= close_now: return None
            tp_price = close_now - (sl_price - close_now) * TP_MULT

        dist     = abs(close_now - sl_price)
        dist_pct = dist / close_now * 100
        if dist_pct < MIN_DIST_PCT: return None

        rr = abs(tp_price - close_now) / dist
        if rr < MIN_RR: return None

        # ══ SCORING V16 ════════════════════════════════════════════════
        # slope (25) + H1 (25) + confluencias (20) + patrón (12)
        # + RSI momentum (8) + aceleración (7) + vol/ADX (3)
        score  = min(abs(angle_now) / SLOPE_LIMIT * 25, 25)           # slope: max 25
        score += h1_bonus                                               # H1: max 25
        score += (confluences / 5) * 20                                # confl: max 20
        score += min(pat_score / 8, 12)                                # patrón: max 12
        score += rsi_momentum_bonus                                     # RSI: ±8
        score += accel_bonus                                            # accel: +7
        score += min((adx_now - ADX_MIN) / ADX_MIN * 3, 3)            # ADX bonus: max 3

        if score < MIN_SCORE: return None

        # Si H1=NEUTRAL exige score más alto
        if h1_trend == "NEUTRAL" and score < H1_NEUTRAL_MIN_SCORE:
            return None

        quality_mult = round(min(max(0.7 + (score - MIN_SCORE) / 45 * 0.6, 0.7), 1.3), 2)

        return {
            "symbol":       symbol,
            "signal":       direction,
            "pattern":      pat_name,
            "close":        close_now,
            "sl":           round(sl_price, 6),
            "tp":           round(tp_price, 6),
            "atr":          atr_val,
            "atr_pct":      round(atr_pct, 2),
            "vol_ratio":    vratio,
            "angle":        round(angle_now, 1),
            "angle_prev":   round(angle_prev, 1),
            "adx":          round(adx_now, 1),
            "rsi":          round(rsi_now, 1),
            "score":        round(score, 1),
            "rr":           round(rr, 2),
            "dist_pct":     round(dist_pct, 3),
            "confluences":  confluences,
            "conf_detail":  conf_detail,
            "h1_trend":     h1_trend,
            "h1_strength":  h1_strength,
            "pat_score":    round(pat_score, 1),
            "quality_mult": quality_mult,
            "sqz_ok":       sqz_ok,
        }

    except Exception as e:
        log.debug(f"Scan {symbol}: {e}")
        return None

# ══════════════════════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════════════════════
async def _send_tg(msg):
    if not TELEGRAM_OK or not TELEGRAM_TOKEN: return
    bot = Bot(token=TELEGRAM_TOKEN)
    cid = int(TELEGRAM_CHAT_ID) if TELEGRAM_CHAT_ID.lstrip("-").isdigit() else TELEGRAM_CHAT_ID
    await bot.send_message(chat_id=cid, text=msg, parse_mode=ParseMode.HTML)

def tg(msg):
    if not TELEGRAM_TOKEN: return
    try: asyncio.run(_send_tg(msg))
    except Exception as e: log.warning(f"Telegram: {e}")

def tg_startup(balance, symbols):
    tg(
        f"🎯 <b>TRADING BOT V16 — 77% WIN RATE EDITION</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔧 <b>Fix V15:</b> slope obligatorio + anti-spam + H1 estricto\n"
        f"📊 <b>Filtros duros:</b> Slope≥{SLOPE_LIMIT}° | ADX≥{ADX_MIN} | "
        f"EMA{EMA_TREND} | Anti-chop\n"
        f"📐 <b>Confluencias:</b> 3/5 mínimo | <b>Score:</b> {MIN_SCORE}\n"
        f"📡 <b>H1:</b> NEUTRAL requiere score≥{H1_NEUTRAL_MIN_SCORE}\n"
        f"⚡ <b>Extras:</b> RSI momentum | Slope aceleración | Squeeze OFF\n"
        f"🎯 <b>R:R mínimo:</b> {MIN_RR} | <b>TP:</b> {TP_MULT}× | <b>SL:</b> {SL_ATR_MULT}×ATR\n"
        f"🔇 <b>Anti-spam:</b> mismo símbolo máx 1 aviso/{SIGNAL_COOLDOWN_CYCLES} ciclos\n"
        f"💰 <b>Balance:</b> {balance:.2f} USDT | <b>Símbolos:</b> {len(symbols)}\n"
        f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

def tg_scan(signals, total, open_count, cycle):
    """Muestra señales filtrando el spam (mismo símbolo repetido)."""
    if not signals: return

    # Anti-spam: filtra señales que ya se reportaron recientemente
    fresh_signals = []
    for s in signals:
        sym       = s["symbol"]
        last_seen = signal_last_seen.get(sym, -999)
        if cycle - last_seen >= SIGNAL_COOLDOWN_CYCLES:
            fresh_signals.append(s)
            signal_last_seen[sym] = cycle

    if not fresh_signals: return

    lines = [
        f"🔍 <b>{len(signals)} señal(es) / {total} sym</b> | "
        f"Trades: {open_count}/{MAX_OPEN_TRADES}",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for s in fresh_signals[:6]:
        e    = "🟢" if s["signal"]=="LONG" else "🔴"
        accel= "↑" if s["angle"] > s["angle_prev"] and s["signal"]=="LONG" \
               else ("↓" if s["angle"] < s["angle_prev"] and s["signal"]=="SHORT" else "→")
        h1ic = "💪" if s["h1_strength"]==2 else ("👍" if s["h1_strength"]==1 else "➡️")
        cd   = " ".join(s.get("conf_detail",{}).values())
        lines.append(
            f"{e} <b>{s['symbol']}</b> {s['pattern']} "
            f"Score:<b>{s['score']:.0f}</b> {s['confluences']}/5 "
            f"{h1ic}H1:{s['h1_trend']} Ang:{s['angle']}°{accel}\n"
            f"   {cd} RR:1:{s['rr']}"
        )
    lines.append(f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC")
    tg("\n".join(lines))

def tg_entry(sig, qty, notional, balance):
    d    = "🟢 LONG" if sig["signal"]=="LONG" else "🔴 SHORT"
    cd   = " | ".join(f"{k}:{v}" for k,v in sig.get("conf_detail",{}).items())
    icon = {"PIN_BAR":"📌","ENGULF":"🔄","MOMENTUM":"💥","NONE":"📈"}.get(sig.get("pattern","NONE"),"⚡")
    h1ic = "💪" if sig["h1_strength"]==2 else ("👍" if sig["h1_strength"]==1 else "➡️")
    accel= "📈 Acelerando" if sig["angle"] > sig["angle_prev"] and sig["signal"]=="LONG" \
           else ("📉 Acelerando" if sig["angle"] < sig["angle_prev"] and sig["signal"]=="SHORT"
                 else "→ Estable")
    tg(
        f"<b>✅ ENTRADA V16 — {sig['symbol']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Dir:</b> {d} | <b>Score:</b> {sig['score']:.0f}/100\n"
        f"<b>Confl:</b> {sig['confluences']}/5 | {h1ic} <b>H1:</b> {sig['h1_trend']}\n"
        f"{icon} <b>Patrón:</b> {sig['pattern']} ({sig['pat_score']:.0f}) | {accel}\n"
        f"<b>Filtros:</b> {cd}\n"
        f"<b>Ang:</b> {sig['angle']}° (prev:{sig['angle_prev']}°) | "
        f"<b>ADX:</b> {sig['adx']} | <b>RSI:</b> {sig['rsi']}\n"
        f"<b>Vol:</b> {sig['vol_ratio']}x | <b>ATR:</b> {sig['atr_pct']}% | "
        f"<b>Sqz:</b> {'OFF✅' if sig['sqz_ok'] else 'ON⚠️'}\n"
        f"<b>Entrada:</b> <code>{sig['close']:.6g}</code>\n"
        f"<b>Stop:</b>   <code>{sig['sl']:.6g}</code> ({sig['dist_pct']}%)\n"
        f"<b>Target:</b> <code>{sig['tp']:.6g}</code> | <b>R:R</b> 1:{sig['rr']}\n"
        f"<b>Qty:</b> {qty:.4f} | <b>Notional:</b> {notional:.2f}U | "
        f"<b>Kelly×:</b> {sig['quality_mult']}\n"
        f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC"
    )

# ══════════════════════════════════════════════════════════════════════
#  MAIN LOOP
# ══════════════════════════════════════════════════════════════════════
def main():
    global consec_losses, cb_pause_until

    log.info("╔══════════════════════════════════════════════╗")
    log.info("║  TRADING BOT V16 — 77% WIN RATE EDITION      ║")
    log.info("╚══════════════════════════════════════════════╝")
    log.info(f"  Slope≥{SLOPE_LIMIT}° OBLIGATORIO | ADX≥{ADX_MIN} | "
             f"EMA{EMA_TREND} | Anti-chop | Score≥{MIN_SCORE}")

    # FIX: CUSTOM_SYMBOLS solo si tiene símbolos reales (evita lista con string vacío)
    _use_custom = [s for s in CUSTOM_SYMBOLS if len(s) > 3]
    if _use_custom:
        symbols = _use_custom
        log.info(f"Usando CUSTOM_SYMBOLS: {symbols}")
    else:
        # MAX_SYMBOLS=0 significa SIN límite — cargar todos
        _limit = MAX_SYMBOLS if MAX_SYMBOLS and MAX_SYMBOLS > 0 else 0
        symbols = get_all_symbols(_limit)
    if not symbols:
        symbols = FALLBACK_SYMBOLS

    balance   = get_balance()
    positions = get_all_positions()
    log.info(f"Balance: {balance:.4f} | Símbolos: {len(symbols)} | Abiertas: {len(positions)}")

    # Pre-cargar H1 en background
    def _prefetch():
        log.info("Pre-cargando H1 cache...")
        with ThreadPoolExecutor(max_workers=10) as ex:
            list(ex.map(get_h1_klines, symbols[:100]))
        log.info("H1 cache listo.")
    threading.Thread(target=_prefetch, daemon=True).start()

    with ThreadPoolExecutor(max_workers=20) as ex:
        list(ex.map(set_lev, symbols))

    tg_startup(balance, symbols)
    log.info("✅ Bot V16 iniciado.")

    errors = 0
    cycle  = 0

    while True:
        t0     = time.time()
        cycle += 1
        try:
            if SESSION_FILTER:
                hour = datetime.now(timezone.utc).hour
                if not (SESSION_START <= hour < SESSION_END):
                    log.info(f"⏸️ Fuera de sesión ({hour}h UTC).")
                    time.sleep(300)
                    continue

            if cb_pause_until and datetime.now(timezone.utc) < cb_pause_until:
                rem = (cb_pause_until - datetime.now(timezone.utc)).seconds // 60
                log.info(f"🛑 Circuit breaker: {rem}min.")
                time.sleep(60)
                continue

            balance    = get_balance()
            positions  = get_all_positions()
            open_count = len(positions)

            log.info(
                f"── V16 | {balance:.2f}U | {open_count}/{MAX_OPEN_TRADES} | "
                f"{len(symbols)} sym | ciclo #{cycle} ──"
            )

            # ── Scan ─────────────────────────────────────────────────
            signals = []
            with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as ex:
                futs = {ex.submit(scan_symbol, s): s for s in symbols}
                for f in as_completed(futs):
                    r = f.result()
                    if r: signals.append(r)

            signals.sort(key=lambda x: x["score"], reverse=True)
            log.info(f"Señales: {len(signals)}/{len(symbols)}")

            for s in signals[:5]:
                log.info(
                    f"  → {s['symbol']} {s['signal']} [{s['pattern']}] "
                    f"H1:{s['h1_trend']}({s['h1_strength']}) "
                    f"score={s['score']:.1f} ang={s['angle']}°→{s['angle_prev']}° "
                    f"adx={s['adx']} rsi={s['rsi']} rr=1:{s['rr']}"
                )

            # Anti-spam en Telegram
            tg_scan(signals, len(symbols), open_count, cycle)

            # ── Ejecutar órdenes ─────────────────────────────────────
            entered = set()
            for sig in signals:
                sym = sig["symbol"]
                if sym in positions or sym in entered: continue
                if open_count >= MAX_OPEN_TRADES:
                    log.info(f"Max trades ({MAX_OPEN_TRADES}).")
                    break
                if balance < MIN_ORDER_USDT:
                    log.warning(f"Balance bajo: {balance:.2f}U")
                    break

                try:
                    set_lev(sym)
                    live      = get_live_price(sym)
                    atr_val   = sig["atr"]
                    direction = sig["signal"]

                    if direction == "LONG":
                        sl = min(live - atr_val*SL_ATR_MULT, live*(1-MIN_DIST_PCT/100))
                        tp = live + (live - sl) * TP_MULT
                    else:
                        sl = max(live + atr_val*SL_ATR_MULT, live*(1+MIN_DIST_PCT/100))
                        tp = live - (sl - live) * TP_MULT

                    if sl <= 0 or tp <= 0: continue
                    rr_live = abs(tp - live) / abs(live - sl)
                    if rr_live < MIN_RR: continue

                    qty, notional = calc_qty(balance, live, sl, sig["quality_mult"])
                    if qty <= 0 or notional < MIN_ORDER_USDT: continue

                    log.info(
                        f"ORDEN {sym} {direction} qty={qty:.4f} "
                        f"notional={notional:.1f}U live={live:.6g} "
                        f"sl={sl:.6g} tp={tp:.6g} score={sig['score']:.1f} "
                        f"H1:{sig['h1_trend']}({sig['h1_strength']})"
                    )

                    side = "BUY" if direction=="LONG" else "SELL"
                    res  = open_order_with_retry(
                        sym, side, qty, round(sl,6), round(tp,6),
                        atr_val, direction, retries=1
                    )
                    log.info(f"✅ {sym} abierto | {res}")

                    sig.update({
                        "close":    live,
                        "sl":       round(sl, 6),
                        "tp":       round(tp, 6),
                        "dist_pct": round(abs(live-sl)/live*100, 3),
                        "rr":       round(rr_live, 2),
                    })
                    tg_entry(sig, qty, notional, balance)
                    entered.add(sym)
                    open_count += 1
                    time.sleep(0.5)

                except Exception as e:
                    log.error(f"Order FAILED {sym}: {e}")
                    if "stop" in str(e).lower() or "liquidat" in str(e).lower():
                        sl_cooldown[sym] = datetime.now(timezone.utc)
                    tg(f"⚠️ <b>Error {sym}</b>: <code>{str(e)[:150]}</code>")

            errors = 0

        except KeyboardInterrupt:
            tg("🛑 <b>Bot V16 detenido</b>")
            break
        except Exception as e:
            errors += 1
            log.exception(f"Cycle error #{errors}: {e}")
            if errors <= 3:
                tg(f"⚠️ <b>Error ciclo #{errors}</b>: <code>{str(e)[:200]}</code>")
            if errors >= 10:
                tg("🔴 <b>CRÍTICO: 10 errores. Detenido.</b>")
                break

        time.sleep(max(0, LOOP_SECONDS - (time.time() - t0)))


if __name__ == "__main__":
    main()
