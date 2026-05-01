"""
THREE STEP FUTURE-TREND BOT v2.0
==================================
Mejoras vs v1.0:
  • SL y TP automáticos en exchange (no solo en bot)
  • Breakeven automático al 50% del recorrido
  • TP parcial 50% en TP1, trail en TP2
  • Notificación de TODAS las entradas y salidas con PnL real
  • 10× leverage configurable (LEVERAGE=10)
  • Resumen de stats cada hora
  • Health server para Railway
"""
from __future__ import annotations
import asyncio
import sys
import time

from loguru import logger
from aiohttp import web

from config import cfg
import client as ex
from scanner import fetch_universe
from strategy import get_signal
from pos_manager import (
    Trade, add_trade, open_symbols, trade_count,
    manage_positions, sync_from_exchange, get_stats,
)
from notifier import (
    start_notifier, notify, notify_entry, notify_stats,
)

logger.remove()
logger.add(
    sys.stdout, level="INFO",
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}"
)

_last_stats_ts: float = 0.0


# ── Health server ─────────────────────────────────────────────────────────────

async def _health(_: web.Request) -> web.Response:
    stats = get_stats()
    return web.json_response({
        "status":   "ok",
        "trades":   trade_count(),
        "wins":     stats["wins"],
        "losses":   stats["losses"],
        "total_pnl": stats["total_pnl"],
    })


async def start_health_server():
    app = web.Application()
    app.router.add_get("/",       _health)
    app.router.add_get("/health", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", cfg.health_port).start()
    logger.info(f"Health server :{cfg.health_port}")


# ── Entry execution ───────────────────────────────────────────────────────────

async def enter_trade(sig, ohlcv: dict) -> None:
    # Guard: ya en este símbolo
    if sig.symbol in open_symbols():
        return

    # Guard: max posiciones
    if trade_count() >= cfg.max_positions:
        logger.debug(f"[SKIP] max_positions={cfg.max_positions}")
        return

    # Verificar balance
    bal = await ex.get_balance()
    if bal < cfg.trade_usdt * 1.1:
        logger.warning(f"[SKIP] balance bajo: {bal:.2f} USDT")
        notify(
            f"⚠️ <b>Balance bajo: {bal:.2f} USDT</b>\n"
            f"Señal ignorada: <code>{sig.symbol}</code>\n"
            f"Recarga tu wallet de Futuros en BingX."
        )
        return

    # Configurar leverage
    await ex.set_leverage(sig.symbol, cfg.leverage)
    await asyncio.sleep(0.2)

    # Calcular TP2
    atr = sig.atr
    tp2 = (sig.price + cfg.tp2_mult * atr) if sig.side == "BUY" else (sig.price - cfg.tp2_mult * atr)

    # Colocar orden con SL y TP1 automáticos en exchange
    resp = await ex.place_market_order(
        symbol=sig.symbol, side=sig.side,
        size_usdt=cfg.trade_usdt,
        sl=sig.sl, tp=sig.tp,
        leverage=cfg.leverage,
    )

    code = resp.get("code", -1)
    if code not in (0, 200, None):
        logger.warning(f"[ORDER FAIL] {sig.symbol} code={code} {resp.get('msg','')}")
        return

    # Obtener qty ejecutada
    order_data = resp.get("data", {})
    if isinstance(order_data, dict):
        order_data = order_data.get("order", order_data)
    qty = float(order_data.get("executedQty", 0) or order_data.get("origQty", 0))
    if qty <= 0:
        # Fallback
        mark_data = await ex._get("/openApi/swap/v2/quote/price", {"symbol": sig.symbol})
        price = float(mark_data.get("data", {}).get("price", sig.price))
        qty = (cfg.trade_usdt * cfg.leverage) / price

    trade = Trade(
        symbol=sig.symbol, side=sig.side,
        entry=sig.price, sl=sig.sl,
        tp=sig.tp, tp2=tp2,
        atr=sig.atr,
        size_usdt=cfg.trade_usdt, qty=round(qty, 6),
        order_id=str(order_data.get("orderId", "")),
    )
    add_trade(trade)

    logger.success(
        f"[ENTRY] {sig.symbol} {sig.side} @ {sig.price:.6g} "
        f"SL={sig.sl:.6g} TP1={sig.tp:.6g} TP2={tp2:.6g} "
        f"qty={qty:.4f} {cfg.leverage}x"
    )

    # ── Notificación completa de entrada ──────────────────────────────────────
    notify_entry(
        symbol=sig.symbol,
        side=sig.side,
        price=sig.price,
        sl=sig.sl,
        tp1=sig.tp,
        tp2=tp2,
        size_usdt=cfg.trade_usdt,
        leverage=cfg.leverage,
        qty=round(qty, 4),
        delta1=sig.delta1,
        delta2=sig.delta2,
        delta3=sig.delta3,
        vol_ratio=sig.vol_ratio,
    )


# ── Scan loop ─────────────────────────────────────────────────────────────────

async def scan_cycle() -> None:
    symbols   = cfg.symbols
    logger.info(
        f"Scanning {len(symbols)} symbols | {cfg.timeframe} | "
        f"period={cfg.period} atr_mult={cfg.atr_mult} rr={cfg.rr} lev={cfg.leverage}x"
    )

    ohlcv_map = await fetch_universe(symbols, cfg.timeframe, cfg.max_concurrent)

    # Gestionar posiciones abiertas primero
    await manage_positions(ohlcv_map)

    # Buscar nuevas señales
    for sym, ohlcv in ohlcv_map.items():
        if sym in open_symbols():
            continue
        sig = get_signal(
            ohlcv, sym,
            period=cfg.period,
            atr_period=cfg.atr_period,
            atr_mult=cfg.atr_mult,
            rr=cfg.rr,
        )
        if sig:
            logger.info(
                f"[SIGNAL] {sym} {sig.side} | "
                f"D1={sig.delta1:+.0f} D2={sig.delta2:+.0f} D3={sig.delta3:+.0f} | "
                f"vol={sig.vol_ratio:.1f}×"
            )
            await enter_trade(sig, ohlcv)


# ── Stats periódicas ──────────────────────────────────────────────────────────

async def maybe_send_stats():
    global _last_stats_ts
    now = time.time()
    if now - _last_stats_ts >= 3600:   # cada hora
        _last_stats_ts = now
        notify_stats(get_stats())


# ── Main ──────────────────────────────────────────────────────────────────────

async def main_loop():
    await start_health_server()
    await start_notifier()

    mode = "🟡 TESTNET" if cfg.testnet else "🔴 LIVE"

    logger.info("=" * 60)
    logger.info("  THREE STEP FUTURE-TREND BOT  v2.0")
    logger.info(f"  {mode}")
    logger.info(f"  Symbols  : {cfg.symbols}")
    logger.info(f"  TF       : {cfg.timeframe}")
    logger.info(f"  Trade    : {cfg.trade_usdt} USDT × {cfg.leverage}x")
    logger.info(f"  Period   : {cfg.period} | ATR×{cfg.atr_mult} | RR={cfg.rr}")
    logger.info(f"  SL auto  : entry ± {cfg.atr_mult}×ATR")
    logger.info(f"  TP1 auto : entry ± {cfg.rr}×ATR (50%)")
    logger.info(f"  TP2 auto : entry ± {cfg.tp2_mult}×ATR (50%)")
    logger.info(f"  Breakeven: al {cfg.be_trigger:.0%} del recorrido")
    logger.info("=" * 60)

    notify(
        f"🚀 <b>Three Step Bot v2.0 — ONLINE</b>\n"
        f"{mode}\n"
        f"{'─'*30}\n"
        f"📊 Símbolos: <code>{', '.join(cfg.symbols)}</code>\n"
        f"⏱ TF: <code>{cfg.timeframe}</code> | Scan: cada <code>{cfg.scan_interval}s</code>\n"
        f"💰 Trade: <code>{cfg.trade_usdt} USDT × {cfg.leverage}x</code>\n"
        f"🛑 SL: <code>±{cfg.atr_mult}×ATR</code> automático en exchange\n"
        f"🎯 TP1: <code>±{cfg.rr}×ATR</code> (50%) | TP2: <code>±{cfg.tp2_mult}×ATR</code> (50%)\n"
        f"🔒 Breakeven: al <code>{cfg.be_trigger:.0%}</code> del camino\n"
        f"📐 Period: <code>{cfg.period}</code> | Max pos: <code>{cfg.max_positions}</code>"
    )

    # Sincronizar posiciones existentes
    await sync_from_exchange()

    while True:
        try:
            t0 = asyncio.get_event_loop().time()
            await scan_cycle()
            await maybe_send_stats()
            elapsed   = asyncio.get_event_loop().time() - t0
            sleep_for = max(0, cfg.scan_interval - elapsed)
            logger.info(f"Ciclo en {elapsed:.1f}s | próximo en {sleep_for:.0f}s")
            await asyncio.sleep(sleep_for)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[LOOP ERROR] {e}")
            notify(f"🆘 <b>BOT ERROR</b>\n<code>{str(e)[:300]}</code>")
            await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(main_loop())
