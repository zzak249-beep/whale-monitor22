# ═══════════════════════════════════════════════════════════════════════════════
# PATH BOOTSTRAP — DEBE SER LO PRIMERO (antes de cualquier import)
# ═══════════════════════════════════════════════════════════════════════════════
import os as _os, sys as _sys
_APP_DIR = _os.path.dirname(_os.path.abspath(__file__))
for _p in (_APP_DIR, "/app", _os.getcwd()):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
_os.makedirs(_os.path.join(_APP_DIR, "logs"), exist_ok=True)
# ═══════════════════════════════════════════════════════════════════════════════

"""
MAIN — Orquestador principal
══════════════════════════════════════════════════════════════════
1. Inicializar BingX, Telegram, RiskManager, PositionMonitor
2. Precargar velas históricas HTF + ENTRY (batch paralelo)
3. WebSocket en tiempo real (< 50ms) como fuente primaria
4. Polling REST como fallback cada POLL_INTERVAL segundos
5. Analizar señales → score → ejecutar si score ≥ MIN_SIGNAL_SCORE
6. Monitor de posiciones: trailing SL, cierre automático
7. Reporte diario a las 23:55 UTC
"""
import asyncio
import signal
import sys
from datetime import datetime, timezone

import config
from exchange.bingx_client           import BingXClient
from strategy.htf_bias               import calculate_htf_bias
from strategy.ema10_cross            import calculate_ema10_signal
from strategy.structure              import detect_bos
from strategy.volume_cvd             import calculate_volume_cvd
from strategy.signals                import aggregate_signals
from risk.manager                    import RiskManager
from risk.monitor                    import PositionMonitor
from notifications.telegram_notifier import TelegramNotifier
from utils.logger                    import get_logger

log    = get_logger("Main")
client = BingXClient()
risk   = RiskManager()
tg     = TelegramNotifier()
pmon   = PositionMonitor(client, risk, tg)

candle_buffer:    dict[str, dict[str, list]] = {}
last_signal_bar:  dict[str, int]             = {}


def _normalize(c) -> dict:
    if isinstance(c, list):
        return {
            "time": c[0], "open": float(c[1]), "high": float(c[2]),
            "low": float(c[3]), "close": float(c[4]), "volume": float(c[5]),
        }
    return {k: (float(v) if k != "time" else int(v)) for k, v in c.items()}


async def load_candles(symbol: str, interval: str, limit: int = None) -> list[dict]:
    lim = limit or config.CANDLES_REQUIRED
    raw = await client.get_klines(symbol, interval, lim)
    return [_normalize(c) for c in raw if c]


async def analyze_and_trade(symbol: str):
    if not RiskManager.is_trading_hour():
        return

    htf_buf   = candle_buffer.get(symbol, {}).get(config.TF_HTF,   [])
    entry_buf = candle_buffer.get(symbol, {}).get(config.TF_ENTRY, [])

    if len(htf_buf) < config.HTF_CANDLES_REQUIRED or len(entry_buf) < 50:
        return

    buf_len = len(entry_buf)
    if buf_len - last_signal_bar.get(symbol, 0) < config.MIN_BARS_COOLDOWN:
        return

    try:
        htf    = calculate_htf_bias(htf_buf)
        if not htf.confirmed:
            return
        ema    = calculate_ema10_signal(entry_buf)
        if ema.direction == "NONE":
            return
        bos    = detect_bos(entry_buf)
        vol    = calculate_volume_cvd(entry_buf)
        signal = aggregate_signals(htf, ema, bos, vol, symbol)
    except Exception as e:
        log.error(f"analyze_and_trade error {symbol}: {e}", exc_info=True)
        return

    if signal.direction == "HOLD":
        return

    last_signal_bar[symbol] = buf_len

    tg.signal_detected(
        symbol, signal.direction, signal.score, signal.confidence,
        signal.entry_price, signal.stop_loss, signal.take_profit,
        signal.risk_reward, signal.reasons,
    )

    balance = await client.get_balance()
    equity  = balance.get("equity", 0)
    if equity <= 0:
        return

    can, reason = await risk.can_trade(symbol, equity)
    if not can:
        log.info(f"{symbol}: BLOQUEADO — {reason}")
        return

    sizing = risk.calculate_position_size(
        equity=equity,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        size_mult=signal.size_mult,
    )
    if not sizing.valid:
        return

    side     = "BUY"  if signal.direction == "LONG"  else "SELL"
    pos_side = "LONG" if signal.direction == "LONG"  else "SHORT"

    log.info(
        f"EJECUTANDO {symbol} {signal.direction} [{signal.entry_type}] "
        f"score={signal.score:.0f} qty={sizing.qty} "
        f"SL={signal.stop_loss:.4f} TP={signal.take_profit:.4f}"
    )

    result = await client.place_order(
        symbol=symbol, side=side, position_side=pos_side,
        qty=sizing.qty, sl_price=signal.stop_loss, tp_price=signal.take_profit,
    )

    if result:
        await risk.register_open(
            symbol, signal.direction, signal.entry_price,
            sizing.qty, signal.stop_loss, signal.take_profit,
        )
        pmon.track(
            symbol, signal.direction, signal.entry_price,
            sizing.qty, signal.stop_loss, signal.take_profit,
        )
        tg.order_placed(
            symbol, signal.direction, sizing.qty,
            signal.entry_price, signal.stop_loss,
            signal.take_profit, config.DRY_RUN,
        )


def make_ws_callback(symbol: str, interval: str):
    async def on_kline(data: dict):
        kline     = data.get("k", data) if isinstance(data, dict) else {}
        is_closed = kline.get("x", kline.get("closed", False))
        candle = {
            "time":   int(kline.get("t",  kline.get("time",   0))),
            "open":   float(kline.get("o", kline.get("open",  0))),
            "high":   float(kline.get("h", kline.get("high",  0))),
            "low":    float(kline.get("l", kline.get("low",   0))),
            "close":  float(kline.get("c", kline.get("close", 0))),
            "volume": float(kline.get("v", kline.get("volume",0))),
        }
        if not candle["time"] or not candle["close"]:
            return

        buf = candle_buffer.setdefault(symbol, {}).setdefault(interval, [])
        if buf and buf[-1]["time"] == candle["time"]:
            buf[-1] = candle
        else:
            buf.append(candle)
        if len(buf) > config.CANDLES_REQUIRED + 50:
            del buf[0]

        if is_closed and interval == config.TF_ENTRY:
            log.debug(f"WS [{symbol} {interval}] close={candle['close']:.4f} ✓")
            await analyze_and_trade(symbol)

    return on_kline


async def polling_loop(symbol: str):
    while True:
        try:
            htf_c, entry_c = await asyncio.gather(
                load_candles(symbol, config.TF_HTF,   config.HTF_CANDLES_REQUIRED),
                load_candles(symbol, config.TF_ENTRY, config.CANDLES_REQUIRED),
            )
            if htf_c:
                candle_buffer.setdefault(symbol, {})[config.TF_HTF]   = htf_c
            if entry_c:
                candle_buffer.setdefault(symbol, {})[config.TF_ENTRY] = entry_c
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
    log.info("=" * 62)
    log.info("   CRYPTOBOT v3  —  EMA10 × HTF × BOS × FVG × CVD")
    log.info(f"   Modo: {'🧪 PAPER TRADING' if config.DRY_RUN else '🔴 LIVE TRADING'}")
    log.info(f"   Símbolos: {', '.join(config.SYMBOLS)}")
    log.info(f"   Riesgo: {config.RISK_PER_TRADE}% | Lev: {config.LEVERAGE}x | RR: {config.RISK_REWARD}x")
    log.info(f"   Score mínimo: {config.MIN_SIGNAL_SCORE}")
    log.info(f"   sys.path[0]: {sys.path[0]}")
    log.info("=" * 62)

    await asyncio.gather(
        *[client.set_leverage(sym, config.LEVERAGE) for sym in config.SYMBOLS],
        return_exceptions=True,
    )

    batch = await client.get_klines_batch([
        (sym, tf, lim)
        for sym in config.SYMBOLS
        for tf, lim in [
            (config.TF_HTF,   config.HTF_CANDLES_REQUIRED),
            (config.TF_ENTRY, config.CANDLES_REQUIRED),
        ]
    ])
    for sym in config.SYMBOLS:
        htf_c   = batch.get(f"{sym}_{config.TF_HTF}",   [])
        entry_c = batch.get(f"{sym}_{config.TF_ENTRY}", [])
        candle_buffer.setdefault(sym, {})[config.TF_HTF]   = htf_c
        candle_buffer.setdefault(sym, {})[config.TF_ENTRY] = entry_c
        log.info(f"  {sym}: HTF={len(htf_c)} ENTRY={len(entry_c)} velas precargadas")

    await tg.start()
    tg.bot_started(config.SYMBOLS, config.DRY_RUN)


async def main():
    await initialize()
    tasks = []
    for sym in config.SYMBOLS:
        if config.WS_ENABLED:
            for tf in [config.TF_HTF, config.TF_ENTRY]:
                client.register_ws_callback(f"{sym}_{tf}", make_ws_callback(sym, tf))
                tasks.append(asyncio.create_task(
                    client.stream_klines(sym, tf), name=f"ws_{sym}_{tf}"
                ))
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
        log.info("Bot detenido limpiamente")


def _sigterm(*_):
    log.info("SIGTERM — deteniendo…")
    for t in asyncio.all_tasks():
        t.cancel()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _sigterm)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot detenido (Ctrl+C)")
