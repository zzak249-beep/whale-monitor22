"""
Cliente BingX Perpetual Futures.
Endpoints: https://bingx-api.github.io/docs/#/en-us/swapV2/
"""
import hashlib
import hmac
import time
import urllib.parse
import aiohttp
import logging

logger = logging.getLogger("bingx")
BASE = "https://open-api.bingx.com"


class BingXClient:
    def __init__(self, api_key: str, secret: str):
        self.api_key = api_key
        self.secret = secret

    def _sign(self, params: dict) -> str:
        params["timestamp"] = int(time.time() * 1000)
        qs = urllib.parse.urlencode(sorted(params.items()))
        sig = hmac.new(self.secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        return qs + "&signature=" + sig

    async def _get(self, path: str, params: dict = None, signed: bool = False) -> dict:
        p = params or {}
        headers = {"X-BX-APIKEY": self.api_key}
        qs = self._sign(p) if signed else (urllib.parse.urlencode(p) if p else "")
        url = f"{BASE}{path}?{qs}" if qs else f"{BASE}{path}"
        async with aiohttp.ClientSession(headers=headers) as s:
            async with s.get(url) as r:
                data = await r.json()
        if data.get("code", 0) != 0:
            raise RuntimeError(f"BingX GET {path} error: {data}")
        return data["data"]

    async def _post(self, path: str, params: dict) -> dict:
        headers = {"X-BX-APIKEY": self.api_key, "Content-Type": "application/x-www-form-urlencoded"}
        qs = self._sign(params)
        async with aiohttp.ClientSession(headers=headers) as s:
            async with s.post(f"{BASE}{path}", data=qs) as r:
                data = await r.json()
        if data.get("code", 0) != 0:
            raise RuntimeError(f"BingX POST {path} error: {data}")
        return data["data"]

    async def all_symbols(self) -> list[str]:
        """Retorna todos los pares USDT perpetuos activos en BingX."""
        data = await self._get("/openApi/swap/v2/quote/contracts")
        symbols = [c["symbol"] for c in data if isinstance(c, dict)
                   and c.get("symbol", "").endswith("-USDT")
                   and c.get("status", 0) == 1]
        logger.info(f"Total pares USDT activos: {len(symbols)}")
        return symbols

    async def top_symbols_by_volume(self, n: int = 20) -> list[str]:
        data = await self._get("/openApi/swap/v2/quote/ticker")
        if isinstance(data, dict):
            data = data.get("tickers", data.get("data", []))
        usdt = [t for t in data if isinstance(t, dict) and t.get("symbol", "").endswith("-USDT")]
        usdt.sort(key=lambda t: float(t.get("quoteVolume", 0)), reverse=True)
        symbols = [t["symbol"] for t in usdt[:n]]
        logger.info(f"Top {n} pares: {symbols}")
        return symbols

    async def klines(self, symbol: str, interval: str, limit: int = 100) -> list:
        raw = await self._get("/openApi/swap/v3/quote/klines", {
            "symbol": symbol, "interval": interval, "limit": limit
        })
        result = []
        for c in raw:
            if isinstance(c, dict):
                result.append({
                    "o": float(c.get("open",   c.get("o", 0))),
                    "h": float(c.get("high",   c.get("h", 0))),
                    "l": float(c.get("low",    c.get("l", 0))),
                    "c": float(c.get("close",  c.get("c", 0))),
                    "v": float(c.get("volume", c.get("v", 0))),
                })
            elif isinstance(c, list) and len(c) >= 5:
                result.append({
                    "o": float(c[1]), "h": float(c[2]),
                    "l": float(c[3]), "c": float(c[4]),
                    "v": float(c[5]) if len(c) > 5 else 0.0,
                })
        return result

    async def balance_usdt(self) -> float:
        data = await self._get("/openApi/swap/v2/user/balance", signed=True)
        if isinstance(data, dict):
            b = data.get("balance", {})
            if isinstance(b, dict):
                return float(b.get("availableMargin", b.get("available", 0)))
            if isinstance(b, list):
                for asset in b:
                    if isinstance(asset, dict) and asset.get("asset") == "USDT":
                        return float(asset.get("availableMargin", asset.get("available", 0)))
        return 0.0

    async def open_order(self, symbol: str, side: str, qty: float, tp_price: float, sl_price: float) -> str:
        action = "BUY" if side == "LONG" else "SELL"
        close  = "SELL" if side == "LONG" else "BUY"
        order = await self._post("/openApi/swap/v2/trade/order", {
            "symbol": symbol, "side": action, "positionSide": side,
            "type": "MARKET", "quantity": qty,
        })
        order_id = order["order"]["orderId"]
        await self._post("/openApi/swap/v2/trade/order", {
            "symbol": symbol, "side": close, "positionSide": side,
            "type": "TAKE_PROFIT_MARKET", "quantity": qty,
            "stopPrice": round(tp_price, 8), "workingType": "MARK_PRICE",
        })
        await self._post("/openApi/swap/v2/trade/order", {
            "symbol": symbol, "side": close, "positionSide": side,
            "type": "STOP_MARKET", "quantity": qty,
            "stopPrice": round(sl_price, 8), "workingType": "MARK_PRICE",
        })
        return order_id

    async def close_position(self, symbol: str, side: str, qty: float):
        close = "SELL" if side == "LONG" else "BUY"
        await self._post("/openApi/swap/v2/trade/order", {
            "symbol": symbol, "side": close, "positionSide": side,
            "type": "MARKET", "quantity": qty, "reduceOnly": "true",
        })
