"""HTF BIAS — Sesgo direccional en timeframe alto (1h) usando EMA50/200"""
from dataclasses import dataclass
import numpy as np
import config
from bot_logger import get_logger

log = get_logger("HTFBias")

def _ema(values: np.ndarray, period: int) -> np.ndarray:
    k = 2 / (period + 1)
    result = np.empty_like(values)
    result[0] = values[0]
    for i in range(1, len(values)):
        result[i] = values[i] * k + result[i-1] * (1 - k)
    return result

@dataclass
class HTFBias:
    direction: str   # "LONG" | "SHORT" | "NEUTRAL"
    confirmed: bool
    ema50:     float
    ema200:    float
    price:     float
    slope:     float

def calculate_htf_bias(candles: list) -> HTFBias:
    if len(candles) < config.EMA_TREND_HTF + 5:
        return HTFBias("NEUTRAL", False, 0.0, 0.0, 0.0, 0.0)
    closes = np.array([c["close"] for c in candles], dtype=float)
    ema50  = _ema(closes, config.EMA_SLOW_HTF)
    ema200 = _ema(closes, config.EMA_TREND_HTF)
    price  = closes[-1]
    e50    = ema50[-1]
    e200   = ema200[-1]
    slope  = (ema50[-1] - ema50[-6]) / ema50[-6] * 100 if ema50[-6] != 0 else 0.0
    if price > e50 > e200 and slope > 0:
        direction = "LONG"
    elif price < e50 < e200 and slope < 0:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"
    log.debug(f"HTF: {direction} price={price:.4f} EMA50={e50:.4f} EMA200={e200:.4f}")
    return HTFBias(direction, direction != "NEUTRAL", round(e50,6), round(e200,6), round(price,6), round(slope,4))
