"""
Cliente BingX Perpetual Futures.
Endpoints verificados contra: https://bingx-api.github.io/docs/#/en-us/swapV2/
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
        if signed:
            qs = self._sign(p)
        else:
            qs = urllib.parse.urlencode(p) if p else ""
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

    async def top_symbols_by_volume(self, n: int = 20) -> list[str]:
        """Retorna los N pares USDT con mayor volumen en 24h."""
        data = await self._get("/openApi/swap/v2/quote/ticker")
        # data es lista de tickers
        usdt = [t for t in data if t["symbol"].endswith("-USDT")]
        usdt.sort(key=lambda t: float(t.get("quoteVolume", 0)), reverse=True)
        symbols = [t["symbol"] for t in usdt[:n]]
        logger.info(f"Top {n} pares: {symbols}")
        return symbols

    async def klines(self, symbol: str, interval: str, limit: int = 100) -> list:
        """
        Velas OHLCV.
        Retorna lista de dicts con keys: o, h, l, c, v (ya convertidos a float).
        Formato real BingX: [{"open":..,"high":..,"low":..,"close":..,"volume":..,"time":..}]
        """
        raw = await self._get("/openApi/swap/v3/quote/klines", {
            "symbol": symbol, "interval": interval, "limit": limit
        })
        return [
            {"o": float(c["open"]), "h": float(c["high"]),
             "l": float(c["low"]),  "c": float(c["close"]), "v": float(c["volume"])}
            for c in raw
        ]

    async def balance_usdt(self) -> float:
        data = await self._get("/openApi/swap/v2/user/balance", signed=True)
        for asset in data.get("balance", []):
            if asset["asset"] == "USDT":
                return float(asset["availableMargin"])
        return 0.0

    async def open_order(self, symbol: str, side: str, qty: float, tp_price: float, sl_price: float) -> str:
        """
        Abre posición MARKET con TP y SL adjuntos.
        side: "LONG" o "SHORT"
        Retorna orderId.
        """
        action = "BUY" if side == "LONG" else "SELL"
        close  = "SELL" if side == "LONG" else "BUY"

        # Orden principal
        order = await self._post("/openApi/swap/v2/trade/order", {
            "symbol": symbol, "side": action, "positionSide": side,
            "type": "MARKET", "quantity": qty,
        })
        order_id = order["order"]["orderId"]

        # TP
        await self._post("/openApi/swap/v2/trade/order", {
            "symbol": symbol, "side": close, "positionSide": side,
            "type": "TAKE_PROFIT_MARKET", "quantity": qty,
            "stopPrice": round(tp_price, 8), "workingType": "MARK_PRICE",
        })

        # SL
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
