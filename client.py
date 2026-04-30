"""exchange/client.py — Async BingX Perpetual Futures client.

KEY FIX: Orders use ONE-WAY MODE only.
  - No positionSide field (was causing error 109400 in Hedge mode)
  - No ReduceOnly field
  - close_position uses closePosition endpoint with correct params
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
_ws_prices: dict[str, float] = {}


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        from core.config import cfg
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


# ── Auth ──────────────────────────────────────────────────────────────────────

def _sign(params: dict, secret: str) -> str:
    qs = urlencode(sorted(params.items()))
    return hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()


def _auth_params(params: dict | None = None) -> dict:
    from core.config import cfg
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000)
    p["signature"] = _sign(p, cfg.bingx_secret_key)
    return p


def _headers() -> dict:
    from core.config import cfg
    return {"X-BX-APIKEY": cfg.bingx_api_key}


# ── HTTP helpers ──────────────────────────────────────────────────────────────

async def _get(path: str, params: dict | None = None, auth: bool = False) -> Any:
    sess = _get_session()
    p = _auth_params(params) if auth else (params or {})
    try:
        async with sess.get(BASE_URL + path, params=p, headers=_headers() if auth else {}) as r:
            return await r.json(content_type=None)
    except Exception as e:
        logger.warning(f"GET {path} error: {e}")
        return {}


async def _post(path: str, params: dict | None = None) -> Any:
    sess = _get_session()
    p = _auth_params(params)
    try:
        async with sess.post(BASE_URL + path, params=p, headers=_headers()) as r:
            return await r.json(content_type=None)
    except Exception as e:
        logger.warning(f"POST {path} error: {e}")
        return {}


async def _delete(path: str, params: dict | None = None) -> Any:
    sess = _get_session()
    p = _auth_params(params)
    try:
        async with sess.delete(BASE_URL + path, params=p, headers=_headers()) as r:
            return await r.json(content_type=None)
    except Exception as e:
        logger.warning(f"DELETE {path} error: {e}")
        return {}


# ── Market data ───────────────────────────────────────────────────────────────

async def fetch_all_tickers() -> list[dict]:
    resp = await _get("/openApi/swap/v2/quote/ticker")
    data = resp.get("data", resp) if isinstance(resp, dict) else resp
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return list(data.values())
    return []


async def fetch_klines(symbol: str, interval: str, limit: int = 200) -> list[list]:
    resp = await _get("/openApi/swap/v3/quote/klines", {
        "symbol": symbol, "interval": interval, "limit": limit
    })
    data = resp.get("data", []) if isinstance(resp, dict) else []
    return data if isinstance(data, list) else []


async def _fetch_ohlcv(symbol: str, tf: str) -> dict | None:
    import numpy as np
    raw = await fetch_klines(symbol, tf, limit=200)
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
        logger.debug(f"OHLCV parse error {symbol} {tf}: {e}")
        return None


async def fetch_universe_concurrent(symbols: list[str]) -> dict[str, dict]:
    from core.config import cfg

    async def _fetch_one(sym: str):
        p, h, t = await asyncio.gather(
            _fetch_ohlcv(sym, cfg.timeframe),
            _fetch_ohlcv(sym, cfg.confirm_tf),
            _fetch_ohlcv(sym, cfg.trend_tf),
        )
        return sym, p, h, t

    # Batch to respect rate limits
    results = {}
    batch_size = cfg.max_concurrent
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        tasks = [asyncio.create_task(_fetch_one(sym)) for sym in batch]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in batch_results:
            if isinstance(r, Exception):
                continue
            sym, p, h, t = r
            if p is not None:
                results[sym] = {"p": p, "h": h, "t": t}
        if i + batch_size < len(symbols):
            await asyncio.sleep(0.2)  # small pause between batches
    return results


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
        logger.warning(f"get_balance parse error: {e}")
    return 0.0


async def get_all_positions() -> dict[str, dict]:
    resp = await _get("/openApi/swap/v2/user/positions", auth=True)
    try:
        data = resp.get("data", [])
        if isinstance(data, list):
            return {
                p["symbol"]: p
                for p in data
                if abs(float(p.get("positionAmt", 0))) > 1e-9
            }
    except Exception as e:
        logger.warning(f"get_positions parse error: {e}")
    return {}


# ── Trading — ONE-WAY MODE (fixes error 109400) ───────────────────────────────

async def set_leverage(symbol: str, leverage: int) -> Any:
    """Set leverage for both sides in one-way mode."""
    return await _post("/openApi/swap/v2/trade/leverage", {
        "symbol": symbol, "side": "LONG", "leverage": leverage
    })


async def place_market_order(
    symbol: str, side: str, size_usdt: float,
    sl: float, tp: float
) -> dict:
    """
    Place a market order — ONE-WAY MODE compatible.
    No positionSide, no ReduceOnly. Fixes BingX error 109400.
    """
    params: dict[str, Any] = {
        "symbol":        symbol,
        "side":          side,          # BUY or SELL
        "type":          "MARKET",
        "quoteOrderQty": size_usdt,     # size in USDT (notional)
        "stopLoss":      str(sl),
        "takeProfit":    str(tp),
        # NOTE: positionSide intentionally omitted → one-way mode
        # NOTE: reduceOnly intentionally omitted → fixes error 109400
    }
    resp = await _post("/openApi/swap/v2/trade/order", params)
    code = resp.get("code", -1)
    if code not in (0, 200, None):
        logger.warning(f"Order {symbol} {side} failed: code={code} msg={resp.get('msg','')}")
    return resp if isinstance(resp, dict) else {"raw": resp}


async def close_position(symbol: str, position: dict) -> Any:
    """Close position using the dedicated closePosition endpoint (one-way safe)."""
    # Try dedicated close endpoint first (works in both modes)
    resp = await _post("/openApi/swap/v2/trade/closePosition", {"symbol": symbol})
    if resp.get("code", -1) in (0, 200):
        return resp

    # Fallback: market order in opposite direction
    amt  = float(position.get("positionAmt", 0))
    if abs(amt) < 1e-9:
        return {}
    close_side = "SELL" if amt > 0 else "BUY"
    params: dict[str, Any] = {
        "symbol":   symbol,
        "side":     close_side,
        "type":     "MARKET",
        "quantity": abs(amt),
        # NO positionSide, NO reduceOnly
    }
    return await _post("/openApi/swap/v2/trade/order", params)


async def cancel_all_orders(symbol: str) -> Any:
    return await _delete("/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol})


async def get_price(symbol: str) -> float:
    if symbol in _ws_prices:
        return _ws_prices[symbol]
    resp = await _get("/openApi/swap/v2/quote/price", {"symbol": symbol})
    try:
        data = resp.get("data", {})
        return float(data.get("price", 0))
    except Exception:
        return 0.0
