"""
Estrategia Maki adaptada a crypto:
- Detecta picos/valles con pivotes (ZigZag)
- Filtra con 20MA en 4H (Ley de Granville, pendiente sobre 3 periodos)
- TP: +0.45% | SL: -0.30%
"""
from typing import Optional
import logging

logger = logging.getLogger("strategy")

TP_PCT = 0.0045
SL_PCT = 0.0030
PIVOT_BARS = 3  # reducido de 5 a 3: menos velas necesarias para confirmar pivote


def _sma(values: list[float], period: int) -> list[Optional[float]]:
    result = []
    for i in range(len(values)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(sum(values[i - period + 1: i + 1]) / period)
    return result


def _last_pivot_high(highs: list[float]) -> Optional[float]:
    n = len(highs)
    for i in range(n - PIVOT_BARS - 1, PIVOT_BARS - 1, -1):
        h = highs[i]
        if all(h > highs[i - j] for j in range(1, PIVOT_BARS + 1)) and \
           all(h > highs[i + j] for j in range(1, PIVOT_BARS + 1)):
            return h
    return None


def _last_pivot_low(lows: list[float]) -> Optional[float]:
    n = len(lows)
    for i in range(n - PIVOT_BARS - 1, PIVOT_BARS - 1, -1):
        l = lows[i]
        if all(l < lows[i - j] for j in range(1, PIVOT_BARS + 1)) and \
           all(l < lows[i + j] for j in range(1, PIVOT_BARS + 1)):
            return l
    return None


def signal(candles_15m: list[dict], candles_4h: list[dict], symbol: str = "") -> Optional[str]:
    """Retorna 'LONG', 'SHORT' o None."""
    if len(candles_15m) < PIVOT_BARS * 2 + 5 or len(candles_4h) < 22:
        logger.debug(f"{symbol} skip: velas insuficientes")
        return None

    # Filtro 4H: pendiente de la 20MA sobre 3 periodos
    closes_4h = [c["c"] for c in candles_4h]
    ma20 = _sma(closes_4h, 20)
    valid_ma = [v for v in ma20 if v is not None]
    if len(valid_ma) < 4:
        logger.debug(f"{symbol} skip: MA insuficiente")
        return None

    ma_up   = valid_ma[-1] > valid_ma[-4]
    ma_down = valid_ma[-1] < valid_ma[-4]

    if not ma_up and not ma_down:
        logger.debug(f"{symbol} skip: MA plana")
        return None

    # Pivotes en 15m
    highs = [c["h"] for c in candles_15m]
    lows  = [c["l"] for c in candles_15m]
    peak   = _last_pivot_high(highs)
    valley = _last_pivot_low(lows)

    if peak is None or valley is None:
        logger.debug(f"{symbol} skip: sin pivotes (peak={peak}, valley={valley})")
        return None

    prev_close = candles_15m[-2]["c"]
    last_close = candles_15m[-1]["c"]

    ma_dir = "UP" if ma_up else "DOWN"
    logger.debug(f"{symbol} MA={ma_dir} peak={peak:.4f} valley={valley:.4f} prev={prev_close:.4f} last={last_close:.4f}")

    if prev_close <= peak < last_close and ma_up:
        return "LONG"

    if prev_close >= valley > last_close and ma_down:
        return "SHORT"

    return None


def tp_sl(entry: float, side: str) -> tuple[float, float]:
    if side == "LONG":
        return entry * (1 + TP_PCT), entry * (1 - SL_PCT)
    else:
        return entry * (1 - TP_PCT), entry * (1 + SL_PCT)
