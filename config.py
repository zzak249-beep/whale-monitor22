# -*- coding: utf-8 -*-
"""config.py -- Three Step Bot v5."""
import os
from dataclasses import dataclass


@dataclass
class Config:
    # ── BingX ──────────────────────────────────────────────────────────────
    bingx_api_key:    str   = ""
    bingx_secret_key: str   = ""

    # ── Telegram ───────────────────────────────────────────────────────────
    telegram_token:   str   = ""
    telegram_chat_id: str   = ""

    # ── Trade sizing ───────────────────────────────────────────────────────
    trade_usdt:  float = 5.0    # hard min 5 USDT
    leverage:    int   = 10     # 10x fixed

    # ── Strategy ───────────────────────────────────────────────────────────
    period:      int   = 20
    atr_period:  int   = 14
    atr_mult:    float = 1.5    # SL = ATR * 1.5
    rr:          float = 3.0    # TP = SL * 3 → 1:3 RR
    timeframe:   str   = "15m"

    # ── Signal filters — RELAXED to actually produce signals ───────────────
    min_volume_mult: float = 0.6    # FIXED: was 0.8 (too strict)
    min_atr_pct:     float = 0.05   # FIXED: was 0.3 (killed low-price coins)
    trend_filter:    bool  = True   # EMA50, with 0.5% tolerance band
    session_filter:  bool  = True   # only 07:00-20:00 UTC (London+NY)
    funding_filter:  bool  = True   # skip when funding extreme

    # ── Position management ────────────────────────────────────────────────
    max_positions:    int   = 5
    breakeven_r:      float = 1.0
    partial_pct:      float = 0.5
    max_daily_trades: int   = 20

    # ── Risk controls ──────────────────────────────────────────────────────
    max_daily_loss_pct: float = 5.0
    min_balance_usdt:   float = 10.0   # FIXED: was 15 — hard to trade with small balance

    # ── Scanning ───────────────────────────────────────────────────────────
    symbols_raw:    str = (
        "BTC-USDT,ETH-USDT,SOL-USDT,BNB-USDT,XRP-USDT,"
        "DOGE-USDT,ADA-USDT,AVAX-USDT,MATIC-USDT,LINK-USDT,"
        "DOT-USDT,LTC-USDT,ATOM-USDT,FIL-USDT,OP-USDT"
    )
    scan_interval:  int  = 60
    max_concurrent: int  = 15

    # ── HTTP / infra ───────────────────────────────────────────────────────
    http_timeout:   int  = 12
    health_port:    int  = 8080

    @property
    def symbols(self) -> list[str]:
        return [s.strip() for s in self.symbols_raw.split(",") if s.strip()]

    def __post_init__(self) -> None:
        self.bingx_api_key      = os.getenv("BINGX_API_KEY",       self.bingx_api_key)
        self.bingx_secret_key   = os.getenv("BINGX_SECRET_KEY",    self.bingx_secret_key)
        self.telegram_token     = os.getenv("TELEGRAM_TOKEN",       self.telegram_token)
        self.telegram_chat_id   = os.getenv("TELEGRAM_CHAT_ID",     self.telegram_chat_id)
        self.trade_usdt         = max(5.0, float(os.getenv("TRADE_USDT",    str(self.trade_usdt))))
        self.leverage           = int(os.getenv("LEVERAGE",         str(self.leverage)))
        self.period             = int(os.getenv("PERIOD",           str(self.period)))
        self.atr_period         = int(os.getenv("ATR_PERIOD",       str(self.atr_period)))
        self.atr_mult           = float(os.getenv("ATR_MULT",       str(self.atr_mult)))
        self.rr                 = float(os.getenv("RR",             str(self.rr)))
        self.timeframe          = os.getenv("TIMEFRAME",            self.timeframe)
        self.max_positions      = int(os.getenv("MAX_POSITIONS",    str(self.max_positions)))
        self.scan_interval      = int(os.getenv("SCAN_INTERVAL",    str(self.scan_interval)))
        self.symbols_raw        = os.getenv("SYMBOLS",              self.symbols_raw)
        self.health_port        = int(os.getenv("PORT",             str(self.health_port)))
        self.max_daily_loss_pct = float(os.getenv("MAX_DAILY_LOSS", str(self.max_daily_loss_pct)))
        self.min_volume_mult    = float(os.getenv("MIN_VOL_MULT",   str(self.min_volume_mult)))
        self.trend_filter       = os.getenv("TREND_FILTER",  "true").lower()  == "true"
        self.session_filter     = os.getenv("SESSION_FILTER","true").lower()  == "true"
        self.funding_filter     = os.getenv("FUNDING_FILTER","true").lower()  == "true"
        self.max_daily_trades   = int(os.getenv("MAX_DAILY_TRADES", str(self.max_daily_trades)))
        self.min_balance_usdt   = float(os.getenv("MIN_BALANCE",    str(self.min_balance_usdt)))

        if not self.bingx_api_key or not self.bingx_secret_key:
            import sys
            print("FATAL: BINGX_API_KEY / BINGX_SECRET_KEY not set", flush=True)
            sys.exit(1)


cfg = Config()
