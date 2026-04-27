"""
EMA10 CROSS — Señal de cruce y retest de la EMA10 en 15m
══════════════════════════════════════════════════════════
Método EMA10 × 15m × 8:
  • CROSS  : precio cruza la EMA10 con vela de cuerpo >= EMA10 × multiplier × ATR
  • RETEST : precio vuelve a tocar la EMA10 tras un cruce previo y rebota
  • Confirma con cierre de vela (sin lookahead)
"""
from dataclasses import dataclass

import numpy as np

import config
from utils.logger import get_logger

log = get_logger("EMA10")


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    k = 2 / (period + 1)
    result = np.empty_like(values)
    result[0] = values[0]
    for i in range(1, len(values)):
        result[i] = values[i] * k + result[i - 1] * (1 - k)
    return result


def _atr(candles: list[dict], period: int = 14) -> float:
    trs = []
    for i in range(1, min(period + 1, len(candles))):
        h = candles[-i]["high"]
        l = candles[-i]["low"]
        c_prev = candles[-i - 1]["close"]
        trs.append(max(h - l, abs(h - c_prev), abs(l - c_prev)))
    return float(np.mean(trs)) if trs else 0.0


@dataclass
class EMASignal:
    signal:      str    # "LONG_CROSS" | "SHORT_CROSS" | "LONG_RETEST" | "SHORT_RETEST" | "NONE"
    entry_type:  str    # "CROSS" | "RETEST" | "NONE"
    direction:   str    # "LONG" | "SHORT" | "NONE"
    entry_price: float
    ema10:       float
    atr:         float


def calculate_ema10_signal(candles: list[dict]) -> EMASignal:
    """
    Evalúa la última vela cerrada contra la EMA10.
    Necesita mínimo 30 velas.
    """
    if len(candles) < 30:
        return EMASignal("NONE", "NONE", "NONE", 0.0, 0.0, 0.0)

    closes = np.array([c["close"] for c in candles], dtype=float)
    ema10  = _ema(closes, config.EMA_FAST)
    atr    = _atr(candles)

    e10_now  = ema10[-1]
    e10_prev = ema10[-2]
    prev     = candles[-2]
    curr     = candles[-1]

    prev_close = prev["close"]
    prev_open  = prev["open"]
    curr_close = curr["close"]
    curr_open  = curr["open"]
    curr_high  = curr["high"]
    curr_low   = curr["low"]

    min_body = atr * config.EMA_MULTIPLIER * 0.1  # threshold mínimo de cuerpo

    # ── CRUCE ALCISTA: cierre anterior < EMA10, cierre actual > EMA10
    if prev_close < e10_prev and curr_close > e10_now:
        body = abs(curr_close - curr_open)
        if body >= min_body:
            entry = curr_close
            log.debug(f"LONG CROSS @ {entry:.4f} EMA10={e10_now:.4f}")
            return EMASignal("LONG_CROSS", "CROSS", "LONG", entry, e10_now, atr)

    # ── CRUCE BAJISTA: cierre anterior > EMA10, cierre actual < EMA10
    if prev_close > e10_prev and curr_close < e10_now:
        body = abs(curr_close - curr_open)
        if body >= min_body:
            entry = curr_close
            log.debug(f"SHORT CROSS @ {entry:.4f} EMA10={e10_now:.4f}")
            return EMASignal("SHORT_CROSS", "CROSS", "SHORT", entry, e10_now, atr)

    # ── RETEST ALCISTA: precio toca EMA10 por abajo y rebota (low ≤ EMA10, cierre > EMA10)
    if curr_low <= e10_now * 1.001 and curr_close > e10_now and curr_close > curr_open:
        # Verificar que las últimas 3 velas estaban por encima (tendencia alcista)
        last3 = [candles[-i]["close"] for i in range(3, 6)]
        if all(c > ema10[-i-1] for i, c in enumerate(last3)):
            entry = curr_close
            log.debug(f"LONG RETEST @ {entry:.4f} EMA10={e10_now:.4f}")
            return EMASignal("LONG_RETEST", "RETEST", "LONG", entry, e10_now, atr)

    # ── RETEST BAJISTA: precio toca EMA10 por arriba y cae (high ≥ EMA10, cierre < EMA10)
    if curr_high >= e10_now * 0.999 and curr_close < e10_now and curr_close < curr_open:
        last3 = [candles[-i]["close"] for i in range(3, 6)]
        if all(c < ema10[-i-1] for i, c in enumerate(last3)):
            entry = curr_close
            log.debug(f"SHORT RETEST @ {entry:.4f} EMA10={e10_now:.4f}")
            return EMASignal("SHORT_RETEST", "RETEST", "SHORT", entry, e10_now, atr)

    return EMASignal("NONE", "NONE", "NONE", closes[-1], e10_now, atr)
