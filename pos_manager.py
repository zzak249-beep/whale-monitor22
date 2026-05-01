# -*- coding: utf-8 -*-
"""pos_manager.py -- Three Step Bot v4 — Full Position Manager.

New in v4:
  - Full P&L calculation on every exit (USDT + %)
  - Exit reason tracking: TRAIL | SL | TP | MANUAL
  - Daily win/loss counter for summary
  - Rich Telegram on every lifecycle event
  - SL/TP validated before entry (price sanity check)
  - Bot-only mode: external positions ignored
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime
from loguru import logger

import client as ex
import notifier
from strategy import delta1_flipped


@dataclass
class Trade:
    symbol:       str
    side:         str
    entry:        float
    sl:           float
    tp:           float
    atr:          float
    size_usdt:    float
    leverage:     int   = 10
    qty:          float = 0.0
    score:        int   = 1
    vol_ratio:    float = 1.0
    delta1:       float = 0.0
    delta2:       float = 0.0
    be_done:      bool  = False
    partial_done: bool  = False
    closed:       bool  = False
    order_id:     str   = ""
    peak_r:       float = 0.0
    bot_opened:   bool  = True
    opened_at:    str   = field(default_factory=lambda: datetime.utcnow().strftime("%H:%M UTC"))


# ── Registry + daily stats ────────────────────────────────────────────────────
_trades:          dict[str, Trade] = {}
_daily_pnl:       float = 0.0
_daily_trades:    int   = 0
_daily_wins:      int   = 0
_daily_losses:    int   = 0
_day_started:     date  = date.today()
_initial_balance: float = 0.0
_halted:          bool  = False


def add_trade(trade: Trade) -> None:
    global _daily_trades
    _trades[trade.symbol] = trade
    _daily_trades += 1


def remove_trade(symbol: str) -> None:
    _trades.pop(symbol, None)


def open_symbols() -> set[str]:
    return set(_trades.keys())


def trade_count() -> int:
    return sum(1 for t in _trades.values() if t.bot_opened and not t.closed)


def is_halted() -> bool:
    return _halted


def get_stats() -> dict:
    return {
        "open":          trade_count(),
        "daily_trades":  _daily_trades,
        "daily_pnl":     round(_daily_pnl, 4),
        "daily_wins":    _daily_wins,
        "daily_losses":  _daily_losses,
        "halted":        _halted,
    }


def _reset_daily_if_needed() -> None:
    global _daily_pnl, _daily_trades, _daily_wins, _daily_losses
    global _day_started, _halted
    today = date.today()
    if today != _day_started:
        logger.info(f"[DAILY RESET] PnL={_daily_pnl:+.4f} W={_daily_wins} L={_daily_losses}")
        _daily_pnl     = 0.0
        _daily_trades  = 0
        _daily_wins    = 0
        _daily_losses  = 0
        _day_started   = today
        _halted        = False


def _record_exit(pnl_usdt: float) -> None:
    global _daily_pnl, _daily_wins, _daily_losses
    _daily_pnl += pnl_usdt
    if pnl_usdt >= 0:
        _daily_wins   += 1
    else:
        _daily_losses += 1


def _calc_pnl(trade: Trade, exit_price: float) -> tuple[float, float]:
    """Returns (pnl_pct, pnl_usdt)."""
    pnl_pct = ((exit_price - trade.entry) / trade.entry * 100) \
              if trade.side == "BUY" \
              else ((trade.entry - exit_price) / trade.entry * 100)
    pnl_usdt = (pnl_pct / 100) * trade.size_usdt * trade.leverage
    return round(pnl_pct, 4), round(pnl_usdt, 4)


async def _check_circuit_breaker() -> bool:
    global _halted
    from config import cfg
    _reset_daily_if_needed()

    if _daily_trades >= cfg.max_daily_trades:
        if not _halted:
            _halted = True
            logger.warning(f"[HALT] Max daily trades {cfg.max_daily_trades}")
            await notifier.notify(
                f"⛔ *Bot pausado* — máximo de trades diarios ({cfg.max_daily_trades}) alcanzado\n"
                f"PnL hoy: `{_daily_pnl:+.4f} USDT`"
            )
        return True

    if _initial_balance > 0:
        loss_pct = ((_initial_balance - (_initial_balance + _daily_pnl)) / _initial_balance) * 100
        if loss_pct >= cfg.max_daily_loss_pct:
            if not _halted:
                _halted = True
                logger.warning(f"[HALT] Daily loss {loss_pct:.2f}%")
                await notifier.notify(
                    f"⛔ *Bot pausado* — pérdida diaria `{loss_pct:.2f}%`\n"
                    f"PnL hoy: `{_daily_pnl:+.4f} USDT`"
                )
            return True
    return False


# ── Startup sync ──────────────────────────────────────────────────────────────

async def sync_from_exchange() -> None:
    global _initial_balance
    live = await ex.get_all_positions()
    bal  = await ex.get_balance()
    _initial_balance = bal

    logger.info(f"[INIT] Balance={bal:.2f} USDT | External positions={len(live)}")

    for sym, pos in live.items():
        if sym in _trades:
            continue
        amt  = float(pos.get("positionAmt", 0))
        side = "BUY" if amt > 0 else "SELL"
        ep   = float(pos.get("avgPrice", 0))
        if ep <= 0:
            continue
        t = Trade(
            symbol=sym, side=side, entry=ep,
            sl=0.0, tp=0.0, atr=0.0, size_usdt=0.0,
            qty=abs(amt), be_done=True, partial_done=True,
            bot_opened=False,
        )
        _trades[sym] = t
        logger.info(f"[SYNC] {sym} {side} @ {ep:.6f} — externo, no gestionado")

    await notifier.notify(
        f"*Bot v4 iniciado* 🚀\n"
        f"Balance: `{bal:.2f} USDT`\n"
        f"Posiciones externas: `{len(live)}` (no gestionadas)"
    )


# ── Position lifecycle ────────────────────────────────────────────────────────

async def _close_partial(trade: Trade, current_price: float) -> None:
    from config import cfg
    qty = round(trade.qty * cfg.partial_pct, 6)
    if qty <= 0:
        return
    close_side = "SELL" if trade.side == "BUY" else "BUY"
    resp = await ex.place_reduce_order(trade.symbol, close_side, qty)
    if resp.get("code", -1) in (0, 200):
        _, pnl_usdt = _calc_pnl(trade, current_price)
        pnl_partial = pnl_usdt * cfg.partial_pct
        trade.qty -= qty
        trade.partial_done = True
        logger.info(f"[PARTIAL] {trade.symbol} -{qty:.6f} | PnL parcial ≈{pnl_partial:+.4f} USDT")
        await notifier.notify_partial(
            symbol=trade.symbol,
            qty_closed=qty,
            qty_remaining=trade.qty,
            price=current_price,
            pnl_usdt=pnl_partial,
        )
    else:
        logger.warning(f"[PARTIAL FAIL] {trade.symbol}: {resp}")


async def _move_to_breakeven(trade: Trade, r_at_be: float) -> None:
    await ex.cancel_all_orders(trade.symbol)
    trade.be_done = True
    logger.info(f"[BE] {trade.symbol} SL→entry {trade.entry:.6f}")
    await notifier.notify_breakeven(
        symbol=trade.symbol,
        side=trade.side,
        entry=trade.entry,
        r_at_be=r_at_be,
    )


async def _do_exit(trade: Trade, live_pos: dict, exit_price: float,
                   r_achieved: float, reason: str) -> bool:
    """Execute close and send full exit notification. Returns True if closed."""
    resp = await ex.close_position(trade.symbol, live_pos)
    if resp.get("code", -1) not in (0, 200):
        logger.warning(f"[CLOSE FAIL] {trade.symbol}: {resp}")
        return False

    _, pnl_usdt = _calc_pnl(trade, exit_price)
    _record_exit(pnl_usdt)
    trade.closed = True

    logger.info(
        f"[EXIT:{reason}] {trade.symbol} @ {exit_price:.6f} | "
        f"R={r_achieved:.2f} | PnL={pnl_usdt:+.4f} USDT"
    )
    await notifier.notify_exit(
        symbol=trade.symbol,
        side=trade.side,
        entry=trade.entry,
        exit_price=exit_price,
        qty=trade.qty,
        size_usdt=trade.size_usdt,
        leverage=trade.leverage,
        r_achieved=r_achieved,
        peak_r=trade.peak_r,
        exit_reason=reason,
    )

    # Daily summary every 5 closed trades
    if (_daily_wins + _daily_losses) % 5 == 0 and (_daily_wins + _daily_losses) > 0:
        bal = await ex.get_balance()
        await notifier.notify_daily_summary(
            total_trades=_daily_trades,
            wins=_daily_wins,
            losses=_daily_losses,
            net_pnl=_daily_pnl,
            balance=bal,
        )

    return True


async def manage_positions(ohlcv_map: dict[str, dict]) -> None:
    from config import cfg
    _reset_daily_if_needed()
    await _check_circuit_breaker()
    closed_syms: list[str] = []

    for sym, trade in list(_trades.items()):
        if trade.closed:
            closed_syms.append(sym)
            continue
        if not trade.bot_opened:
            continue

        price = await ex.get_price(sym)
        if price <= 0:
            continue

        r_dist = (trade.atr * cfg.atr_mult) if trade.atr > 0 else abs(trade.entry - trade.sl)
        if r_dist <= 0:
            continue

        pnl     = (price - trade.entry) if trade.side == "BUY" else (trade.entry - price)
        r_now   = pnl / r_dist
        if r_now > trade.peak_r:
            trade.peak_r = r_now

        logger.debug(f"[POS] {sym} {trade.side} price={price:.6f} R={r_now:.2f} peak={trade.peak_r:.2f}")

        # ── Breakeven + partial at +1R ────────────────────────────────────
        if not trade.be_done and r_now >= cfg.breakeven_r:
            live = await ex.get_all_positions()
            if sym not in live:
                # Hit SL before reaching BE — exchange closed it
                _, pnl_usdt = _calc_pnl(trade, price)
                _record_exit(pnl_usdt)
                trade.closed = True
                closed_syms.append(sym)
                await notifier.notify_exit(
                    symbol=sym, side=trade.side,
                    entry=trade.entry, exit_price=price,
                    qty=trade.qty, size_usdt=trade.size_usdt, leverage=trade.leverage,
                    r_achieved=r_now, peak_r=trade.peak_r, exit_reason="SL",
                )
                continue
            await _move_to_breakeven(trade, r_now)
            await _close_partial(trade, price)

        # ── After BE: check position still alive ─────────────────────────
        if trade.be_done:
            live = await ex.get_all_positions()

            if sym not in live:
                # Exchange closed it (TP hit or manual)
                reason = "TP" if r_now >= cfg.rr * 0.9 else "MANUAL"
                _, pnl_usdt = _calc_pnl(trade, price)
                _record_exit(pnl_usdt)
                trade.closed = True
                closed_syms.append(sym)
                await notifier.notify_exit(
                    symbol=sym, side=trade.side,
                    entry=trade.entry, exit_price=price,
                    qty=trade.qty, size_usdt=trade.size_usdt, leverage=trade.leverage,
                    r_achieved=r_now, peak_r=trade.peak_r, exit_reason=reason,
                )
                continue

            # ── Trailing: delta1 flip ─────────────────────────────────────
            if sym in ohlcv_map and delta1_flipped(ohlcv_map[sym], cfg.period, trade.side):
                closed = await _do_exit(trade, live[sym], price, r_now, "TRAIL")
                if closed:
                    closed_syms.append(sym)

    for sym in closed_syms:
        remove_trade(sym)
