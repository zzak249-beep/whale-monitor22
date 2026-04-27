"""STRUCTURE — Detección de Break of Structure (BOS)"""
from dataclasses import dataclass
import config
from bot_logger import get_logger

log = get_logger("Structure")

@dataclass
class BOSResult:
    bullish_bos:  bool
    bearish_bos:  bool
    swing_high:   float
    swing_low:    float
    bos_strength: float

def detect_bos(candles: list) -> BOSResult:
    lb = config.BOS_LOOKBACK
    if len(candles) < lb + 5:
        return BOSResult(False, False, 0.0, 0.0, 0.0)
    history = candles[-(lb + 1):-1]
    last    = candles[-1]
    swing_high = max(c["high"] for c in history)
    swing_low  = min(c["low"]  for c in history)
    close = last["close"]
    bullish_bos = bearish_bos = False
    bos_strength = 0.0
    if close > swing_high and last["close"] > last["open"]:
        bullish_bos  = True
        bos_strength = (close - swing_high) / swing_high * 100
    elif close < swing_low and last["close"] < last["open"]:
        bearish_bos  = True
        bos_strength = (swing_low - close) / swing_low * 100
    if bullish_bos: log.debug(f"BOS BULLISH +{bos_strength:.3f}%")
    if bearish_bos: log.debug(f"BOS BEARISH +{bos_strength:.3f}%")
    return BOSResult(bullish_bos, bearish_bos, swing_high, swing_low, round(bos_strength, 4))
