# -*- coding: utf-8 -*-
"""client.py -- Three Step Bot v5 — BingX Async Client.

Added in v5:
  - fetch_funding_rate(symbol) — used by strategy to filter bad entries
  - All previous fixes retained
"""
from __future__ import annotations
import asyncio
import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

import aiohttp
from loguru import logger

BASE_URL = "https://open-api.bingx.com"
_session: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        from config import cfg
        connector = aiohttp.TCPConnector(limit=200, ttl_dns_cache=300, ssl=False)
        _session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=cfg.http_timeout),
        )
    return _session


async def close_session() -> None:
    global _session
    if _session and not _session.closed:
        await _session.close()


def _sign(params: dict, secret: str) -> str:
    qs = urlencode(sorted(params.items()))
    return hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()


def _auth_params(params: dict | None = None) -> dict:
    from config import cfg
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000)
    p["signature"] = _sign(p, cfg.bingx_secret_key)
    return p


def _headers() -> dict:
    from config import cfg
    return {"X-BX-APIKEY": cfg.bingx_api_key}


async def _request(method: str, path: str, params: dict | None = None,
                   auth: bool = True, retries: int = 3) -> Any:
    sess = _get_session()
    p = _auth_params(params) if auth else (params or {})
    headers = _headers() if auth else {}
    url = BASE_URL + path
    for attempt in range(retries):
        try:
            if method == "GET":
                async with sess.get(url, params=p, headers=headers) as r:
                    return await r.json(content_type=None)
            elif method == "POST":
                async with sess.post(url, params=p, headers=headers) as r:
                    return await r.json(content_type=None)
            elif method == "DELETE":
                async with sess.delete(url, params=p, headers=headers) as r:
                    return await r.json(content_type=None)
        except asyncio.TimeoutError:
            wait = 1.5 ** attempt
            logger.warning(f"{method} {path} timeout #{attempt+1} — retry {wait:.1f}s")
            if attempt < retries - 1:
                await asyncio.sleep(wait)
        except Exception as e:
            logger.warning(f"{method} {path} error: {e}")
            return {}
    return {}


async def _get(path: str, params: dict | None = None, auth: bool = False) -> Any:
    return await _request("GET", path, params, auth=auth)

async def _post(path: str, params: dict | None = None) -> Any:
    return await _request("POST", path, params, auth=True)

async def _delete(path: str, params: dict | None = None) -> Any:
    return await _request("DELETE", path, params, auth=True)


# ── Market data ───────────────────────────────────────────────────────────────

async def fetch_klines(symbol: str, interval: str, limit: int = 300) -> list[list]:
    resp = await _get("/openApi/swap/v3/quote/klines",
                      {"symbol": symbol, "interval": interval, "limit": limit})
    data = resp.get("data", []) if isinstance(resp, dict) else []
    return data if isinstance(data, list) else []


async def fetch_ohlcv(symbol: str, tf: str, limit: int = 300) -> dict | None:
    import numpy as np
    raw = await fetch_klines(symbol, tf, limit=limit)
    if len(raw) < 50:
        return None
    try:
        opens  = np.array([float(c[1]) for c in raw], dtype=np.float64)
        highs  = np.array([float(c[2]) for c in raw], dtype=np.float64)
        lows   = np.array([float(c[3]) for c in raw], dtype=np.float64)
        closes = np.array([float(c[4]) for c in raw], dtype=np.float64)
        vols   = np.array([float(c[5]) for c in raw], dtype=np.float64)
        return {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols}
    except Exception as e:
        logger.debug(f"OHLCV parse {symbol}: {e}")
        return None


async def fetch_funding_rate(symbol: str) -> float:
    """Fetch current funding rate for symbol. Returns 0.0 on failure.
    Positive = longs pay shorts. Negative = shorts pay longs."""
    resp = await _get("/openApi/swap/v2/quote/premiumIndex", {"symbol": symbol})
    try:
        data = resp.get("data", {})
        if isinstance(data, list) and data:
            data = data[0]
        rate = float(data.get("lastFundingRate", data.get("fundingRate", 0)))
        return rate
    except Exception:
        return 0.0


async def fetch_all_funding_rates(symbols: list[str]) -> dict[str, float]:
    """Batch fetch funding rates for all symbols."""
    rates: dict[str, float] = {}
    # Try batch endpoint first
    resp = await _get("/openApi/swap/v2/quote/premiumIndex")
    try:
        data = resp.get("data", [])
        if isinstance(data, list):
            for item in data:
                sym  = item.get("symbol", "")
                rate = float(item.get("lastFundingRate", item.get("fundingRate", 0)))
                rates[sym] = rate
    except Exception:
        pass

    # Fallback: fetch missing symbols individually
    missing = [s for s in symbols if s not in rates]
    for sym in missing:
        rates[sym] = await fetch_funding_rate(sym)
    return rates


# ── Account ───────────────────────────────────────────────────────────────────

async def get_balance() -> float:
    resp = await _get("/openApi/swap/v2/user/balance", auth=True)
    try:
        data = resp.get("data", {})
        if isinstance(data, dict):
            bal = data.get("balance", {})
            if isinstance(bal, dict):
                return float(bal.get("availableMargin", bal.get("balance", 0)))
            return float(data.get("availableMargin", data.get("equity", 0)))
    except Exception as e:
        logger.warning(f"get_balance error: {e}")
    return 0.0


async def get_all_positions() -> dict[str, dict]:
    resp = await _get("/openApi/swap/v2/user/positions", auth=True)
    try:
        data = resp.get("data", [])
        if isinstance(data, list):
            return {p["symbol"]: p for p in data
                    if abs(float(p.get("positionAmt", 0))) > 1e-9}
    except Exception as e:
        logger.warning(f"get_positions error: {e}")
    return {}


# ── Trading ───────────────────────────────────────────────────────────────────

async def set_leverage(symbol: str, leverage: int) -> Any:
    await _post("/openApi/swap/v2/trade/leverage",
                {"symbol": symbol, "side": "LONG",  "leverage": leverage})
    return await _post("/openApi/swap/v2/trade/leverage",
                       {"symbol": symbol, "side": "SHORT", "leverage": leverage})


async def place_market_order(symbol: str, side: str, size_usdt: float,
                             sl: float, tp: float) -> dict:
    params: dict[str, Any] = {
        "symbol":        symbol,
        "side":          side,
        "type":          "MARKET",
        "quoteOrderQty": size_usdt,
        "stopLoss":      str(sl),
        "takeProfit":    str(tp),
    }
    resp = await _post("/openApi/swap/v2/trade/order", params)
    code = resp.get("code", -1)
    if code not in (0, 200, None):
        logger.warning(f"[ORDER FAIL] {symbol} {side} code={code} msg={resp.get('msg','')} sl={sl} tp={tp}")
    return resp if isinstance(resp, dict) else {"raw": resp}


async def place_reduce_order(symbol: str, side: str, quantity: float) -> dict:
    resp = await _post("/openApi/swap/v2/trade/order", {
        "symbol": symbol, "side": side, "type": "MARKET",
        "quantity": quantity, "reduceOnly": "true",
    })
    return resp if isinstance(resp, dict) else {}


async def close_position(symbol: str, position: dict) -> Any:
    resp = await _post("/openApi/swap/v2/trade/closePosition", {"symbol": symbol})
    if resp.get("code", -1) in (0, 200):
        return resp
    amt = float(position.get("positionAmt", 0))
    if abs(amt) < 1e-9:
        return {}
    close_side = "SELL" if amt > 0 else "BUY"
    return await _post("/openApi/swap/v2/trade/order", {
        "symbol": symbol, "side": close_side,
        "type": "MARKET", "quantity": abs(amt),
    })


async def cancel_all_orders(symbol: str) -> Any:
    return await _delete("/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol})


async def get_price(symbol: str) -> float:
    resp = await _get("/openApi/swap/v2/quote/price", {"symbol": symbol})
    try:
        price = float(resp.get("data", {}).get("price", 0))
        if price > 0:
            return price
    except Exception:
        pass
    resp2 = await _get("/openApi/swap/v2/quote/ticker", {"symbol": symbol})
    try:
        data2 = resp2.get("data", {})
        if isinstance(data2, list) and data2:
            return float(data2[0].get("lastPrice", 0))
        if isinstance(data2, dict):
            return float(data2.get("lastPrice", 0))
    except Exception:
        pass
    return 0.0
