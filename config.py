"""
THREE STEP FUTURE-TREND BOT — config.py
========================================
Toda la configuración via variables de entorno.
Sin defaults peligrosos — BINGX_TESTNET=true por defecto.
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import List


def _e(k, d=""): return os.environ.get(k, d)
def _ef(k, d): 
    try: return float(os.environ.get(k, d))
    except: return float(d)
def _ei(k, d):
    try: return int(os.environ.get(k, d))
    except: return int(d)
def _eb(k, d):
    return os.environ.get(k, str(d)).lower() in ("1","true","yes")


@dataclass
class Config:
    # ── BingX ────────────────────────────────────────────────────────────────
    api_key:    str  = field(default_factory=lambda: _e("BINGX_API_KEY"))
    secret_key: str  = field(default_factory=lambda: _e("BINGX_SECRET_KEY"))
    testnet:    bool = field(default_factory=lambda: _eb("BINGX_TESTNET", True))

    @property
    def base_url(self) -> str:
        return ("https://open-api-vst.bingx.com" if self.testnet
                else "https://open-api.bingx.com")

    # ── Telegram ─────────────────────────────────────────────────────────────
    tg_token:   str  = field(default_factory=lambda: _e("TELEGRAM_BOT_TOKEN"))
    tg_chat_id: str  = field(default_factory=lambda: _e("TELEGRAM_CHAT_ID"))

    # ── Trading ──────────────────────────────────────────────────────────────
    symbols: List[str] = field(default_factory=lambda: [
        s.strip() for s in _e(
            "SYMBOLS",
            "BTC-USDT,ETH-USDT,SOL-USDT,BNB-USDT,"
            "XRP-USDT,DOGE-USDT,ADA-USDT,AVAX-USDT"
        ).split(",") if s.strip()
    ])
    timeframe:     str   = field(default_factory=lambda: _e("TIMEFRAME", "1h"))
    trade_usdt:    float = field(default_factory=lambda: _ef("TRADE_USDT", 5.0))
    leverage:      int   = field(default_factory=lambda: _ei("LEVERAGE", 10))
    max_positions: int   = field(default_factory=lambda: _ei("MAX_POSITIONS", 3))

    # ── Strategy: Three Step Volume Delta ────────────────────────────────────
    period:     int   = field(default_factory=lambda: _ei("PERIOD", 25))
    atr_period: int   = field(default_factory=lambda: _ei("ATR_PERIOD", 14))
    atr_mult:   float = field(default_factory=lambda: _ef("ATR_MULT", 2.0))
    rr:         float = field(default_factory=lambda: _ef("RR", 2.0))

    # ── Stop Loss / Take Profit automático ───────────────────────────────────
    # SL = entry ± atr_mult × ATR
    # TP1 = entry ± rr × rr_atr × ATR  (parcial 50%)
    # TP2 = entry ± rr2 × rr_atr × ATR (resto)
    rr_atr:       float = field(default_factory=lambda: _ef("RR_ATR", 1.0))
    tp2_mult:     float = field(default_factory=lambda: _ef("TP2_MULT", 3.5))
    partial_pct:  float = field(default_factory=lambda: _ef("PARTIAL_PCT", 50.0))

    # Breakeven: mover SL a entry cuando precio avanza X% del recorrido SL→TP1
    be_trigger:   float = field(default_factory=lambda: _ef("BE_TRIGGER", 0.5))

    # ── Runtime ──────────────────────────────────────────────────────────────
    scan_interval:  int = field(default_factory=lambda: _ei("SCAN_INTERVAL", 300))
    max_concurrent: int = field(default_factory=lambda: _ei("MAX_CONCURRENT", 8))
    health_port:    int = field(default_factory=lambda: _ei("PORT", 8080))

    # ── Risk ─────────────────────────────────────────────────────────────────
    max_daily_loss_usdt: float = field(default_factory=lambda: _ef("MAX_DAILY_LOSS", 50.0))


cfg = Config()
