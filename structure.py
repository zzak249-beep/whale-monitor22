"""
BREAK OF STRUCTURE (BOS) — Ruptura de estructura de mercado
─────────────────────────────────────────────────────────────
Lógica:
  1. Detectar swing highs/lows en los últimos N periodos
  2. BOS alcista: el precio cierra sobre el último swing high relevante
  3. BOS bajista: el precio cierra bajo el último swing low relevante
  4. Calcular zona de entrada óptima (POI — Point of Interest)
"""
import numpy as np
from dataclasses import dataclass, field
from typing import Literal, Optional

import config
from utils.logger import get_logger

log = get_logger("BOS")

BosType = Literal["BULLISH", "BEARISH", "NONE"]


@dataclass
class SwingPoint:
    index:  int
    price:  float
    kind:   Literal["HIGH", "LOW"]


@dataclass
class BOSState:
    bos_type:       BosType
    broken_level:   float   # Nivel de estructura roto
    last_swing_high: float
    last_swing_low:  float
    poi_zone_top:   float   # Zona de retorno óptimo (para entrada)
    poi_zone_bot:   float
    valid:          bool


def _find_swings(highs: np.ndarray, lows: np.ndarray, lookback: int) -> list[SwingPoint]:
    """Swing pivot: punto más alto/bajo vs sus N vecinos a cada lado."""
    swings = []
    n = 3  # Velas a cada lado para confirmar el swing

    for i in range(n, len(highs) - n):
        # Swing High
        if all(highs[i] > highs[i-j] for j in range(1, n+1)) and \
           all(highs[i] > highs[i+j] for j in range(1, n+1)):
            swings.append(SwingPoint(i, highs[i], "HIGH"))
        # Swing Low
        if all(lows[i] < lows[i-j] for j in range(1, n+1)) and \
           all(lows[i] < lows[i+j] for j in range(1, n+1)):
            swings.append(SwingPoint(i, lows[i], "LOW"))

    # Solo últimos lookback relevantes
    return swings[-lookback:] if len(swings) > lookback else swings


def detect_bos(candles: list[dict]) -> BOSState:
    """
    Detecta ruptura de estructura en las velas de 15m.
    """
    _none = BOSState("NONE", 0, 0, 0, 0, 0, False)

    if len(candles) < config.BOS_LOOKBACK + 10:
        return _none

    highs  = np.array([float(c["high"])  for c in candles], dtype=np.float64)
    lows   = np.array([float(c["low"])   for c in candles], dtype=np.float64)
    closes = np.array([float(c["close"]) for c in candles], dtype=np.float64)

    swings = _find_swings(highs, lows, config.BOS_LOOKBACK)

    if not swings:
        return _none

    swing_highs = [s for s in swings if s.kind == "HIGH"]
    swing_lows  = [s for s in swings if s.kind == "LOW"]

    if not swing_highs or not swing_lows:
        return _none

    last_sh = max(swing_highs, key=lambda s: s.index)
    last_sl = min(swing_lows,  key=lambda s: s.index)

    price_now = closes[-1]

    # ── Ruptura alcista: precio cierra sobre último swing high
    if price_now > last_sh.price:
        # POI: zona entre el 50% y 100% del swing roto (para entry en retroceso)
        poi_top = last_sh.price
        poi_bot = last_sh.price - (last_sh.price - last_sl.price) * 0.382  # Retroceso 38.2%

        state = BOSState(
            bos_type        = "BULLISH",
            broken_level    = last_sh.price,
            last_swing_high = last_sh.price,
            last_swing_low  = last_sl.price,
            poi_zone_top    = poi_top,
            poi_zone_bot    = poi_bot,
            valid           = True,
        )
        log.info(f"BOS BULLISH | roto={last_sh.price:.4f} | POI [{poi_bot:.4f}–{poi_top:.4f}]")
        return state

    # ── Ruptura bajista: precio cierra bajo último swing low
    if price_now < last_sl.price:
        poi_bot = last_sl.price
        poi_top = last_sl.price + (last_sh.price - last_sl.price) * 0.382

        state = BOSState(
            bos_type        = "BEARISH",
            broken_level    = last_sl.price,
            last_swing_high = last_sh.price,
            last_swing_low  = last_sl.price,
            poi_zone_top    = poi_top,
            poi_zone_bot    = poi_bot,
            valid           = True,
        )
        log.info(f"BOS BEARISH | roto={last_sl.price:.4f} | POI [{poi_bot:.4f}–{poi_top:.4f}]")
        return state

    log.debug(f"BOS: sin ruptura | precio={price_now:.4f} SH={last_sh.price:.4f} SL={last_sl.price:.4f}")
    return _none
