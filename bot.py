"""
Maki Bot — ZigZag + 20MA 4H para BingX Futures
TP: +0.45% | SL: -0.30% | Top 20 pares por volumen
"""
import asyncio
import logging
import os
from datetime import datetime, timezone

from bingx import BingXClient
from strategy import signal, tp_sl
import telegram

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bot")

# --- Config desde entorno ---
API_KEY        = os.environ["BINGX_API_KEY"]
API_SECRET     = os.environ["BINGX_API_SECRET"]
TG_TOKEN       = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT        = os.environ["TELEGRAM_CHAT_ID"]
TRADE_USDT     = float(os.environ.get("TRADE_AMOUNT_USDT", "10"))
MAX_TRADES     = int(os.environ.get("MAX_OPEN_TRADES", "3"))
SCAN_SECONDS   = int(os.environ.get("SCAN_INTERVAL_SECONDS", "60"))
TOP_N          = int(os.environ.get("TOP_N_SYMBOLS", "20"))

# Estado global (suficiente para este bot)
open_trades: dict = {}   # symbol -> {side, entry, tp, sl, qty}


async def notify(text: str):
    await telegram.send(TG_TOKEN, TG_CHAT, text)


async def monitor(client: BingXClient):
    """Cierra trades que alcanzaron TP o SL manualmente si la orden no se ejecutó sola."""
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


async def scan(client: BingXClient, symbols: list[str]):
    """Escanea señales en todos los símbolos."""
    for symbol in symbols:
        if symbol in open_trades:
            continue
        if len(open_trades) >= MAX_TRADES:
            break
        try:
            c15 = await client.klines(symbol, "15m", limit=60)
            c4h = await client.klines(symbol, "4h",  limit=30)
            sig = signal(c15, c4h)
            if sig is None:
                continue

            price = c15[-1]["c"]
            tp, sl = tp_sl(price, sig)
            qty = round(TRADE_USDT / price, 6)

            order_id = await client.open_order(symbol, sig, qty, tp, sl)
            open_trades[symbol] = {"side": sig, "entry": price, "tp": tp, "sl": sl, "qty": qty}

            emoji = "📈" if sig == "LONG" else "📉"
            logger.info(f"Trade abierto: {symbol} {sig} @ {price:.4f} TP={tp:.4f} SL={sl:.4f}")
            await notify(
                f"{emoji} *{sig} — {symbol}*\n"
                f"Entrada: `{price:.4f}`\n"
                f"TP: `{tp:.4f}` (+0.45%)\n"
                f"SL: `{sl:.4f}` (-0.30%)\n"
                f"Cantidad: `{qty}` | Monto: `{TRADE_USDT} USDT`"
            )
        except Exception as e:
            logger.warning(f"Error escaneando {symbol}: {e}")


async def main():
    client = BingXClient(API_KEY, API_SECRET)

    balance = await client.balance_usdt()
    symbols = await client.top_symbols_by_volume(TOP_N)

    logger.info(f"Balance: {balance:.2f} USDT | Pares: {len(symbols)}")
    await notify(
        f"🤖 *Maki Bot iniciado*\n"
        f"Balance: `{balance:.2f} USDT`\n"
        f"Monto/trade: `{TRADE_USDT} USDT`\n"
        f"Pares activos: `{len(symbols)}`\n"
        f"TP: +0.45% | SL: -0.30%\n"
        f"Max trades: `{MAX_TRADES}`"
    )

    while True:
        try:
            await monitor(client)
            await scan(client, symbols)

            # Refrescar top pares cada hora (cada 60 ciclos de 60s)
            if datetime.now(timezone.utc).minute == 0:
                symbols = await client.top_symbols_by_volume(TOP_N)
                logger.info(f"Pares actualizados: {symbols}")

        except Exception as e:
            logger.error(f"Error en loop: {e}")
            await notify(f"⚠️ Error en bot: `{str(e)[:200]}`")

        await asyncio.sleep(SCAN_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
