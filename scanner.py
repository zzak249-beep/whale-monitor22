# -*- coding: utf-8 -*-
"""scanner.py -- Concurrent OHLCV fetcher."""
from __future__ import annotations
import asyncio
from loguru import logger
from client import fetch_ohlcv


async def fetch_universe(symbols: list[str], timeframe: str,
                         max_concurrent: int = 15) -> dict[str, dict]:
    results: dict[str, dict] = {}
    sem = asyncio.Semaphore(max_concurrent)

    async def _one(sym: str) -> None:
        async with sem:
            data = await fetch_ohlcv(sym, timeframe, limit=300)
            if data is not None:
                results[sym] = data

    await asyncio.gather(*[asyncio.create_task(_one(s)) for s in symbols],
                         return_exceptions=True)
    logger.debug(f"Fetched {len(results)}/{len(symbols)} symbols")
    return results
