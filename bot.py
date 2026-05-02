"""
Maki Bot v2 — ZigZag + 20MA 4H + RSI para BingX Perpetual Futures
TP: +0.45%  |  SL: -0.30%  |  Top N pares por volumen

Flujo principal
───────────────
1. Arranca, muestra balance y lista de pares activos en Telegram
2. Cada SCAN_INTERVAL_SECONDS segundos:
   a. monitor()  → detecta posiciones cerradas por TP/SL en BingX
   b. scan()     → busca nuevas señales en todos los pares
3. Cada hora actualiza el ranking de pares por volumen
4. Heartbeat periódico a Telegram con resumen de estado
5. Captura SIGTERM (Railway) para un shutdown limpio
"""
import asyncio
import logging
import os
import signal
from datetime import datetime, timezone

from bingx   import BingXClient
from strategy import signal as get_signal, tp_sl
from telegram import TelegramNotifier

# ── Logging ────────────────────────────────────────────────────────── #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("bot")

# ── Config desde variables de entorno ──────────────────────────────── #
API_KEY       = os.environ["BINGX_API_KEY"]
API_SECRET    = os.environ["BINGX_API_SECRET"]
TG_TOKEN      = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT       = os.environ["TELEGRAM_CHAT_ID"]

TRADE_USDT    = float(os.environ.get("TRADE_AMOUNT_USDT",       "10"))
MAX_TRADES    = int(os.environ.get("MAX_OPEN_TRADES",            "3"))
SCAN_SECS     = int(os.environ.get("SCAN_INTERVAL_SECONDS",     "60"))
TOP_N         = int(os.environ.get("TOP_N_SYMBOLS",             "20"))
LEVERAGE      = int(os.environ.get("LEVERAGE",                   "5"))
MIN_BALANCE   = float(os.environ.get("MIN_BALANCE_USDT",        "20"))
COOLDOWN_S    = int(os.environ.get("COOLDOWN_SECONDS",          "300"))
HEARTBEAT_MIN = int(os.environ.get("HEARTBEAT_MINUTES",         "60"))

# ── Estado en memoria ──────────────────────────────────────────────── #
open_trades: dict[str, dict] = {}   # symbol → {side,entry,tp,sl,qty,order_id}
cooldowns:   dict[str, float] = {}  # symbol → timestamp del último cierre
_shutdown = False


# ── Utilidades ─────────────────────────────────────────────────────── #

def _now() -> float:
    return datetime.now(timezone.utc).timestamp()

def _in_cooldown(symbol: str) -> bool:
    return (_now() - cooldowns.get(symbol, 0)) < COOLDOWN_S

def _qty(price: float) -> float:
    return round(TRADE_USDT / price, 4) if price > 0 else 0.0

def _pnl(trade: dict, exit_price: float) -> float:
    if trade["side"] == "LONG":
        return (exit_price - trade["entry"]) * trade["qty"]
    return (trade["entry"] - exit_price) * trade["qty"]


# ── Monitor de posiciones ──────────────────────────────────────────── #

async def monitor(client: BingXClient, tg: TelegramNotifier):
    """
    Compara el estado interno con las posiciones reales de BingX.
    Si una posición ya no existe en la API → fue cerrada por TP o SL.
    """
    if not open_trades:
        return

    try:
        positions   = await client.get_open_positions()
        open_syms   = {p["symbol"] for p in positions}
    except Exception as e:
        logger.warning(f"monitor: no se pudieron obtener posiciones: {e}")
        open_syms = set(open_trades.keys())   # asumir abiertas si falla la API

    for symbol in list(open_trades.keys()):
        trade = open_trades[symbol]

        # ── Cerrada automáticamente por BingX (TP/SL ejecutado) ── #
        if symbol not in open_syms:
            try:
                price = await client.last_price(symbol)
            except Exception:
                price = trade["entry"]

            side   = trade["side"]
            pnl    = _pnl(trade, price)
            hit_tp = (price >= trade["tp"]) if side == "LONG" else (price <= trade["tp"])
            reason = "TP ✅" if hit_tp else "SL ❌"

            del open_trades[symbol]
            cooldowns[symbol] = _now()
            logger.info(f"Cerrado externamente {symbol} {side} {reason} pnl≈{pnl:.2f}")
            await tg.notify(
                f"{'🟢' if hit_tp else '🔴'} *{reason} — {symbol}*\n"
                f"Dirección: `{side}`\n"
                f"Entrada: `{trade['entry']:.4f}` → Precio: `{price:.4f}`\n"
                f"PnL est.: `{pnl:+.2f} USDT`"
            )
            continue

        # ── Verificación manual por si las órdenes TP/SL no se ejecutaron ── #
        try:
            price = await client.last_price(symbol)
        except Exception as e:
            logger.warning(f"monitor last_price {symbol}: {e}")
            continue

        side   = trade["side"]
        hit_tp = price >= trade["tp"] if side == "LONG" else price <= trade["tp"]
        hit_sl = price <= trade["sl"] if side == "LONG" else price >= trade["sl"]

        if hit_tp or hit_sl:
            reason = "TP ✅" if hit_tp else "SL ❌"
            pnl    = _pnl(trade, price)
            try:
                await client.close_position(symbol, side, trade["qty"])
            except Exception as e:
                logger.error(f"close_position {symbol}: {e}")

            del open_trades[symbol]
            cooldowns[symbol] = _now()
            logger.info(f"Cerrado manual {symbol} {side} {reason} pnl={pnl:.2f}")
            await tg.notify(
                f"{'🟢' if hit_tp else '🔴'} *{reason} — {symbol}*\n"
                f"Dirección: `{side}`\n"
                f"Entrada: `{trade['entry']:.4f}` → Salida: `{price:.4f}`\n"
                f"PnL est.: `{pnl:+.2f} USDT`"
            )


# ── Scanner de señales ─────────────────────────────────────────────── #

async def scan(client: BingXClient, tg: TelegramNotifier, symbols: list[str]):
    if len(open_trades) >= MAX_TRADES:
        return

    try:
        balance = await client.balance_usdt()
    except Exception:
        balance = MIN_BALANCE

    if balance < MIN_BALANCE:
        logger.warning(f"Balance {balance:.2f} < mínimo {MIN_BALANCE} — scan suspendido")
        return

    for symbol in symbols:
        if _shutdown or len(open_trades) >= MAX_TRADES:
            break
        if symbol in open_trades or _in_cooldown(symbol):
            continue

        try:
            c15 = await client.klines(symbol, "15m", limit=60)
            c4h = await client.klines(symbol, "4h",  limit=30)
        except Exception as e:
            logger.warning(f"klines {symbol}: {e}")
            continue

        sig = get_signal(c15, c4h)
        if sig is None:
            continue

        try:
            price = await client.last_price(symbol)
            if price <= 0:
                price = c15[-1]["c"]
        except Exception:
            price = c15[-1]["c"]

        tp, sl = tp_sl(price, sig, c15)
        qty    = _qty(price)
        if qty <= 0:
            logger.warning(f"qty inválida {symbol} @ {price}")
            continue

        try:
            order_id = await client.open_order(symbol, sig, qty, tp, sl, LEVERAGE)
        except Exception as e:
            logger.error(f"open_order {symbol}: {e}")
            await tg.notify(f"⚠️ Error abriendo `{symbol}`: `{str(e)[:150]}`")
            continue

        open_trades[symbol] = {
            "side": sig, "entry": price,
            "tp": tp, "sl": sl, "qty": qty,
            "order_id": order_id, "opened_at": _now(),
        }

        emoji = "📈" if sig == "LONG" else "📉"
        logger.info(f"Trade: {symbol} {sig} entry={price:.4f} tp={tp:.4f} sl={sl:.4f}")
        await tg.notify(
            f"{emoji} *{sig} — {symbol}*\n"
            f"Entrada: `{price:.4f}`\n"
            f"TP: `{tp:.4f}` (+0.45%)\n"
            f"SL: `{sl:.4f}` (-0.30%)\n"
            f"Cantidad: `{qty}` | ~`{TRADE_USDT} USDT` | `{LEVERAGE}x`"
        )
        await asyncio.sleep(0.3)


# ── Heartbeat ──────────────────────────────────────────────────────── #

async def heartbeat(client: BingXClient, tg: TelegramNotifier):
    try:
        balance = await client.balance_usdt()
        lines   = "\n".join(
            f"  • `{sym}` {t['side']} @ `{t['entry']:.4f}`"
            for sym, t in open_trades.items()
        ) or "  _Ninguno_"
        await tg.notify(
            f"💓 *Heartbeat*\n"
            f"Balance: `{balance:.2f} USDT`\n"
            f"Trades: `{len(open_trades)}/{MAX_TRADES}`\n"
            f"{lines}"
        )
    except Exception as e:
        logger.warning(f"heartbeat: {e}")


# ── Shutdown ───────────────────────────────────────────────────────── #

def _on_signal(sig, _):
    global _shutdown
    logger.info(f"Señal {sig} — iniciando shutdown limpio")
    _shutdown = True


# ── Main ───────────────────────────────────────────────────────────── #

async def main():
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT,  _on_signal)

    client = BingXClient(API_KEY, API_SECRET)
    tg     = TelegramNotifier(TG_TOKEN, TG_CHAT)
    tg.start()

    try:
        balance = await client.balance_usdt()
        symbols = await client.top_symbols_by_volume(TOP_N)
    except Exception as e:
        logger.critical(f"Error al arrancar: {e}")
        await tg.notify(f"🚨 *Maki Bot — error al arrancar*\n`{e}`")
        await client.close()
        return

    logger.info(f"Balance: {balance:.2f} USDT | Pares: {len(symbols)}")
    await tg.notify(
        f"🤖 *Maki Bot v2 iniciado*\n"
        f"Balance: `{balance:.2f} USDT`\n"
        f"Trade: `{TRADE_USDT} USDT` | Leverage: `{LEVERAGE}x`\n"
        f"Pares: `{len(symbols)}` | TP: +0.45% | SL: -0.30%\n"
        f"Max trades: `{MAX_TRADES}` | Cooldown: `{COOLDOWN_S}s`"
    )

    cycle          = 0
    last_heartbeat = _now()
    cycles_per_h   = max(1, 3600 // SCAN_SECS)

    while not _shutdown:
        cycle += 1
        try:
            await monitor(client, tg)
            await scan(client, tg, symbols)

            if cycle % cycles_per_h == 0:
                symbols = await client.top_symbols_by_volume(TOP_N)
                logger.info(f"Pares actualizados: {len(symbols)}")

            if (_now() - last_heartbeat) / 60 >= HEARTBEAT_MIN:
                await heartbeat(client, tg)
                last_heartbeat = _now()

        except Exception as e:
            logger.error(f"Loop error ciclo {cycle}: {e}", exc_info=True)
            await tg.notify(f"⚠️ Error en bot: `{str(e)[:200]}`")

        await asyncio.sleep(SCAN_SECS)

    await tg.notify("🛑 *Maki Bot detenido* — posiciones abiertas NO cerradas automáticamente.")
    await tg.stop()
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
