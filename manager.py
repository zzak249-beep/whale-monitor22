"""
RISK MANAGER — Gestión de riesgo y position sizing
───────────────────────────────────────────────────
• Kelly fraccionado para sizing óptimo
• Control de pérdida diaria máxima
• Anti-duplicado de posiciones
• Cálculo de cantidad exacta en contratos
"""
import asyncio
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional

import config
from utils.logger import get_logger

log = get_logger("Risk")


@dataclass
class DailyStats:
    date:       str   = ""
    pnl:        float = 0.0
    trades:     int   = 0
    wins:       int   = 0
    losses:     int   = 0
    max_drawdown: float = 0.0


@dataclass
class PositionSizing:
    qty:          float   # Contratos a comprar/vender
    risk_usdt:    float   # USDT en riesgo
    notional:     float   # Valor nocional de la posición
    sl_distance:  float   # Distancia al SL en %
    valid:        bool
    reason:       str


class RiskManager:
    """
    Gestiona el riesgo global del bot:
    • Límite de pérdida diaria
    • Número máximo de trades simultáneos
    • Position sizing basado en riesgo fijo
    • Tracking de PnL
    """

    def __init__(self):
        self._open_positions: dict[str, dict] = {}   # symbol → info
        self._daily: DailyStats = DailyStats()
        self._equity_start: float = 0.0
        self._lock = asyncio.Lock()

    # ──────────────────────────────────────────────────────────────────────────
    # DAILY RESET
    # ──────────────────────────────────────────────────────────────────────────
    def _check_reset_daily(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily.date != today:
            log.info(f"Nuevo día trading: reset stats ({today})")
            self._daily = DailyStats(date=today)

    # ──────────────────────────────────────────────────────────────────────────
    # POSITION SIZING
    # ──────────────────────────────────────────────────────────────────────────
    def calculate_position_size(
        self,
        equity:      float,
        entry_price: float,
        stop_loss:   float,
        size_mult:   float = 1.0,
        min_qty:     float = 0.001,
        qty_step:    float = 0.001,
    ) -> PositionSizing:
        """
        Sizing por riesgo fijo (%):
          riesgo_usdt = equity × RISK_PER_TRADE% × size_mult
          qty         = riesgo_usdt / (distancia_SL × leverage)
        """
        if equity <= 0 or entry_price <= 0 or stop_loss <= 0:
            return PositionSizing(0, 0, 0, 0, False, "Parámetros inválidos")

        sl_distance_pct = abs(entry_price - stop_loss) / entry_price
        if sl_distance_pct < 0.001:
            return PositionSizing(0, 0, 0, 0, False, "SL demasiado cercano (<0.1%)")
        if sl_distance_pct > 0.10:
            return PositionSizing(0, 0, 0, 0, False, "SL demasiado lejos (>10%)")

        # Riesgo en USDT
        risk_usdt  = equity * (config.RISK_PER_TRADE / 100) * size_mult
        # Con apalancamiento, el SL mueve: distance * leverage para la cuenta
        # pero qty = riesgo / (precio * sl_pct) sin apalancamiento
        qty_raw    = risk_usdt / (entry_price * sl_distance_pct)
        # Redondear al step mínimo
        qty        = max(min_qty, round(qty_raw / qty_step) * qty_step)
        notional   = qty * entry_price

        log.info(
            f"Sizing: equity={equity:.2f} riesgo={risk_usdt:.2f}USDT "
            f"SL={sl_distance_pct:.2%} qty={qty:.4f} notional={notional:.2f}"
        )

        return PositionSizing(
            qty          = round(qty, 6),
            risk_usdt    = round(risk_usdt, 2),
            notional     = round(notional, 2),
            sl_distance  = round(sl_distance_pct * 100, 3),
            valid        = True,
            reason       = "OK",
        )

    # ──────────────────────────────────────────────────────────────────────────
    # GATES — ¿Puede el bot operar ahora?
    # ──────────────────────────────────────────────────────────────────────────
    async def can_trade(self, symbol: str, equity: float) -> tuple[bool, str]:
        """Comprueba todas las condiciones antes de ejecutar."""
        async with self._lock:
            self._check_reset_daily()

            # 1. Límite de pérdida diaria
            daily_loss_pct = abs(self._daily.pnl) / max(equity, 1) * 100
            if self._daily.pnl < 0 and daily_loss_pct >= config.MAX_DAILY_LOSS:
                return False, f"Límite pérdida diaria ({daily_loss_pct:.1f}% ≥ {config.MAX_DAILY_LOSS}%)"

            # 2. Máximo trades simultáneos
            if len(self._open_positions) >= config.MAX_OPEN_TRADES:
                return False, f"Máx trades simultáneos ({len(self._open_positions)}/{config.MAX_OPEN_TRADES})"

            # 3. Ya hay posición abierta en este símbolo
            if symbol in self._open_positions:
                return False, f"Posición ya abierta en {symbol}"

            return True, "OK"

    # ──────────────────────────────────────────────────────────────────────────
    # TRACKING
    # ──────────────────────────────────────────────────────────────────────────
    async def register_open(self, symbol: str, direction: str, entry: float, qty: float, sl: float, tp: float):
        async with self._lock:
            self._open_positions[symbol] = {
                "direction": direction,
                "entry":     entry,
                "qty":       qty,
                "sl":        sl,
                "tp":        tp,
                "ts":        datetime.now(timezone.utc).isoformat(),
            }
            self._daily.trades += 1
            log.info(f"Posición registrada: {symbol} {direction} @ {entry}")

    async def register_close(self, symbol: str, exit_price: float):
        async with self._lock:
            pos = self._open_positions.pop(symbol, None)
            if not pos:
                return
            pnl = (exit_price - pos["entry"]) * pos["qty"]
            if pos["direction"] == "SHORT":
                pnl = -pnl
            self._daily.pnl += pnl
            if pnl > 0:
                self._daily.wins += 1
            else:
                self._daily.losses += 1
            log.info(f"Posición cerrada: {symbol} PnL={pnl:+.2f}USDT | Día={self._daily.pnl:+.2f}USDT")

    def get_stats(self) -> dict:
        self._check_reset_daily()
        total = self._daily.wins + self._daily.losses
        wr    = self._daily.wins / total * 100 if total > 0 else 0
        return {
            "date":         self._daily.date,
            "pnl":          round(self._daily.pnl, 2),
            "trades":       self._daily.trades,
            "wins":         self._daily.wins,
            "losses":       self._daily.losses,
            "winrate":      round(wr, 1),
            "open":         len(self._open_positions),
            "open_symbols": list(self._open_positions.keys()),
        }

    # ──────────────────────────────────────────────────────────────────────────
    # FILTRO HORARIO
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def is_trading_hour() -> bool:
        """Verifica si estamos en horario de trading (UTC)."""
        try:
            start, end = config.TRADE_HOURS_UTC.split("-")
            now_h = datetime.now(timezone.utc).hour
            return int(start) <= now_h < int(end)
        except Exception:
            return True   # Si error en config, operar siempre
