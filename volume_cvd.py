"""
VOLUME CVD — Cumulative Volume Delta
══════════════════════════════════════
CVD = suma de (volumen alcista - volumen bajista) en N velas.
Una vela se considera alcista si close > open.
Confirma presión compradora/vendedora detrás del movimiento.
"""
from dataclasses import dataclass

import numpy as np

import config
from utils.logger import get_logger

log = get_logger("CVD")


@dataclass
class CVDResult:
    bullish:    bool     # CVD positivo y por encima del umbral
    bearish:    bool     # CVD negativo y por debajo del umbral
    cvd:        float    # CVD normalizado (-1 a 1)
    cvd_raw:    float    # CVD en unidades de volumen
    volume_ok:  bool     # Volumen de la última vela superior a la media
    avg_volume: float    # Volumen medio del período


def calculate_volume_cvd(candles: list[dict]) -> CVDResult:
    """
    Calcula el CVD sobre las últimas CVD_PERIOD velas.
    Necesita al menos CVD_PERIOD + 1 velas.
    """
    period = config.CVD_PERIOD
    if len(candles) < period + 1:
        return CVDResult(False, False, 0.0, 0.0, False, 0.0)

    window = candles[-period:]
    last   = candles[-1]

    # ── CVD acumulado
    bull_vol = sum(c["volume"] for c in window if c["close"] >= c["open"])
    bear_vol = sum(c["volume"] for c in window if c["close"] <  c["open"])
    total_vol = bull_vol + bear_vol

    cvd_raw = bull_vol - bear_vol
    cvd_norm = cvd_raw / total_vol if total_vol > 0 else 0.0  # normalizado -1..1

    # ── Volumen de la última vela vs media
    vols      = np.array([c["volume"] for c in window], dtype=float)
    avg_vol   = float(np.mean(vols[:-1]))  # media sin la última
    volume_ok = last["volume"] >= avg_vol * config.MIN_VOLUME_MULT

    bullish = cvd_norm >=  config.MIN_CVD_DELTA and volume_ok
    bearish = cvd_norm <= -config.MIN_CVD_DELTA and volume_ok

    log.debug(
        f"CVD: norm={cvd_norm:.3f} raw={cvd_raw:.1f} "
        f"vol_ratio={last['volume']/avg_vol:.2f}x "
        f"→ bull={bullish} bear={bearish}"
    )

    return CVDResult(
        bullish    = bullish,
        bearish    = bearish,
        cvd        = round(cvd_norm, 4),
        cvd_raw    = round(cvd_raw, 2),
        volume_ok  = volume_ok,
        avg_volume = round(avg_vol, 4),
    )
