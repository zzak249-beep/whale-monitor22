import aiohttp
import logging

logger = logging.getLogger("telegram")


async def send(token: str, chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload) as r:
                body = await r.json()
                if r.status != 200 or not body.get("ok"):
                    logger.error(f"Telegram error {r.status}: {body}")
                    return False
                return True
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False
