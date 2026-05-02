"""
Módulo Telegram — v2
Cola asíncrona para no saturar la API de Telegram (rate-limit: ~30 msg/s por chat).
Retry automático. Truncado a 4096 chars (límite de Telegram).
"""
import asyncio
import logging
import aiohttp

logger      = logging.getLogger("telegram")
MAX_LENGTH  = 4096
MAX_RETRIES = 3


async def send(token: str, chat_id: str, text: str):
    """Envía un mensaje con reintentos. Modo Markdown."""
    if len(text) > MAX_LENGTH:
        text = text[:MAX_LENGTH - 3] + "..."
    url     = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}

    for attempt in range(MAX_RETRIES):
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as s:
                async with s.post(url, json=payload) as r:
                    if r.status == 200:
                        return
                    body = await r.text()
                    # Si falla por Markdown inválido, reintentar sin formato
                    if r.status == 400 and "parse_mode" in payload:
                        logger.warning("Telegram 400 — reintentando sin Markdown")
                        payload.pop("parse_mode")
                        continue
                    logger.error(f"Telegram {r.status}: {body}")
                    return
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                logger.error(f"Telegram send fallido: {e}")
            else:
                await asyncio.sleep(2 ** attempt)


class TelegramNotifier:
    """
    Wrapper con cola FIFO.
    Garantiza al menos 500ms entre mensajes para evitar rate-limiting.
    """
    def __init__(self, token: str, chat_id: str):
        self.token   = token
        self.chat_id = chat_id
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task:  asyncio.Task | None = None

    def start(self):
        self._task = asyncio.create_task(self._worker())

    async def _worker(self):
        while True:
            text = await self._queue.get()
            await send(self.token, self.chat_id, text)
            await asyncio.sleep(0.5)
            self._queue.task_done()

    async def notify(self, text: str):
        """Encola un mensaje (no bloqueante)."""
        await self._queue.put(text)

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
