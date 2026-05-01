"""
THREE STEP BOT — scanner.py
============================
Descarga velas OHLCV para todos los símbolos en paralelo.
"""
from __future__ import annotations
import asyncio
from typing import Dict

from loguru import logger
import client as ex


async def fetch_universe(
    symbols: list[str],
    timeframe: str,
    max_concurrent: int = 8,
    limit: int = 150,
) -> Dict[str, dict]:
    """
    Descarga velas para todos los símbolos con semáforo controlado.
    Retorna dict {symbol: {"candles": [...]}}
    """
    sem     = asyncio.Semaphore(max_concurrent)
    results = {}

    async def _fetch_one(sym: str):
        async with sem:
            try:
                candles = await ex.get_klines(sym, timeframe, limit)
                if len(candles) >= 30:
                    results[sym] = {"candles": candles}
                else:
                    logger.warning(f"[SCANNER] {sym}: solo {len(candles)} velas")
            except Exception as e:
                logger.warning(f"[SCANNER] {sym}: {e}")
            await asyncio.sleep(0.1)

    await asyncio.gather(*[_fetch_one(s) for s in symbols])
    logger.debug(f"[SCANNER] OK: {len(results)}/{len(symbols)} símbolos")
    return results
