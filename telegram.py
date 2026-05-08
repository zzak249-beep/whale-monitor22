# -*- coding: utf-8 -*-
"""telegram.py -- Maki Bot PRO — Notificador Telegram."""
from __future__ import annotations
import asyncio
import re
from loguru import logger


def _esc(text: str) -> str:
    """Escapa caracteres especiales para MarkdownV2."""
    return re.sub(r'([_\*\[\]()~`>#+\-=|{}.!])', r'\\\1', str(text))


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self._token   = token
        self._chat_id = chat_id
        self._queue:  asyncio.Queue | None = None
        self._task:   asyncio.Task  | None = None

    def start(self):
        """Arranca el worker de envío. Llamar dentro del event loop."""
        self._queue = asyncio.Queue()
        self._task  = asyncio.create_task(self._worker())

    async def stop(self):
        if self._queue:
            await self._queue.join()
        if self._task:
            self._task.cancel()

    async def notify(self, text: str):
        """Encola un mensaje para enviar."""
        if self._queue is None:
            await self._send(text)
        else:
            await self._queue.put(text)

    async def _worker(self):
        while True:
            text = await self._queue.get()
            try:
                await self._send(text)
            except Exception as e:
                logger.warning(f"[TG worker] {e}")
            finally:
                self._queue.task_done()
            await asyncio.sleep(0.5)   # rate-limit Telegram

    async def _send(self, text: str, retries: int = 3) -> bool:
        if not self._token or not self._chat_id:
            logger.warning("[TG] Token o chat_id no configurados")
            return False

        import aiohttp
        url     = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload = {
            "chat_id":    self._chat_id,
            "text":       text,
            "parse_mode": "Markdown",
        }

        for attempt in range(1, retries + 1):
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.post(url, json=payload,
                                      timeout=aiohttp.ClientTimeout(total=10)) as r:
                        body = await r.text()
                        if r.status == 200:
                            return True
                        logger.warning(f"[TG] HTTP {r.status} intento {attempt}: {body[:150]}")
                        if r.status == 400:
                            logger.error(f"[TG] Formato inválido: {text[:200]}")
                            return False
            except asyncio.TimeoutError:
                logger.warning(f"[TG] Timeout intento {attempt}")
            except Exception as e:
                logger.warning(f"[TG] Error intento {attempt}: {e}")

            if attempt < retries:
                await asyncio.sleep(2 ** attempt)

        return False
