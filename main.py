"""
MAIN — Orquestador principal
══════════════════════════════════════════════════════════════
Todos los módulos están en la raíz del proyecto (sin subcarpetas).
"""
import asyncio
import os
import signal
import sys
from datetime import datetime, timezone

os.makedirs("logs", exist_ok=True)

import config
from bingx_client       import BingXClient
from htf_bias           import calculate_htf_bias
from ema10_cross        import calculate_ema10_signal
from structure          import detect_bos
from volume_cvd         import calculate_volume_cvd
from signals            import aggregate_signals
from risk_manager       import RiskManager
from position_monitor   import PositionMonitor
from telegram_notifier  import TelegramNotifier
from bot_logger         import get_logger

log    = get_logger("Main")
client = BingXClient()
risk   = RiskManager()
tg     = TelegramNotifier()
pmon   = PositionMonitor(client, risk, tg)

candle_buffer:    dict = {}
last_signal_bar:  dict = {}
MIN_BARS_COOLDOWN = 3


def _normalize(c) -> dict:
    if isinstance(c, list):
        return {"time": c[0], "open": float(c[1]), "high": float(c[2]),
                "low":  float(c[3]), "close": float(c[4]), "volume": float(c[5])}
    return {k: float(v) if k != "time" else v for k, v in c.items()}


async def load_candles(symbol: str, interval: str, limit: int = 300) -> list:
    raw = await client.get_klines(symbol, interval, limit)
    return [_normalize(c) for c in raw if c]


async def analyze_and_trade(symbol: str):
    if not RiskManager.is_trading_hour():
        return

    htf_buf   = candle_buffer.get(symbol, {}).get(config.TF_HTF,   [])
    entry_buf = candle_buffer.get(symbol, {}).get(config.TF_ENTRY, [])
    if len(htf_buf) < 205 or len(entry_buf) < 50:
        return

    buf_len = len(entry_buf)
    if buf_len - last_signal_bar.get(symbol, 0) < MIN_BARS_COOLDOWN:
        return

    try:
        htf = calculate_htf_bias(htf_buf)
        if not htf.confirmed:
            return
        ema = calculate_ema10_signal(entry_buf)
        if ema.signal == "NONE":
            return
        bos    = detect_bos(entry_buf)
        vol    = calculate_volume_cvd(entry_buf)
        signal = aggregate_signals(htf, ema, bos, vol, symbol)
    except Exception as e:
        log.error(f"analyze_and_trade error {symbol}: {e}")
        return

    if signal.direction == "HOLD":
        return

    last_signal_bar[symbol] = buf_len

    tg.signal_detected(symbol, signal.direction, signal.score, signal.confidence,
                       signal.entry_price, signal.stop_loss, signal.take_profit,
                       signal.risk_reward, signal.reasons)

    balance = await client.get_balance()
    equity  = balance.get("equity", 0)
    if equity <= 0:
        return

    can, reason = await risk.can_trade(symbol, equity)
    if not can:
        log.info(f"{symbol}: BLOQUEADO — {reason}")
        return

    sizing = risk.calculate_position_size(
        equity=equity, entry_price=signal.entry_price,
        stop_loss=signal.stop_loss, size_mult=signal.size_mult)
    if not sizing.valid:
        return

    side     = "BUY"  if signal.direction == "LONG"  else "SELL"
    pos_side = "LONG" if signal.direction == "LONG"  else "SHORT"

    log.info(f"EJECUTANDO {symbol} {signal.direction} [{signal.entry_type}] "
             f"qty={sizing.qty} SL={signal.stop_loss:.4f} TP={signal.take_profit:.4f}")

    result = await client.place_order(
        symbol=symbol, side=side, position_side=pos_side,
        qty=sizing.qty, sl_price=signal.stop_loss, tp_price=signal.take_profit)

    if result:
        await risk.register_open(symbol, signal.direction, signal.entry_price,
                                 sizing.qty, signal.stop_loss, signal.take_profit)
        pmon.track(symbol, signal.direction, signal.entry_price,
                   sizing.qty, signal.stop_loss, signal.take_profit)
        tg.order_placed(symbol, signal.direction, sizing.qty,
                        signal.entry_price, signal.stop_loss,
                        signal.take_profit, config.DRY_RUN)


def make_ws_callback(symbol: str, interval: str):
    async def on_kline(data: dict):
        kline     = data.get("k", data) if isinstance(data, dict) else {}
        is_closed = kline.get("x", False)
        if not kline or not is_closed:
            return
        candle = {
            "time":   kline.get("t", 0),
            "open":   float(kline.get("o", 0)),
            "high":   float(kline.get("h", 0)),
            "low":    float(kline.get("l", 0)),
            "close":  float(kline.get("c", 0)),
            "volume": float(kline.get("v", 0)),
        }
        buf = candle_buffer.setdefault(symbol, {}).setdefault(interval, [])
        buf.append(candle)
        if len(buf) > config.CANDLES_REQUIRED + 50:
            buf.pop(0)
        log.debug(f"WS [{symbol} {interval}] close={candle['close']:.4f} ✓")
        if interval == config.TF_ENTRY:
            await analyze_and_trade(symbol)
    return on_kline


async def polling_loop(symbol: str):
    while True:
        try:
            for tf in [config.TF_HTF, config.TF_ENTRY]:
                candles = await load_candles(symbol, tf, config.CANDLES_REQUIRED)
                if candles:
                    candle_buffer.setdefault(symbol, {})[tf] = candles
            await analyze_and_trade(symbol)
        except Exception as e:
            log.error(f"Polling error {symbol}: {e}")
            tg.error_alert(f"Polling {symbol}: {str(e)[:200]}")
        await asyncio.sleep(config.POLL_INTERVAL)


async def daily_report_loop():
    while True:
        now = datetime.now(timezone.utc)
        if now.hour == 23 and 55 <= now.minute <= 57:
            tg.daily_stats(risk.get_stats())
            await asyncio.sleep(180)
        await asyncio.sleep(30)


async def initialize():
    log.info("=" * 58)
    log.info("   CRYPTOBOT v2  —  EMA10 × 15m × 8  ×  HTF × BOS × CVD")
    log.info(f"   Modo: {'PAPER TRADING 🧪' if config.DRY_RUN else 'LIVE TRADING 🔴'}")
    log.info(f"   Símbolos: {', '.join(config.SYMBOLS)}")
    log.info(f"   Riesgo: {config.RISK_PER_TRADE}% | Lev: {config.LEVERAGE}x | RR: {config.RISK_REWARD}x")
    log.info("=" * 58)

    for sym in config.SYMBOLS:
        try:
            await client.set_leverage(sym, config.LEVERAGE)
        except Exception as e:
            log.warning(f"Leverage {sym}: {e}")

    for sym in config.SYMBOLS:
        for tf in [config.TF_HTF, config.TF_ENTRY]:
            candles = await load_candles(sym, tf, config.CANDLES_REQUIRED)
            candle_buffer.setdefault(sym, {})[tf] = candles
            log.info(f"  Precargado {sym} {tf}: {len(candles)} velas")

    await tg.start()
    tg.bot_started(config.SYMBOLS, config.DRY_RUN)


async def main():
    await initialize()
    tasks = []
    for sym in config.SYMBOLS:
        for tf in [config.TF_HTF, config.TF_ENTRY]:
            key = f"{sym}_{tf}"
            client.register_ws_callback(key, make_ws_callback(sym, tf))
            tasks.append(asyncio.create_task(
                client.stream_klines(sym, tf), name=f"ws_{sym}_{tf}"))
        tasks.append(asyncio.create_task(polling_loop(sym), name=f"poll_{sym}"))
    tasks.append(asyncio.create_task(pmon.run(),          name="position_monitor"))
    tasks.append(asyncio.create_task(daily_report_loop(), name="daily_report"))
    log.info(f"Bot corriendo — {len(tasks)} tareas activas")
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        await client.close()
        log.info("Bot detenido")


def _sigterm(*_):
    for t in asyncio.all_tasks():
        t.cancel()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _sigterm)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot detenido (Ctrl+C)")
