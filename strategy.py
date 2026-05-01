"""
THREE STEP FUTURE-TREND BOT — strategy.py
==========================================
Implementa "Three Step" basado en Volume Delta acumulado.
Inspirado en el indicador BigBeluga de TradingView.

ESTRATEGIA EXPLICADA:
──────────────────────
El "Three Step" analiza 3 ventanas temporales de volumen delta
(volumen comprador - vendedor) para confirmar momentum:

  Step 1 (corto):  últimas N/5 velas   → impulso inmediato
  Step 2 (medio):  últimas N/2 velas   → confirmación
  Step 3 (largo):  últimas N   velas   → tendencia de fondo

SEÑAL LONG  → los 3 deltas son positivos Y precio > EMA
SEÑAL SHORT → los 3 deltas son negativos Y precio < EMA

Exit logic:
  • SL: precio ± ATR × atr_mult (automático en exchange)
  • TP1: precio ± ATR × rr × rr_atr (parcial 50%)
  • TP2: precio ± ATR × tp2_mult   (resto)
  • Breakeven: mover SL a entry cuando gana 50% del camino a TP1
  • Trail: seguir con ATR si el precio sigue a favor
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class Signal:
    symbol:  str
    side:    str         # BUY | SELL
    price:   float
    sl:      float
    tp:      float       # TP1
    tp2:     float       # TP2 (trailing objetivo)
    atr:     float
    delta1:  float       # Step 1 delta
    delta2:  float       # Step 2 delta
    delta3:  float       # Step 3 delta
    ema:     float
    vol_ratio: float     # volumen relativo (spike detector)


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    k   = 2.0 / (period + 1)
    out = np.empty_like(arr)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out


def _atr(highs, lows, closes, period: int = 14) -> float:
    if len(closes) < period + 2:
        return float(closes[-1]) * 0.01
    n   = min(period * 2, len(closes) - 1)
    h   = highs[-(n + 1):]
    l   = lows[-(n + 1):]
    c   = closes[-(n + 1):]
    trs = []
    for i in range(1, len(c)):
        hh, ll, pc = float(h[i]), float(l[i]), float(c[i - 1])
        trs.append(max(hh - ll, abs(hh - pc), abs(ll - pc)))
    # Wilder smoothing
    atr_v = float(np.mean(trs[:period]))
    for tr in trs[period:]:
        atr_v = (atr_v * (period - 1) + tr) / period
    return atr_v


def _volume_delta(opens, closes, volumes) -> np.ndarray:
    """
    Aproximación del Volume Delta por vela:
    Si la vela es alcista (close > open): todo el volumen es comprador.
    Si es bajista: todo es vendedor.
    Versión mejorada: proporcional al rango de la vela.
    """
    body = closes - opens
    total_range = np.abs(closes - opens) + 1e-10
    # Fracción compradora (0 a 1)
    bull_frac = np.clip((closes - np.minimum(opens, closes)) / total_range, 0, 1)
    buy_vol   = volumes * bull_frac
    sell_vol  = volumes * (1 - bull_frac)
    return buy_vol - sell_vol


def get_signal(
    ohlcv: dict,
    symbol: str,
    period: int      = 25,
    atr_period: int  = 14,
    atr_mult: float  = 2.0,
    rr: float        = 2.0,
) -> Optional[Signal]:
    """
    Evalúa la estrategia Three Step en las velas dadas.
    Retorna Signal si hay entrada válida, None si no.
    """
    candles = ohlcv.get("candles", [])
    if len(candles) < period + atr_period + 5:
        return None

    opens   = np.array([c["open"]   for c in candles], dtype=float)
    highs   = np.array([c["high"]   for c in candles], dtype=float)
    lows    = np.array([c["low"]    for c in candles], dtype=float)
    closes  = np.array([c["close"]  for c in candles], dtype=float)
    volumes = np.array([c["volume"] for c in candles], dtype=float)

    # Usar solo las últimas velas necesarias
    n = period + atr_period + 10
    opens   = opens[-n:]
    highs   = highs[-n:]
    lows    = lows[-n:]
    closes  = closes[-n:]
    volumes = volumes[-n:]

    # ── Volume Delta ──────────────────────────────────────────────────────────
    delta = _volume_delta(opens, closes, volumes)

    # Ventanas de los 3 steps
    w1 = max(1, period // 5)
    w2 = max(1, period // 2)
    w3 = period

    d1 = float(np.sum(delta[-w1:]))   # Step 1: momentum inmediato
    d2 = float(np.sum(delta[-w2:]))   # Step 2: confirmación
    d3 = float(np.sum(delta[-w3:]))   # Step 3: tendencia fondo

    # ── EMA de tendencia ──────────────────────────────────────────────────────
    ema_val = float(_ema(closes, period)[-1])

    # ── ATR para SL/TP ────────────────────────────────────────────────────────
    atr = _atr(highs, lows, closes, atr_period)

    # ── Volumen relativo ──────────────────────────────────────────────────────
    avg_vol   = float(np.mean(volumes[-20:-1])) + 1e-10
    vol_ratio = float(volumes[-1]) / avg_vol

    close = float(closes[-1])

    # ── SEÑAL LONG: los 3 deltas positivos + precio sobre EMA ────────────────
    all_bull = d1 > 0 and d2 > 0 and d3 > 0
    all_bear = d1 < 0 and d2 < 0 and d3 < 0

    if all_bull and close > ema_val:
        sl  = close - atr_mult * atr
        tp1 = close + rr * atr
        tp2 = close + 3.5 * atr
        return Signal(
            symbol=symbol, side="BUY",
            price=close, sl=sl, tp=tp1, tp2=tp2,
            atr=atr, delta1=d1, delta2=d2, delta3=d3,
            ema=ema_val, vol_ratio=vol_ratio,
        )

    # ── SEÑAL SHORT: los 3 deltas negativos + precio bajo EMA ────────────────
    if all_bear and close < ema_val:
        sl  = close + atr_mult * atr
        tp1 = close - rr * atr
        tp2 = close - 3.5 * atr
        return Signal(
            symbol=symbol, side="SELL",
            price=close, sl=sl, tp=tp1, tp2=tp2,
            atr=atr, delta1=d1, delta2=d2, delta3=d3,
            ema=ema_val, vol_ratio=vol_ratio,
        )

    return None
