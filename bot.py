# -*- coding: utf-8 -*-
"""
Maki Bot PRO — Motor de ejecución
══════════════════════════════════
Estrategia: ZigZag++ 15m · HMA · Volume Delta · ATR dinámico
"""
import asyncio
import logging
import os
import sys
import signal as _signal
from datetime import datetime, timezone

from bingx    import BingXClient
import strategy as _strategy
get_signal   = _strategy.signal
qty_by_risk  = _strategy.qty_by_risk
risk_reward  = _strategy.risk_reward
from telegram import TelegramNotifier
from risk     import RiskManager

# ── logging ───────────────────────────────────────────────────────── #
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("bot")


# ── config desde variables de entorno ─────────────────────────────── #
def _env(key: str, default: str = None) -> str:
    v = os.environ.get(key, default)
    if v is None:
        logger.critical(f"Variable requerida: {key}")
        sys.exit(1)
    return v

API_KEY       = _env("BINGX_API_KEY")
API_SECRET    = _env("BINGX_API_SECRET")
TG_TOKEN      = _env("TELEGRAM_BOT_TOKEN")
TG_CHAT       = _env("TELEGRAM_CHAT_ID")

TRADE_USDT    = float(_env("TRADE_AMOUNT_USDT",    "10"))
MAX_TRADES    = int(_env("MAX_OPEN_TRADES",          "3"))
SCAN_SECS     = int(_env("SCAN_INTERVAL_SECONDS",   "60"))
TOP_N         = int(_env("TOP_N_SYMBOLS",           "20"))
LEVERAGE      = int(_env("LEVERAGE",                 "5"))
MIN_BAL       = float(_env("MIN_BALANCE_USDT",      "20"))
COOLDOWN_S    = int(_env("COOLDOWN_SECONDS",        "300"))
HEARTBEAT_MIN = int(_env("HEARTBEAT_MINUTES",       "60"))
MAX_DD_USDT   = float(_env("MAX_DAILY_LOSS_USDT",  "30"))
MAX_SAME_DIR  = int(_env("MAX_SAME_DIRECTION",       "2"))


# ── estado global ─────────────────────────────────────────────────── #
open_trades: dict = {}   # symbol → trade dict
cooldowns:   dict = {}
_shutdown         = False


# ── helpers ───────────────────────────────────────────────────────── #

def _now() -> float:
    return datetime.now(timezone.utc).timestamp()

def _in_cooldown(sym: str) -> bool:
    return (_now() - cooldowns.get(sym, 0)) < COOLDOWN_S

def _pnl(trade: dict, exit_price: float) -> float:
    if trade["side"] in ("BUY", "LONG"):
        return (exit_price - trade["entry"]) * trade["qty"]
    return (trade["entry"] - exit_price) * trade["qty"]


# ── monitor posiciones ────────────────────────────────────────────── #

async def monitor(client: BingXClient, tg: TelegramNotifier, rm: RiskManager):
    if not open_trades:
        return

    try:
        positions = await client.get_open_positions()
        open_syms = {p["symbol"] for p in positions}
    except Exception as e:
        logger.warning(f"monitor positions: {e}")
        open_syms = set(open_trades.keys())

    prices = await client.prices_multi(list(open_trades.keys()))

    for sym in list(open_trades.keys()):
        trade = open_trades[sym]
        side  = trade["side"]
        price = prices.get(sym, 0)

        # Cerrada por BingX (TP/SL automático)
        if sym not in open_syms:
            if price <= 0:
                price = trade["entry"]
            pnl    = _pnl(trade, price)
            hit_tp = (price >= trade["tp"]) if side in ("BUY","LONG") else (price <= trade["tp"])
            reason = "TP ✅" if hit_tp else "SL ❌"
            icon   = "🟢" if hit_tp else "🔴"
            del open_trades[sym]
            cooldowns[sym] = _now()
            rm.register_close(pnl)
            logger.info(f"{sym} cerrado externamente {reason} pnl={pnl:+.2f}")
            await tg.notify(
                f"{icon} *{reason} — {sym}*\n"
                f"Dir: `{side}` | `{trade['entry']:.5f}` → `{price:.5f}`\n"
                f"PnL: `{pnl:+.2f} USDT`"
            )
            continue

        if price <= 0:
            continue

        # Trailing stop → break-even
        new_sl = rm.check_trailing(trade, price)
        if new_sl is not None:
            ok = await client.update_sl(sym, side, trade["qty"], new_sl)
            if ok:
                open_trades[sym]["sl"]           = new_sl
                open_trades[sym]["be_activated"] = True
                await tg.notify(
                    f"🔒 *Break-even — {sym}*\n"
                    f"SL movido a `{new_sl:.5f}` (entrada protegida)"
                )

        # TP/SL manual como respaldo
        hit_tp = price >= trade["tp"] if side in ("BUY","LONG") else price <= trade["tp"]
        hit_sl = price <= trade["sl"] if side in ("BUY","LONG") else price >= trade["sl"]

        if hit_tp or hit_sl:
            reason = "TP ✅" if hit_tp else "SL ❌"
            icon   = "🟢" if hit_tp else "🔴"
            pnl    = _pnl(trade, price)
            try:
                await client.close_position(sym, side, trade["qty"])
            except Exception as e:
                logger.error(f"close_position {sym}: {e}")
            del open_trades[sym]
            cooldowns[sym] = _now()
            rm.register_close(pnl)
            logger.info(f"{sym} cerrado manual {reason} pnl={pnl:+.2f}")
            await tg.notify(
                f"{icon} *{reason} — {sym}*\n"
                f"Dir: `{side}` | `{trade['entry']:.5f}` → `{price:.5f}`\n"
                f"PnL: `{pnl:+.2f} USDT`"
            )


# ── scan paralelo ─────────────────────────────────────────────────── #

async def _fetch_symbol(client: BingXClient, sym: str) -> tuple:
    try:
        c15, _ = await client.klines_multi(sym)
        return sym, c15
    except Exception as e:
        logger.debug(f"fetch {sym}: {e}")
        return sym, None


async def scan(client: BingXClient, tg: TelegramNotifier, rm: RiskManager, symbols: list):
    if len(open_trades) >= MAX_TRADES:
        return

    can, reason = rm.can_trade(len(open_trades))
    if not can:
        logger.info(f"Scan omitido: {reason}")
        return

    if not RiskManager.is_safe_time():
        return

    try:
        balance = await client.balance_usdt()
    except Exception:
        balance = MIN_BAL + 1

    if balance < MIN_BAL:
        logger.warning(f"Balance {balance:.2f} < {MIN_BAL} — scan pausado")
        return

    candidates = [s for s in symbols if s not in open_trades and not _in_cooldown(s)]
    if not candidates:
        return

    # Descarga paralela
    tasks   = [_fetch_symbol(client, sym) for sym in candidates]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    for sym, c15 in results:
        if _shutdown or len(open_trades) >= MAX_TRADES:
            break
        if not c15 or len(c15) < 120:
            continue

        sig = get_signal(c15)
        if sig is None:
            continue

        if not rm.direction_ok(open_trades, sig["side"], MAX_SAME_DIR):
            continue

        try:
            price = await client.last_price(sym)
        except Exception:
            price = c15[-1]["c"]
        if price <= 0:
            continue

        tp  = sig["tp"]
        sl  = sig["sl"]
        qty = rm.calc_qty(price)
        if qty <= 0:
            continue

        rr = sig["rr"]
        logger.info(f"SEÑAL: {sym} {sig['side']} @ {price:.5f} TP={tp:.5f} SL={sl:.5f} R:R={rr}")

        try:
            order_id = await client.open_order(sym, sig["side"], qty, tp, sl, LEVERAGE)
        except Exception as e:
            logger.error(f"open_order {sym}: {e}")
            await tg.notify(f"⚠️ Error abriendo `{sym}`:\n`{str(e)[:180]}`")
            continue

        open_trades[sym] = {
            "symbol":       sym,
            "side":         sig["side"],
            "entry":        price,
            "tp":           tp,
            "sl":           sl,
            "qty":          qty,
            "order_id":     order_id,
            "be_activated": False,
            "ts":           _now(),
        }

        emoji = "📈" if sig["side"] in ("BUY","LONG") else "📉"
        reasons = " · ".join(sig.get("reasons", []))
        await tg.notify(
            f"{emoji} *{sig['side']} — {sym}*\n"
            f"Entrada: `{price:.5f}`\n"
            f"TP: `{tp:.5f}` | SL: `{sl:.5f}`\n"
            f"R:R `{rr}:1` | Qty: `{qty}` | `{LEVERAGE}x`\n"
            f"_{reasons}_"
        )
        await asyncio.sleep(0.3)


# ── heartbeat ─────────────────────────────────────────────────────── #

async def heartbeat(client: BingXClient, tg: TelegramNotifier, rm: RiskManager):
    try:
        balance = await client.balance_usdt()
        st      = rm.status()
        detalle = "\n".join(
            f"  • `{s}` {t['side']} @ `{t['entry']:.5f}`"
            + (" 🔒" if t.get("be_activated") else "")
            for s, t in open_trades.items()
        ) or "  _Sin trades_"
        await tg.notify(
            f"💓 *Heartbeat — Maki Bot PRO*\n"
            f"Balance: `{balance:.2f} USDT`\n"
            f"Trades: `{len(open_trades)}/{MAX_TRADES}`\n"
            f"PnL hoy: `{st['daily_pnl']:+.2f} USDT` | Trades hoy: `{st['trades_today']}`\n"
            f"{detalle}"
        )
    except Exception as e:
        logger.warning(f"heartbeat: {e}")


# ── shutdown ──────────────────────────────────────────────────────── #

def _on_shutdown(signum, frame):
    global _shutdown
    logger.info(f"Señal {signum} → shutdown")
    _shutdown = True


# ── main ──────────────────────────────────────────────────────────── #

async def main():
    _signal.signal(_signal.SIGTERM, _on_shutdown)
    _signal.signal(_signal.SIGINT,  _on_shutdown)

    logger.info("=== Maki Bot PRO arrancando ===")

    client = BingXClient(API_KEY, API_SECRET)
    tg     = TelegramNotifier(TG_TOKEN, TG_CHAT)
    rm     = RiskManager(
        trade_usdt=TRADE_USDT,
        max_trades=MAX_TRADES,
        max_dd_pct=MAX_DD_USDT,
    )
    tg.start()

    try:
        balance = await client.balance_usdt()
        symbols = await client.top_symbols_by_volume(TOP_N)
    except Exception as e:
        logger.critical(f"Error al arrancar: {e}")
        await tg.notify(f"🚨 *Maki Bot PRO — error al arrancar*\n`{str(e)[:300]}`")
        await asyncio.sleep(2)
        await tg.stop()
        await client.close()
        sys.exit(1)

    logger.info(f"Balance: {balance:.2f} USDT | Pares: {len(symbols)}")
    await tg.notify(
        f"🤖 *Maki Bot PRO iniciado*\n"
        f"Balance: `{balance:.2f} USDT`\n"
        f"Trade: `{TRADE_USDT} USDT` × `{LEVERAGE}x`\n"
        f"Pares: `{len(symbols)}` | Max: `{MAX_TRADES}` | Scan: `{SCAN_SECS}s`\n"
        f"ZigZag++ · HMA · Volume Delta · ATR dinámico\n"
        f"Max pérdida diaria: `{MAX_DD_USDT} USDT`"
    )

    last_heartbeat = _now()
    last_refresh   = _now()
    cycle          = 0

    while not _shutdown:
        cycle += 1
        try:
            await monitor(client, tg, rm)
            await scan(client, tg, rm, symbols)

            logger.info(
                f"CICLO {cycle:04d} | {len(symbols)} pares | "
                f"open={len(open_trades)}/{MAX_TRADES}"
            )

            if (_now() - last_refresh) >= 3600:
                symbols      = await client.top_symbols_by_volume(TOP_N)
                last_refresh = _now()
                logger.info(f"Pares actualizados: {len(symbols)}")

            if (_now() - last_heartbeat) >= HEARTBEAT_MIN * 60:
                await heartbeat(client, tg, rm)
                last_heartbeat = _now()

        except Exception as e:
            logger.error(f"Error en loop: {e}", exc_info=True)
            await tg.notify(f"⚠️ Error: `{str(e)[:200]}`")

        await asyncio.sleep(SCAN_SECS)

    logger.info("Shutdown limpio")
    await tg.notify("🛑 *Maki Bot PRO detenido.*")
    await asyncio.sleep(1)
    await tg.stop()
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
