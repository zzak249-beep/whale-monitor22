"""TELEGRAM NOTIFIER — Alertas al canal de Telegram"""
import asyncio
from typing import List, Optional
import aiohttp
import config
from bot_logger import get_logger

log = get_logger("Telegram")

class TelegramNotifier:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._enabled = bool(config.TELEGRAM_TOKEN and config.TELEGRAM_CHAT_ID)

    async def start(self):
        if self._enabled:
            self._session = aiohttp.ClientSession()
            log.info("Telegram conectado")
        else:
            log.warning("Telegram desactivado (TOKEN/CHAT_ID vacíos)")

    async def _send(self, text: str):
        if not self._enabled or not self._session: return
        try:
            url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
            async with self._session.post(url, json={"chat_id": config.TELEGRAM_CHAT_ID,
                                                      "text": text, "parse_mode": "HTML"},
                                          timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    log.warning(f"Telegram {r.status}: {(await r.text())[:100]}")
        except Exception as e:
            log.warning(f"Telegram error: {e}")

    def _fire(self, text: str):
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running(): asyncio.ensure_future(self._send(text))
            else: loop.run_until_complete(self._send(text))
        except Exception as e:
            log.warning(f"Telegram fire: {e}")

    def bot_started(self, symbols: list, dry_run: bool):
        mode = "🧪 PAPER" if dry_run else "🔴 LIVE"
        self._fire(f"🤖 <b>CryptoBot iniciado</b>\nModo: {mode}\n"
                   f"Símbolos: {', '.join(symbols)}\n"
                   f"Riesgo: {config.RISK_PER_TRADE}% | Lev: {config.LEVERAGE}x | RR: {config.RISK_REWARD}x")

    def signal_detected(self, symbol: str, direction: str, score: float, confidence: str,
                        entry: float, sl: float, tp: float, rr: float, reasons: List[str]):
        emoji = "📈" if direction == "LONG" else "📉"
        conf_e = {"HIGH":"🟢","MEDIUM":"🟡","LOW":"🔴"}.get(confidence,"⚪")
        self._fire(f"{emoji} <b>SEÑAL</b> {symbol} {direction}\n"
                   f"Score: <b>{score:.0f}/100</b> {conf_e} {confidence}\n"
                   f"Entrada: <code>{entry:.4f}</code>  SL: <code>{sl:.4f}</code>  TP: <code>{tp:.4f}</code>\n"
                   f"RR: <b>{rr:.2f}x</b>\n• " + "\n• ".join(reasons))

    def order_placed(self, symbol: str, direction: str, qty: float,
                     entry: float, sl: float, tp: float, dry_run: bool):
        label = "🧪 PAPER" if dry_run else "✅ ORDEN"
        self._fire(f"{label} <b>{symbol} {direction}</b>\n"
                   f"Qty: <code>{qty:.4f}</code>  Entrada: <code>{entry:.4f}</code>\n"
                   f"SL: <code>{sl:.4f}</code>  TP: <code>{tp:.4f}</code>")

    def error_alert(self, message: str):
        self._fire(f"⚠️ {message}")

    def daily_stats(self, stats: dict):
        pnl  = stats.get("pnl", 0)
        sign = "+" if pnl >= 0 else ""
        self._fire(f"📊 <b>Reporte Diario — {stats.get('date','')}</b>\n"
                   f"PnL: <b>{sign}{pnl:.2f} USDT</b>\n"
                   f"Trades: {stats.get('trades',0)}  ✅{stats.get('wins',0)}  ❌{stats.get('losses',0)}\n"
                   f"Winrate: <b>{stats.get('winrate',0):.1f}%</b>")
