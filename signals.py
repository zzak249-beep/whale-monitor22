"""
SIGNALS — Agrega HTFBias + EMASignal + BOSResult + CVDResult
══════════════════════════════════════════════════════════════
Sistema de puntuación (0–100):
  • HTF confirmado en misma dirección  → +35 pts
  • EMA10 CROSS                        → +25 pts  (RETEST → +20)
  • BOS en misma dirección             → +20 pts
  • CVD confirmado                     → +15 pts
  • Volumen por encima de media        → +5  pts

Score ≥ 60  → señal válida
Score ≥ 75  → HIGH confidence, size_mult = 1.0
Score ≥ 60  → MEDIUM confidence, size_mult = 0.75
Score < 60  → HOLD

SL/TP:
  • SL = ATR × SL_ATR_MULT por debajo/encima de la entrada
  • TP = entrada ± (SL_distance × RISK_REWARD)
"""
from dataclasses import dataclass, field
from typing import List

import config
from strategy.htf_bias    import HTFBias
from strategy.ema10_cross import EMASignal
from strategy.structure   import BOSResult
from strategy.volume_cvd  import CVDResult
from utils.logger         import get_logger

log = get_logger("Signals")

MIN_SCORE = 60


@dataclass
class TradeSignal:
    direction:      str           # "LONG" | "SHORT" | "HOLD"
    entry_type:     str           # "CROSS" | "RETEST" | "NONE"
    entry_price:    float
    stop_loss:      float
    take_profit:    float
    risk_reward:    float
    score:          float         # 0-100
    confidence:     str           # "HIGH" | "MEDIUM" | "LOW"
    size_mult:      float         # multiplicador de tamaño
    reasons:        List[str] = field(default_factory=list)
    candle_pattern: str = ""


def aggregate_signals(
    htf:    HTFBias,
    ema:    EMASignal,
    bos:    BOSResult,
    vol:    CVDResult,
    symbol: str,
) -> TradeSignal:
    """
    Combina las cuatro señales en un único TradeSignal.
    Devuelve HOLD si no hay consenso suficiente.
    """
    _hold = TradeSignal("HOLD", "NONE", 0.0, 0.0, 0.0, 0.0, 0.0, "LOW", 0.0)

    # Dirección base viene de la EMA10
    if ema.direction not in ("LONG", "SHORT"):
        return _hold

    direction  = ema.direction
    entry_type = ema.entry_type

    score   = 0.0
    reasons = []

    # ── 1. HTF bias
    if htf.confirmed and htf.direction == direction:
        score += 35
        reasons.append(f"HTF {direction} (EMA50/200 alineadas)")
    elif htf.direction == "NEUTRAL":
        score += 10  # Neutro no bloquea pero no aporta
        reasons.append("HTF neutral")
    else:
        # HTF en contra → señal bloqueada
        log.debug(f"Señal {direction} bloqueada: HTF={htf.direction}")
        return _hold

    # ── 2. EMA10 tipo de entrada
    if entry_type == "CROSS":
        score += 25
        reasons.append(f"EMA10 CROSS {direction}")
    else:
        score += 20
        reasons.append(f"EMA10 RETEST {direction}")

    # ── 3. BOS
    if direction == "LONG" and bos.bullish_bos:
        score += 20
        reasons.append(f"BOS bullish (+{bos.bos_strength:.2f}%)")
    elif direction == "SHORT" and bos.bearish_bos:
        score += 20
        reasons.append(f"BOS bearish (+{bos.bos_strength:.2f}%)")

    # ── 4. CVD
    if direction == "LONG" and vol.bullish:
        score += 15
        reasons.append(f"CVD bullish ({vol.cvd:.2f})")
    elif direction == "SHORT" and vol.bearish:
        score += 15
        reasons.append(f"CVD bearish ({vol.cvd:.2f})")

    # ── 5. Volumen
    if vol.volume_ok:
        score += 5
        reasons.append("Volumen elevado")

    # ── Filtro mínimo
    if score < MIN_SCORE:
        log.debug(f"Score insuficiente: {score:.0f} < {MIN_SCORE} para {symbol} {direction}")
        return _hold

    # ── SL/TP
    entry = ema.entry_price
    atr   = ema.atr if ema.atr > 0 else entry * 0.002  # fallback 0.2%
    sl_dist = atr * config.SL_ATR_MULT

    if direction == "LONG":
        sl = entry - sl_dist
        tp = entry + sl_dist * config.RISK_REWARD
    else:
        sl = entry + sl_dist
        tp = entry - sl_dist * config.RISK_REWARD

    rr = abs(tp - entry) / abs(sl - entry) if abs(sl - entry) > 0 else 0.0

    if rr < config.RISK_REWARD * 0.8:
        log.debug(f"RR insuficiente: {rr:.2f} < {config.RISK_REWARD}")
        return _hold

    # ── Confianza y tamaño
    if score >= 75:
        confidence = "HIGH"
        size_mult  = 1.0
    else:
        confidence = "MEDIUM"
        size_mult  = 0.75

    log.info(
        f"SEÑAL {symbol} {direction} [{entry_type}] "
        f"score={score:.0f} conf={confidence} "
        f"entry={entry:.4f} SL={sl:.4f} TP={tp:.4f} RR={rr:.2f}x"
    )

    return TradeSignal(
        direction    = direction,
        entry_type   = entry_type,
        entry_price  = round(entry, 6),
        stop_loss    = round(sl, 6),
        take_profit  = round(tp, 6),
        risk_reward  = round(rr, 2),
        score        = round(score, 1),
        confidence   = confidence,
        size_mult    = size_mult,
        reasons      = reasons,
    )
