"""
HTF BIAS — Sesgo direccional en timeframe alto (1h)
════════════════════════════════════════════════════
Lógica:
  • EMA50 y EMA200 sobre velas 1h
  • LONG  si precio > EMA50 > EMA200  (uptrend)
  • SHORT si precio < EMA50 < EMA200  (downtrend)
  • NEUTRAL en cualquier otro caso
  • confirmed = True sólo en LONG o SHORT
"""
from dataclasses import dataclass

import numpy as np

import config
from utils.logger import get_logger

log = get_logger("HTFBias")


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    k = 2 / (period + 1)
    ema = np.empty_like(values)
    ema[0] = values[0]
    for i in range(1, len(values)):
        ema[i] = values[i] * k + ema[i - 1] * (1 - k)
    return ema


@dataclass
class HTFBias:
    direction: str    # "LONG" | "SHORT" | "NEUTRAL"
    confirmed: bool
    ema50:     float
    ema200:    float
    price:     float
    slope:     float  # pendiente EMA50 (positiva=subiendo)


def calculate_htf_bias(candles: list[dict]) -> HTFBias:
    """
    Recibe lista de velas HTF (1h) y devuelve el sesgo.
    Necesita al menos 205 velas para EMA200 estable.
    """
    if len(candles) < config.EMA_TREND_HTF + 5:
        return HTFBias("NEUTRAL", False, 0.0, 0.0, 0.0, 0.0)

    closes = np.array([c["close"] for c in candles], dtype=float)
    ema50  = _ema(closes, config.EMA_SLOW_HTF)
    ema200 = _ema(closes, config.EMA_TREND_HTF)

    last_price = closes[-1]
    e50        = ema50[-1]
    e200       = ema200[-1]

    # Pendiente de la EMA50 (últimas 5 velas)
    slope = (ema50[-1] - ema50[-6]) / ema50[-6] * 100 if ema50[-6] != 0 else 0.0

    if last_price > e50 > e200 and slope > 0:
        direction = "LONG"
    elif last_price < e50 < e200 and slope < 0:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"

    confirmed = direction != "NEUTRAL"

    log.debug(
        f"HTFBias: {direction} | price={last_price:.4f} "
        f"EMA50={e50:.4f} EMA200={e200:.4f} slope={slope:.4f}%"
    )
    return HTFBias(direction, confirmed, round(e50, 6), round(e200, 6),
                   round(last_price, 6), round(slope, 4))
