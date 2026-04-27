"""EMA10 CROSS — Señal de cruce y retest de la EMA10 en 15m"""
from dataclasses import dataclass
import numpy as np
import config
from bot_logger import get_logger

log = get_logger("EMA10")

def _ema(values: np.ndarray, period: int) -> np.ndarray:
    k = 2 / (period + 1)
    result = np.empty_like(values)
    result[0] = values[0]
    for i in range(1, len(values)):
        result[i] = values[i] * k + result[i-1] * (1 - k)
    return result

def _atr(candles: list, period: int = 14) -> float:
    trs = []
    for i in range(1, min(period + 1, len(candles))):
        h, l, cp = candles[-i]["high"], candles[-i]["low"], candles[-i-1]["close"]
        trs.append(max(h - l, abs(h - cp), abs(l - cp)))
    return float(np.mean(trs)) if trs else 0.0

@dataclass
class EMASignal:
    signal:      str   # "LONG_CROSS"|"SHORT_CROSS"|"LONG_RETEST"|"SHORT_RETEST"|"NONE"
    entry_type:  str   # "CROSS"|"RETEST"|"NONE"
    direction:   str   # "LONG"|"SHORT"|"NONE"
    entry_price: float
    ema10:       float
    atr:         float

def calculate_ema10_signal(candles: list) -> EMASignal:
    if len(candles) < 30:
        return EMASignal("NONE", "NONE", "NONE", 0.0, 0.0, 0.0)
    closes = np.array([c["close"] for c in candles], dtype=float)
    ema10  = _ema(closes, config.EMA_FAST)
    atr    = _atr(candles)
    e10    = ema10[-1]
    e10p   = ema10[-2]
    prev   = candles[-2]
    curr   = candles[-1]
    min_body = atr * config.EMA_MULTIPLIER * 0.1

    # CRUCE ALCISTA
    if prev["close"] < e10p and curr["close"] > e10 and abs(curr["close"] - curr["open"]) >= min_body:
        log.debug(f"LONG CROSS @ {curr['close']:.4f}")
        return EMASignal("LONG_CROSS", "CROSS", "LONG", curr["close"], e10, atr)
    # CRUCE BAJISTA
    if prev["close"] > e10p and curr["close"] < e10 and abs(curr["close"] - curr["open"]) >= min_body:
        log.debug(f"SHORT CROSS @ {curr['close']:.4f}")
        return EMASignal("SHORT_CROSS", "CROSS", "SHORT", curr["close"], e10, atr)
    # RETEST ALCISTA
    if curr["low"] <= e10 * 1.001 and curr["close"] > e10 and curr["close"] > curr["open"]:
        last3 = [candles[-i]["close"] for i in range(3, 6)]
        if all(c > ema10[-i-1] for i, c in enumerate(last3)):
            log.debug(f"LONG RETEST @ {curr['close']:.4f}")
            return EMASignal("LONG_RETEST", "RETEST", "LONG", curr["close"], e10, atr)
    # RETEST BAJISTA
    if curr["high"] >= e10 * 0.999 and curr["close"] < e10 and curr["close"] < curr["open"]:
        last3 = [candles[-i]["close"] for i in range(3, 6)]
        if all(c < ema10[-i-1] for i, c in enumerate(last3)):
            log.debug(f"SHORT RETEST @ {curr['close']:.4f}")
            return EMASignal("SHORT_RETEST", "RETEST", "SHORT", curr["close"], e10, atr)

    return EMASignal("NONE", "NONE", "NONE", closes[-1], e10, atr)
