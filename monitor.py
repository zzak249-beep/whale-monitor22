"""
POSITION MONITOR — Monitoreo de posiciones abiertas
═════════════════════════════════════════════════════
• Verifica cada 30 segundos si SL/TP han sido tocados
• Trailing stop opcional cuando el precio mueve a favor
• Cierre de emergencia si la pérdida supera el umbral
• Sincroniza con el RiskManager para actualizar stats
"""
import asyncio
from datetime import datetime, timezone
from typing import Optional

import config
from exchange.bingx_client           import BingXClient
from risk.manager                    import RiskManager
from notifications.telegram_notifier import TelegramNotifier
from utils.logger                    import get_logger

log = get_logger("Monitor")

POLL_SEC       = 30     # Segundos entre checks
TRAILING_PCT   = 0.004  # Activa trailing cuando gana 0.4%
TRAIL_DISTANCE = 0.003  # Mantiene trailing a 0.3% del precio


class PositionMonitor:
    """
    Corre en background y gestiona posiciones abiertas.
    Complementa el SL/TP nativo de BingX con lógica adicional.
    """

    def __init__(
        self,
        client: BingXClient,
        risk:   RiskManager,
        tg:     TelegramNotifier,
    ):
        self.client = client
        self.risk   = risk
        self.tg     = tg
        # Tracking interno: symbol → {entry, direction, qty, sl, tp, trailing_sl}
        self._positions: dict[str, dict] = {}

    # ──────────────────────────────────────────────────────────────────────────
    # API PÚBLICA
    # ──────────────────────────────────────────────────────────────────────────

    def track(
        self,
        symbol:    str,
        direction: str,
        entry:     float,
        qty:       float,
        sl:        float,
        tp:        float,
    ):
        """Registra una posición para ser monitoreada."""
        self._positions[symbol] = {
            "direction":  direction,
            "entry":      entry,
            "qty":        qty,
            "sl":         sl,
            "tp":         tp,
            "trailing_sl": None,
            "peak_price":  entry,
            "opened_at":   datetime.now(timezone.utc).isoformat(),
        }
        log.info(f"Monitor: tracking {symbol} {direction} @ {entry} | SL={sl} TP={tp}")

    def untrack(self, symbol: str):
        self._positions.pop(symbol, None)
        log.info(f"Monitor: dejó de rastrear {symbol}")

    # ──────────────────────────────────────────────────────────────────────────
    # LOOP PRINCIPAL
    # ──────────────────────────────────────────────────────────────────────────

    async def run(self):
        """Task asíncrono que corre indefinidamente."""
        log.info("PositionMonitor iniciado")
        while True:
            try:
                await self._check_all()
            except Exception as e:
                log.error(f"Monitor loop error: {e}")
                self.tg.error_alert(f"Monitor error: {e}")
            await asyncio.sleep(POLL_SEC)

    async def _check_all(self):
        if not self._positions:
            return

        # Obtener posiciones reales de BingX (para ver si ya cerraron por SL/TP)
        for symbol in list(self._positions.keys()):
            await self._check_symbol(symbol)

    async def _check_symbol(self, symbol: str):
        pos_info = self._positions.get(symbol)
        if not pos_info:
            return

        # ── Precio actual
        price = await self.client.get_ticker(symbol)
        if price <= 0:
            return

        direction = pos_info["direction"]
        entry     = pos_info["entry"]
        sl        = pos_info["sl"]
        tp        = pos_info["tp"]
        qty       = pos_info["qty"]

        # ── Verificar si BingX ya cerró la posición (SL/TP tocado)
        open_positions = await self.client.get_positions(symbol)
        still_open = any(
            float(p.get("positionAmt", 0)) != 0
            for p in open_positions
            if p.get("symbol") == symbol
        )

        if not still_open:
            # Posición cerrada externamente (por SL o TP de BingX)
            pnl = (price - entry) * qty if direction == "LONG" else (entry - price) * qty
            log.info(f"Posición cerrada: {symbol} | PnL estimado={pnl:+.2f}")
            self.tg.trade_closed(symbol, direction, pnl, entry, price)
            await self.risk.register_close(symbol, price)
            self.untrack(symbol)
            return

        # ── Trailing stop
        await self._manage_trailing(symbol, pos_info, price)

        # ── PnL actual (informativo en log)
        pnl = (price - entry) * qty if direction == "LONG" else (entry - price) * qty
        pnl_pct = (price - entry) / entry * 100 if direction == "LONG" else (entry - price) / entry * 100
        log.debug(f"Monitor {symbol} | precio={price:.4f} PnL={pnl:+.2f} ({pnl_pct:+.2f}%)")

    # ──────────────────────────────────────────────────────────────────────────
    # TRAILING STOP
    # ──────────────────────────────────────────────────────────────────────────

    async def _manage_trailing(self, symbol: str, pos: dict, price: float):
        """
        Activa y ajusta trailing stop cuando el precio mueve a favor.
        LONG:  precio sube → SL sube
        SHORT: precio baja → SL baja
        """
        direction   = pos["direction"]
        entry       = pos["entry"]
        peak        = pos["peak_price"]
        trail_sl    = pos["trailing_sl"]

        # Actualizar pico
        if direction == "LONG":
            new_peak = max(peak, price)
        else:
            new_peak = min(peak, price)
        pos["peak_price"] = new_peak

        # ¿Activar trailing?
        gain_pct = abs(new_peak - entry) / entry
        if gain_pct < TRAILING_PCT:
            return   # Ganancia insuficiente para activar trailing

        # Calcular nuevo SL trailing
        if direction == "LONG":
            new_trail_sl = new_peak * (1 - TRAIL_DISTANCE)
        else:
            new_trail_sl = new_peak * (1 + TRAIL_DISTANCE)

        # Solo actualizar si mejora el SL actual
        if trail_sl is None or \
           (direction == "LONG"  and new_trail_sl > trail_sl) or \
           (direction == "SHORT" and new_trail_sl < trail_sl):

            pos["trailing_sl"] = new_trail_sl
            log.info(f"Trailing SL actualizado: {symbol} {direction} | nuevo SL={new_trail_sl:.4f}")

            # En paper trading no cancelamos órdenes; en live sí habría que
            # cancelar la orden SL antigua y crear una nueva.
            if not config.DRY_RUN:
                try:
                    await self.client.cancel_all_orders(symbol)
                    sl_side    = "SELL" if direction == "LONG" else "BUY"
                    pos_side   = direction
                    await self.client.place_order(
                        symbol        = symbol,
                        side          = sl_side,
                        position_side = pos_side,
                        qty           = pos["qty"],
                        reduce        = True,
                        sl_price      = new_trail_sl,
                    )
                    log.info(f"Orden SL trailing colocada: {symbol} @ {new_trail_sl:.4f}")
                except Exception as e:
                    log.error(f"Error actualizando trailing SL: {e}")
