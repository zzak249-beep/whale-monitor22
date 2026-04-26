"""
TELEGRAM — Notificaciones en tiempo real
──────────────────────────────────────────
Envía alertas formateadas con emoji y datos clave.
Cola asíncrona para no bloquear el bot principal.
"""
import asyncio
from typing import Optional
from datetime import datetime, timezone

import aiohttp

import config
from utils.logger import get_logger

log = get_logger("Telegram")


class TelegramNotifier:

    BASE_URL = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}"

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self):
        """Inicia el worker de mensajes en background."""
        asyncio.create_task(self._worker())
        log.info("Telegram worker iniciado")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _send(self, text: str, parse_mode: str = "HTML"):
        session = await self._get_session()
        try:
            async with session.post(
                f"{self.BASE_URL}/sendMessage",
                json={
                    "chat_id":    config.TELEGRAM_CHAT_ID,
                    "text":       text,
                    "parse_mode": parse_mode,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.error(f"Telegram error {resp.status}: {body[:100]}")
        except Exception as e:
            log.error(f"Telegram send error: {e}")

    async def _worker(self):
        """Procesa la cola de mensajes con rate limit (30 msg/seg Telegram)."""
        while True:
            try:
                text = await self._queue.get()
                await self._send(text)
                await asyncio.sleep(0.5)   # Rate limit suave
                self._queue.task_done()
            except Exception as e:
                log.error(f"Telegram worker error: {e}")
                await asyncio.sleep(1)

    def _push(self, text: str):
        """Encola mensaje sin bloquear."""
        try:
            self._queue.put_nowait(text)
        except asyncio.QueueFull:
            log.warning("Cola Telegram llena — mensaje descartado")

    # ──────────────────────────────────────────────────────────────────────────
    # MENSAJES PREDEFINIDOS
    # ──────────────────────────────────────────────────────────────────────────

    def bot_started(self, symbols: list[str], dry_run: bool):
        emoji = "🧪" if dry_run else "🚀"
        mode  = "PAPER TRADING" if dry_run else "LIVE TRADING"
        self._push(
            f"{emoji} <b>BOT INICIADO [{mode}]</b>\n"
            f"📊 Símbolos: {', '.join(symbols)}\n"
            f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )

    def signal_detected(self, symbol: str, direction: str, score: int,
                        confidence: str, entry: float, sl: float, tp: float,
                        rr: float, reasons: list[str]):
        dir_emoji = "🟢" if direction == "LONG" else "🔴"
        conf_emoji = {"HIGH": "🔥", "MEDIUM": "✅", "LOW": "⚠️"}.get(confidence, "")
        self._push(
            f"{dir_emoji} <b>{symbol} {direction}</b> {conf_emoji} <b>{confidence}</b>\n"
            f"📊 Score: <b>{score}/10</b>\n"
            f"💰 Entry: <code>{entry:.4f}</code>\n"
            f"🛑 SL: <code>{sl:.4f}</code>\n"
            f"🎯 TP: <code>{tp:.4f}</code>\n"
            f"⚖️ RR: <b>{rr:.1f}x</b>\n"
            f"✅ Filtros: {' | '.join(reasons)}"
        )

    def order_placed(self, symbol: str, direction: str, qty: float,
                     entry: float, sl: float, tp: float, dry_run: bool):
        mode = "[PAPER] " if dry_run else ""
        dir_emoji = "📈" if direction == "LONG" else "📉"
        self._push(
            f"{dir_emoji} <b>{mode}ORDEN EJECUTADA</b>\n"
            f"🪙 {symbol} {direction} × {qty}\n"
            f"💰 Entry: <code>{entry:.4f}</code>\n"
            f"🛑 SL: <code>{sl:.4f}</code>\n"
            f"🎯 TP: <code>{tp:.4f}</code>"
        )

    def trade_closed(self, symbol: str, direction: str, pnl: float,
                     entry: float, exit_price: float):
        emoji = "✅ GANADA" if pnl > 0 else "❌ PERDIDA"
        self._push(
            f"{emoji}\n"
            f"🪙 {symbol} {direction}\n"
            f"📊 Entry: <code>{entry:.4f}</code> → Exit: <code>{exit_price:.4f}</code>\n"
            f"💵 PnL: <b>{'+'if pnl>0 else ''}{pnl:.2f} USDT</b>"
        )

    def daily_stats(self, stats: dict):
        pnl = stats.get("pnl", 0)
        emoji = "📈" if pnl >= 0 else "📉"
        self._push(
            f"{emoji} <b>RESUMEN DEL DÍA</b>\n"
            f"💰 PnL: <b>{'+' if pnl>=0 else ''}{pnl:.2f} USDT</b>\n"
            f"📊 Trades: {stats['trades']} | "
            f"✅ {stats['wins']} | ❌ {stats['losses']}\n"
            f"🏆 Winrate: <b>{stats['winrate']:.1f}%</b>"
        )

    def error_alert(self, message: str):
        self._push(f"⚠️ <b>ERROR BOT</b>\n<code>{message[:300]}</code>")

    def daily_limit_reached(self, pnl: float):
        self._push(
            f"🛑 <b>LÍMITE DIARIO ALCANZADO</b>\n"
            f"💸 PnL hoy: <b>{pnl:+.2f} USDT</b>\n"
            f"⏸ Bot pausado hasta mañana (UTC)"
        )
