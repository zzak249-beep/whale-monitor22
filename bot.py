"""bot.py — UltraBot v3 main entry point.

Fixes applied vs original Maki Bot:
  - One-way mode orders (no ReduceOnly conflict, fixes error 109400)
  - Full UltraBot v3 pipeline: ADX + RSI + Volume Delta multi-timeframe
  - Trailing stop-loss via periodic position monitoring
  - Graceful shutdown, DB persistence, Telegram alerts
"""
from __future__ import annotations
import asyncio
import os
import sys
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

# Agregar directorio raíz al path para resolver imports
sys.path.insert(0, str(Path(__file__).parent))

from loguru import logger

from core.config import cfg
from core.database import init_db, save_trade_open, save_trade_close, save_signal, get_performance_stats, get_recent_trades
from core.risk import RiskManager
from exchange.client import (
    fetch_all_tickers, fetch_universe_concurrent,
    get_balance, get_all_positions,
    set_leverage, place_market_order, close_position,
    cancel_all_orders, get_price, close_session,
)
from strategies.indicators import generate_signal
from notifications.telegram import send, send_now, start_sender, msg_start, msg_entry, msg_close, msg_performance, msg_halt, msg_error
from dashboard.server import start_dashboard, update_state

# ── Globals ───────────────────────────────────────────────────────────────────

risk = RiskManager()
_open_trades: dict[str, dict] = {}   # symbol → {trade_id, side, entry, sl, tp, sl_pct, tp_pct, size, opened_at, metrics}
_running = True


# ── Universe ──────────────────────────────────────────────────────────────────

async def get_universe() -> list[str]:
    """Top N symbols by 24h volume, filtered by blacklist."""
    tickers = await fetch_all_tickers()
    filtered = []
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("-USDT"):
            continue
        if sym in cfg.blacklist:
            continue
        try:
            vol = float(t.get("quoteVolume", t.get("volume", 0)))
        except Exception:
            continue
        if vol >= cfg.min_volume_usdt:
            filtered.append((sym, vol))

    filtered.sort(key=lambda x: x[1], reverse=True)
    symbols = [s for s, _ in filtered[: cfg.top_n_symbols]]
    logger.info(f"Universe: {len(symbols)} symbols (min vol ${cfg.min_volume_usdt:,.0f})")
    return symbols


# ── Position monitoring ───────────────────────────────────────────────────────

async def monitor_positions() -> None:
    """Check open positions for SL/TP hit and apply trailing SL."""
    positions = await get_all_positions()
    balance = await get_balance()
    risk.set_balance(balance)

    for symbol, trade in list(_open_trades.items()):
        pos = positions.get(symbol)
        if pos is None:
            # Position closed externally (exchange SL/TP hit)
            price = await get_price(symbol)
            await _close_trade(symbol, trade, price, "Exchange SL/TP", balance)
            continue

        price = float(pos.get("markPrice", pos.get("entryPrice", 0)))
        if price <= 0:
            continue

        side   = trade["side"]
        sl     = trade["sl"]
        tp     = trade["tp"]
        entry  = trade["entry"]

        # Check TP / SL hit (belt-and-suspenders over exchange orders)
        hit_tp = (price >= tp) if side == "BUY" else (price <= tp)
        hit_sl = (price <= sl) if side == "BUY" else (price >= sl)

        if hit_tp or hit_sl:
            reason = "TP" if hit_tp else "SL"
            await cancel_all_orders(symbol)
            await close_position(symbol, pos)
            await _close_trade(symbol, trade, price, reason, balance)
            continue

        # Trailing stop-loss
        if cfg.trailing_sl:
            sl_pct = trade["sl_pct"]
            if side == "BUY":
                new_sl = round(price * (1 - sl_pct / 100), 8)
                if new_sl > sl:
                    _open_trades[symbol]["sl"] = new_sl
                    logger.debug(f"Trailing SL {symbol}: {sl:.6g} → {new_sl:.6g}")
            else:
                new_sl = round(price * (1 + sl_pct / 100), 8)
                if new_sl < sl:
                    _open_trades[symbol]["sl"] = new_sl
                    logger.debug(f"Trailing SL {symbol}: {sl:.6g} → {new_sl:.6g}")


async def _close_trade(symbol: str, trade: dict, price: float, reason: str, balance: float) -> None:
    entry  = trade["entry"]
    side   = trade["side"]
    size   = trade["size"]

    if side == "BUY":
        pnl_pct = (price - entry) / entry * 100 * cfg.leverage
    else:
        pnl_pct = (entry - price) / entry * 100 * cfg.leverage

    pnl = size * pnl_pct / 100

    risk.record_close(symbol, pnl, balance)
    _open_trades.pop(symbol, None)

    await save_trade_close(
        trade["trade_id"], price, pnl, pnl_pct,
        reason, entry, trade["opened_at"]
    )

    duration_s = int(time.time() - trade.get("opened_ts", time.time()))
    await send(msg_close(symbol, side, pnl, pnl_pct, reason, duration_s))
    logger.info(f"CLOSE {symbol} {side} @ {price:.6g} | {reason} | PnL {pnl:+.2f} USDT ({pnl_pct:+.2f}%)")


# ── Scanning ──────────────────────────────────────────────────────────────────

async def scan_symbols(symbols: list[str]) -> list[dict]:
    """Fetch OHLCV for all symbols and run signal generation."""
    t0 = time.time()
    data = await fetch_universe_concurrent(symbols)
    signals = []

    for sym, ohlcv in data.items():
        if sym in _open_trades:
            continue
        try:
            p = ohlcv["p"]
            h = ohlcv.get("h")
            t = ohlcv.get("t")

            sig, metrics = generate_signal(
                p["high"], p["low"], p["close"], p["open"], p["volume"],
                h["high"] if h else None, h["low"] if h else None,
                h["close"] if h else None, h["open"] if h else None,
                h["volume"] if h else None,
                t["high"] if t else None, t["low"] if t else None,
                t["close"] if t else None,
                cfg,
            )

            if sig and metrics.get("confidence", 0) >= cfg.min_confidence:
                signals.append({"symbol": sym, "signal": sig, **metrics})

        except Exception as e:
            logger.debug(f"Signal error {sym}: {e}")

    elapsed = (time.time() - t0) * 1000
    n_buy  = sum(1 for s in signals if s["signal"] == "BUY")
    n_sell = sum(1 for s in signals if s["signal"] == "SELL")
    logger.info(f"Scan {len(data)} symbols in {elapsed:.0f}ms — 🟢{n_buy} 🔴{n_sell} signals")

    # Sort by confidence desc
    signals.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    return signals, elapsed, len(data)


# ── Order execution ───────────────────────────────────────────────────────────

async def execute_signal(sig_data: dict, balance: float) -> None:
    symbol = sig_data["symbol"]
    side   = sig_data["signal"]

    # Gates
    can, reason = risk.can_trade(balance)
    if not can:
        logger.debug(f"Skip {symbol}: {reason}")
        return

    if not risk.correlation_ok(symbol):
        logger.debug(f"Skip {symbol}: correlation block")
        return

    if symbol in _open_trades:
        return

    # Get current price
    price = await get_price(symbol)
    if price <= 0:
        return

    n_open   = len(_open_trades)
    conf     = sig_data.get("confidence", 50)
    atr_pct  = sig_data.get("atr_pct", 1.0)
    atr      = sig_data.get("atr", 0.0)
    size     = risk.position_size(balance, n_open, conf, atr_pct)
    sl, tp, sl_pct, tp_pct = risk.dynamic_sl_tp(price, side, atr)

    # Set leverage (one-way mode compatible)
    await set_leverage(symbol, cfg.leverage)

    # Place order — ONE-WAY MODE (no positionSide, no ReduceOnly)
    resp = await place_market_order(symbol, side, size, sl, tp)

    if resp.get("code", 0) not in (0, None) and resp.get("code", 0) != 200:
        err = resp.get("msg", str(resp))
        logger.warning(f"Order failed {symbol}: {err}")
        await send(msg_error(f"{symbol}: {err}"))
        await save_signal(symbol, side, sig_data, executed=False)
        return

    # Record trade
    metrics = {k: sig_data[k] for k in ("adx", "rsi", "atr_pct", "confidence", "delta1", "delta2", "delta3") if k in sig_data}
    trade_id = await save_trade_open(symbol, side, price, 0, size, sl, tp, metrics)
    await save_signal(symbol, side, sig_data, executed=True)

    opened_at = datetime.now(timezone.utc).isoformat()
    _open_trades[symbol] = {
        "trade_id":  trade_id,
        "side":      side,
        "entry":     price,
        "sl":        sl,
        "tp":        tp,
        "sl_pct":    sl_pct,
        "tp_pct":    tp_pct,
        "size":      size,
        "opened_at": opened_at,
        "opened_ts": time.time(),
        "metrics":   metrics,
    }
    risk.record_open(symbol)

    await send(msg_entry(symbol, side, price, size, sl, tp, sl_pct, tp_pct, {**metrics, "confidence": conf}))
    logger.info(f"OPEN {symbol} {side} @ {price:.6g} | size={size:.1f} USDT | SL={sl:.6g} TP={tp:.6g} | conf={conf:.0f}%")


# ── Dashboard state update ────────────────────────────────────────────────────

async def push_dashboard(balance: float, signals: list[dict], elapsed_ms: float, n_scanned: int) -> None:
    positions = await get_all_positions()
    risk_summary = risk.summary()
    perf = await get_performance_stats()

    n_buy  = sum(1 for s in signals if s.get("signal") == "BUY")
    n_sell = sum(1 for s in signals if s.get("signal") == "SELL")

    trade_metrics = {sym: t["metrics"] for sym, t in _open_trades.items()}

    update_state(
        status       = "halted" if risk.is_halted else "running",
        balance      = balance,
        positions    = positions,
        scan_stats   = {"last_ms": elapsed_ms, "n_scanned": n_scanned, "n_buy": n_buy, "n_sell": n_sell},
        risk         = risk_summary,
        perf         = perf,
        last_signals = signals[:20],
        trade_metrics= trade_metrics,
    )


# ── Main loop ─────────────────────────────────────────────────────────────────

async def main() -> None:
    global _running

    logger.info("UltraBot v3 starting…")
    os.makedirs("data", exist_ok=True)
    await init_db()

    if cfg.dashboard_enabled:
        await start_dashboard()

    start_sender()

    # Initial universe
    symbols = await get_universe()
    balance = await get_balance()
    risk.set_balance(balance)

    await send_now(msg_start(len(symbols)))
    logger.info(f"Balance: {balance:.2f} USDT | Universe: {len(symbols)} symbols")

    # Perf report every 6h
    last_perf_report = time.time()
    # Universe refresh every hour
    last_universe_refresh = time.time()

    iteration = 0
    while _running:
        try:
            # Refresh universe hourly
            if time.time() - last_universe_refresh > 3600:
                symbols = await get_universe()
                last_universe_refresh = time.time()

            # Monitor open positions
            if _open_trades:
                await monitor_positions()

            # Update balance
            balance = await get_balance()
            risk.set_balance(balance)

            if risk.is_halted:
                await push_dashboard(balance, [], 0, 0)
                if iteration % 30 == 0:
                    await send(msg_halt(risk.summary().get("halt_reason", "unknown")))
                await asyncio.sleep(cfg.scan_interval)
                iteration += 1
                continue

            # Scan
            signals, elapsed_ms, n_scanned = await scan_symbols(symbols)

            # Execute top signals
            for sig in signals[:3]:  # max 3 new trades per cycle
                if len(_open_trades) >= cfg.max_open_trades:
                    break
                await execute_signal(sig, balance)

            # Dashboard
            await push_dashboard(balance, signals, elapsed_ms, n_scanned)

            # Perf report every 6h
            if time.time() - last_perf_report > 21600:
                perf = await get_performance_stats()
                await send(msg_performance(perf, risk.summary()))
                last_perf_report = time.time()

            await asyncio.sleep(cfg.scan_interval)
            iteration += 1

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"Main loop error: {e}")
            await send(msg_error(str(e)))
            await asyncio.sleep(30)

    logger.info("Shutting down…")
    await close_session()


def _handle_signal(sig, frame):
    global _running
    logger.info(f"Received {sig}, stopping…")
    _running = False


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Configure loguru
    logger.remove()
    logger.add(sys.stdout, level="INFO", format="{time:HH:mm:ss} [{level}] {message}")
    logger.add("data/bot.log", level="DEBUG", rotation="50 MB", retention="7 days")

    asyncio.run(main())
