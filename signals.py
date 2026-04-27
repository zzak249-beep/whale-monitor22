"""SIGNALS — Agrega HTFBias + EMASignal + BOSResult + CVDResult → TradeSignal"""
from dataclasses import dataclass, field
from typing import List
import config
from bot_logger import get_logger

log = get_logger("Signals")
MIN_SCORE = 60

@dataclass
class TradeSignal:
    direction:      str
    entry_type:     str
    entry_price:    float
    stop_loss:      float
    take_profit:    float
    risk_reward:    float
    score:          float
    confidence:     str
    size_mult:      float
    reasons:        List[str] = field(default_factory=list)
    candle_pattern: str = ""

def aggregate_signals(htf, ema, bos, vol, symbol: str) -> TradeSignal:
    _hold = TradeSignal("HOLD","NONE",0.0,0.0,0.0,0.0,0.0,"LOW",0.0)
    if ema.direction not in ("LONG", "SHORT"):
        return _hold
    direction  = ema.direction
    entry_type = ema.entry_type
    score, reasons = 0.0, []

    # HTF
    if htf.confirmed and htf.direction == direction:
        score += 35; reasons.append(f"HTF {direction} confirmado")
    elif htf.direction == "NEUTRAL":
        score += 10; reasons.append("HTF neutral")
    else:
        log.debug(f"Señal {direction} bloqueada: HTF={htf.direction}")
        return _hold

    # EMA
    if entry_type == "CROSS":
        score += 25; reasons.append(f"EMA10 CROSS {direction}")
    else:
        score += 20; reasons.append(f"EMA10 RETEST {direction}")

    # BOS
    if direction == "LONG" and bos.bullish_bos:
        score += 20; reasons.append(f"BOS bullish +{bos.bos_strength:.2f}%")
    elif direction == "SHORT" and bos.bearish_bos:
        score += 20; reasons.append(f"BOS bearish +{bos.bos_strength:.2f}%")

    # CVD
    if direction == "LONG" and vol.bullish:
        score += 15; reasons.append(f"CVD bullish {vol.cvd:.2f}")
    elif direction == "SHORT" and vol.bearish:
        score += 15; reasons.append(f"CVD bearish {vol.cvd:.2f}")

    # Volumen
    if vol.volume_ok:
        score += 5; reasons.append("Volumen elevado")

    if score < MIN_SCORE:
        log.debug(f"Score {score:.0f} < {MIN_SCORE} para {symbol} {direction}")
        return _hold

    # SL/TP
    entry   = ema.entry_price
    atr     = ema.atr if ema.atr > 0 else entry * 0.002
    sl_dist = atr * config.SL_ATR_MULT
    if direction == "LONG":
        sl = entry - sl_dist
        tp = entry + sl_dist * config.RISK_REWARD
    else:
        sl = entry + sl_dist
        tp = entry - sl_dist * config.RISK_REWARD
    rr = abs(tp - entry) / abs(sl - entry) if abs(sl - entry) > 0 else 0.0
    if rr < config.RISK_REWARD * 0.8:
        return _hold

    confidence = "HIGH" if score >= 75 else "MEDIUM"
    size_mult  = 1.0   if score >= 75 else 0.75
    log.info(f"SEÑAL {symbol} {direction} [{entry_type}] score={score:.0f} conf={confidence} entry={entry:.4f} SL={sl:.4f} TP={tp:.4f} RR={rr:.2f}x")
    return TradeSignal(direction, entry_type, round(entry,6), round(sl,6), round(tp,6),
                       round(rr,2), round(score,1), confidence, size_mult, reasons)
