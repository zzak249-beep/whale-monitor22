"""core/config.py — All configuration via environment variables."""
from __future__ import annotations
import os
from typing import List
from pydantic import field_validator
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    # ── Exchange ──────────────────────────────────────────────────────────
    bingx_api_key:    str = ""
    bingx_secret_key: str = ""

    # ── Telegram ──────────────────────────────────────────────────────────
    telegram_token:   str = ""
    telegram_chat_id: str = ""

    # ── Strategy ──────────────────────────────────────────────────────────
    timeframe:       str   = "15m"
    confirm_tf:      str   = "1h"
    trend_tf:        str   = "4h"
    period:          int   = 20
    adx_len:         int   = 14
    di_len:          int   = 14
    adx_thresh:      float = 25.0
    rsi_len:         int   = 14
    rsi_ob:          float = 70.0
    rsi_os:          float = 30.0
    vol_spike_mult:  float = 1.6
    min_confidence:  float = 52.0

    # ── Universe ──────────────────────────────────────────────────────────
    min_volume_usdt: float     = 2_000_000.0
    top_n_symbols:   int       = 50
    blacklist:       List[str] = ["LUNA-USDT", "FTT-USDT", "LUNC-USDT"]

    # ── Risk ─────────────────────────────────────────────────────────────
    leverage:               int   = 5
    risk_pct:               float = 1.2
    max_open_trades:        int   = 3
    sl_pct:                 float = 2.0
    tp_pct:                 float = 4.0
    trailing_sl:            bool  = True
    max_drawdown_pct:       float = 8.0
    daily_loss_limit:       float = 4.0
    max_consecutive_losses: int   = 4
    cooldown_after_loss:    int   = 300

    # ── Performance ───────────────────────────────────────────────────────
    scan_interval:  int = 10
    max_concurrent: int = 25
    http_timeout:   int = 8

    # ── Dashboard ─────────────────────────────────────────────────────────
    dashboard_enabled: bool = True
    dashboard_port:    int  = 8080

    model_config = {"env_file": ".env", "case_sensitive": False, "extra": "ignore"}

    @field_validator("blacklist", mode="before")
    @classmethod
    def _parse_blacklist(cls, v: object) -> List[str]:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        if isinstance(v, (list, set, tuple)):
            return [str(x).strip() for x in v if str(x).strip()]
        return []

    @property
    def effective_port(self) -> int:
        """Railway injects PORT; fall back to dashboard_port."""
        return int(os.environ.get("PORT", self.dashboard_port))


cfg = Config()
