# -*- coding: utf-8 -*-
"""telegram.py -- Maki Bot PRO — Notificador Telegram."""
from __future__ import annotations
import asyncio
import re
from loguru import logger


def _md_to_html(text: str) -> str:
    """
    Convierte formato Markdown básico (*bold*, `code`, _italic_) a HTML.
    Más robusto que Markdown nativo de Telegram.
    """
    # Escapar caracteres especiales HTML primero
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # *bold*
    text = re.sub(r'\*([^*\n]+)\*', r'<b>\1</b>', text)
    # `code`
    text = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', text)
    # _italic_  (solo si no forma parte de un identificador como H1_bull)
    text = re.sub(r'(?<!\w)_([^_\n]+)_(?!\w)', r'<i>\1</i>', text)
    return text


def _strip_format(text: str) -> str:
    """Elimina toda la sintaxis Markdown — fallback de último recurso."""
    text = re.sub(r'\*([^*\n]+)\*', r'\1', text)
    text = re.sub(r'`([^`\n]+)`',   r'\1', text)
    text = re.sub(r'_([^_\n]+)_',   r'\1', text)
    return text


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

        url = f"https://api.telegram.org/bot{self._token}/sendMessage"

        # Intentar primero con HTML (más robusto que Markdown)
        # Si falla 400, reintentar como texto plano sin formato
        attempts = [
            {"text": _md_to_html(text), "parse_mode": "HTML"},
            {"text": _strip_format(text), "parse_mode": None},
        ]

        for variant in attempts:
            payload: dict = {
                "chat_id": self._chat_id,
                "text":    variant["text"],
            }
            if variant["parse_mode"]:
                payload["parse_mode"] = variant["parse_mode"]

            for attempt in range(1, retries + 1):
                try:
                    async with aiohttp.ClientSession() as s:
                        async with s.post(
                            url, json=payload,
                            timeout=aiohttp.ClientTimeout(total=10)
                        ) as r:
                            body = await r.text()
                            if r.status == 200:
                                return True

                            logger.warning(
                                f"[TG] HTTP {r.status} "
                                f"parse={variant['parse_mode']} "
                                f"intento {attempt}: {body[:150]}"
                            )

                            if r.status == 400:
                                # No reintentar con el mismo formato
                                break

                except asyncio.TimeoutError:
                    logger.warning(f"[TG] Timeout intento {attempt}")
                except Exception as e:
                    logger.warning(f"[TG] Error intento {attempt}: {e}")

                if attempt < retries:
                    await asyncio.sleep(2 ** attempt)

        logger.error(f"[TG] Mensaje no enviado tras todos los intentos: {text[:80]}")
        return False
