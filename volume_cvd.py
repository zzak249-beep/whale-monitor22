"""VOLUME CVD — Cumulative Volume Delta"""
from dataclasses import dataclass
import numpy as np
import config
from bot_logger import get_logger

log = get_logger("CVD")

@dataclass
class CVDResult:
    bullish:    bool
    bearish:    bool
    cvd:        float
    cvd_raw:    float
    volume_ok:  bool
    avg_volume: float

def calculate_volume_cvd(candles: list) -> CVDResult:
    period = config.CVD_PERIOD
    if len(candles) < period + 1:
        return CVDResult(False, False, 0.0, 0.0, False, 0.0)
    window   = candles[-period:]
    last     = candles[-1]
    bull_vol = sum(c["volume"] for c in window if c["close"] >= c["open"])
    bear_vol = sum(c["volume"] for c in window if c["close"] <  c["open"])
    total    = bull_vol + bear_vol
    cvd_raw  = bull_vol - bear_vol
    cvd_norm = cvd_raw / total if total > 0 else 0.0
    vols     = np.array([c["volume"] for c in window], dtype=float)
    avg_vol  = float(np.mean(vols[:-1]))
    vol_ok   = last["volume"] >= avg_vol * config.MIN_VOLUME_MULT
    bullish  = cvd_norm >=  config.MIN_CVD_DELTA and vol_ok
    bearish  = cvd_norm <= -config.MIN_CVD_DELTA and vol_ok
    log.debug(f"CVD norm={cvd_norm:.3f} vol_ok={vol_ok} bull={bullish} bear={bearish}")
    return CVDResult(bullish, bearish, round(cvd_norm,4), round(cvd_raw,2), vol_ok, round(avg_vol,4))
