"""
STRUCTURE — Detección de Break of Structure (BOS)
══════════════════════════════════════════════════
Un BOS ocurre cuando el precio rompe el último swing high/low
con una vela de cierre, confirmando momentum estructural.
"""
from dataclasses import dataclass

import config
from utils.logger import get_logger

log = get_logger("Structure")


@dataclass
class BOSResult:
    bullish_bos:  bool    # Precio rompió swing high anterior
    bearish_bos:  bool    # Precio rompió swing low anterior
    swing_high:   float   # Último swing high relevante
    swing_low:    float   # Último swing low relevante
    bos_strength: float   # % de ruptura sobre el nivel


def _find_swing_high(candles: list[dict], lookback: int) -> float:
    highs = [c["high"] for c in candles[-lookback:]]
    return max(highs) if highs else 0.0


def _find_swing_low(candles: list[dict], lookback: int) -> float:
    lows = [c["low"] for c in candles[-lookback:]]
    return min(lows) if lows else 0.0


def detect_bos(candles: list[dict]) -> BOSResult:
    """
    Detecta BOS usando las últimas BOS_LOOKBACK velas (excluyendo la última).
    La última vela es la que intenta romper el nivel.
    """
    lb = config.BOS_LOOKBACK

    if len(candles) < lb + 5:
        return BOSResult(False, False, 0.0, 0.0, 0.0)

    # Buscar swing H/L en las velas previas (sin incluir la última)
    history    = candles[-(lb + 1):-1]
    last_candle = candles[-1]

    swing_high = _find_swing_high(history, lb)
    swing_low  = _find_swing_low(history,  lb)

    close = last_candle["close"]

    bullish_bos = False
    bearish_bos = False
    bos_strength = 0.0

    # BOS alcista: cierre supera el swing high con cuerpo positivo
    if close > swing_high and last_candle["close"] > last_candle["open"]:
        bullish_bos  = True
        bos_strength = (close - swing_high) / swing_high * 100

    # BOS bajista: cierre rompe el swing low con cuerpo negativo
    elif close < swing_low and last_candle["close"] < last_candle["open"]:
        bearish_bos  = True
        bos_strength = (swing_low - close) / swing_low * 100

    if bullish_bos:
        log.debug(f"BOS BULLISH | close={close:.4f} > swing_high={swing_high:.4f} (+{bos_strength:.3f}%)")
    if bearish_bos:
        log.debug(f"BOS BEARISH | close={close:.4f} < swing_low={swing_low:.4f} (+{bos_strength:.3f}%)")

    return BOSResult(bullish_bos, bearish_bos, swing_high, swing_low, round(bos_strength, 4))
