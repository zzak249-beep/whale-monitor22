"""
Notificador Telegram con cola y reintentos.
Solo usa stdlib + aiohttp.
"""
import asyncio
import logging
import aiohttp

logger     = logging.getLogger("telegram")
MAX_LEN    = 4096
MAX_RETRY  = 3


async def _send_once(token: str, chat_id: str, text: str) -> bool:
    """Intenta enviar un mensaje. Retorna True si tuvo éxito."""
    if len(text) > MAX_LEN:
        text = text[:MAX_LEN - 3] + "..."
    url     = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        ) as sess:
            async with sess.post(url, json=payload) as r:
                if r.status == 200:
                    return True
                body = await r.text()
                # Si falla por Markdown inválido → reintentar sin formato
                if r.status == 400:
                    payload.pop("parse_mode", None)
                    async with sess.post(url, json=payload) as r2:
                        return r2.status == 200
                logger.error(f"Telegram {r.status}: {body[:200]}")
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
    return False


async def send(token: str, chat_id: str, text: str):
    """Envía un mensaje con reintentos."""
    for attempt in range(MAX_RETRY):
        ok = await _send_once(token, chat_id, text)
        if ok:
            return
        if attempt < MAX_RETRY - 1:
            await asyncio.sleep(2 ** attempt)
    logger.error(f"Telegram: mensaje descartado tras {MAX_RETRY} intentos")


class TelegramNotifier:
    """
    Cola FIFO asíncrona para enviar mensajes sin bloquear el loop del bot.
    Respeta el límite de ~30 mensajes/segundo de Telegram.
    """

    def __init__(self, token: str, chat_id: str):
        self.token   = token
        self.chat_id = chat_id
        self._q: asyncio.Queue = asyncio.Queue()
        self._task = None

    def start(self):
        self._task = asyncio.create_task(self._worker())

    async def _worker(self):
        while True:
            text = await self._q.get()
            await send(self.token, self.chat_id, text)
            await asyncio.sleep(0.4)   # max ~2.5 msg/s, bien por debajo del límite
            self._q.task_done()

    async def notify(self, text: str):
        """Encola un mensaje (no bloqueante)."""
        await self._q.put(text)

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
