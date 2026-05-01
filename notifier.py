"""
THREE STEP BOT — notifier.py
==============================
Notificaciones Telegram con entradas, salidas, PnL y alertas.
Todas las órdenes ejecutadas se notifican con resultado y PnL.
"""
from __future__ import annotations
import asyncio
import time
from typing import Optional

import aiohttp
from loguru import logger

from config import cfg

_QUEUE:   asyncio.Queue = asyncio.Queue(maxsize=200)
_TASK:    Optional[asyncio.Task] = None
_SESSION: Optional[aiohttp.ClientSession] = None


async def _worker():
    global _SESSION
    timeout = aiohttp.ClientTimeout(total=10)
    _SESSION = aiohttp.ClientSession(timeout=timeout)
    while True:
        try:
            text = await _QUEUE.get()
            await _send_raw(text)
            await asyncio.sleep(0.3)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"[TG worker] {e}")


async def _send_raw(text: str):
    if not cfg.tg_token or not cfg.tg_chat_id:
        return
    url = f"https://api.telegram.org/bot{cfg.tg_token}/sendMessage"
    payload = {
        "chat_id":    cfg.tg_chat_id,
        "text":       text,
        "parse_mode": "HTML",
    }
    for attempt in range(3):
        try:
            async with _SESSION.post(url, json=payload) as r:
                if r.status == 200:
                    return
                body = await r.text()
                logger.warning(f"[TG] {r.status}: {body[:100]}")
                return
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
            else:
                logger.warning(f"[TG] fallo: {e}")


async def start_notifier():
    """Iniciar worker de Telegram en background."""
    global _TASK
    _TASK = asyncio.create_task(_worker())
    logger.info("Notifier Telegram activo")


def notify(text: str):
    """No bloqueante — encola el mensaje."""
    if not cfg.tg_token or not cfg.tg_chat_id:
        return
    try:
        _QUEUE.put_nowait(text)
    except asyncio.QueueFull:
        logger.warning("[TG] queue llena")


# ── Mensajes específicos ─────────────────────────────────────────────────────

def notify_entry(
    symbol: str,
    side: str,
    price: float,
    sl: float,
    tp1: float,
    tp2: float,
    size_usdt: float,
    leverage: int,
    qty: float,
    delta1: float,
    delta2: float,
    delta3: float,
    vol_ratio: float,
):
    """Notifica apertura de trade con todos los detalles."""
    emoji  = "🟢" if side == "BUY" else "🔴"
    dir_   = "LONG" if side == "BUY" else "SHORT"
    sl_pct = abs(price - sl) / price * 100
    tp_pct = abs(price - tp1) / price * 100
    mode   = "🟡 TESTNET" if cfg.testnet else "🔴 LIVE"

    text = (
        f"{emoji} <b>{dir_} — {symbol}</b>  {mode}\n"
        f"{'─'*28}\n"
        f"📍 Entrada:  <code>{price:.6g}</code>\n"
        f"🛑 SL:       <code>{sl:.6g}</code>  <i>−{sl_pct:.2f}%</i>\n"
        f"🎯 TP1:      <code>{tp1:.6g}</code>  <i>+{tp_pct:.2f}%</i>\n"
        f"🎯 TP2:      <code>{tp2:.6g}</code>\n"
        f"{'─'*28}\n"
        f"💰 Capital:  <code>{size_usdt} USDT ×{leverage}</code>\n"
        f"📦 Qty:      <code>{qty:.4f}</code>\n"
        f"🌊 Delta:    D1=<code>{delta1:+.0f}</code>  D2=<code>{delta2:+.0f}</code>  D3=<code>{delta3:+.0f}</code>\n"
        f"📊 Vol ratio: <code>{vol_ratio:.1f}×</code>"
    )
    notify(text)


def notify_tp(
    symbol: str,
    side: str,
    tp_num: int,
    tp_price: float,
    entry: float,
    qty_closed: float,
    pnl_usdt: float,
    pnl_pct: float,
):
    """Notifica TP parcial o total alcanzado."""
    emoji   = "✅"
    sign    = "+" if pnl_usdt >= 0 else ""
    dir_    = "LONG" if side == "BUY" else "SHORT"
    text = (
        f"{emoji} <b>TP{tp_num} — {symbol}</b>\n"
        f"Par: <code>{dir_}</code>  @  <code>{tp_price:.6g}</code>\n"
        f"Entrada fue:  <code>{entry:.6g}</code>\n"
        f"Cerrado qty:  <code>{qty_closed:.4f}</code>\n"
        f"{'─'*24}\n"
        f"💵 PnL: <b><code>{sign}{pnl_usdt:.2f} USDT</code></b>  (<code>{sign}{pnl_pct:.2f}%</code>)\n"
        f"<i>Stop movido a breakeven</i>"
    )
    notify(text)


def notify_stop(
    symbol: str,
    side: str,
    stop_price: float,
    entry: float,
    qty: float,
    pnl_usdt: float,
    pnl_pct: float,
    reason: str = "STOP",
):
    """Notifica cierre por stop loss con PnL real."""
    win     = pnl_usdt >= 0
    emoji   = "✅" if win else "❌"
    sign    = "+" if pnl_usdt >= 0 else ""
    dir_    = "LONG" if side == "BUY" else "SHORT"
    text = (
        f"{emoji} <b>{reason} — {symbol}</b>\n"
        f"Par: <code>{dir_}</code>  @  <code>{stop_price:.6g}</code>\n"
        f"Entrada fue:  <code>{entry:.6g}</code>\n"
        f"Qty cerrada:  <code>{qty:.4f}</code>\n"
        f"{'─'*24}\n"
        f"💵 PnL: <b><code>{sign}{pnl_usdt:.2f} USDT</code></b>  (<code>{sign}{pnl_pct:.2f}%</code>)"
    )
    notify(text)


def notify_breakeven(symbol: str, be_price: float):
    """Notifica que el stop fue movido a breakeven."""
    notify(
        f"🔒 <b>Breakeven — {symbol}</b>\n"
        f"Stop movido a entrada: <code>{be_price:.6g}</code>\n"
        f"<i>Operación sin riesgo</i>"
    )


def notify_error(symbol: str, error: str):
    notify(f"🆘 <b>ERROR — {symbol}</b>\n<code>{str(error)[:200]}</code>")


def notify_stats(stats: dict):
    """Resumen periódico de performance."""
    wins   = stats.get("wins", 0)
    losses = stats.get("losses", 0)
    total  = wins + losses
    wr     = wins / total * 100 if total > 0 else 0
    pnl    = stats.get("total_pnl", 0)
    sign   = "+" if pnl >= 0 else ""
    pnl_e  = "📈" if pnl >= 0 else "📉"
    text = (
        f"📊 <b>ESTADÍSTICAS — Three Step Bot</b>\n"
        f"{'─'*26}\n"
        f"🏆 Win rate:  <code>{wr:.1f}%</code>  ({wins}W / {losses}L)\n"
        f"{pnl_e} PnL total:  <code>{sign}{pnl:.2f} USDT</code>\n"
        f"📂 Posiciones abiertas: <code>{stats.get('open', 0)}</code>\n"
        f"🔢 Trades totales: <code>{total}</code>"
    )
    notify(text)
