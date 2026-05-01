# -*- coding: utf-8 -*-
"""bot.py -- Three Step Bot v3.

Key improvements over v2:
  - Score-based signal prioritization (higher score enters first)
  - Circuit breaker: halts on daily loss or max trades
  - Bot-only position mode: ignores external positions
  - Rich /health endpoint with stats
  - Graceful SIGTERM shutdown
  - Min balance guard before every entry
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
from notifier import notify

# ── Logging ───────────────────────────────────────────────────────────────────
logger.remove()
logger.add(
    sys.stdout, level="INFO",
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}"
)


# ── Health endpoint ───────────────────────────────────────────────────────────
async def _health(_: web.Request) -> web.Response:
    stats = get_stats()
    return web.json_response({
        "status":        "halted" if stats["halted"] else "ok",
        "open_trades":   stats["open"],
        "daily_trades":  stats["daily_trades"],
        "daily_pnl":     stats["daily_pnl"],
        "symbols":       list(open_symbols()),
        "version":       "3.0",
    })


async def start_health_server() -> None:
    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/health", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", cfg.health_port)
    await site.start()
    logger.info(f"Health server on :{cfg.health_port}")


# ── Entry ─────────────────────────────────────────────────────────────────────
async def enter_trade(sig) -> None:
    if sig.symbol in open_symbols():
        return
    if trade_count() >= cfg.max_positions:
        logger.debug(f"[SKIP] {sig.symbol} — max positions ({cfg.max_positions})")
        return
    if is_halted():
        logger.warning(f"[SKIP] {sig.symbol} — bot halted (circuit breaker)")
        return

    size = max(cfg.trade_usdt, 5.0)
    required_margin = (size / cfg.leverage) * 1.3

    bal = await ex.get_balance()
    if bal < cfg.min_balance_usdt:
        logger.warning(f"[SKIP] {sig.symbol} — balance {bal:.2f} < min {cfg.min_balance_usdt}")
        return
    if bal < required_margin:
        logger.warning(f"[SKIP] {sig.symbol} — need {required_margin:.2f} USDT, have {bal:.2f}")
        return

    await ex.set_leverage(sig.symbol, cfg.leverage)
    await asyncio.sleep(0.3)

    resp = await ex.place_market_order(
        symbol=sig.symbol, side=sig.side,
        size_usdt=size, sl=sig.sl, tp=sig.tp,
    )
    code = resp.get("code", -1)
    if code not in (0, 200, None):
        logger.warning(f"[ORDER FAIL] {sig.symbol} code={code} {resp.get('msg','')}")
        return

    order_data = resp.get("data", {})
    if isinstance(order_data, dict):
        order_data = order_data.get("order", order_data)
    qty = float(order_data.get("executedQty", 0) or order_data.get("origQty", 0))
    if qty <= 0:
        qty = (size * cfg.leverage) / sig.price

    trade = Trade(
        symbol=sig.symbol, side=sig.side,
        entry=sig.price, sl=sig.sl, tp=sig.tp,
        atr=sig.atr, size_usdt=size, qty=qty,
        score=sig.score, order_id=str(order_data.get("orderId", "")),
        bot_opened=True,
    )
    add_trade(trade)

    stars = "⭐" * sig.score
    logger.success(
        f"[ENTRY] {sig.symbol} {sig.side} @ {sig.price:.6f} "
        f"SL={sig.sl:.6f} TP={sig.tp:.6f} score={sig.score}/5"
    )
    await notify(
        f"🚀 *[ENTRY]* {sig.symbol} `{sig.side}` {stars}\n"
        f"Price: `{sig.price:.6f}`\n"
        f"SL: `{sig.sl:.6f}` | TP: `{sig.tp:.6f}`\n"
        f"Size: `{size} USDT ×{cfg.leverage}` | Vol: `{sig.vol_ratio:.1f}x`\n"
        f"Δ1: `{sig.delta1:+.0f}` Δ2: `{sig.delta2:+.0f}` Score: `{sig.score}/5`"
    )


# ── Scan cycle ────────────────────────────────────────────────────────────────
async def scan_cycle() -> None:
    if is_halted():
        logger.warning("[HALTED] Skipping scan cycle — circuit breaker active")
        return

    symbols = cfg.symbols
    logger.info(
        f"Scanning {len(symbols)} symbols | TF={cfg.timeframe} "
        f"open={trade_count()}/{cfg.max_positions}"
    )

    ohlcv_map = await fetch_universe(symbols, cfg.timeframe, cfg.max_concurrent)
    await manage_positions(ohlcv_map)

    # Collect all signals, sort by score (best first)
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

    # Sort by score descending — best signals enter first
    signals.sort(key=lambda s: s.score, reverse=True)

    for sig in signals:
        if trade_count() >= cfg.max_positions:
            break
        logger.info(
            f"[SIGNAL] {sig.symbol} {sig.side} score={sig.score}/5 "
            f"vol={sig.vol_ratio:.1f}x | Δ1={sig.delta1:+.0f} Δ2={sig.delta2:+.0f}"
        )
        await enter_trade(sig)


# ── Main ──────────────────────────────────────────────────────────────────────
async def main_loop() -> None:
    await start_health_server()

    logger.info("=" * 62)
    logger.info("  THREE STEP BOT  v3.0  — Professional Edition")
    logger.info(f"  TF={cfg.timeframe} | Period={cfg.period} | ATR×{cfg.atr_mult} | RR={cfg.rr}")
    logger.info(f"  Trade={max(cfg.trade_usdt,5)}USDT ×{cfg.leverage} | MaxPos={cfg.max_positions}")
    logger.info(f"  Filters: vol≥{cfg.min_volume_mult}x | trend={cfg.trend_filter} | atr≥{cfg.min_atr_pct}%")
    logger.info(f"  Risk: maxLoss={cfg.max_daily_loss_pct}% | maxTrades={cfg.max_daily_trades}/day")
    logger.info(f"  Symbols ({len(cfg.symbols)}): {', '.join(cfg.symbols)}")
    logger.info("=" * 62)

    bal = await ex.get_balance()
    logger.info(f"  Balance: {bal:.2f} USDT")

    await notify(
        f"*Three Step Bot v3.0 Started* 🚀\n"
        f"TF: `{cfg.timeframe}` | RR: `1:{cfg.rr}` | ×`{cfg.leverage}`\n"
        f"Trade: `{max(cfg.trade_usdt,5)} USDT` | MaxPos: `{cfg.max_positions}`\n"
        f"Symbols: `{len(cfg.symbols)}` | Balance: `{bal:.2f} USDT`\n"
        f"Filters: vol≥`{cfg.min_volume_mult}x` | trend=`{cfg.trend_filter}`"
    )

    await sync_from_exchange()

    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def _shutdown(*_):
        logger.info("SIGTERM received — shutting down")
        stop_event.set()

    for s in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(s, _shutdown)

    while not stop_event.is_set():
        try:
            t0 = loop.time()
            await scan_cycle()
            elapsed = loop.time() - t0
            sleep_for = max(0, cfg.scan_interval - elapsed)
            logger.info(f"Cycle {elapsed:.1f}s | next in {sleep_for:.0f}s")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=sleep_for)
            except asyncio.TimeoutError:
                pass
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[LOOP ERROR] {e}")
            await notify(f"⚠️ Loop error: {e}")
            await asyncio.sleep(30)

    logger.info("Bot stopped")
    await ex.close_session()


if __name__ == "__main__":
    asyncio.run(main_loop())
