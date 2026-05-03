"""
Maki Bot — ZigZag + SMA-20 (4H) + RSI-14 (15m) para BingX Perpetual Futures
TP: +0.45%  |  SL: -0.30%

Sin dependencias externas salvo aiohttp.
"""
import asyncio
import logging
import os
import sys
import signal as _signal
from datetime import datetime, timezone

from bingx    import BingXClient
from strategy import signal as get_signal, tp_sl
from telegram import TelegramNotifier

# ── logging a stdout (visible en Railway) ─────────────────────────── #
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("bot")

# ── variables de entorno ──────────────────────────────────────────── #
def _env(key: str, default: str = None) -> str:
    val = os.environ.get(key, default)
    if val is None:
        logger.critical(f"Falta variable de entorno: {key}")
        sys.exit(1)
    return val

API_KEY       = _env("BINGX_API_KEY")
API_SECRET    = _env("BINGX_API_SECRET")
TG_TOKEN      = _env("TELEGRAM_BOT_TOKEN")
TG_CHAT       = _env("TELEGRAM_CHAT_ID")

TRADE_USDT    = float(_env("TRADE_AMOUNT_USDT",      "10"))
MAX_TRADES    = int(_env("MAX_OPEN_TRADES",            "3"))
SCAN_SECS     = int(_env("SCAN_INTERVAL_SECONDS",     "60"))
TOP_N         = int(_env("TOP_N_SYMBOLS",             "20"))
LEVERAGE      = int(_env("LEVERAGE",                   "5"))
MIN_BAL       = float(_env("MIN_BALANCE_USDT",        "20"))
COOLDOWN_S    = int(_env("COOLDOWN_SECONDS",          "300"))
HEARTBEAT_MIN = int(_env("HEARTBEAT_MINUTES",         "60"))

# ── estado en memoria ─────────────────────────────────────────────── #
open_trades: dict = {}   # symbol → {side, entry, tp, sl, qty, order_id}
cooldowns:   dict = {}   # symbol → timestamp del último cierre
_shutdown         = False


# ── helpers ───────────────────────────────────────────────────────── #

def _now() -> float:
    return datetime.now(timezone.utc).timestamp()

def _in_cooldown(sym: str) -> bool:
    return (_now() - cooldowns.get(sym, 0)) < COOLDOWN_S

def _calc_qty(price: float) -> float:
    """Cantidad en moneda base para el presupuesto TRADE_USDT."""
    if price <= 0:
        return 0.0
    qty = TRADE_USDT / price
    if price >= 1000:
        return round(qty, 4)
    elif price >= 1:
        return round(qty, 3)
    else:
        return round(qty, 1)

def _pnl(trade: dict, exit_price: float) -> float:
    if trade["side"] == "LONG":
        return (exit_price - trade["entry"]) * trade["qty"]
    return (trade["entry"] - exit_price) * trade["qty"]


# ── monitor ───────────────────────────────────────────────────────── #

async def monitor(client: BingXClient, tg: TelegramNotifier):
    """Detecta posiciones cerradas automáticamente por BingX (TP/SL)."""
    if not open_trades:
        return

    try:
        positions = await client.get_open_positions()
        open_syms = {p["symbol"] for p in positions}
    except Exception as e:
        logger.warning(f"monitor: error consultando posiciones: {e}")
        return

    for sym in list(open_trades.keys()):
        trade = open_trades[sym]
        side  = trade["side"]

        # Posición ya no existe en BingX → cerrada por TP o SL
        if sym not in open_syms:
            try:
                price = await client.last_price(sym)
            except Exception:
                price = trade["entry"]

            pnl    = _pnl(trade, price)
            hit_tp = (price >= trade["tp"]) if side == "LONG" else (price <= trade["tp"])
            reason = "TP ✅" if hit_tp else "SL ❌"
            icon   = "🟢" if hit_tp else "🔴"

            del open_trades[sym]
            cooldowns[sym] = _now()
            logger.info(f"{sym} {side} cerrado externamente {reason} pnl={pnl:+.2f}")
            await tg.notify(
                f"{icon} *{reason} — {sym}*\n"
                f"Dir: `{side}` | Entrada: `{trade['entry']:.5f}` → `{price:.5f}`\n"
                f"PnL est: `{pnl:+.2f} USDT`"
            )
            continue

        # Respaldo manual: verificar TP/SL por precio actual
        try:
            price = await client.last_price(sym)
        except Exception as e:
            logger.warning(f"last_price {sym}: {e}")
            continue

        hit_tp = price >= trade["tp"] if side == "LONG" else price <= trade["tp"]
        hit_sl = price <= trade["sl"] if side == "LONG" else price >= trade["sl"]

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
            logger.info(f"{sym} {side} cerrado manual {reason} pnl={pnl:+.2f}")
            await tg.notify(
                f"{icon} *{reason} — {sym}*\n"
                f"Dir: `{side}` | Entrada: `{trade['entry']:.5f}` → `{price:.5f}`\n"
                f"PnL est: `{pnl:+.2f} USDT`"
            )


# ── scan ──────────────────────────────────────────────────────────── #

async def scan(client: BingXClient, tg: TelegramNotifier, symbols: list):
    """Busca señales en todos los pares y abre trades."""
    if len(open_trades) >= MAX_TRADES:
        return

    try:
        balance = await client.balance_usdt()
    except Exception:
        balance = MIN_BAL + 1

    if balance < MIN_BAL:
        logger.warning(f"Balance {balance:.2f} < mínimo {MIN_BAL} — scan pausado")
        return

    for sym in symbols:
        if _shutdown or len(open_trades) >= MAX_TRADES:
            break
        if sym in open_trades or _in_cooldown(sym):
            continue

        # FIX: pedir 100 velas 15m (antes 60) y 50 velas 4h (antes 30)
        try:
            c15 = await client.klines(sym, "15m", limit=100)
            c4h = await client.klines(sym, "4h",  limit=50)
        except Exception as e:
            logger.warning(f"klines {sym}: {e}")
            continue

        sig = get_signal(c15, c4h)
        if sig is None:
            continue

        try:
            price = await client.last_price(sym)
        except Exception:
            price = c15[-1]["c"]

        if price <= 0:
            continue

        tp, sl = tp_sl(price, sig)
        qty    = _calc_qty(price)
        if qty <= 0:
            logger.warning(f"qty inválida {sym} @ {price}")
            continue

        try:
            order_id = await client.open_order(sym, sig, qty, tp, sl, LEVERAGE)
        except Exception as e:
            logger.error(f"open_order {sym}: {e}")
            await tg.notify(f"⚠️ Error abriendo `{sym}`:\n`{str(e)[:200]}`")
            continue

        open_trades[sym] = {
            "side": sig,   "entry": price,
            "tp":   tp,    "sl":    sl,
            "qty":  qty,   "order_id": order_id,
            "ts":   _now(),
        }
        emoji = "📈" if sig == "LONG" else "📉"
        logger.info(f"TRADE: {sym} {sig} @ {price:.5f} TP={tp:.5f} SL={sl:.5f} qty={qty}")
        await tg.notify(
            f"{emoji} *{sig} — {sym}*\n"
            f"Entrada: `{price:.5f}`\n"
            f"TP: `{tp:.5f}` (+0.45%) | SL: `{sl:.5f}` (-0.30%)\n"
            f"Qty: `{qty}` (~{TRADE_USDT} USDT) | `{LEVERAGE}x`"
        )
        await asyncio.sleep(0.5)


# ── heartbeat ─────────────────────────────────────────────────────── #

async def heartbeat(client: BingXClient, tg: TelegramNotifier):
    try:
        balance = await client.balance_usdt()
        detalle = "\n".join(
            f"  • `{s}` {t['side']} @ `{t['entry']:.5f}`"
            for s, t in open_trades.items()
        ) or "  _Sin trades_"
        await tg.notify(
            f"💓 *Heartbeat*\n"
            f"Balance: `{balance:.2f} USDT` | Trades: `{len(open_trades)}/{MAX_TRADES}`\n"
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

    logger.info("=== Maki Bot arrancando ===")

    client = BingXClient(API_KEY, API_SECRET)
    tg     = TelegramNotifier(TG_TOKEN, TG_CHAT)
    tg.start()

    try:
        balance = await client.balance_usdt()
        symbols = await client.top_symbols_by_volume(TOP_N)
    except Exception as e:
        logger.critical(f"Error al arrancar: {e}")
        await tg.notify(f"🚨 *Maki Bot — error al arrancar*\n`{str(e)[:300]}`")
        await asyncio.sleep(2)
        await tg.stop()
        await client.close()
        sys.exit(1)

    logger.info(f"Balance: {balance:.2f} USDT | Pares: {len(symbols)}")
    await tg.notify(
        f"🤖 *Maki Bot iniciado*\n"
        f"Balance: `{balance:.2f} USDT`\n"
        f"Trade: `{TRADE_USDT} USDT` × `{LEVERAGE}x`\n"
        f"Pares: `{len(symbols)}` | Max: `{MAX_TRADES}` | Scan: `{SCAN_SECS}s`\n"
        f"TP: +0.45% | SL: -0.30%"
    )

    last_heartbeat = _now()
    last_refresh   = _now()

    while not _shutdown:
        try:
            await monitor(client, tg)
            await scan(client, tg, symbols)

            if (_now() - last_refresh) >= 3600:
                symbols      = await client.top_symbols_by_volume(TOP_N)
                last_refresh = _now()
                logger.info(f"Pares actualizados: {len(symbols)}")

            if (_now() - last_heartbeat) >= HEARTBEAT_MIN * 60:
                await heartbeat(client, tg)
                last_heartbeat = _now()

        except Exception as e:
            logger.error(f"Error en loop: {e}", exc_info=True)
            await tg.notify(f"⚠️ Error: `{str(e)[:200]}`")

        await asyncio.sleep(SCAN_SECS)

    logger.info("Shutdown limpio")
    await tg.notify("🛑 *Maki Bot detenido.* Las posiciones quedan abiertas en BingX.")
    await asyncio.sleep(1)
    await tg.stop()
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
