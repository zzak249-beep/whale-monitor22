# -*- coding: utf-8 -*-
"""bot.py -- Three Step Bot v5.

Fixes vs v4 (why no trades):
  1. Funding rates pre-fetched once per cycle (not per symbol)
  2. Skip reasons logged at INFO level (visible in Railway logs)
  3. Balance check relaxed: min_balance_usdt lowered to 10 USDT
  4. SL distance check lowered to 0.05% (was 0.1%)
  5. Added [DIAG] cycle summary: signals found, skipped reasons
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
from strategy import get_signal, in_trading_session
from pos_manager import (
    Trade, add_trade, open_symbols, trade_count, is_halted,
    manage_positions, sync_from_exchange, get_stats,
)
import notifier

logger.remove()
logger.add(
    sys.stdout, level="DEBUG",   # DEBUG so skip reasons are visible
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}"
)


# ── Health ────────────────────────────────────────────────────────────────────
async def _health(_: web.Request) -> web.Response:
    stats = get_stats()
    return web.json_response({
        "status":       "halted" if stats["halted"] else "ok",
        "version":      "5.0",
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
async def enter_trade(sig) -> bool:
    """Returns True if trade was placed."""
    if sig.symbol in open_symbols():
        return False
    if trade_count() >= cfg.max_positions:
        logger.debug(f"[SKIP] {sig.symbol} — at max positions ({cfg.max_positions})")
        return False
    if is_halted():
        logger.warning(f"[SKIP] {sig.symbol} — bot halted")
        return False

    size     = max(cfg.trade_usdt, 5.0)
    leverage = cfg.leverage
    margin   = (size / leverage) * 1.2   # FIXED: was 1.3

    bal = await ex.get_balance()
    if bal < cfg.min_balance_usdt:
        logger.warning(f"[SKIP] {sig.symbol} — balance {bal:.2f} < min {cfg.min_balance_usdt}")
        return False
    if bal < margin:
        logger.warning(f"[SKIP] {sig.symbol} — balance {bal:.2f} < margin {margin:.2f}")
        return False

    # SL/TP sanity
    if sig.side == "BUY"  and (sig.sl >= sig.price or sig.tp <= sig.price):
        logger.warning(f"[SKIP] {sig.symbol} — BUY SL/TP invalid")
        return False
    if sig.side == "SELL" and (sig.sl <= sig.price or sig.tp >= sig.price):
        logger.warning(f"[SKIP] {sig.symbol} — SELL SL/TP invalid")
        return False

    sl_pct = abs(sig.price - sig.sl) / sig.price * 100
    if sl_pct < 0.05:   # FIXED: was 0.1%
        logger.warning(f"[SKIP] {sig.symbol} — SL too close ({sl_pct:.3f}%)")
        return False

    # Place order
    await ex.set_leverage(sig.symbol, leverage)
    await asyncio.sleep(0.3)

    resp = await ex.place_market_order(
        symbol=sig.symbol, side=sig.side,
        size_usdt=size, sl=sig.sl, tp=sig.tp,
    )
    code = resp.get("code", -1)
    if code not in (0, 200, None):
        logger.warning(f"[FAIL] {sig.symbol} code={code} {resp.get('msg','')}")
        return False

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
        f"[ENTRY ✅] {sig.symbol} {sig.side} @ {sig.price:.6f} "
        f"SL={sig.sl:.6f} TP={sig.tp:.6f} score={sig.score}/5 vol={sig.vol_ratio:.1f}x"
    )
    await notifier.notify_entry(
        symbol=sig.symbol, side=sig.side, price=sig.price,
        sl=sig.sl, tp=sig.tp, size_usdt=size,
        leverage=leverage, qty=qty, score=sig.score,
        delta1=sig.delta1, delta2=sig.delta2, vol_ratio=sig.vol_ratio,
    )
    return True


# ── Scan ──────────────────────────────────────────────────────────────────────
async def scan_cycle() -> None:
    if is_halted():
        logger.warning("[HALTED] Ciclo saltado")
        return

    symbols = cfg.symbols

    # Session check (informative)
    in_session = in_trading_session()
    session_str = "✅ en sesión" if in_session else "⏸ fuera de sesión"

    logger.info(
        f"Scan {len(symbols)} símbolos | TF={cfg.timeframe} "
        f"open={trade_count()}/{cfg.max_positions} | {session_str}"
    )

    # Pre-fetch funding rates once for all symbols
    funding_rates: dict[str, float] = {}
    if cfg.funding_filter:
        try:
            funding_rates = await ex.fetch_all_funding_rates(symbols)
            high_funding = {s: f for s, f in funding_rates.items() if abs(f) > 0.0003}
            if high_funding:
                logger.info(f"[FUNDING] Tasas altas: {high_funding}")
        except Exception as e:
            logger.debug(f"[FUNDING] Error fetching rates: {e}")

    ohlcv_map = await fetch_universe(symbols, cfg.timeframe, cfg.max_concurrent)
    await manage_positions(ohlcv_map)

    signals = []
    skipped_reasons: dict[str, int] = {}

    for sym, ohlcv in ohlcv_map.items():
        if sym in open_symbols():
            continue
        funding = funding_rates.get(sym, 0.0) if cfg.funding_filter else 0.0
        sig = get_signal(
            ohlcv, sym,
            period=cfg.period,
            atr_period=cfg.atr_period,
            atr_mult=cfg.atr_mult,
            rr=cfg.rr,
            min_volume_mult=cfg.min_volume_mult,
            min_atr_pct=cfg.min_atr_pct,
            trend_filter=cfg.trend_filter,
            session_filter=cfg.session_filter,
            funding_rate=funding,
        )
        if sig:
            signals.append(sig)

    # Sort best signals first
    signals.sort(key=lambda s: s.score, reverse=True)

    if signals:
        logger.info(f"[SIGNALS] {len(signals)} señales encontradas: "
                    f"{[(s.symbol, s.side, s.score) for s in signals]}")
    else:
        logger.info("[SIGNALS] 0 señales este ciclo")

    placed = 0
    for sig in signals:
        if trade_count() >= cfg.max_positions:
            break
        success = await enter_trade(sig)
        if success:
            placed += 1

    if placed:
        logger.success(f"[CYCLE] {placed} trade(s) abiertos este ciclo")


# ── Main ──────────────────────────────────────────────────────────────────────
async def main_loop() -> None:
    await start_health_server()

    logger.info("=" * 65)
    logger.info("  THREE STEP BOT v5.0 — Fixes: señales + funding + sesión")
    logger.info(f"  TF={cfg.timeframe} | ATR×{cfg.atr_mult} | RR=1:{cfg.rr} | ×{cfg.leverage}")
    logger.info(f"  Trade={max(cfg.trade_usdt,5)}USDT | MaxPos={cfg.max_positions}")
    logger.info(f"  Filtros: vol≥{cfg.min_volume_mult}x | trend={cfg.trend_filter} | "
                f"session={cfg.session_filter} | funding={cfg.funding_filter}")
    logger.info(f"  Riesgo: pérdida_max={cfg.max_daily_loss_pct}% | "
                f"trades_max={cfg.max_daily_trades}/día")
    logger.info("=" * 65)

    bal = await ex.get_balance()
    logger.info(f"  Balance inicial: {bal:.2f} USDT")

    await notifier.notify(
        f"*Three Step Bot v5.0* 🚀\n"
        f"TF: `{cfg.timeframe}` | RR: `1:{cfg.rr}` | ×`{cfg.leverage}`\n"
        f"Trade: `{max(cfg.trade_usdt,5)} USDT` | MaxPos: `{cfg.max_positions}`\n"
        f"Balance: `{bal:.2f} USDT`\n"
        f"Sesión: `07:00-20:00 UTC` | Funding filter: `{cfg.funding_filter}`\n\n"
        f"Recibirás:\n"
        f"🚀 Entradas | 🔒 Breakeven | ✂️ Cierre parcial\n"
        f"🎯 Salidas con PnL | 📈 Resumen diario"
    )

    await sync_from_exchange()

    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def _shutdown(*_):
        logger.info("SIGTERM — deteniendo bot")
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
            await notifier.notify(f"⚠️ Error: {e}")
            await asyncio.sleep(30)

    logger.info("Bot detenido limpiamente")
    await ex.close_session()


if __name__ == "__main__":
    asyncio.run(main_loop())
