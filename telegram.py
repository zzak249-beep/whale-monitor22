"""Telegram notifications for trading alerts."""
import asyncio
import aiohttp
from typing import Optional, Dict
from loguru import logger
from datetime import datetime

from core.config import cfg


class TelegramSender:
    """Async Telegram message sender with queue."""
    
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.queue: asyncio.Queue = asyncio.Queue()
        self.session: Optional[aiohttp.ClientSession] = None
        self.running = False
    
    async def _init_session(self) -> None:
        """Initialize HTTP session."""
        if self.session is None:
            self.session = aiohttp.ClientSession()
    
    async def send_message(self, text: str) -> bool:
        """Send a message to Telegram."""
        if not self.token or not self.chat_id:
            logger.warning("Telegram not configured")
            return False
        
        await self._init_session()
        
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        data = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        
        try:
            async with self.session.post(url, json=data) as resp:
                if resp.status == 200:
                    logger.debug("Telegram message sent")
                    return True
                else:
                    logger.warning(f"Telegram error: {resp.status}")
                    return False
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False
    
    async def start(self) -> None:
        """Start the message queue worker."""
        self.running = True
        while self.running:
            try:
                msg = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                await self.send_message(msg)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Queue worker error: {e}")
    
    async def queue_message(self, text: str) -> None:
        """Add a message to the queue."""
        await self.queue.put(text)
    
    async def close(self) -> None:
        """Close the sender."""
        self.running = False
        if self.session:
            await self.session.close()


# Global telegram instance
_sender: Optional[TelegramSender] = None


def start_sender() -> None:
    """Start the Telegram sender."""
    global _sender
    if cfg.telegram_token and cfg.telegram_chat_id:
        _sender = TelegramSender(cfg.telegram_token, cfg.telegram_chat_id)
        asyncio.create_task(_sender.start())
        logger.info("Telegram sender started")


async def send(text: str) -> None:
    """Queue a message to be sent."""
    if _sender:
        await _sender.queue_message(text)


async def send_now(text: str) -> None:
    """Send a message immediately (blocking)."""
    if _sender:
        await _sender.send_message(text)
    else:
        logger.info(f"[TELEGRAM] {text}")


# Message formatters
def msg_start(n_symbols: int) -> str:
    """Format startup message."""
    return f"""
🤖 <b>UltraBot v3 Starting</b>
━━━━━━━━━━━━━━━━━━
📊 Universe: {n_symbols} symbols
⏰ Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
🚀 Status: <b>LIVE</b>
"""


def msg_entry(
    symbol: str, side: str, price: float, size: float,
    sl: float, tp: float, sl_pct: float, tp_pct: float,
    metrics: Dict
) -> str:
    """Format entry message."""
    emoji = "🟢" if side == "BUY" else "🔴"
    conf = metrics.get("confidence", 0)
    return f"""
{emoji} <b>{side} ENTRY</b>
━━━━━━━━━━━━━━━━━━
💱 {symbol}
💰 Price: ${price:.8g}
📊 Size: {size:.2f} USDT
🎯 SL: ${sl:.8g} ({-sl_pct:.2f}%)
🏆 TP: ${tp:.8g} (+{tp_pct:.2f}%)
📈 Confidence: {conf:.0f}%
"""


def msg_close(
    symbol: str, side: str, pnl: float, pnl_pct: float,
    reason: str, duration_s: int
) -> str:
    """Format close message."""
    emoji = "✅" if pnl >= 0 else "❌"
    return f"""
{emoji} <b>{reason}</b>
━━━━━━━━━━━━━━━━━━
💱 {symbol}
💰 PnL: {pnl:+.2f} USDT ({pnl_pct:+.2f}%)
⏱️ Duration: {duration_s // 60}m {duration_s % 60}s
"""


def msg_performance(perf: Dict, risk: Dict) -> str:
    """Format performance report message."""
    return f"""
📊 <b>6-Hour Performance Report</b>
━━━━━━━━━━━━━━━━━━
✅ Wins: {perf.get('wins', 0)}
❌ Losses: {perf.get('losses', 0)}
📈 Win Rate: {perf.get('win_rate', 0):.1f}%
💰 Total PnL: {perf.get('total_pnl', 0):+.2f} USDT
📊 Avg/Trade: {perf.get('avg_trade', 0):+.2f} USDT
⚠️ Daily PnL: {risk.get('daily_pnl', 0):+.2f} USDT
"""


def msg_halt(reason: str) -> str:
    """Format halt message."""
    return f"""
🛑 <b>BOT HALTED</b>
━━━━━━━━━━━━━━━━━━
⚠️ Reason: {reason}
⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""


def msg_error(error: str) -> str:
    """Format error message."""
    return f"""
❌ <b>ERROR</b>
━━━━━━━━━━━━━━━━━━
📌 {error}
⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
