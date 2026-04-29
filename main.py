"""
╔══════════════════════════════════════════════════════════════╗
║  CRYPTOBOT v4  —  EMA10 × 15m × 8  ×  HTF × BOS × CVD      ║
║  Archivo único — Railway-ready                               ║
╚══════════════════════════════════════════════════════════════╝
Estrategia:
  • Sesgo HTF (1h): EMA50 / EMA200 — filtra dirección macro
  • Entrada 15m : EMA10 CROSS o RETEST con cuerpo confirmado
  • Filtro BOS  : Break of Structure valida momentum estructural
  • Filtro CVD  : Cumulative Volume Delta confirma presión real
  • Scoring 0-100: necesita ≥ 60 pts para ejecutar

Gestión de riesgo:
  • Riesgo fijo por trade (% configurable)
  • Límite de pérdida diaria máxima
  • Trailing SL automático (+0.5% de ganancia activa el trail)
  • Máximo trades simultáneos configurable

Infraestructura:
  • WebSocket BingX con reconexión automática
  • Polling REST como fallback (cada POLL_INTERVAL segundos)
  • Notificaciones Telegram con HTML enriquecido
  • Paper trading por defecto (DRY_RUN=true)
  • Reporte diario automático 23:55 UTC
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import aiohttp
import numpy as np
import websockets
from dotenv import load_dotenv

# ─── Entorno ────────────────────────────────────────────────────────────────
load_dotenv()
os.makedirs("logs", exist_ok=True)

# ══════════════════════════════════════════════════════════════════════════════
# 1. CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════
BINGX_API_KEY    = os.getenv("BINGX_API_KEY",    "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SYMBOLS         = os.getenv("SYMBOLS",         "BTC-USDT,ETH-USDT").split(",")
TF_HTF          = "1h"
TF_ENTRY        = "15m"

# Parámetros EMA
EMA_FAST        = 10
EMA_SLOW_HTF    = 50
EMA_TREND_HTF   = 200
EMA_MULTIPLIER  = 8      # EMA10×15m×8: factor mínimo de cuerpo

# Parámetros estrategia
BOS_LOOKBACK    = 20     # velas para detectar swing H/L
CVD_PERIOD      = 20     # período CVD
MIN_CVD_DELTA   = 0.15   # 15% mínimo de presión neta
MIN_VOLUME_MULT = 1.3    # 30% encima de la media
SL_ATR_MULT     = 1.5    # SL = ATR × 1.5
MIN_SCORE       = 60     # Score mínimo para operar

# Gestión de riesgo
RISK_PER_TRADE  = float(os.getenv("RISK_PER_TRADE",  "1.0"))  # % capital
MAX_DAILY_LOSS  = float(os.getenv("MAX_DAILY_LOSS",  "5.0"))  # % pérdida diaria máx
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES",   "2"))
RISK_REWARD     = float(os.getenv("RISK_REWARD",     "2.5"))
LEVERAGE        = int(os.getenv("LEVERAGE",          "10"))

# Filtros
TRADE_HOURS_UTC = os.getenv("TRADE_HOURS_UTC", "7-23")  # "HH-HH" UTC

# Sistema
LOG_LEVEL       = os.getenv("LOG_LEVEL",    "INFO")
DRY_RUN         = os.getenv("DRY_RUN",      "true").lower() == "true"
POLL_INTERVAL   = int(os.getenv("POLL_INTERVAL", "10"))   # segundos
CANDLES_REQUIRED = max(EMA_TREND_HTF, BOS_LOOKBACK) + 60  # buffer mínimo

# ══════════════════════════════════════════════════════════════════════════════
# 2. LOGGER
# ══════════════════════════════════════════════════════════════════════════════
_C = {"DEBUG":"\033[36m","INFO":"\033[32m","WARNING":"\033[33m",
      "ERROR":"\033[31m","CRITICAL":"\033[35m","RESET":"\033[0m"}

class _Fmt(logging.Formatter):
    def format(self, r: logging.LogRecord) -> str:
        c = _C.get(r.levelname, _C["RESET"])
        r = logging.makeLogRecord(r.__dict__)
        r.levelname = f"{c}{r.levelname:<8}{_C['RESET']}"
        r.name = f"\033[1m{r.name}\033[0m"
        return super().format(r)

def _make_logger(name: str) -> logging.Logger:
    lvl = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
    lg  = logging.getLogger(name)
    if lg.handlers:
        return lg
    lg.setLevel(lvl)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(lvl)
    ch.setFormatter(_Fmt(fmt="%(asctime)s │ %(levelname)s │ %(name)s │ %(message)s",
                         datefmt="%H:%M:%S"))
    lg.addHandler(ch)
    fh = logging.FileHandler("logs/bot.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"))
    lg.addHandler(fh)
    lg.propagate = False
    return lg

log = _make_logger("Main")

# ══════════════════════════════════════════════════════════════════════════════
# 3. BINGX CLIENT  (REST + WebSocket)
# ══════════════════════════════════════════════════════════════════════════════
def _sign(params: dict, secret: str) -> str:
    qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()


class BingXClient:
    BASE = "https://open-api.bingx.com"
    _log = _make_logger("BingX")

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws_cb:   Dict[str, Callable] = {}

    # ── sesión ────────────────────────────────────────────────────────────────
    def _s(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"X-BX-APIKEY": BINGX_API_KEY},
                connector=aiohttp.TCPConnector(ssl=True),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ── firma ─────────────────────────────────────────────────────────────────
    def _signed(self, p: dict) -> dict:
        p["timestamp"] = int(time.time() * 1000)
        p["signature"] = _sign(p, BINGX_SECRET_KEY)
        return p

    # ── REST helpers ──────────────────────────────────────────────────────────
    async def _get(self, path: str, params: Optional[dict] = None,
                   signed: bool = False) -> Any:
        p = dict(params or {})
        if signed:
            p = self._signed(p)
        try:
            async with self._s().get(
                self.BASE + path, params=p,
                timeout=aiohttp.ClientTimeout(total=12)
            ) as r:
                data = await r.json(content_type=None)
                if isinstance(data, dict) and data.get("code", 0) != 0:
                    self._log.warning(f"API [{path}]: {data.get('msg', data)}")
                return data
        except asyncio.TimeoutError:
            self._log.error(f"Timeout GET {path}"); return {}
        except Exception as e:
            self._log.error(f"GET {path}: {e}"); return {}

    async def _post(self, path: str, params: Optional[dict] = None) -> Any:
        p = self._signed(dict(params or {}))
        try:
            async with self._s().post(
                self.BASE + path, params=p,
                timeout=aiohttp.ClientTimeout(total=12)
            ) as r:
                return await r.json(content_type=None)
        except asyncio.TimeoutError:
            self._log.error(f"Timeout POST {path}"); return {}
        except Exception as e:
            self._log.error(f"POST {path}: {e}"); return {}

    # ── datos de mercado ──────────────────────────────────────────────────────
    async def get_klines(self, symbol: str, interval: str,
                         limit: int = 300) -> List[dict]:
        data = await self._get(
            "/openApi/swap/v2/quote/klines",
            {"symbol": symbol, "interval": interval, "limit": min(limit, 1440)},
        )
        out: List[dict] = []
        for c in (data.get("data", []) if isinstance(data, dict) else []):
            if isinstance(c, dict):
                out.append({
                    "time":   int(c.get("time",   c.get("t", 0))),
                    "open":   float(c.get("open",  c.get("o", 0))),
                    "high":   float(c.get("high",  c.get("h", 0))),
                    "low":    float(c.get("low",   c.get("l", 0))),
                    "close":  float(c.get("close", c.get("c", 0))),
                    "volume": float(c.get("volume",c.get("v", 0))),
                })
            elif isinstance(c, list) and len(c) >= 6:
                out.append({"time":int(c[0]),"open":float(c[1]),"high":float(c[2]),
                             "low":float(c[3]),"close":float(c[4]),"volume":float(c[5])})
        out.sort(key=lambda x: x["time"])
        return out

    async def get_ticker(self, symbol: str) -> dict:
        d = await self._get("/openApi/swap/v2/quote/ticker", {"symbol": symbol})
        return d.get("data", {}) if isinstance(d, dict) else {}

    # ── cuenta ────────────────────────────────────────────────────────────────
    async def get_balance(self) -> dict:
        if DRY_RUN:
            return {"equity": 10_000.0, "available": 10_000.0}
        d = await self._get("/openApi/swap/v2/user/balance", {}, signed=True)
        b = ((d.get("data") or {}).get("balance") or {}) if isinstance(d, dict) else {}
        return {
            "equity":    float(b.get("equity",          0)),
            "available": float(b.get("availableMargin", 0)),
        }

    async def get_open_positions(self, symbol: str = "") -> list:
        if DRY_RUN: return []
        p = {"symbol": symbol} if symbol else {}
        d = await self._get("/openApi/swap/v2/user/positions", p, signed=True)
        return d.get("data", []) if isinstance(d, dict) else []

    # ── órdenes ───────────────────────────────────────────────────────────────
    async def set_leverage(self, symbol: str, leverage: int) -> None:
        if DRY_RUN:
            self._log.info(f"[DRY] Leverage {symbol} → {leverage}x"); return
        for side in ("LONG", "SHORT"):
            await self._post("/openApi/swap/v2/trade/leverage",
                             {"symbol": symbol, "leverage": leverage, "side": side})

    async def place_order(self, symbol: str, side: str, position_side: str,
                          qty: float, sl_price: float = 0.0,
                          tp_price: float = 0.0) -> dict:
        if DRY_RUN:
            self._log.info(
                f"[DRY] {symbol} {side}/{position_side} qty={qty:.4f} "
                f"SL={sl_price:.4f} TP={tp_price:.4f}"
            )
            return {"orderId": f"DRY_{int(time.time())}", "status": "FILLED"}
        p: dict = {
            "symbol": symbol, "side": side, "positionSide": position_side,
            "type": "MARKET", "quantity": qty,
        }
        if sl_price:
            p["stopLoss"] = json.dumps({
                "type":"STOP_MARKET","stopPrice":sl_price,
                "price":sl_price,"workingType":"MARK_PRICE"})
        if tp_price:
            p["takeProfit"] = json.dumps({
                "type":"TAKE_PROFIT_MARKET","stopPrice":tp_price,
                "price":tp_price,"workingType":"MARK_PRICE"})
        d = await self._post("/openApi/swap/v2/trade/order", p)
        order = ((d.get("data") or {}) if isinstance(d, dict) else {})
        self._log.info(f"Orden ejecutada: {symbol} {side} id={order.get('orderId','?')}")
        return order

    async def close_position(self, symbol: str,
                             position_side: str, qty: float) -> dict:
        side = "SELL" if position_side == "LONG" else "BUY"
        return await self.place_order(symbol, side, position_side, qty)

    # ── WebSocket ─────────────────────────────────────────────────────────────
    def register_ws_callback(self, key: str, cb: Callable) -> None:
        self._ws_cb[key] = cb

    async def stream_klines(self, symbol: str, interval: str) -> None:
        """
        Conecta al WS de BingX y re-entrega velas cerradas al callback.
        Si falla 5 veces seguidas, descansa 5 min (el polling cubre el gap).
        """
        import gzip as _gz
        key     = f"{symbol}_{interval}"
        topic   = f"market.{symbol}.kline.{interval}"
        ws_url  = "wss://open-api.bingx.com/market"
        backoff = 10
        fails   = 0

        while True:
            if fails >= 5:
                self._log.info(f"WS {key}: 5 fallos → modo solo-polling 5 min")
                await asyncio.sleep(300)
                fails = 0
                continue
            try:
                async with websockets.connect(
                    ws_url, ping_interval=None, open_timeout=15, close_timeout=5
                ) as ws:
                    await ws.send(json.dumps({
                        "id": key, "reqType": "sub", "dataType": topic
                    }))
                    self._log.info(f"WS suscrito ✓ {topic}")
                    backoff = 10
                    fails   = 0

                    async for raw in ws:
                        try:
                            if isinstance(raw, bytes):
                                try:    raw = _gz.decompress(raw).decode()
                                except: raw = raw.decode("utf-8", errors="ignore")
                            msg = json.loads(raw)
                            # ping/pong keepalive
                            if "ping" in msg:
                                await ws.send(json.dumps({"pong": msg["ping"]}))
                                continue
                            # datos de kline
                            if "kline" in msg.get("dataType","") and "data" in msg:
                                cb = self._ws_cb.get(key)
                                if cb:
                                    items = (msg["data"] if isinstance(msg["data"], list)
                                             else [msg["data"]])
                                    for kline in items:
                                        await cb(kline)
                        except Exception as e:
                            self._log.debug(f"WS parse {key}: {e}")

            except Exception as e:
                fails += 1
                self._log.warning(
                    f"WS {key} (fallo {fails}/5): {type(e).__name__}: {e} "
                    f"→ reintento en {backoff}s"
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)


# ══════════════════════════════════════════════════════════════════════════════
# 4. ESTRATEGIA
# ══════════════════════════════════════════════════════════════════════════════

# ── helpers numpy ─────────────────────────────────────────────────────────────
def _ema(arr: np.ndarray, p: int) -> np.ndarray:
    k = 2.0 / (p + 1)
    out = np.empty_like(arr)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = arr[i] * k + out[i-1] * (1 - k)
    return out

def _atr(candles: list, period: int = 14) -> float:
    n = min(period, len(candles) - 1)
    if n <= 0:
        return 0.0
    trs = []
    for i in range(1, n + 1):
        h, l, cp = candles[-i]["high"], candles[-i]["low"], candles[-i-1]["close"]
        trs.append(max(h - l, abs(h - cp), abs(l - cp)))
    return float(np.mean(trs))

def _slope_pct(arr: np.ndarray, lookback: int = 5) -> float:
    if len(arr) < lookback + 1 or arr[-lookback-1] == 0:
        return 0.0
    return (arr[-1] - arr[-lookback-1]) / arr[-lookback-1] * 100


# ── HTF Bias ──────────────────────────────────────────────────────────────────
@dataclass
class HTFBias:
    direction: str    # "LONG" | "SHORT" | "NEUTRAL"
    confirmed: bool
    ema50:     float
    ema200:    float
    price:     float
    slope:     float  # pendiente % de EMA50

def calc_htf_bias(candles: list) -> HTFBias:
    _neutral = HTFBias("NEUTRAL", False, 0, 0, 0, 0)
    if len(candles) < EMA_TREND_HTF + 10:
        return _neutral
    closes = np.array([c["close"] for c in candles], dtype=float)
    e50  = _ema(closes, EMA_SLOW_HTF)
    e200 = _ema(closes, EMA_TREND_HTF)
    price = closes[-1]
    s50   = float(e50[-1])
    s200  = float(e200[-1])
    slope = _slope_pct(e50)

    # Condición estricta: alineación precio > EMA50 > EMA200
    if price > s50 > s200 and slope > 0.0:
        d = "LONG"
    elif price < s50 < s200 and slope < 0.0:
        d = "SHORT"
    else:
        d = "NEUTRAL"

    return HTFBias(d, d != "NEUTRAL", round(s50,4), round(s200,4),
                   round(price,4), round(slope,4))


# ── EMA10 Signal ──────────────────────────────────────────────────────────────
@dataclass
class EMASignal:
    signal:      str    # "LONG_CROSS"|"SHORT_CROSS"|"LONG_RETEST"|"SHORT_RETEST"|"NONE"
    entry_type:  str    # "CROSS" | "RETEST" | "NONE"
    direction:   str    # "LONG" | "SHORT" | "NONE"
    entry_price: float
    ema10:       float
    atr:         float

def calc_ema10_signal(candles: list) -> EMASignal:
    _none = EMASignal("NONE","NONE","NONE",0,0,0)
    if len(candles) < 35:
        return _none
    closes = np.array([c["close"] for c in candles], dtype=float)
    e10    = _ema(closes, EMA_FAST)
    atr    = _atr(candles)
    e10n   = float(e10[-1])
    e10p   = float(e10[-2])
    prev   = candles[-2]
    curr   = candles[-1]
    body   = abs(curr["close"] - curr["open"])
    min_b  = atr * EMA_MULTIPLIER * 0.08   # umbral mínimo de cuerpo

    # CRUCE ALCISTA
    if prev["close"] < e10p and curr["close"] > e10n and body >= min_b:
        return EMASignal("LONG_CROSS","CROSS","LONG", curr["close"], e10n, atr)
    # CRUCE BAJISTA
    if prev["close"] > e10p and curr["close"] < e10n and body >= min_b:
        return EMASignal("SHORT_CROSS","CROSS","SHORT", curr["close"], e10n, atr)
    # RETEST ALCISTA: low toca EMA, cierre bullish por encima
    if (curr["low"] <= e10n * 1.002 and curr["close"] > e10n
            and curr["close"] > curr["open"] and body >= min_b * 0.5):
        # confirmar que las últimas 3 velas ceraban por encima de su EMA10
        if all(candles[-i]["close"] > float(e10[-i]) for i in range(3, 6)):
            return EMASignal("LONG_RETEST","RETEST","LONG", curr["close"], e10n, atr)
    # RETEST BAJISTA: high toca EMA, cierre bearish por debajo
    if (curr["high"] >= e10n * 0.998 and curr["close"] < e10n
            and curr["close"] < curr["open"] and body >= min_b * 0.5):
        if all(candles[-i]["close"] < float(e10[-i]) for i in range(3, 6)):
            return EMASignal("SHORT_RETEST","RETEST","SHORT", curr["close"], e10n, atr)

    return _none


# ── Break of Structure ────────────────────────────────────────────────────────
@dataclass
class BOSResult:
    bullish_bos:  bool
    bearish_bos:  bool
    swing_high:   float
    swing_low:    float
    bos_strength: float  # % de ruptura

def calc_bos(candles: list) -> BOSResult:
    if len(candles) < BOS_LOOKBACK + 5:
        return BOSResult(False, False, 0, 0, 0)
    hist = candles[-(BOS_LOOKBACK + 1):-1]
    last = candles[-1]
    sh   = max(c["high"]  for c in hist)
    sl_v = min(c["low"]   for c in hist)
    cls  = last["close"]
    bull = bear = False
    st   = 0.0
    if cls > sh and last["close"] > last["open"]:
        bull = True; st = (cls - sh) / sh * 100
    elif cls < sl_v and last["close"] < last["open"]:
        bear = True; st = (sl_v - cls) / sl_v * 100
    return BOSResult(bull, bear, round(sh,4), round(sl_v,4), round(st,4))


# ── CVD (Cumulative Volume Delta) ─────────────────────────────────────────────
@dataclass
class CVDResult:
    bullish:    bool
    bearish:    bool
    cvd:        float   # normalizado -1..1
    cvd_raw:    float
    volume_ok:  bool
    avg_volume: float

def calc_cvd(candles: list) -> CVDResult:
    if len(candles) < CVD_PERIOD + 1:
        return CVDResult(False, False, 0, 0, False, 0)
    w    = candles[-CVD_PERIOD:]
    last = candles[-1]
    bv   = sum(c["volume"] for c in w if c["close"] >= c["open"])
    sv   = sum(c["volume"] for c in w if c["close"] <  c["open"])
    tot  = bv + sv
    raw  = bv - sv
    norm = raw / tot if tot > 0 else 0.0
    vols = np.array([c["volume"] for c in w], dtype=float)
    avg  = float(np.mean(vols[:-1])) if len(vols) > 1 else 0.0
    vok  = avg > 0 and last["volume"] >= avg * MIN_VOLUME_MULT
    return CVDResult(
        bullish    = norm >=  MIN_CVD_DELTA and vok,
        bearish    = norm <= -MIN_CVD_DELTA and vok,
        cvd        = round(norm, 4),
        cvd_raw    = round(raw,  2),
        volume_ok  = vok,
        avg_volume = round(avg,  4),
    )


# ── Agregador de señales ──────────────────────────────────────────────────────
@dataclass
class TradeSignal:
    direction:   str          # "LONG" | "SHORT" | "HOLD"
    entry_type:  str          # "CROSS" | "RETEST" | "NONE"
    entry_price: float
    stop_loss:   float
    take_profit: float
    risk_reward: float
    score:       float        # 0-100
    confidence:  str          # "HIGH" | "MEDIUM"
    size_mult:   float        # 0.75 o 1.0
    reasons:     List[str] = field(default_factory=list)

_HOLD = TradeSignal("HOLD","NONE",0,0,0,0,0,"LOW",0)

def aggregate_signals(htf: HTFBias, ema: EMASignal,
                      bos: BOSResult, vol: CVDResult,
                      symbol: str) -> TradeSignal:
    if ema.direction not in ("LONG","SHORT"):
        return _HOLD

    d, et    = ema.direction, ema.entry_type
    score    = 0.0
    reasons: List[str] = []

    # ── 1. HTF sesgo (35 pts máx)
    if htf.confirmed and htf.direction == d:
        score += 35
        reasons.append(f"HTF {d} (EMA50/200 alineadas, slope={htf.slope:+.3f}%)")
    elif htf.direction == "NEUTRAL":
        score += 10
        reasons.append("HTF neutral — sin filtro macro")
    else:
        # HTF opuesto → señal bloqueada
        return _HOLD

    # ── 2. EMA10 tipo de entrada (25/20 pts)
    if et == "CROSS":
        score += 25
        reasons.append(f"EMA10 CROSS {d}")
    else:
        score += 20
        reasons.append(f"EMA10 RETEST {d}")

    # ── 3. BOS (20 pts)
    if d == "LONG"  and bos.bullish_bos:
        score += 20
        reasons.append(f"BOS bullish +{bos.bos_strength:.2f}%")
    elif d == "SHORT" and bos.bearish_bos:
        score += 20
        reasons.append(f"BOS bearish +{bos.bos_strength:.2f}%")

    # ── 4. CVD presión de volumen (15 pts)
    if d == "LONG"  and vol.bullish:
        score += 15
        reasons.append(f"CVD bullish {vol.cvd:+.2f}")
    elif d == "SHORT" and vol.bearish:
        score += 15
        reasons.append(f"CVD bearish {vol.cvd:+.2f}")

    # ── 5. Volumen elevado (5 pts)
    if vol.volume_ok:
        score += 5
        reasons.append("Volumen superior a media")

    if score < MIN_SCORE:
        return _HOLD

    # ── SL / TP
    entry   = ema.entry_price
    atr     = ema.atr if ema.atr > 0 else entry * 0.002
    sl_dist = atr * SL_ATR_MULT

    if d == "LONG":
        sl = entry - sl_dist
        tp = entry + sl_dist * RISK_REWARD
    else:
        sl = entry + sl_dist
        tp = entry - sl_dist * RISK_REWARD

    rr = abs(tp - entry) / sl_dist if sl_dist > 0 else 0.0
    if rr < RISK_REWARD * 0.8:
        return _HOLD

    conf = "HIGH"   if score >= 75 else "MEDIUM"
    mult = 1.0      if score >= 75 else 0.75

    log.info(
        f"✦ SEÑAL {symbol} {d} [{et}] "
        f"score={score:.0f} conf={conf} "
        f"entry={entry:.4f} SL={sl:.4f} TP={tp:.4f} RR={rr:.2f}x"
    )
    return TradeSignal(d, et,
                       round(entry,6), round(sl,6), round(tp,6),
                       round(rr,2), round(score,1), conf, mult, reasons)


# ══════════════════════════════════════════════════════════════════════════════
# 5. RISK MANAGER
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class _DailyStats:
    date:   str   = ""
    pnl:    float = 0.0
    trades: int   = 0
    wins:   int   = 0
    losses: int   = 0

@dataclass
class PositionSizing:
    qty:         float
    risk_usdt:   float
    notional:    float
    sl_distance: float   # en %
    valid:       bool
    reason:      str

class RiskManager:
    _log = _make_logger("Risk")

    def __init__(self) -> None:
        self._open:  Dict[str, dict] = {}
        self._daily: _DailyStats     = _DailyStats()
        self._lock   = asyncio.Lock()

    def _reset_if_new_day(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily.date != today:
            self._log.info(f"Nuevo día — reset stats ({today})")
            self._daily = _DailyStats(date=today)

    def calc_size(self, equity: float, entry: float,
                  sl: float, mult: float = 1.0) -> PositionSizing:
        if equity <= 0 or entry <= 0 or sl <= 0:
            return PositionSizing(0,0,0,0,False,"Parámetros inválidos")
        slp = abs(entry - sl) / entry
        if slp < 0.001:
            return PositionSizing(0,0,0,0,False,"SL demasiado cercano (<0.1%)")
        if slp > 0.10:
            return PositionSizing(0,0,0,0,False,"SL demasiado lejos (>10%)")
        risk_u  = equity * (RISK_PER_TRADE / 100) * mult
        step    = 0.001
        qty_raw = risk_u / (entry * slp)
        qty     = max(step, round(qty_raw / step) * step)
        self._log.info(
            f"Sizing │ equity={equity:.2f} riesgo={risk_u:.2f}U "
            f"SL={slp:.2%} qty={qty:.4f} nocional={qty*entry:.2f}U"
        )
        return PositionSizing(round(qty,6), round(risk_u,2),
                              round(qty*entry,2), round(slp*100,3), True, "OK")

    async def can_trade(self, symbol: str, equity: float) -> tuple:
        async with self._lock:
            self._reset_if_new_day()
            dlp = abs(self._daily.pnl) / max(equity, 1) * 100
            if self._daily.pnl < 0 and dlp >= MAX_DAILY_LOSS:
                return False, f"Límite pérdida diaria ({dlp:.1f}% ≥ {MAX_DAILY_LOSS}%)"
            if len(self._open) >= MAX_OPEN_TRADES:
                return False, f"Máx trades abiertos ({len(self._open)}/{MAX_OPEN_TRADES})"
            if symbol in self._open:
                return False, f"Ya existe posición en {symbol}"
            return True, "OK"

    async def register_open(self, symbol: str, direction: str,
                            entry: float, qty: float,
                            sl: float, tp: float) -> None:
        async with self._lock:
            self._open[symbol] = {
                "direction": direction, "entry": entry,
                "qty": qty, "sl": sl, "tp": tp,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            self._daily.trades += 1
            self._log.info(f"Registrado │ {symbol} {direction} @ {entry}")

    async def register_close(self, symbol: str, exit_price: float) -> None:
        async with self._lock:
            pos = self._open.pop(symbol, None)
            if not pos:
                return
            pnl = (exit_price - pos["entry"]) * pos["qty"]
            if pos["direction"] == "SHORT":
                pnl = -pnl
            self._daily.pnl += pnl
            if pnl > 0:
                self._daily.wins   += 1
            else:
                self._daily.losses += 1
            self._log.info(
                f"Cerrado │ {symbol} PnL={pnl:+.2f}U │ Día={self._daily.pnl:+.2f}U"
            )

    def get_stats(self) -> dict:
        self._reset_if_new_day()
        total = self._daily.wins + self._daily.losses
        wr    = self._daily.wins / total * 100 if total > 0 else 0.0
        return {
            "date":         self._daily.date,
            "pnl":          round(self._daily.pnl, 2),
            "trades":       self._daily.trades,
            "wins":         self._daily.wins,
            "losses":       self._daily.losses,
            "winrate":      round(wr, 1),
            "open":         len(self._open),
            "open_symbols": list(self._open.keys()),
        }

    @staticmethod
    def is_trading_hour() -> bool:
        try:
            s, e = TRADE_HOURS_UTC.split("-")
            return int(s) <= datetime.now(timezone.utc).hour < int(e)
        except Exception:
            return True


# ══════════════════════════════════════════════════════════════════════════════
# 6. POSITION MONITOR  (trailing SL + cierre automático)
# ══════════════════════════════════════════════════════════════════════════════
_TRAIL_ACTIVATION_PCT = 0.5   # Activar trailing al +0.5% PnL
_TRAIL_DISTANCE_PCT   = 0.30  # SL se mueve a 0.30% del precio

@dataclass
class _TPos:
    symbol:    str
    direction: str
    entry:     float
    qty:       float
    sl:        float
    tp:        float
    trail_on:  bool = False

class PositionMonitor:
    _log = _make_logger("Monitor")

    def __init__(self, client: BingXClient,
                 risk: RiskManager, tg: "TelegramNotifier") -> None:
        self._c   = client
        self._r   = risk
        self._tg  = tg
        self._pos: Dict[str, _TPos] = {}
        self._lock = asyncio.Lock()

    def track(self, symbol: str, direction: str, entry: float,
              qty: float, sl: float, tp: float) -> None:
        self._pos[symbol] = _TPos(symbol, direction, entry, qty, sl, tp)
        self._log.info(f"Tracking │ {symbol} {direction} @ {entry:.4f}")

    async def _tick(self, pos: _TPos) -> Optional[str]:
        """Devuelve 'SL', 'TP', 'TRAIL' o None."""
        try:
            t     = await self._c.get_ticker(pos.symbol)
            price = float(t.get("lastPrice", t.get("price", 0)))
            if price == 0:
                return None
        except Exception as e:
            self._log.warning(f"Ticker {pos.symbol}: {e}")
            return None

        # ── comprobación SL/TP
        if pos.direction == "LONG":
            if price <= pos.sl: return "SL"
            if price >= pos.tp: return "TP"
        else:
            if price >= pos.sl: return "SL"
            if price <= pos.tp: return "TP"

        # ── trailing
        pnl_pct = (price - pos.entry) / pos.entry * 100
        if pos.direction == "SHORT":
            pnl_pct = -pnl_pct
        if pnl_pct >= _TRAIL_ACTIVATION_PCT:
            pos.trail_on = True

        if pos.trail_on:
            if pos.direction == "LONG":
                nsl = price * (1 - _TRAIL_DISTANCE_PCT / 100)
                if nsl > pos.sl:
                    self._log.info(
                        f"Trail ↑ {pos.symbol}: {pos.sl:.4f} → {nsl:.4f}")
                    pos.sl = nsl
                    return "TRAIL"
            else:
                nsl = price * (1 + _TRAIL_DISTANCE_PCT / 100)
                if nsl < pos.sl:
                    self._log.info(
                        f"Trail ↓ {pos.symbol}: {pos.sl:.4f} → {nsl:.4f}")
                    pos.sl = nsl
                    return "TRAIL"
        return None

    async def _close(self, pos: _TPos, reason: str) -> None:
        self._log.info(f"Cerrando {pos.symbol} motivo={reason}")
        try:
            if not DRY_RUN:
                await self._c.close_position(pos.symbol, pos.direction, pos.qty)
        except Exception as e:
            self._log.error(f"close_position {pos.symbol}: {e}")
        try:
            t  = await self._c.get_ticker(pos.symbol)
            ep = float(t.get("lastPrice", pos.entry))
        except Exception:
            ep = pos.entry
        await self._r.register_close(pos.symbol, ep)
        emoji = "✅" if reason == "TP" else "🛑"
        self._tg.alert(f"{emoji} <b>{pos.symbol}</b> cerrado ({reason}) @ {ep:.4f}")

    async def run(self) -> None:
        self._log.info("PositionMonitor activo")
        while True:
            try:
                async with self._lock:
                    syms = list(self._pos.keys())
                for sym in syms:
                    async with self._lock:
                        pos = self._pos.get(sym)
                    if not pos:
                        continue
                    result = await self._tick(pos)
                    if result in ("SL", "TP"):
                        await self._close(pos, result)
                        async with self._lock:
                            self._pos.pop(sym, None)
            except Exception as e:
                self._log.error(f"run() error: {e}")
            await asyncio.sleep(POLL_INTERVAL)


# ══════════════════════════════════════════════════════════════════════════════
# 7. TELEGRAM NOTIFIER
# ══════════════════════════════════════════════════════════════════════════════
class TelegramNotifier:
    _log = _make_logger("Telegram")

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None
        self._ok = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)

    async def start(self) -> None:
        if self._ok:
            self._session = aiohttp.ClientSession()
            self._log.info("Telegram conectado ✓")
        else:
            self._log.warning("Telegram desactivado (TOKEN/CHAT_ID vacíos)")

    async def _send(self, text: str) -> None:
        if not self._ok or not self._session:
            return
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            async with self._session.post(
                url,
                json={"chat_id": TELEGRAM_CHAT_ID,
                      "text": text, "parse_mode": "HTML"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    self._log.warning(f"TG {r.status}: {(await r.text())[:120]}")
        except Exception as e:
            self._log.warning(f"TG send: {e}")

    def _fire(self, text: str) -> None:
        """Envío no bloqueante."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._send(text))
            else:
                loop.run_until_complete(self._send(text))
        except Exception:
            pass

    # ── mensajes ──────────────────────────────────────────────────────────────
    def bot_started(self, symbols: list, dry_run: bool) -> None:
        mode = "🧪 <b>PAPER TRADING</b>" if dry_run else "🔴 <b>LIVE TRADING</b>"
        self._fire(
            f"🤖 <b>CryptoBot v4 iniciado</b>\n"
            f"Modo: {mode}\n"
            f"Símbolos: <code>{', '.join(symbols)}</code>\n"
            f"Riesgo: {RISK_PER_TRADE}% │ Leverage: {LEVERAGE}x │ RR: {RISK_REWARD}x\n"
            f"Horario: {TRADE_HOURS_UTC} UTC"
        )

    def signal_detected(self, sym: str, sig: TradeSignal) -> None:
        emoji = "📈" if sig.direction == "LONG" else "📉"
        ce    = {"HIGH":"🟢","MEDIUM":"🟡"}.get(sig.confidence,"⚪")
        self._fire(
            f"{emoji} <b>SEÑAL DETECTADA — {sym}</b>\n"
            f"Dirección: <b>{sig.direction}</b> [{sig.entry_type}]\n"
            f"Score: <b>{sig.score:.0f}/100</b> {ce} {sig.confidence}\n"
            f"Entrada:  <code>{sig.entry_price:.4f}</code>\n"
            f"Stop Loss:<code>{sig.stop_loss:.4f}</code>  "
            f"Take Profit:<code>{sig.take_profit:.4f}</code>\n"
            f"RR: <b>{sig.risk_reward:.2f}x</b>\n"
            f"• " + "\n• ".join(sig.reasons)
        )

    def order_placed(self, sym: str, sig: TradeSignal,
                     qty: float, dry_run: bool) -> None:
        label = "🧪 PAPER TRADE" if dry_run else "✅ ORDEN ENVIADA"
        self._fire(
            f"{label}\n"
            f"<b>{sym} {sig.direction}</b> [{sig.entry_type}]\n"
            f"Qty: <code>{qty:.4f}</code>  "
            f"Entrada: <code>{sig.entry_price:.4f}</code>\n"
            f"SL: <code>{sig.stop_loss:.4f}</code>  "
            f"TP: <code>{sig.take_profit:.4f}</code>"
        )

    def alert(self, msg: str) -> None:
        self._fire(f"⚠️ {msg}")

    def daily_report(self, stats: dict) -> None:
        pnl  = stats.get("pnl", 0)
        sign = "+" if pnl >= 0 else ""
        emo  = "🟢" if pnl >= 0 else "🔴"
        self._fire(
            f"📊 <b>Reporte Diario — {stats.get('date','')}</b>\n"
            f"PnL: {emo} <b>{sign}{pnl:.2f} USDT</b>\n"
            f"Trades: {stats.get('trades',0)}  "
            f"✅ {stats.get('wins',0)}  ❌ {stats.get('losses',0)}\n"
            f"Winrate: <b>{stats.get('winrate',0):.1f}%</b>\n"
            f"Posiciones abiertas: {stats.get('open',0)} "
            f"({', '.join(stats.get('open_symbols',[])) or '—'})"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 8. ORQUESTADOR PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════
client = BingXClient()
risk   = RiskManager()
tg     = TelegramNotifier()
pmon   = PositionMonitor(client, risk, tg)

candle_buffer:   Dict[str, Dict[str, list]] = {}
last_signal_bar: Dict[str, int]             = {}
MIN_COOLDOWN_BARS = 3


def _norm(c: Any) -> dict:
    if isinstance(c, list):
        return {"time":c[0],"open":float(c[1]),"high":float(c[2]),
                "low":float(c[3]),"close":float(c[4]),"volume":float(c[5])}
    return {k: float(v) if k != "time" else v for k, v in c.items()}


async def load_candles(symbol: str, interval: str,
                       limit: int = 300) -> List[dict]:
    raw = await client.get_klines(symbol, interval, limit)
    return [_norm(c) for c in raw if c]


async def analyze_and_trade(symbol: str) -> None:
    if not RiskManager.is_trading_hour():
        return

    htf_buf   = candle_buffer.get(symbol, {}).get(TF_HTF,   [])
    entry_buf = candle_buffer.get(symbol, {}).get(TF_ENTRY, [])

    if len(htf_buf) < EMA_TREND_HTF + 10 or len(entry_buf) < 50:
        return

    # cooldown: evitar señales consecutivas en la misma vela
    n = len(entry_buf)
    if n - last_signal_bar.get(symbol, 0) < MIN_COOLDOWN_BARS:
        return

    try:
        htf = calc_htf_bias(htf_buf)
        if not htf.confirmed:
            return
        ema = calc_ema10_signal(entry_buf)
        if ema.signal == "NONE":
            return
        bos = calc_bos(entry_buf)
        vol = calc_cvd(entry_buf)
        sig = aggregate_signals(htf, ema, bos, vol, symbol)
    except Exception as e:
        log.error(f"analyze error {symbol}: {e}")
        return

    if sig.direction == "HOLD":
        return

    last_signal_bar[symbol] = n
    tg.signal_detected(symbol, sig)

    balance = await client.get_balance()
    equity  = balance.get("equity", 0)
    if equity <= 0:
        return

    can, reason = await risk.can_trade(symbol, equity)
    if not can:
        log.info(f"{symbol}: BLOQUEADO — {reason}")
        return

    sz = risk.calc_size(equity, sig.entry_price, sig.stop_loss, sig.size_mult)
    if not sz.valid:
        log.warning(f"{symbol}: sizing inválido — {sz.reason}")
        return

    side     = "BUY"  if sig.direction == "LONG"  else "SELL"
    pos_side = "LONG" if sig.direction == "LONG"  else "SHORT"

    log.info(
        f"⚡ EJECUTANDO {symbol} {sig.direction} [{sig.entry_type}] "
        f"qty={sz.qty} SL={sig.stop_loss:.4f} TP={sig.take_profit:.4f}"
    )

    result = await client.place_order(
        symbol, side, pos_side, sz.qty,
        sig.stop_loss, sig.take_profit,
    )

    if result:
        await risk.register_open(
            symbol, sig.direction, sig.entry_price,
            sz.qty, sig.stop_loss, sig.take_profit,
        )
        pmon.track(
            symbol, sig.direction, sig.entry_price,
            sz.qty, sig.stop_loss, sig.take_profit,
        )
        tg.order_placed(symbol, sig, sz.qty, DRY_RUN)


def _make_ws_cb(symbol: str, interval: str) -> Callable:
    async def on_kline(data: dict) -> None:
        kline     = data.get("k", data) if isinstance(data, dict) else {}
        is_closed = kline.get("x", False)
        if not kline or not is_closed:
            return
        candle = {
            "time":   kline.get("t", 0),
            "open":   float(kline.get("o", 0)),
            "high":   float(kline.get("h", 0)),
            "low":    float(kline.get("l", 0)),
            "close":  float(kline.get("c", 0)),
            "volume": float(kline.get("v", 0)),
        }
        buf = candle_buffer.setdefault(symbol, {}).setdefault(interval, [])
        buf.append(candle)
        if len(buf) > CANDLES_REQUIRED + 50:
            buf.pop(0)
        log.debug(
            f"WS [{symbol} {interval}] "
            f"close={candle['close']:.4f} vol={candle['volume']:.2f}"
        )
        if interval == TF_ENTRY:
            await analyze_and_trade(symbol)
    return on_kline


async def polling_loop(symbol: str) -> None:
    """
    Loop de respaldo REST.
    Precarga los buffers y analiza cada POLL_INTERVAL segundos.
    """
    while True:
        try:
            for tf in [TF_HTF, TF_ENTRY]:
                candles = await load_candles(symbol, tf, CANDLES_REQUIRED)
                if candles:
                    candle_buffer.setdefault(symbol, {})[tf] = candles
            await analyze_and_trade(symbol)
        except Exception as e:
            log.error(f"Polling error {symbol}: {e}")
            tg.alert(f"Polling {symbol}: {str(e)[:200]}")
        await asyncio.sleep(POLL_INTERVAL)


async def daily_report_loop() -> None:
    while True:
        now = datetime.now(timezone.utc)
        if now.hour == 23 and 55 <= now.minute <= 57:
            tg.daily_report(risk.get_stats())
            await asyncio.sleep(180)   # evitar doble envío
        await asyncio.sleep(30)


async def initialize() -> None:
    log.info("═" * 60)
    log.info("  CRYPTOBOT v4  │  EMA10×15m×8 × HTF × BOS × CVD")
    log.info(f"  Modo    : {'PAPER TRADING 🧪' if DRY_RUN else 'LIVE TRADING 🔴'}")
    log.info(f"  Símbolos: {', '.join(SYMBOLS)}")
    log.info(f"  Riesgo  : {RISK_PER_TRADE}% │ Leverage: {LEVERAGE}x │ RR: {RISK_REWARD}x")
    log.info(f"  Horario : {TRADE_HOURS_UTC} UTC")
    log.info("═" * 60)

    for sym in SYMBOLS:
        try:
            await client.set_leverage(sym, LEVERAGE)
        except Exception as e:
            log.warning(f"Leverage {sym}: {e}")

    for sym in SYMBOLS:
        for tf in [TF_HTF, TF_ENTRY]:
            candles = await load_candles(sym, tf, CANDLES_REQUIRED)
            candle_buffer.setdefault(sym, {})[tf] = candles
            log.info(f"  Precargado {sym} {tf}: {len(candles)} velas")

    await tg.start()
    tg.bot_started(SYMBOLS, DRY_RUN)


async def _run() -> None:
    await initialize()
    tasks: List[asyncio.Task] = []

    for sym in SYMBOLS:
        for tf in [TF_HTF, TF_ENTRY]:
            client.register_ws_callback(f"{sym}_{tf}", _make_ws_cb(sym, tf))
            tasks.append(asyncio.create_task(
                client.stream_klines(sym, tf), name=f"ws_{sym}_{tf}"))
        tasks.append(asyncio.create_task(
            polling_loop(sym), name=f"poll_{sym}"))

    tasks.append(asyncio.create_task(pmon.run(),          name="monitor"))
    tasks.append(asyncio.create_task(daily_report_loop(), name="daily_report"))

    log.info(f"Bot corriendo — {len(tasks)} tareas activas ✓")
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        await client.close()
        log.info("Bot detenido ✓")


def _on_sigterm(*_: Any) -> None:
    log.info("SIGTERM recibido — cerrando…")
    for t in asyncio.all_tasks():
        t.cancel()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        log.info("Bot detenido (Ctrl+C)")
