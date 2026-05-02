# -*- coding: utf-8 -*-
"""notifier.py -- Three Step Bot v4 — Rich Telegram Notifications.

Sends detailed trade reports:
  - ENTRY: symbol, side, price, SL, TP, size, score
  - BREAKEVEN: moved SL to entry
  - PARTIAL TP: closed 50%, amount received
  - EXIT (trail/SL/TP): price, R achieved, PnL in USDT, win/loss emoji
  - DAILY SUMMARY: total trades, wins, losses, net PnL
  - HALT/RESUME: circuit breaker alerts
"""
from __future__ import annotations
import aiohttp
from loguru import logger


async def _send(text: str) -> None:
    from config import cfg
    if not cfg.telegram_token or not cfg.telegram_chat_id:
        return
    url = f"https://api.telegram.org/bot{cfg.telegram_token}/sendMessage"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json={
                "chat_id":    cfg.telegram_chat_id,
                "text":       text,
                "parse_mode": "Markdown",
            }, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    body = await r.text()
                    logger.warning(f"Telegram {r.status}: {body[:120]}")
    except Exception as e:
        logger.warning(f"Telegram failed: {e}")


# ── Public API ────────────────────────────────────────────────────────────────

async def notify(text: str) -> None:
    """Generic notification."""
    await _send(text)


async def notify_entry(
    symbol: str, side: str, price: float,
    sl: float, tp: float, size_usdt: float,
    leverage: int, qty: float, score: int,
    delta1: float, delta2: float, vol_ratio: float,
) -> None:
    side_emoji = "🟢 LONG" if side == "BUY" else "🔴 SHORT"
    stars = "⭐" * score
    sl_pct = abs(price - sl) / price * 100
    tp_pct = abs(tp - price) / price * 100
    rr = round(tp_pct / sl_pct, 1) if sl_pct > 0 else 0
    exposure = size_usdt * leverage

    await _send(
        f"🚀 *ENTRADA* — {symbol}\n"
        f"┣ Dirección: *{side_emoji}*\n"
        f"┣ Precio entrada: `{price:.6f}`\n"
        f"┣ Stop Loss:  `{sl:.6f}` (-{sl_pct:.2f}%)\n"
        f"┣ Take Profit: `{tp:.6f}` (+{tp_pct:.2f}%)\n"
        f"┣ RR: `1:{rr}` | Score: {stars} ({score}/5)\n"
        f"┣ Tamaño: `{size_usdt} USDT ×{leverage}` = `{exposure:.0f} USDT exp.`\n"
        f"┣ Cantidad: `{qty:.6f}` unidades\n"
        f"┗ Vol ratio: `{vol_ratio:.1f}x` | Δ1:`{delta1:+.0f}` Δ2:`{delta2:+.0f}`"
    )


async def notify_breakeven(symbol: str, side: str, entry: float, r_at_be: float) -> None:
    await _send(
        f"🔒 *BREAKEVEN* — {symbol}\n"
        f"┣ SL movido a precio de entrada: `{entry:.6f}`\n"
        f"┣ Dirección: `{side}`\n"
        f"┗ R en el momento: `{r_at_be:.2f}R` — riesgo eliminado ✅"
    )


async def notify_partial(
    symbol: str, qty_closed: float, qty_remaining: float,
    price: float, pnl_usdt: float,
) -> None:
    emoji = "✅" if pnl_usdt >= 0 else "❌"
    await _send(
        f"✂️ *CIERRE PARCIAL* — {symbol}\n"
        f"┣ Cerrado 50% a breakeven\n"
        f"┣ Precio: `{price:.6f}`\n"
        f"┣ Cantidad cerrada: `{qty_closed:.6f}`\n"
        f"┣ Cantidad restante: `{qty_remaining:.6f}`\n"
        f"┗ PnL parcial: {emoji} `{pnl_usdt:+.4f} USDT`"
    )


async def notify_exit(
    symbol: str, side: str,
    entry: float, exit_price: float,
    qty: float, size_usdt: float, leverage: int,
    r_achieved: float, peak_r: float,
    exit_reason: str,   # "TRAIL" | "SL" | "TP" | "MANUAL"
) -> None:
    pnl_pct = ((exit_price - entry) / entry * 100) if side == "BUY" \
              else ((entry - exit_price) / entry * 100)
    pnl_usdt = pnl_pct / 100 * size_usdt * leverage

    if exit_reason == "TP":
        header = "🎯 *TAKE PROFIT*"
        result_emoji = "✅ GANANCIA"
    elif exit_reason == "SL":
        header = "🛑 *STOP LOSS*"
        result_emoji = "❌ PÉRDIDA"
    elif exit_reason == "TRAIL":
        header = "🎯 *SALIDA TRAILING*"
        result_emoji = "✅ GANANCIA" if pnl_usdt >= 0 else "❌ PÉRDIDA"
    else:
        header = "📤 *SALIDA*"
        result_emoji = "✅ GANANCIA" if pnl_usdt >= 0 else "❌ PÉRDIDA"

    await _send(
        f"{header} — {symbol}\n"
        f"┣ Resultado: *{result_emoji}*\n"
        f"┣ Dirección: `{side}`\n"
        f"┣ Entrada: `{entry:.6f}`\n"
        f"┣ Salida: `{exit_price:.6f}`\n"
        f"┣ PnL: `{pnl_usdt:+.4f} USDT` ({pnl_pct:+.2f}%)\n"
        f"┣ R alcanzado: `{r_achieved:.2f}R` | Peak R: `{peak_r:.2f}R`\n"
        f"┗ Razón salida: `{exit_reason}`"
    )


async def notify_daily_summary(
    total_trades: int, wins: int, losses: int,
    net_pnl: float, balance: float,
) -> None:
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    emoji = "📈" if net_pnl >= 0 else "📉"
    await _send(
        f"{emoji} *RESUMEN DIARIO*\n"
        f"┣ Trades totales: `{total_trades}`\n"
        f"┣ Ganados: `{wins}` ✅ | Perdidos: `{losses}` ❌\n"
        f"┣ Win rate: `{win_rate:.1f}%`\n"
        f"┣ PnL neto: `{net_pnl:+.4f} USDT`\n"
        f"┗ Balance actual: `{balance:.2f} USDT`"
    )
