"""
Estrategia Maki — ZigZag + SMA-20 (4H) + RSI-14 (15m)

BUGS CORREGIDOS vs versión anterior:
  1. PIVOT_BARS era 5 → ahora 3: con 5 barras a cada lado casi nunca
     se confirma un pivote en las últimas velas disponibles.
  2. Solo pedíamos 60 velas 15m → ahora 100: más historia = más pivotes.
  3. Condición de cruce era prev_c <= peak < last_c (ventana de 1 vela,
     casi imposible). Ahora: prev_c < peak <= last_c, que captura el
     momento exacto en que el cierre supera el nivel por primera vez.
  4. RSI_OB=65 / RSI_OS=35 eran demasiado estrictos → ahora 70/30.
"""
import logging
from typing import Optional

logger = logging.getLogger("strategy")

# ── parámetros ────────────────────────────────────────────────────── #
TP_PCT     = 0.0045   # Take Profit +0.45%
SL_PCT     = 0.0030   # Stop Loss   -0.30%
PIVOT_BARS = 3        # FIX: era 5, demasiado restrictivo
RSI_PERIOD = 14
RSI_OB     = 70       # FIX: era 65, filtraba demasiado
RSI_OS     = 30       # FIX: era 35, filtraba demasiado
MIN_15M    = 100      # FIX: era 60, necesitamos más historia para pivotes
MIN_4H     = 25


# ── indicadores ───────────────────────────────────────────────────── #

def _sma(values: list, period: int) -> list:
    out = []
    for i in range(len(values)):
        if i < period - 1:
            out.append(None)
        else:
            out.append(sum(values[i - period + 1: i + 1]) / period)
    return out


def _rsi(closes: list, period: int = RSI_PERIOD) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + ag / al))


# ── detección de pivotes ──────────────────────────────────────────── #

def _pivot_high(highs: list) -> Optional[float]:
    """Último máximo local confirmado por PIVOT_BARS velas a cada lado."""
    n = len(highs)
    for i in range(n - PIVOT_BARS - 1, PIVOT_BARS - 1, -1):
        h = highs[i]
        if (all(h > highs[i - j] for j in range(1, PIVOT_BARS + 1)) and
                all(h > highs[i + j] for j in range(1, PIVOT_BARS + 1))):
            return h
    return None


def _pivot_low(lows: list) -> Optional[float]:
    """Último mínimo local confirmado por PIVOT_BARS velas a cada lado."""
    n = len(lows)
    for i in range(n - PIVOT_BARS - 1, PIVOT_BARS - 1, -1):
        lo = lows[i]
        if (all(lo < lows[i - j] for j in range(1, PIVOT_BARS + 1)) and
                all(lo < lows[i + j] for j in range(1, PIVOT_BARS + 1))):
            return lo
    return None


# ── señal principal ───────────────────────────────────────────────── #

def signal(candles_15m: list, candles_4h: list) -> Optional[str]:
    """
    Retorna 'LONG', 'SHORT' o None.
    Velas en orden CRONOLÓGICO (más antigua → más reciente).
    Cada vela: dict {o, h, l, c, v}.
    """
    if len(candles_15m) < MIN_15M or len(candles_4h) < MIN_4H:
        return None

    # ── 1. Tendencia 4H: dirección de SMA-20 ──────────────────────── #
    closes_4h = [c["c"] for c in candles_4h]
    ma20      = [v for v in _sma(closes_4h, 20) if v is not None]
    if len(ma20) < 2:
        return None
    ma_up   = ma20[-1] > ma20[-2]
    ma_down = ma20[-1] < ma20[-2]
    if not ma_up and not ma_down:
        return None

    # ── 2. RSI 15m: filtro de sobreextensión ──────────────────────── #
    closes_15m = [c["c"] for c in candles_15m]
    rsi = _rsi(closes_15m)
    if rsi is None:
        return None

    # ── 3. Pivotes 15m ────────────────────────────────────────────── #
    highs  = [c["h"] for c in candles_15m]
    lows   = [c["l"] for c in candles_15m]
    peak   = _pivot_high(highs)
    valley = _pivot_low(lows)
    if peak is None or valley is None:
        return None

    prev_c = candles_15m[-2]["c"]
    last_c = candles_15m[-1]["c"]

    # ── 4. Condición de entrada ────────────────────────────────────── #
    # FIX: era prev_c <= peak < last_c (solo dispara 1 vela)
    # AHORA: prev_c < peak <= last_c  (captura el cruce alcista)
    long_cross  = prev_c < peak  <= last_c
    short_cross = prev_c > valley >= last_c

    if long_cross and ma_up and rsi < RSI_OB:
        logger.info(f"LONG  | peak={peak:.5f} | MA↑ | RSI={rsi:.1f}")
        return "LONG"

    if short_cross and ma_down and rsi > RSI_OS:
        logger.info(f"SHORT | valley={valley:.5f} | MA↓ | RSI={rsi:.1f}")
        return "SHORT"

    return None


# ── TP / SL ───────────────────────────────────────────────────────── #

def tp_sl(entry: float, side: str) -> tuple:
    """Retorna (take_profit, stop_loss)."""
    if side == "LONG":
        return entry * (1 + TP_PCT), entry * (1 - SL_PCT)
    return entry * (1 - TP_PCT), entry * (1 + SL_PCT)
