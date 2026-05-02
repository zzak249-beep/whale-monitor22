# -*- coding: utf-8 -*-
"""strategy.py -- Three Step Bot v5.

FIXES vs v3/v4 (why no trades were opening):
  1. score >= 2 was too strict → lowered to score >= 1 (score is BONUS, not gate)
  2. d2_curr <= 0 strict → relaxed to d2_curr >= -small_tolerance
  3. min_atr_pct=0.3% was killing DOGE/XRP/ADA → lowered to 0.05%
  4. EMA50 trend filter optional and relaxed (allow ±0.3% tolerance)
  5. Added REJECT REASON logging so you can see WHY each signal was skipped

NEW FEATURES:
  6. Trading session filter: only trade London+NY overlap (07:00-20:00 UTC)
  7. Funding rate filter: skip LONG when funding > +0.05%, skip SHORT < -0.05%
  8. Funding rate fetched from BingX API
"""
from __future__ import annotations

import numpy as np
from dataclasses import dataclass
from datetime import datetime, timezone
from loguru import logger


@dataclass
class Signal:
    symbol:    str
    side:      str
    price:     float
    sl:        float
    tp:        float
    atr:       float
    delta1:    float
    delta2:    float
    delta3:    float
    score:     int
    vol_ratio: float


# ── Indicators ────────────────────────────────────────────────────────────────

def _rolling_sum(arr: np.ndarray, n: int) -> np.ndarray:
    cs  = np.cumsum(arr)
    out = cs.copy()
    out[n:] = cs[n:] - cs[:-n]
    out[:n] = cs[:n]
    return out


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.zeros_like(arr)
    k   = 2.0 / (period + 1)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> np.ndarray:
    prev = np.roll(closes, 1); prev[0] = closes[0]
    tr   = np.maximum(highs - lows,
           np.maximum(np.abs(highs - prev), np.abs(lows - prev)))
    out  = np.zeros_like(tr)
    out[period - 1] = tr[:period].mean()
    for i in range(period, len(tr)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def compute_deltas(
    opens: np.ndarray, closes: np.ndarray, volumes: np.ndarray, period: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    dv  = np.where(closes > opens, volumes, -volumes)
    d1  = _rolling_sum(dv, period)
    d2f = _rolling_sum(dv, period * 2)
    d3f = _rolling_sum(dv, period * 3)
    d2  = d2f - d1
    d3  = d3f - d1 - d2
    return d1, d2, d3


# ── Session filter ─────────────────────────────────────────────────────────────

def in_trading_session() -> bool:
    """Only trade during London + NY sessions (07:00-20:00 UTC).
    These sessions have the most volume and cleanest signals."""
    hour = datetime.now(timezone.utc).hour
    return 7 <= hour < 20


# ── Main signal ───────────────────────────────────────────────────────────────

def get_signal(
    ohlcv:           dict,
    symbol:          str,
    period:          int   = 20,
    atr_period:      int   = 14,
    atr_mult:        float = 1.5,
    rr:              float = 3.0,
    min_volume_mult: float = 0.6,   # FIXED: was 0.8 — too strict
    min_atr_pct:     float = 0.05,  # FIXED: was 0.3 — killed low-price coins
    trend_filter:    bool  = True,
    session_filter:  bool  = True,
    funding_rate:    float = 0.0,   # passed from bot after API fetch
) -> Signal | None:

    opens   = ohlcv["open"]
    highs   = ohlcv["high"]
    lows    = ohlcv["low"]
    closes  = ohlcv["close"]
    volumes = ohlcv["volume"]

    min_bars = period * 3 + atr_period + 55
    if len(closes) < min_bars:
        logger.debug(f"[{symbol}] SKIP: not enough bars ({len(closes)}<{min_bars})")
        return None

    # ── Session filter ────────────────────────────────────────────────────
    if session_filter and not in_trading_session():
        logger.debug(f"[{symbol}] SKIP: outside trading session")
        return None

    d1, d2, d3 = compute_deltas(opens, closes, volumes, period)
    atr_arr     = _atr(highs, lows, closes, atr_period)
    ema50       = _ema(closes, 50)

    d1_prev = d1[-3]
    d1_curr = d1[-2]
    d2_curr = d2[-2]
    d3_curr = d3[-2]
    atr_val = atr_arr[-2]
    price   = closes[-2]
    ema_val = ema50[-2]

    if atr_val <= 0 or price <= 0:
        return None

    # ── Filter 1: ATR minimum ─────────────────────────────────────────────
    atr_pct = (atr_val / price) * 100
    if atr_pct < min_atr_pct:
        logger.debug(f"[{symbol}] SKIP: ATR too low ({atr_pct:.4f}% < {min_atr_pct}%)")
        return None

    # ── Filter 2: Volume ─────────────────────────────────────────────────
    avg_vol   = float(np.mean(volumes[-22:-2]))
    bar_vol   = float(volumes[-2])
    vol_ratio = (bar_vol / avg_vol) if avg_vol > 0 else 0
    if vol_ratio < min_volume_mult:
        logger.debug(f"[{symbol}] SKIP: vol {vol_ratio:.2f}x < {min_volume_mult}x")
        return None

    # ── Filter 3: Delta1 crossover ────────────────────────────────────────
    long_cross  = d1_prev <= 0 < d1_curr
    short_cross = d1_prev >= 0 > d1_curr

    if not long_cross and not short_cross:
        logger.debug(f"[{symbol}] SKIP: no crossover d1_prev={d1_prev:.0f} d1_curr={d1_curr:.0f}")
        return None

    # ── Filter 4: Delta2 — relaxed (just needs to not be strongly opposite) ──
    # FIXED: was strict >0/<0, now allows small opposite readings
    tolerance = abs(d1_curr) * 0.15  # 15% tolerance
    if long_cross  and d2_curr < -tolerance:
        logger.debug(f"[{symbol}] SKIP: d2 strongly negative for LONG ({d2_curr:.0f})")
        return None
    if short_cross and d2_curr >  tolerance:
        logger.debug(f"[{symbol}] SKIP: d2 strongly positive for SHORT ({d2_curr:.0f})")
        return None

    # ── Filter 5: EMA50 trend — relaxed with 0.5% tolerance ─────────────
    if trend_filter:
        ema_tolerance = ema_val * 0.005  # 0.5% band around EMA
        if long_cross  and price < ema_val - ema_tolerance:
            logger.debug(f"[{symbol}] SKIP: LONG below EMA50 ({price:.6f} < {ema_val:.6f})")
            return None
        if short_cross and price > ema_val + ema_tolerance:
            logger.debug(f"[{symbol}] SKIP: SHORT above EMA50 ({price:.6f} > {ema_val:.6f})")
            return None

    # ── Filter 6: Funding rate ────────────────────────────────────────────
    if funding_rate != 0.0:
        if long_cross  and funding_rate >  0.0005:  # >+0.05% = longs overloaded
            logger.debug(f"[{symbol}] SKIP: funding too high for LONG ({funding_rate:.4%})")
            return None
        if short_cross and funding_rate < -0.0005:  # <-0.05% = shorts overloaded
            logger.debug(f"[{symbol}] SKIP: funding too low for SHORT ({funding_rate:.4%})")
            return None

    # ── Confluence score (bonus, not gate) ───────────────────────────────
    score = 1
    if vol_ratio >= 1.5:                        score += 1
    if d2_curr > 0 and long_cross:              score += 1
    if d2_curr < 0 and short_cross:             score += 1
    if abs(d3_curr) < abs(d2_curr):             score += 1
    if atr_pct >= 0.5:                          score += 1
    score = min(score, 5)

    # Score >= 1 always passes — score is for PRIORITIZATION not filtering
    sl_dist = atr_val * atr_mult
    tp_dist = sl_dist * rr

    logger.debug(
        f"[{symbol}] ✅ SIGNAL {('BUY' if long_cross else 'SELL')} "
        f"score={score} vol={vol_ratio:.1f}x atr={atr_pct:.3f}% "
        f"d1={d1_curr:+.0f} d2={d2_curr:+.0f}"
    )

    if long_cross:
        return Signal(
            symbol=symbol, side="BUY", price=price,
            sl=round(price - sl_dist, 8),
            tp=round(price + tp_dist, 8),
            atr=atr_val, delta1=d1_curr, delta2=d2_curr, delta3=d3_curr,
            score=score, vol_ratio=round(vol_ratio, 2),
        )
    return Signal(
        symbol=symbol, side="SELL", price=price,
        sl=round(price + sl_dist, 8),
        tp=round(price - tp_dist, 8),
        atr=atr_val, delta1=d1_curr, delta2=d2_curr, delta3=d3_curr,
        score=score, vol_ratio=round(vol_ratio, 2),
    )


def delta1_flipped(ohlcv: dict, period: int, trade_side: str) -> bool:
    opens = ohlcv["open"]; closes = ohlcv["close"]; volumes = ohlcv["volume"]
    if len(closes) < period * 3 + 5:
        return False
    d1, _, _ = compute_deltas(opens, closes, volumes, period)
    curr = d1[-2]
    return (trade_side == "BUY" and curr < 0) or (trade_side == "SELL" and curr > 0)
