# -*- coding: utf-8 -*-
"""bot.py -- Three Step Bot v4.

New in v4:
  - Uses rich notify_entry/notify_exit instead of generic notify()
  - SL/TP sanity check before placing order
  - Leverage hardcoded to 10x as default
  - Daily summary on startup
  - Score-sorted signal prioritization
  - Telegram connection test on startup (v4.1)
"""
from __future__ import annotations
import asyncio
import signal
import sys

from loguru import logger
from aiohttp import web

from config import cfg
import client as ex
from scanner import fetch_universe
from strategy import get_signal
from pos_manager import (
    Trade, add_trade, open_symbols, trade_count, is_halted,
    manage_positions, sync_from_exchange, get_stats,
)
import notifier

logger.remove()
logger.add(
    sys.stdout, level="INFO",
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}"
)


# ── Health endpoint ───────────────────────────────────────────────────────────
async def _health(_: web.Request) -> web.Response:
    stats = get_stats()
    return web.json_response({
        "status":       "halted" if stats["halted"] else "ok",
        "version":      "4.1",
        "open_trades":  stats["open"],
        "daily_trades": stats["daily_trades"],
        "daily_pnl":    stats["daily_pnl"],
        "daily_wins":   stats["daily_wins"],
        "daily_losses": stats["daily_losses"],
        "symbols":      list(open_symbols()),
    })


async def start_health_server() -> None:
    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/health", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", cfg.health_port)
    await site.start()
    logger.info(f"Health :{cfg.health_port}")


# ── Entry ─────────────────────────────────────────────────────────────────────
async def enter_trade(sig) -> None:
    if sig.symbol in open_symbols():
        return
    if trade_count() >= cfg.max_positions:
        logger.debug(f"[SKIP] {sig.symbol} -- max pos {cfg.max_positions}")
        return
    if is_halted():
        return

    size     = max(cfg.trade_usdt, 5.0)
    leverage = cfg.leverage  # 10x default
    margin   = (size / leverage) * 1.3

    bal = await ex.get_balance()
    if bal < cfg.min_balance_usdt or bal < margin:
        logger.warning(f"[SKIP] {sig.symbol} -- balance {bal:.2f} < {max(cfg.min_balance_usdt, margin):.2f}")
        return

    # SL/TP sanity check
    if sig.side == "BUY":
        if sig.sl >= sig.price or sig.tp <= sig.price:
            logger.warning(f"[SKIP] {sig.symbol} -- SL/TP invalido para BUY")
            return
    else:
        if sig.sl <= sig.price or sig.tp >= sig.price:
            logger.warning(f"[SKIP] {sig.symbol} -- SL/TP invalido para SELL")
            return

    sl_dist_pct = abs(sig.price - sig.sl) / sig.price * 100
    if sl_dist_pct < 0.1:
        logger.warning(f"[SKIP] {sig.symbol} -- SL muy cerca ({sl_dist_pct:.3f}%)")
        return

    await ex.set_leverage(sig.symbol, leverage)
    await asyncio.sleep(0.3)

    resp = await ex.place_market_order(
        symbol=sig.symbol, side=sig.side,
        size_usdt=size, sl=sig.sl, tp=sig.tp,
    )
    code = resp.get("code", -1)
    if code not in (0, 200, None):
        logger.warning(f"[FAIL] {sig.symbol} code={code} {resp.get('msg','')}")
        return

    order_data = resp.get("data", {})
    if isinstance(order_data, dict):
        order_data = order_data.get("order", order_data)
    qty = float(order_data.get("executedQty", 0) or order_data.get("origQty", 0))
    if qty <= 0:
        qty = (size * leverage) / sig.price

    trade = Trade(
        symbol=sig.symbol, side=sig.side,
        entry=sig.price, sl=sig.sl, tp=sig.tp,
        atr=sig.atr, size_usdt=size, leverage=leverage,
        qty=qty, score=sig.score, vol_ratio=sig.vol_ratio,
        delta1=sig.delta1, delta2=sig.delta2,
        order_id=str(order_data.get("orderId", "")),
        bot_opened=True,
    )
    add_trade(trade)

    logger.success(
        f"[ENTRY] {sig.symbol} {sig.side} @ {sig.price:.6f} "
        f"SL={sig.sl:.6f} TP={sig.tp:.6f} score={sig.score}/5"
    )
    await notifier.notify_entry(
        symbol=sig.symbol, side=sig.side, price=sig.price,
        sl=sig.sl, tp=sig.tp, size_usdt=size,
        leverage=leverage, qty=qty, score=sig.score,
        delta1=sig.delta1, delta2=sig.delta2, vol_ratio=sig.vol_ratio,
    )


# ── Scan ──────────────────────────────────────────────────────────────────────
async def scan_cycle() -> None:
    if is_halted():
        return

    symbols = cfg.symbols
    logger.info(
        f"Scan {len(symbols)} simbolos | TF={cfg.timeframe} "
        f"open={trade_count()}/{cfg.max_positions}"
    )

    ohlcv_map = await fetch_universe(symbols, cfg.timeframe, cfg.max_concurrent)
    await manage_positions(ohlcv_map)

    signals = []
    for sym, ohlcv in ohlcv_map.items():
        if sym in open_symbols():
            continue
        sig = get_signal(
            ohlcv, sym,
            period=cfg.period,
            atr_period=cfg.atr_period,
            atr_mult=cfg.atr_mult,
            rr=cfg.rr,
            min_volume_mult=cfg.min_volume_mult,
            min_atr_pct=cfg.min_atr_pct,
            trend_filter=cfg.trend_filter,
        )
        if sig:
            signals.append(sig)

    signals.sort(key=lambda s: s.score, reverse=True)

    for sig in signals:
        if trade_count() >= cfg.max_positions:
            break
        logger.info(
            f"[SIGNAL] {sig.symbol} {sig.side} score={sig.score}/5 "
            f"vol={sig.vol_ratio:.1f}x D1={sig.delta1:+.0f}"
        )
        await enter_trade(sig)


# ── Main ──────────────────────────────────────────────────────────────────────
async def main_loop() -> None:
    await start_health_server()

    logger.info("=" * 62)
    logger.info("  THREE STEP BOT v4.1")
    logger.info(f"  TF={cfg.timeframe} | ATR x{cfg.atr_mult} | RR=1:{cfg.rr} | x{cfg.leverage}")
    logger.info(f"  Trade={max(cfg.trade_usdt,5)}USDT | MaxPos={cfg.max_positions}")
    logger.info(f"  Simbolos: {len(cfg.symbols)}")
    logger.info("=" * 62)

    bal = await ex.get_balance()
    logger.info(f"  Balance: {bal:.2f} USDT")

    # Test Telegram connection before anything else
    await notifier.test_telegram()

    await notifier.notify(
        f"Three Step Bot v4.1 iniciado\n"
        f"TF: {cfg.timeframe} | RR: 1:{cfg.rr} | x{cfg.leverage}\n"
        f"Trade: {max(cfg.trade_usdt,5)} USDT | MaxPos: {cfg.max_positions}\n"
        f"Simbolos: {len(cfg.symbols)} | Balance: {bal:.2f} USDT"
    )

    await sync_from_exchange()

    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def _shutdown(*_):
        stop_event.set()

    for s in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(s, _shutdown)

    while not stop_event.is_set():
        try:
            t0 = loop.time()
            await scan_cycle()
            elapsed = loop.time() - t0
            sleep_for = max(0, cfg.scan_interval - elapsed)
            logger.info(f"Ciclo {elapsed:.1f}s | siguiente en {sleep_for:.0f}s")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=sleep_for)
            except asyncio.TimeoutError:
                pass
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[ERROR] {e}")
            await notifier.notify(f"Error: {e}")
            await asyncio.sleep(30)

    logger.info("Bot detenido")
    await ex.close_session()


if __name__ == "__main__":
    asyncio.run(main_loop())
