import aiohttp
import logging

logger = logging.getLogger("telegram")


async def send(token: str, chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload) as r:
                if r.status != 200:
                    logger.error(f"Telegram error {r.status}: {await r.text()}")
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
