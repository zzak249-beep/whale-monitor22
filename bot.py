"""
Maki Bot — ZigZag + 20MA 4H para BingX Futures
TP: +0.45% | SL: -0.30% | Todos los pares USDT en lotes de 50
"""
import asyncio
import logging
import os
from datetime import datetime, timezone

from bingx import BingXClient
from strategy import signal, tp_sl
import telegram

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bot")

API_KEY      = os.environ["BINGX_API_KEY"]
API_SECRET   = os.environ["BINGX_API_SECRET"]
TG_TOKEN     = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT      = os.environ["TELEGRAM_CHAT_ID"]
TRADE_USDT   = float(os.environ.get("TRADE_AMOUNT_USDT", "10"))
MAX_TRADES   = int(os.environ.get("MAX_OPEN_TRADES", "3"))
BATCH_PAUSE  = float(os.environ.get("BATCH_PAUSE_SECONDS", "2"))  # pausa entre lotes

open_trades: dict = {}


async def notify(text: str):
    ok = await telegram.send(TG_TOKEN, TG_CHAT, text)
    if not ok:
        logger.error("Telegram send FAILED — revisa TG_TOKEN y TG_CHAT_ID")


async def monitor(client: BingXClient):
    for symbol, trade in list(open_trades.items()):
        try:
            ticker_data = await client._get("/openApi/swap/v2/quote/ticker", {"symbol": symbol})
            price = float(ticker_data[0]["lastPrice"] if isinstance(ticker_data, list) else ticker_data["lastPrice"])
            side  = trade["side"]
            hit_tp = price >= trade["tp"] if side == "LONG" else price <= trade["tp"]
            hit_sl = price <= trade["sl"] if side == "LONG" else price >= trade["sl"]

            if hit_tp or hit_sl:
                reason = "TP ✅" if hit_tp else "SL ❌"
                pnl = (price - trade["entry"]) * trade["qty"] if side == "LONG" else (trade["entry"] - price) * trade["qty"]
                await client.close_position(symbol, side, trade["qty"])
                del open_trades[symbol]
                logger.info(f"Cerrado {symbol} {side} {reason} pnl={pnl:.2f}")
                await notify(
                    f"{'🟢' if hit_tp else '🔴'} *{reason} — {symbol}*\n"
                    f"Dirección: `{side}`\n"
                    f"Entrada: `{trade['entry']:.4f}` → Salida: `{price:.4f}`\n"
                    f"PnL estimado: `{pnl:.2f} USDT`"
                )
        except Exception as e:
            logger.warning(f"Error monitorizando {symbol}: {e}")


async def scan_batch(client: BingXClient, symbols: list[str]):
    """Escanea una lista de símbolos en lotes de 50 con pausa entre lotes."""
    BATCH = 50
    for i in range(0, len(symbols), BATCH):
        batch = symbols[i:i + BATCH]
        logger.info(f"Escaneando lote {i//BATCH + 1}: {batch[0]} … {batch[-1]}")

        for symbol in batch:
            if symbol in open_trades:
                continue
            if len(open_trades) >= MAX_TRADES:
                logger.info("Max trades alcanzado, pausando escaneo.")
                return
            try:
                c15 = await client.klines(symbol, "15m", limit=60)
                c4h = await client.klines(symbol, "4h",  limit=30)
                sig = signal(c15, c4h, symbol=symbol)
                if sig is None:
                    continue

                price = c15[-1]["c"]
                tp, sl = tp_sl(price, sig)
                qty = round(TRADE_USDT / price, 6)

                await client.open_order(symbol, sig, qty, tp, sl)
                open_trades[symbol] = {"side": sig, "entry": price, "tp": tp, "sl": sl, "qty": qty}

                emoji = "📈" if sig == "LONG" else "📉"
                logger.info(f"Trade abierto: {symbol} {sig} @ {price:.4f}")
                await notify(
                    f"{emoji} *{sig} — {symbol}*\n"
                    f"Entrada: `{price:.4f}`\n"
                    f"TP: `{tp:.4f}` (+0.45%)\n"
                    f"SL: `{sl:.4f}` (-0.30%)\n"
                    f"Cantidad: `{qty}` | Monto: `{TRADE_USDT} USDT`"
                )
            except RuntimeError as e:
                err = str(e)
                logger.warning(f"Error orden {symbol}: {err}")
                if any(k in err.lower() for k in ["insufficient", "balance", "margin", "1101", "2001"]):
                    await notify(
                        f"⚠️ *Señal detectada pero sin fondos*\n"
                        f"Par: `{symbol}` | `{sig}`\n"
                        f"Recarga tu wallet de Futuros en BingX."
                    )
                else:
                    await notify(f"⚠️ *Error al abrir orden*\n`{symbol}`\n`{err[:200]}`")
            except Exception as e:
                logger.warning(f"Error escaneando {symbol}: {e}")

        # Pausa entre lotes para respetar rate limit
        await asyncio.sleep(BATCH_PAUSE)


async def main():
    client = BingXClient(API_KEY, API_SECRET)

    balance = await client.balance_usdt()
    symbols = await client.all_symbols()

    logger.info(f"Balance: {balance:.2f} USDT | Pares totales: {len(symbols)}")
    await notify(
        f"🤖 *Maki Bot iniciado*\n"
        f"Balance: `{balance:.2f} USDT`\n"
        f"Monto/trade: `{TRADE_USDT} USDT`\n"
        f"Pares: `{len(symbols)}` (todos)\n"
        f"TP: +0.45% | SL: -0.30%\n"
        f"Max trades: `{MAX_TRADES}`"
    )

    last_symbol_refresh = datetime.now(timezone.utc).hour

    while True:
        try:
            await monitor(client)
            await scan_batch(client, symbols)

            # Refrescar lista de pares cada hora
            now_hour = datetime.now(timezone.utc).hour
            if now_hour != last_symbol_refresh:
                symbols = await client.all_symbols()
                last_symbol_refresh = now_hour
                logger.info(f"Pares actualizados: {len(symbols)}")

        except Exception as e:
            logger.error(f"Error en loop: {e}")
            await notify(f"⚠️ Error en bot: `{str(e)[:200]}`")

        await asyncio.sleep(10)  # ciclo corto; el throttle real lo hace BATCH_PAUSE


if __name__ == "__main__":
    asyncio.run(main())
