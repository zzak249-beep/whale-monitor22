# -*- coding: utf-8 -*-
"""notifier.py -- Telegram notifications."""
from __future__ import annotations
import aiohttp
from loguru import logger


async def notify(text: str) -> None:
    from config import cfg
    if not cfg.telegram_token or not cfg.telegram_chat_id:
        return
    url = f"https://api.telegram.org/bot{cfg.telegram_token}/sendMessage"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json={
                "chat_id": cfg.telegram_chat_id,
                "text": text,
                "parse_mode": "Markdown",
            }, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    body = await r.text()
                    logger.warning(f"Telegram {r.status}: {body[:120]}")
    except Exception as e:
        logger.warning(f"Telegram failed: {e}")
