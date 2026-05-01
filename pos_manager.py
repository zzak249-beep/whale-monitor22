"""
THREE STEP BOT — pos_manager.py
=================================
Gestión completa de posiciones:
  • Trade dataclass con estado completo
  • Breakeven automático al llegar al 50% del recorrido TP1
  • TP parcial (50% en TP1, resto en TP2)
  • Trail con ATR
  • Notificación de TODAS las entradas y salidas con PnL real
  • Sincronización con el exchange al arrancar
"""
from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from loguru import logger

from config import cfg
import client as ex
from notifier import (
    notify_tp, notify_stop, notify_breakeven, notify_error,
)

# ── Estado global ─────────────────────────────────────────────────────────────
_TRADES:     Dict[str, "Trade"] = {}   # symbol → Trade
_CLOSED:     List[dict]         = []   # historial de trades cerrados
_STATS = {"wins": 0, "losses": 0, "total_pnl": 0.0}


@dataclass
class Trade:
    symbol:    str
    side:      str        # BUY | SELL
    entry:     float
    sl:        float
    tp:        float      # TP1
    tp2:       float      # TP2
    atr:       float
    size_usdt: float
    qty:       float
    order_id:  str = ""

    # Estado interno
    be_triggered:   bool  = False   # ¿ya se movió stop a breakeven?
    tp1_hit:        bool  = False   # ¿ya se cerró el 50% en TP1?
    qty_remaining:  float = 0.0     # qty que queda tras TP1 parcial
    open_time:      float = field(default_factory=time.time)
    high_water:     float = 0.0     # precio máximo favorable visto
    sl_current:     float = 0.0     # SL actual (puede haberse movido)

    def __post_init__(self):
        self.qty_remaining = self.qty
        self.sl_current    = self.sl
        self.high_water    = self.entry

    @property
    def is_long(self) -> bool:
        return self.side == "BUY"

    def calc_pnl(self, exit_price: float, qty: float | None = None) -> tuple[float, float]:
        """Retorna (pnl_usdt, pnl_pct)."""
        q      = qty if qty is not None else self.qty_remaining
        pnl    = (exit_price - self.entry) * q if self.is_long else (self.entry - exit_price) * q
        pnl_pct = pnl / (self.entry * q + 1e-10) * 100
        return round(pnl, 4), round(pnl_pct, 3)

    def sl_distance(self) -> float:
        return abs(self.entry - self.sl_current)

    def tp1_reached(self, high: float, low: float) -> bool:
        if self.tp1_hit:
            return False
        return (self.is_long and high >= self.tp) or (not self.is_long and low <= self.tp)

    def tp2_reached(self, high: float, low: float) -> bool:
        return (self.is_long and high >= self.tp2) or (not self.is_long and low <= self.tp2)

    def sl_hit(self, high: float, low: float) -> bool:
        return (self.is_long and low <= self.sl_current) or (not self.is_long and high >= self.sl_current)

    def should_breakeven(self, price: float) -> bool:
        """True cuando el precio avanzó ≥50% del recorrido entry→TP1."""
        if self.be_triggered:
            return False
        progress = abs(price - self.entry) / (abs(self.tp - self.entry) + 1e-10)
        return progress >= cfg.be_trigger - 1e-9

    def trail_stop(self, price: float) -> float:
        """
        Trail con ATR: actualiza sl_current si el precio avanzó más.
        Solo activo después del breakeven.
        """
        if not self.be_triggered:
            return self.sl_current
        if self.is_long:
            new_sl = price - self.atr * cfg.atr_mult
            if new_sl > self.sl_current:
                self.sl_current = new_sl
        else:
            new_sl = price + self.atr * cfg.atr_mult
            if new_sl < self.sl_current:
                self.sl_current = new_sl
        return self.sl_current


# ── API pública ───────────────────────────────────────────────────────────────

def add_trade(trade: Trade):
    _TRADES[trade.symbol] = trade
    logger.info(f"[POS] Registrado: {trade.symbol} {trade.side} qty={trade.qty}")


def open_symbols() -> List[str]:
    return list(_TRADES.keys())


def trade_count() -> int:
    return len(_TRADES)


def get_stats() -> dict:
    return {**_STATS, "open": trade_count()}


# ── Manage positions (llamado cada ciclo) ─────────────────────────────────────

async def manage_positions(ohlcv_map: Dict[str, dict]) -> None:
    """
    Revisa todas las posiciones abiertas contra las velas actuales.
    Lógica:
      1. Breakeven automático
      2. TP1 parcial (50% de qty)
      3. TP2 o stop loss (resto)
      4. Trail ATR post-breakeven
    """
    for sym in list(_TRADES.keys()):
        trade = _TRADES.get(sym)
        if not trade:
            continue
        ohlcv = ohlcv_map.get(sym)
        if not ohlcv or not ohlcv.get("candles"):
            continue

        candles = ohlcv["candles"]
        last    = candles[-1]
        price   = float(last["close"])
        high    = float(last["high"])
        low     = float(last["low"])

        try:
            await _process_trade(trade, price, high, low)
        except Exception as e:
            logger.error(f"[POS] Error en {sym}: {e}")
            notify_error(sym, str(e))


async def _process_trade(trade: Trade, price: float, high: float, low: float):
    sym = trade.symbol

    # ── Trail stop ────────────────────────────────────────────────────────────
    new_sl = trade.trail_stop(price)
    if new_sl != trade.sl_current:
        logger.debug(f"[TRAIL] {sym} SL → {new_sl:.6g}")

    # ── Breakeven ─────────────────────────────────────────────────────────────
    if not trade.be_triggered and trade.should_breakeven(price):
        trade.be_triggered = True
        trade.sl_current   = trade.entry
        logger.info(f"[BE] {sym} stop → breakeven @ {trade.entry:.6g}")
        notify_breakeven(sym, trade.entry)
        # Cancelar SL anterior y colocar nuevo en exchange
        await ex.cancel_all_orders(sym)
        # Recolocar SL en breakeven
        sl_side  = "SELL" if trade.is_long else "BUY"
        pos_side = "LONG" if trade.is_long else "SHORT"
        try:
            await ex._post("/openApi/swap/v2/trade/order", {
                "symbol":       sym,
                "side":         sl_side,
                "positionSide": pos_side,
                "type":         "STOP_MARKET",
                "quantity":     round(trade.qty_remaining, 4),
                "stopPrice":    round(trade.entry, 6),
                "workingType":  "MARK_PRICE",
            })
        except Exception as e:
            logger.warning(f"[BE] Recolocar SL error: {e}")

    # ── Stop loss hit ─────────────────────────────────────────────────────────
    if trade.sl_hit(high, low):
        exit_price = trade.sl_current
        pnl, pnl_pct = trade.calc_pnl(exit_price, trade.qty_remaining)

        logger.info(f"[SL] {sym} @ {exit_price:.6g} | PnL={pnl:+.4f} USDT ({pnl_pct:+.2f}%)")

        if not cfg.testnet:
            await ex.close_position(sym, "LONG" if trade.is_long else "SHORT", trade.qty_remaining)

        reason = "STOP LOSS" if pnl < 0 else "STOP (breakeven/profit)"
        notify_stop(
            symbol=sym, side=trade.side, stop_price=exit_price,
            entry=trade.entry, qty=trade.qty_remaining,
            pnl_usdt=pnl, pnl_pct=pnl_pct, reason=reason,
        )
        _record_close(trade, exit_price, pnl, "STOP")
        _TRADES.pop(sym, None)
        return

    # ── TP1 parcial ───────────────────────────────────────────────────────────
    if not trade.tp1_hit and trade.tp1_reached(high, low):
        qty_close = round(trade.qty * (cfg.partial_pct / 100), 4)
        exit_price = trade.tp

        pnl, pnl_pct = trade.calc_pnl(exit_price, qty_close)
        logger.info(f"[TP1] {sym} @ {exit_price:.6g} | qty={qty_close} | PnL={pnl:+.4f} USDT")

        if not cfg.testnet:
            await ex.close_position(sym, "LONG" if trade.is_long else "SHORT", qty_close)

        trade.tp1_hit       = True
        trade.qty_remaining = round(trade.qty - qty_close, 4)

        notify_tp(
            symbol=sym, side=trade.side, tp_num=1, tp_price=exit_price,
            entry=trade.entry, qty_closed=qty_close,
            pnl_usdt=pnl, pnl_pct=pnl_pct,
        )
        _record_close(trade, exit_price, pnl, "TP1", partial=True)
        return

    # ── TP2 (resto de la posición) ────────────────────────────────────────────
    if trade.tp1_hit and trade.tp2_reached(high, low):
        exit_price = trade.tp2
        pnl, pnl_pct = trade.calc_pnl(exit_price, trade.qty_remaining)

        logger.info(f"[TP2] {sym} @ {exit_price:.6g} | qty={trade.qty_remaining} | PnL={pnl:+.4f} USDT")

        if not cfg.testnet:
            await ex.close_position(sym, "LONG" if trade.is_long else "SHORT", trade.qty_remaining)

        notify_tp(
            symbol=sym, side=trade.side, tp_num=2, tp_price=exit_price,
            entry=trade.entry, qty_closed=trade.qty_remaining,
            pnl_usdt=pnl, pnl_pct=pnl_pct,
        )
        _record_close(trade, exit_price, pnl, "TP2")
        _TRADES.pop(sym, None)
        return


def _record_close(trade: Trade, exit_price: float, pnl: float, reason: str, partial: bool = False):
    """Registra cierre en historial y actualiza stats."""
    _CLOSED.append({
        "symbol":   trade.symbol,
        "side":     trade.side,
        "entry":    trade.entry,
        "exit":     exit_price,
        "pnl":      pnl,
        "reason":   reason,
        "partial":  partial,
        "time":     time.time(),
    })
    if not partial:
        _STATS["total_pnl"] = round(_STATS["total_pnl"] + pnl, 4)
        if pnl >= 0:
            _STATS["wins"] += 1
        else:
            _STATS["losses"] += 1


async def sync_from_exchange():
    """Al arrancar: sincroniza posiciones existentes en BingX."""
    try:
        positions = await ex.get_open_positions()
        for pos in positions:
            sym  = pos.get("symbol", "")
            size = float(pos.get("positionAmt", 0) or 0)
            if abs(size) < 1e-6 or sym in _TRADES:
                continue

            entry = float(pos.get("entryPrice", 0) or 0)
            side  = "BUY" if size > 0 else "SELL"
            pos_side_str = "LONG" if size > 0 else "SHORT"

            if entry <= 0:
                continue

            # Estimar ATR y stops desde posición existente
            mark = float(pos.get("markPrice", entry) or entry)
            liq  = float(pos.get("liquidationPrice", 0) or 0)
            # SL estimado: 80% del camino a liquidación
            sl_est = entry - (entry - liq) * 0.5 if size > 0 else entry + (liq - entry) * 0.5
            tp_est = entry + (entry - sl_est) * cfg.rr if size > 0 else entry - (sl_est - entry) * cfg.rr

            trade = Trade(
                symbol=sym, side=side,
                entry=entry, sl=sl_est,
                tp=tp_est, tp2=tp_est,
                atr=abs(entry - sl_est) / cfg.atr_mult,
                size_usdt=cfg.trade_usdt, qty=abs(size),
                order_id="synced",
            )
            _TRADES[sym] = trade
            logger.info(f"[SYNC] {sym} {pos_side_str} entry={entry:.4f} qty={abs(size):.4f}")

        logger.info(f"[SYNC] {len(_TRADES)} posiciones sincronizadas del exchange")
    except Exception as e:
        logger.warning(f"[SYNC] Error: {e}")
