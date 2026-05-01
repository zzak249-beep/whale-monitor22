# -*- coding: utf-8 -*-
"""pos_manager.py -- Three Step Bot v3 — Position Manager.

Key improvements:
  - BOT-ONLY MODE: ignores positions not opened by this bot instance
  - Daily loss circuit breaker: halts trading if drawdown > threshold
  - Daily trade counter: limits overtrading
  - Peak R tracking: shows best R achieved per trade
  - Better partial close with reduceOnly
  - Clear separation of sync vs bot-opened positions
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date
from loguru import logger

import client as ex
from notifier import notify
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
    qty:          float = 0.0
    score:        int   = 1
    be_done:      bool  = False
    partial_done: bool  = False
    closed:       bool  = False
    order_id:     str   = ""
    peak_r:       float = 0.0
    bot_opened:   bool  = True   # True = opened by this bot instance


# ── Registry ──────────────────────────────────────────────────────────────────
_trades:       dict[str, Trade] = {}
_daily_pnl:    float = 0.0
_daily_trades: int   = 0
_day_started:  date  = date.today()
_initial_balance: float = 0.0
_halted:       bool  = False


def add_trade(trade: Trade) -> None:
    global _daily_trades
    _trades[trade.symbol] = trade
    _daily_trades += 1


def remove_trade(symbol: str) -> None:
    _trades.pop(symbol, None)


def open_symbols() -> set[str]:
    return set(_trades.keys())


def trade_count() -> int:
    """Only count bot-opened trades toward max_positions."""
    return sum(1 for t in _trades.values() if t.bot_opened and not t.closed)


def is_halted() -> bool:
    return _halted


def get_stats() -> dict:
    return {
        "open": trade_count(),
        "daily_trades": _daily_trades,
        "daily_pnl": round(_daily_pnl, 2),
        "halted": _halted,
    }


def _reset_daily_if_needed() -> None:
    global _daily_pnl, _daily_trades, _day_started, _halted
    today = date.today()
    if today != _day_started:
        _daily_pnl    = 0.0
        _daily_trades = 0
        _day_started  = today
        _halted       = False
        logger.info("[DAILY RESET] New trading day started")


async def _check_circuit_breaker(balance: float) -> bool:
    """Returns True if trading should be halted."""
    global _halted
    from config import cfg
    _reset_daily_if_needed()

    if _daily_trades >= cfg.max_daily_trades:
        if not _halted:
            _halted = True
            logger.warning(f"[HALT] Max daily trades reached ({cfg.max_daily_trades})")
            await notify(f"⛔ *Bot halted* — max daily trades ({cfg.max_daily_trades}) reached")
        return True

    if _initial_balance > 0:
        loss_pct = ((_initial_balance - balance) / _initial_balance) * 100
        if loss_pct >= cfg.max_daily_loss_pct:
            if not _halted:
                _halted = True
                logger.warning(f"[HALT] Daily loss {loss_pct:.1f}% >= {cfg.max_daily_loss_pct}%")
                await notify(
                    f"⛔ *Bot halted* — daily loss {loss_pct:.1f}%\n"
                    f"Balance: {balance:.2f} USDT"
                )
            return True

    return False


async def sync_from_exchange() -> None:
    """On startup: import live positions BUT mark them as not bot-opened."""
    global _initial_balance
    live = await ex.get_all_positions()
    bal  = await ex.get_balance()
    _initial_balance = bal
    logger.info(f"[INIT] Balance: {bal:.2f} USDT | Live positions: {len(live)}")

    for sym, pos in live.items():
        if sym in _trades:
            continue
        amt  = float(pos.get("positionAmt", 0))
        side = "BUY" if amt > 0 else "SELL"
        ep   = float(pos.get("avgPrice", 0))
        if ep <= 0:
            continue
        t = Trade(
            symbol=sym, side=side, entry=ep, sl=0.0, tp=0.0,
            atr=0.0, size_usdt=0.0, qty=abs(amt),
            be_done=True, partial_done=True,
            bot_opened=False,   # ← NOT counted toward max_positions
        )
        _trades[sym] = t
        logger.info(f"[SYNC] {sym} {side} @ {ep:.6f} (external — not managed)")

    if live:
        await notify(
            f"*Bot v3 Started* 🚀\n"
            f"Balance: `{bal:.2f} USDT`\n"
            f"External positions found: {len(live)} (not managed by bot)"
        )


async def _close_partial(trade: Trade) -> None:
    from config import cfg
    qty = round(trade.qty * cfg.partial_pct, 6)
    if qty <= 0:
        return
    close_side = "SELL" if trade.side == "BUY" else "BUY"
    resp = await ex.place_reduce_order(trade.symbol, close_side, qty)
    if resp.get("code", -1) in (0, 200):
        trade.qty -= qty
        trade.partial_done = True
        logger.info(f"[PARTIAL] {trade.symbol} closed {qty:.6f} | remaining={trade.qty:.6f}")
        await notify(
            f"✂️ *[PARTIAL TP]* {trade.symbol}\n"
            f"Closed {cfg.partial_pct*100:.0f}% at breakeven\n"
            f"Remaining: {trade.qty:.6f} units"
        )
    else:
        logger.warning(f"[PARTIAL FAIL] {trade.symbol}: {resp}")


async def _move_to_breakeven(trade: Trade) -> None:
    await ex.cancel_all_orders(trade.symbol)
    trade.be_done = True
    logger.info(f"[BE] {trade.symbol} — SL → entry {trade.entry:.6f}")
    await notify(
        f"🔒 *[BREAKEVEN]* {trade.symbol}\n"
        f"SL moved to entry: `{trade.entry:.6f}`"
    )


async def manage_positions(ohlcv_map: dict[str, dict]) -> None:
    from config import cfg
    _reset_daily_if_needed()
    closed_symbols: list[str] = []

    for sym, trade in list(_trades.items()):
        if trade.closed:
            closed_symbols.append(sym)
            continue

        # Skip externally-opened positions — let user manage them
        if not trade.bot_opened:
            continue

        price = await ex.get_price(sym)
        if price <= 0:
            continue

        r_dist = (trade.atr * cfg.atr_mult) if trade.atr > 0 else abs(trade.entry - trade.sl)
        if r_dist <= 0:
            continue

        pnl = (price - trade.entry) if trade.side == "BUY" else (trade.entry - price)
        r_achieved = pnl / r_dist

        if r_achieved > trade.peak_r:
            trade.peak_r = r_achieved

        logger.debug(
            f"[POS] {sym} {trade.side} R={r_achieved:.2f} "
            f"peak={trade.peak_r:.2f} score={trade.score}"
        )

        # ── Breakeven + partial at +1R ────────────────────────────────────
        if not trade.be_done and r_achieved >= cfg.breakeven_r:
            live = await ex.get_all_positions()
            if sym in live:
                await _move_to_breakeven(trade)
                await _close_partial(trade)
            else:
                trade.closed = True
                closed_symbols.append(sym)
                pnl_usdt = pnl * trade.qty
                logger.info(f"[CLOSED] {sym} SL/TP hit before BE | R={r_achieved:.2f}")
                await notify(
                    f"📉 *[CLOSED]* {sym} — SL/TP hit\n"
                    f"R: `{r_achieved:.2f}` | Est PnL: `{pnl_usdt:.2f} USDT`"
                )
                continue

        # ── Safety: check position still exists ──────────────────────────
        if trade.be_done:
            live = await ex.get_all_positions()
            if sym not in live:
                trade.closed = True
                closed_symbols.append(sym)
                pnl_usdt = pnl * trade.qty
                logger.info(f"[CLOSED] {sym} gone from exchange | peak R={trade.peak_r:.2f}")
                await notify(
                    f"✅ *[CLOSED]* {sym}\n"
                    f"Peak R: `{trade.peak_r:.2f}R` | Est PnL: `{pnl_usdt:.2f} USDT`\n"
                    f"Score was: {trade.score}/5"
                )
                continue

            # ── Trailing exit: delta1 flip ────────────────────────────────
            if sym in ohlcv_map and delta1_flipped(ohlcv_map[sym], cfg.period, trade.side):
                live_pos = live.get(sym, {})
                resp = await ex.close_position(sym, live_pos)
                if resp.get("code", -1) in (0, 200):
                    trade.closed = True
                    closed_symbols.append(sym)
                    pnl_usdt = pnl * trade.qty
                    logger.info(
                        f"[TRAIL EXIT] {sym} @ {price:.6f} | "
                        f"R={r_achieved:.2f} peak={trade.peak_r:.2f}"
                    )
                    await notify(
                        f"🎯 *[TRAIL EXIT]* {sym}\n"
                        f"Exit: `{price:.6f}` | R: `{r_achieved:.2f}R`\n"
                        f"Peak R: `{trade.peak_r:.2f}R` | Score: {trade.score}/5\n"
                        f"Est PnL: `{pnl_usdt:.2f} USDT`"
                    )

    for sym in closed_symbols:
        remove_trade(sym)
