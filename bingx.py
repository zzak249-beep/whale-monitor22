# -*- coding: utf-8 -*-
"""bingx.py -- Maki Bot PRO — Cliente BingX Perpetual Futures."""
from __future__ import annotations
import asyncio, hashlib, hmac, random, time
from typing import Any
from urllib.parse import urlencode

import aiohttp
from loguru import logger

BASE_URL = "https://open-api.bingx.com"


class BingXClient:
    def __init__(self, api_key: str, api_secret: str, timeout: int = 12):
        self._key    = api_key
        self._secret = api_secret
        self._timeout = aiohttp.ClientTimeout(total=timeout, connect=4)
        self._session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            conn = aiohttp.TCPConnector(
                limit=200, limit_per_host=80,
                ttl_dns_cache=600, keepalive_timeout=60,
                ssl=False,
            )
            self._session = aiohttp.ClientSession(
                connector=conn, timeout=self._timeout
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _sign(self, params: dict) -> str:
        qs = urlencode(sorted(params.items()))
        return hmac.new(self._secret.encode(), qs.encode(), hashlib.sha256).hexdigest()

    def _auth(self, extra: dict | None = None) -> dict:
        p = dict(extra or {})
        p["timestamp"] = int(time.time() * 1000)
        p["signature"] = self._sign(p)
        return p

    def _hdrs(self) -> dict:
        return {"X-BX-APIKEY": self._key}

    async def _request(self, method: str, path: str,
                       params: dict | None = None,
                       auth: bool = True,
                       retries: int = 3) -> Any:
        sess = self._get_session()
        p    = self._auth(params) if auth else (params or {})
        url  = BASE_URL + path

        for attempt in range(retries):
            try:
                if method == "GET":
                    async with sess.get(url, params=p, headers=self._hdrs()) as r:
                        return await r.json(content_type=None)
                elif method == "POST":
                    async with sess.post(url, params=p, headers=self._hdrs()) as r:
                        return await r.json(content_type=None)
                elif method == "DELETE":
                    async with sess.delete(url, params=p, headers=self._hdrs()) as r:
                        return await r.json(content_type=None)
            except asyncio.TimeoutError:
                if attempt < retries - 1:
                    await asyncio.sleep(1.5 ** attempt + random.uniform(0, 0.3))
            except Exception as e:
                logger.debug(f"{method} {path}: {e}")
                return {}
        return {}

    async def _get(self, path, params=None, auth=False):
        return await self._request("GET", path, params, auth=auth)

    async def _post(self, path, params=None):
        return await self._request("POST", path, params, auth=True)

    async def _delete(self, path, params=None):
        return await self._request("DELETE", path, params, auth=True)

    # ── Market data ──────────────────────────────────────────────────────────

    async def klines(self, symbol: str, interval: str, limit: int = 200) -> list[dict]:
        """Descarga velas OHLCV y las convierte a dicts con o/h/l/c/v."""
        resp = await self._get("/openApi/swap/v3/quote/klines",
                               {"symbol": symbol, "interval": interval, "limit": limit})
        raw = resp.get("data", []) if isinstance(resp, dict) else []
        if not isinstance(raw, list):
            return []
        result = []
        for c in raw:
            try:
                result.append({
                    "t": int(c[0]),
                    "o": float(c[1]),
                    "h": float(c[2]),
                    "l": float(c[3]),
                    "c": float(c[4]),
                    "v": float(c[5]),
                })
            except Exception:
                continue
        return result

    async def klines_multi(self, symbol: str) -> tuple[list, list]:
        """Descarga 15m y 4h en paralelo."""
        c15, c4h = await asyncio.gather(
            self.klines(symbol, "15m", 200),
            self.klines(symbol, "4h",  100),
        )
        return c15, c4h

    async def last_price(self, symbol: str) -> float:
        resp = await self._get("/openApi/swap/v2/quote/price", {"symbol": symbol})
        try:
            return float(resp.get("data", {}).get("price", 0))
        except Exception:
            return 0.0

    async def prices_multi(self, symbols: list[str]) -> dict[str, float]:
        """Obtiene precios de todos los símbolos en UNA sola llamada."""
        resp = await self._get("/openApi/swap/v2/quote/ticker")
        out: dict[str, float] = {}
        try:
            sym_set = set(symbols)
            for item in (resp.get("data", []) or []):
                sym = item.get("symbol", "")
                if sym in sym_set:
                    p = float(item.get("lastPrice", 0) or 0)
                    if p > 0:
                        out[sym] = p
        except Exception as e:
            logger.warning(f"prices_multi: {e}")
        return out

    async def top_symbols_by_volume(self, n: int = 20) -> list[str]:
        """Retorna los N pares USDT perpetuos con más volumen 24h."""
        resp = await self._get("/openApi/swap/v2/quote/ticker")
        items = resp.get("data", []) if isinstance(resp, dict) else []
        pairs = []
        for item in items:
            sym = item.get("symbol", "")
            if not sym.endswith("-USDT"):
                continue
            if any(x in sym for x in ("1000", "DEFI", "INDEX", "BEAR", "BULL")):
                continue
            try:
                vol = float(item.get("quoteVolume", 0) or 0)
                pairs.append((vol, sym))
            except Exception:
                continue
        pairs.sort(reverse=True)
        result = [sym for _, sym in pairs[:n]]
        logger.info(f"[BINGX] Top {n} por volumen: {result[:5]}...")
        return result

    # ── Account ──────────────────────────────────────────────────────────────

    async def balance_usdt(self) -> float:
        resp = await self._get("/openApi/swap/v2/user/balance", auth=True)
        try:
            if not isinstance(resp, dict): return 0.0
            data = resp.get("data", {})
            if isinstance(data, dict):
                bal = data.get("balance", {})
                if isinstance(bal, dict):
                    for k in ("availableMargin", "available", "balance"):
                        if k in bal: return float(bal[k])
                for k in ("availableMargin", "available", "equity", "balance"):
                    if k in data: return float(data[k])
        except Exception as e:
            logger.warning(f"balance_usdt: {e}")
        return 0.0

    async def get_open_positions(self) -> list[dict]:
        resp = await self._get("/openApi/swap/v2/user/positions", auth=True)
        try:
            data = resp.get("data", [])
            if isinstance(data, list):
                return [p for p in data if abs(float(p.get("positionAmt", 0))) > 1e-9]
        except Exception as e:
            logger.warning(f"get_open_positions: {e}")
        return []

    # ── Trading ──────────────────────────────────────────────────────────────

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        await asyncio.gather(
            self._post("/openApi/swap/v2/trade/leverage",
                       {"symbol": symbol, "side": "LONG",  "leverage": leverage}),
            self._post("/openApi/swap/v2/trade/leverage",
                       {"symbol": symbol, "side": "SHORT", "leverage": leverage}),
        )

    async def open_order(self, symbol: str, side: str, qty: float,
                         tp: float, sl: float, leverage: int) -> str:
        """Abre una orden de mercado con TP y SL. Retorna order_id."""
        await self.set_leverage(symbol, leverage)

        bx_side = "BUY" if side in ("BUY", "LONG") else "SELL"
        resp = await self._post("/openApi/swap/v2/trade/order", {
            "symbol":     symbol,
            "side":       bx_side,
            "type":       "MARKET",
            "quantity":   qty,
            "stopLoss":   str(round(sl, 8)),
            "takeProfit": str(round(tp, 8)),
        })
        code = resp.get("code", -1) if isinstance(resp, dict) else -1
        if code not in (0, 200, None):
            raise RuntimeError(f"order fail code={code} {resp.get('msg','')}")
        od = resp.get("data", {})
        if isinstance(od, dict):
            od = od.get("order", od)
        return str(od.get("orderId", ""))

    async def close_position(self, symbol: str, side: str, qty: float) -> dict:
        close_side = "SELL" if side in ("BUY", "LONG") else "BUY"
        resp = await self._post("/openApi/swap/v2/trade/closePosition", {"symbol": symbol})
        if isinstance(resp, dict) and resp.get("code", -1) in (0, 200):
            return resp
        # Fallback manual
        return await self._post("/openApi/swap/v2/trade/order", {
            "symbol": symbol, "side": close_side,
            "type": "MARKET", "quantity": qty, "reduceOnly": "true",
        })

    async def update_sl(self, symbol: str, side: str, qty: float, new_sl: float) -> bool:
        """Cancela órdenes abiertas y recoloca SL (trailing)."""
        await self._delete("/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol})
        bx_side = "BUY" if side in ("BUY", "LONG") else "SELL"
        resp = await self._post("/openApi/swap/v2/trade/order", {
            "symbol":     symbol,
            "side":       bx_side,
            "type":       "STOP_MARKET",
            "quantity":   qty,
            "stopPrice":  str(round(new_sl, 8)),
            "reduceOnly": "true",
        })
        return isinstance(resp, dict) and resp.get("code", -1) in (0, 200)
