"""
Estrategia Maki adaptada a crypto:
- Detecta picos/valles con pivotes (ZigZag simplificado)
- Filtra con 20MA en 4H (Ley de Granville)
- TP: +0.45% | SL: -0.30% del precio de entrada
"""
from typing import Optional


TP_PCT = 0.0045  # 0.45%
SL_PCT = 0.0030  # 0.30%

PIVOT_BARS = 5   # velas a cada lado para confirmar pico/valle


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


def signal(candles_15m: list[dict], candles_4h: list[dict]) -> Optional[str]:
    """
    Retorna "LONG", "SHORT" o None.
    candles: lista de dicts {o, h, l, c, v} ordenados de más antiguo a más reciente.
    """
    if len(candles_15m) < PIVOT_BARS * 2 + 5 or len(candles_4h) < 22:
        return None

    # Filtro 4H: dirección de la 20MA
    closes_4h = [c["c"] for c in candles_4h]
    ma20 = _sma(closes_4h, 20)
    valid_ma = [v for v in ma20 if v is not None]
    if len(valid_ma) < 2:
        return None
    ma_up   = valid_ma[-1] > valid_ma[-2]
    ma_down = valid_ma[-1] < valid_ma[-2]

    if not ma_up and not ma_down:
        return None  # MA plana, sin tendencia

    # Pivotes en 15m
    highs = [c["h"] for c in candles_15m]
    lows  = [c["l"] for c in candles_15m]
    peak   = _last_pivot_high(highs)
    valley = _last_pivot_low(lows)

    if peak is None or valley is None:
        return None

    prev_close = candles_15m[-2]["c"]
    last_close = candles_15m[-1]["c"]

    if prev_close <= peak < last_close and ma_up:
        return "LONG"

    if prev_close >= valley > last_close and ma_down:
        return "SHORT"

    return None


def tp_sl(entry: float, side: str) -> tuple[float, float]:
    """Retorna (take_profit, stop_loss) para el precio de entrada dado."""
    if side == "LONG":
        return entry * (1 + TP_PCT), entry * (1 - SL_PCT)
    else:
        return entry * (1 - TP_PCT), entry * (1 + SL_PCT)
