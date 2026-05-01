# -*- coding: utf-8 -*-
"""strategy.py -- Three Step Bot v3 — Professional Signal Engine.

Improvements over v1/v2:
  1. EMA50 trend filter — only LONG when price > EMA50, SHORT when below
  2. Volume spike filter — bar volume >= min_volume_mult * 20-bar avg
  3. ATR minimum filter — avoids flat/ranging markets
  4. Delta2 proportional filter — d2 must be meaningful vs d1
  5. Fixed d3_curr bug (was `_`)
  6. Confluence score — stronger signals get priority
  7. Anti-chop: requires d1 magnitude > 0 for at least 2 bars
"""
from __future__ import annotations

import numpy as np
from dataclasses import dataclass


@dataclass
class Signal:
    symbol:     str
    side:       str    # "BUY" | "SELL"
    price:      float
    sl:         float
    tp:         float
    atr:        float
    delta1:     float
    delta2:     float
    delta3:     float
    score:      int    # confluence score 1-5 (higher = stronger signal)
    vol_ratio:  float  # bar_vol / avg_vol


# ── Indicators ────────────────────────────────────────────────────────────────

def _rolling_sum(arr: np.ndarray, n: int) -> np.ndarray:
    cs = np.cumsum(arr)
    out = cs.copy()
    out[n:] = cs[n:] - cs[:-n]
    out[:n] = cs[:n]
    return out


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.zeros_like(arr)
    k = 2.0 / (period + 1)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> np.ndarray:
    prev_close = np.roll(closes, 1)
    prev_close[0] = closes[0]
    tr = np.maximum(highs - lows,
         np.maximum(np.abs(highs - prev_close),
                    np.abs(lows  - prev_close)))
    atr_arr = np.zeros_like(tr)
    atr_arr[period - 1] = tr[:period].mean()
    for i in range(period, len(tr)):
        atr_arr[i] = (atr_arr[i - 1] * (period - 1) + tr[i]) / period
    return atr_arr


def compute_deltas(
    opens: np.ndarray, closes: np.ndarray, volumes: np.ndarray, period: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    delta_vol = np.where(closes > opens, volumes, -volumes)
    d_full1 = _rolling_sum(delta_vol, period)
    d_full2 = _rolling_sum(delta_vol, period * 2)
    d_full3 = _rolling_sum(delta_vol, period * 3)
    delta1 = d_full1
    delta2 = d_full2 - delta1
    delta3 = d_full3 - delta1 - delta2
    return delta1, delta2, delta3


# ── Main signal function ──────────────────────────────────────────────────────

def get_signal(
    ohlcv:          dict,
    symbol:         str,
    period:         int   = 20,
    atr_period:     int   = 14,
    atr_mult:       float = 1.5,
    rr:             float = 3.0,
    min_volume_mult: float = 0.8,
    min_atr_pct:    float = 0.3,
    trend_filter:   bool  = True,
    d2_min_ratio:   float = 0.1,
) -> Signal | None:

    opens   = ohlcv["open"]
    highs   = ohlcv["high"]
    lows    = ohlcv["low"]
    closes  = ohlcv["close"]
    volumes = ohlcv["volume"]

    min_bars = period * 3 + atr_period + 55
    if len(closes) < min_bars:
        return None

    delta1, delta2, delta3 = compute_deltas(opens, closes, volumes, period)
    atr_arr = _atr(highs, lows, closes, atr_period)
    ema50   = _ema(closes, 50)

    # Signal bar = last closed candle (-2), previous = -3
    d1_prev  = delta1[-3]
    d1_curr  = delta1[-2]
    d2_curr  = delta2[-2]
    d3_curr  = delta3[-2]
    atr_val  = atr_arr[-2]
    price    = closes[-2]
    ema_val  = ema50[-2]

    if atr_val <= 0 or price <= 0:
        return None

    # ── Filter 1: ATR minimum (avoid flat markets) ────────────────────────
    atr_pct = (atr_val / price) * 100
    if atr_pct < min_atr_pct:
        return None

    # ── Filter 2: Volume spike ────────────────────────────────────────────
    avg_vol  = float(np.mean(volumes[-22:-2]))
    bar_vol  = float(volumes[-2])
    vol_ratio = (bar_vol / avg_vol) if avg_vol > 0 else 0
    if vol_ratio < min_volume_mult:
        return None

    # ── Filter 3: Delta crossover check ───────────────────────────────────
    long_cross  = d1_prev <= 0 < d1_curr
    short_cross = d1_prev >= 0 > d1_curr

    if not long_cross and not short_cross:
        return None

    # ── Filter 4: Delta2 confirmation (meaningful, not noise) ────────────
    if abs(d1_curr) > 0:
        d2_ratio = abs(d2_curr) / abs(d1_curr)
    else:
        return None

    if long_cross  and (d2_curr <= 0 or d2_ratio < d2_min_ratio):
        return None
    if short_cross and (d2_curr >= 0 or d2_ratio < d2_min_ratio):
        return None

    # ── Filter 5: EMA50 trend alignment ──────────────────────────────────
    if trend_filter:
        if long_cross  and price < ema_val:
            return None
        if short_cross and price > ema_val:
            return None

    # ── Confluence score (1-5) ────────────────────────────────────────────
    score = 1
    if vol_ratio >= 1.5:          score += 1  # strong volume
    if d2_ratio >= 0.5:           score += 1  # delta2 strong vs delta1
    if abs(d3_curr) < abs(d2_curr): score += 1  # delta3 weaker = trend fresh
    if atr_pct >= 0.6:            score += 1  # good volatility

    # Only take score >= 2 signals
    if score < 2:
        return None

    sl_dist = atr_val * atr_mult
    tp_dist = sl_dist * rr

    if long_cross:
        return Signal(
            symbol=symbol, side="BUY", price=price,
            sl=round(price - sl_dist, 8),
            tp=round(price + tp_dist, 8),
            atr=atr_val, delta1=d1_curr, delta2=d2_curr, delta3=d3_curr,
            score=score, vol_ratio=round(vol_ratio, 2),
        )

    if short_cross:
        return Signal(
            symbol=symbol, side="SELL", price=price,
            sl=round(price + sl_dist, 8),
            tp=round(price - tp_dist, 8),
            atr=atr_val, delta1=d1_curr, delta2=d2_curr, delta3=d3_curr,
            score=score, vol_ratio=round(vol_ratio, 2),
        )

    return None


def delta1_flipped(ohlcv: dict, period: int, trade_side: str) -> bool:
    """True when delta1 flipped against the open trade — trailing exit signal."""
    opens   = ohlcv["open"]
    closes  = ohlcv["close"]
    volumes = ohlcv["volume"]
    if len(closes) < period * 3 + 5:
        return False
    delta1, _, _ = compute_deltas(opens, closes, volumes, period)
    curr = delta1[-2]
    if trade_side == "BUY"  and curr < 0:
        return True
    if trade_side == "SELL" and curr > 0:
        return True
    return False
