"""
Estrategia Maki — ZigZag (pivotes) + 20MA en 4H + RSI en 15m
TP: +0.45% | SL: -0.30%

Lógica de la estrategia
────────────────────────
1. TENDENCIA (filtro 4H)
   Calculamos la SMA-20 sobre las últimas 30 velas de 4H.
   - Si la MA está subiendo  → solo buscamos LONGs
   - Si la MA está bajando   → solo buscamos SHORTs
   - Si está plana           → sin operación

2. ENTRADA (señal 15m — ZigZag)
   Detectamos el último pico (pivot high) y el último valle (pivot low)
   usando N velas a cada lado para confirmar el extremo.
   - LONG:  el cierre anterior estaba ≤ pico y el cierre actual lo supera
             (ruptura del pico al alza) + tendencia alcista + RSI no sobrecomprado
   - SHORT: el cierre anterior estaba ≥ valle y el cierre actual lo rompe
             (ruptura del valle a la baja) + tendencia bajista + RSI no sobrevendido

3. RSI (filtro de sobreextensión)
   RSI-14 sobre el 15m. Evita entrar en movimientos ya agotados:
   - No LONG si RSI > 65 (mercado sobrecomprado)
   - No SHORT si RSI < 35 (mercado sobrevendido)

4. TP/SL
   Porcentaje fijo respecto al precio de entrada:
   - TP: +0.45%  |  SL: -0.30%  (ratio R:R ≈ 1.5)
"""
from typing import Optional
import logging

logger = logging.getLogger("strategy")

# ── Parámetros ─────────────────────────────────────────────────────── #
TP_PCT      = 0.0045   # Take Profit 0.45%
SL_PCT      = 0.0030   # Stop Loss   0.30%
PIVOT_BARS  = 5        # velas a cada lado para confirmar pico/valle
RSI_PERIOD  = 14
RSI_OB      = 65       # RSI sobrecompra — no abrir LONG por encima
RSI_OS      = 35       # RSI sobreventa  — no abrir SHORT por debajo


# ── Indicadores ────────────────────────────────────────────────────── #

def _sma(values: list[float], period: int) -> list[Optional[float]]:
    result = []
    for i in range(len(values)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(sum(values[i - period + 1 : i + 1]) / period)
    return result


def _rsi(closes: list[float], period: int = RSI_PERIOD) -> Optional[float]:
    """RSI de Wilder. Retorna el valor más reciente o None si no hay datos."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    return 100 - (100 / (1 + avg_g / avg_l))


# ── Detección de pivotes ────────────────────────────────────────────── #

def _last_pivot_high(highs: list[float]) -> Optional[float]:
    n = len(highs)
    for i in range(n - PIVOT_BARS - 1, PIVOT_BARS - 1, -1):
        h = highs[i]
        if (all(h > highs[i - j] for j in range(1, PIVOT_BARS + 1)) and
                all(h > highs[i + j] for j in range(1, PIVOT_BARS + 1))):
            return h
    return None


def _last_pivot_low(lows: list[float]) -> Optional[float]:
    n = len(lows)
    for i in range(n - PIVOT_BARS - 1, PIVOT_BARS - 1, -1):
        l = lows[i]
        if (all(l < lows[i - j] for j in range(1, PIVOT_BARS + 1)) and
                all(l < lows[i + j] for j in range(1, PIVOT_BARS + 1))):
            return l
    return None


# ── Señal principal ────────────────────────────────────────────────── #

def signal(candles_15m: list[dict], candles_4h: list[dict]) -> Optional[str]:
    """
    Retorna "LONG", "SHORT" o None.
    Las listas deben estar en orden cronológico (más antigua primero).
    Cada vela es un dict {o, h, l, c, v}.
    """
    MIN_15M = PIVOT_BARS * 2 + RSI_PERIOD + 5
    if len(candles_15m) < MIN_15M:
        logger.debug(f"15m insuficiente ({len(candles_15m)}<{MIN_15M})")
        return None
    if len(candles_4h) < 22:
        logger.debug(f"4H insuficiente ({len(candles_4h)})")
        return None

    # 1. Filtro de tendencia 4H ── MA-20
    closes_4h = [c["c"] for c in candles_4h]
    ma20      = [v for v in _sma(closes_4h, 20) if v is not None]
    if len(ma20) < 2:
        return None
    ma_up   = ma20[-1] > ma20[-2]
    ma_down = ma20[-1] < ma20[-2]
    if not ma_up and not ma_down:
        logger.debug("MA20 4H plana")
        return None

    # 2. RSI 15m
    closes_15m = [c["c"] for c in candles_15m]
    rsi = _rsi(closes_15m)
    if rsi is None:
        return None

    # 3. Pivotes 15m
    highs  = [c["h"] for c in candles_15m]
    lows   = [c["l"] for c in candles_15m]
    peak   = _last_pivot_high(highs)
    valley = _last_pivot_low(lows)
    if peak is None or valley is None:
        logger.debug("Sin pivote reciente en 15m")
        return None

    prev_c = candles_15m[-2]["c"]
    last_c = candles_15m[-1]["c"]

    # 4. Condiciones de entrada
    if prev_c <= peak < last_c and ma_up and rsi < RSI_OB:
        logger.info(f"LONG | peak={peak:.4f} MA↑ RSI={rsi:.1f}")
        return "LONG"

    if prev_c >= valley > last_c and ma_down and rsi > RSI_OS:
        logger.info(f"SHORT | valley={valley:.4f} MA↓ RSI={rsi:.1f}")
        return "SHORT"

    return None


# ── TP / SL ────────────────────────────────────────────────────────── #

def tp_sl(entry: float, side: str, candles_15m: list[dict] = None) -> tuple[float, float]:
    """Retorna (take_profit, stop_loss) para el precio de entrada dado."""
    if side == "LONG":
        return entry * (1 + TP_PCT), entry * (1 - SL_PCT)
    else:
        return entry * (1 - TP_PCT), entry * (1 + SL_PCT)
