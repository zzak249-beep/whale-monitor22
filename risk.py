# -*- coding: utf-8 -*-
"""risk.py -- Maki Bot PRO — Gestión de riesgo."""
from __future__ import annotations
import math
from datetime import datetime, timezone


class RiskManager:
    def __init__(self, trade_usdt: float, max_trades: int, max_dd_pct: float):
        self._trade_usdt  = trade_usdt
        self._max_trades  = max_trades
        self._max_dd_usdt = max_dd_pct   # recibe como USDT absoluto

        self._daily_pnl:    float = 0.0
        self._trades_today: int   = 0
        self._paused:       bool  = False
        self._day:          int   = datetime.now(timezone.utc).day

    # ── Reset diario ─────────────────────────────────────────────────────────

    def _check_day(self):
        today = datetime.now(timezone.utc).day
        if today != self._day:
            self._daily_pnl    = 0.0
            self._trades_today = 0
            self._paused       = False
            self._day          = today

    # ── Registro de cierres ──────────────────────────────────────────────────

    def register_close(self, pnl: float):
        self._check_day()
        self._daily_pnl    += pnl
        self._trades_today += 1
        if self._daily_pnl <= -abs(self._max_dd_usdt):
            self._paused = True

    # ── Validaciones ─────────────────────────────────────────────────────────

    def can_trade(self, open_count: int) -> tuple[bool, str]:
        self._check_day()
        if self._paused:
            return False, f"drawdown diario alcanzado ({self._daily_pnl:.2f} USDT)"
        if open_count >= self._max_trades:
            return False, f"máximo trades abiertos ({self._max_trades})"
        return True, "ok"

    def direction_ok(self, open_trades: dict, side: str, max_same: int) -> bool:
        """Máximo N trades en la misma dirección (anti-correlación)."""
        count = sum(1 for t in open_trades.values() if t["side"] == side)
        return count < max_same

    def calc_qty(self, price: float) -> float:
        """Cantidad en contratos para operar trade_usdt a precio dado."""
        if price <= 0:
            return 0.0
        qty = self._trade_usdt / price
        # Redondear a 3 decimales (mínimo BingX)
        qty = math.floor(qty * 1000) / 1000
        return qty if qty > 0 else 0.0

    # ── Trailing stop (break-even) ───────────────────────────────────────────

    def check_trailing(self, trade: dict, current_price: float) -> float | None:
        """
        Si el precio ha avanzado ≥50% hacia el TP, mueve el SL al entry.
        Retorna el nuevo SL si debe moverse, None si no.
        """
        if trade.get("be_activated"):
            return None

        entry = trade["entry"]
        tp    = trade["tp"]
        sl    = trade["sl"]
        side  = trade["side"]

        tp_dist = abs(tp - entry)
        if tp_dist <= 0:
            return None

        if side in ("BUY", "LONG"):
            progress = (current_price - entry) / tp_dist
            if progress >= 0.5 and current_price > entry:
                return entry + (tp_dist * 0.02)  # SL ligeramente sobre entry
        else:
            progress = (entry - current_price) / tp_dist
            if progress >= 0.5 and current_price < entry:
                return entry - (tp_dist * 0.02)

        return None

    # ── Filtro horario ───────────────────────────────────────────────────────

    @staticmethod
    def is_safe_time() -> bool:
        """
        Evita los 3 primeros minutos de cada hora (zona de manipulación).
        """
        m = datetime.now(timezone.utc).minute
        return m >= 3

    # ── Estado ───────────────────────────────────────────────────────────────

    def status(self) -> dict:
        self._check_day()
        return {
            "daily_pnl":    round(self._daily_pnl, 4),
            "trades_today": self._trades_today,
            "paused":       self._paused,
        }
