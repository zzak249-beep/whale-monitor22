"""
Cliente BingX Perpetual Futures — v2
Endpoints: https://bingx-api.github.io/docs/#/en-us/swapV2/

Correcciones:
  - Sesión aiohttp reutilizada (evita leak de conexiones)
  - Retry con backoff exponencial
  - Ticker acepta lista o dict
  - Velas invertidas a orden cronológico (BingX devuelve más reciente primero)
  - set_leverage() antes de cada orden
  - reduceOnly en TP/SL y cierre
  - get_open_positions() para sincronizar estado
"""
import hashlib
import hmac
import time
import urllib.parse
import asyncio
import logging
from typing import Optional

import aiohttp

logger      = logging.getLogger("bingx")
BASE        = "https://open-api.bingx.com"
MAX_RETRIES = 3
RETRY_BASE  = 1.0   # segundos (se dobla en cada reintento)


class BingXClient:
    def __init__(self, api_key: str, secret: str):
        self.api_key = api_key
        self.secret  = secret
        self._session: Optional[aiohttp.ClientSession] = None

    async def _session_(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _sign(self, params: dict) -> str:
        params["timestamp"] = int(time.time() * 1000)
        qs  = urllib.parse.urlencode(sorted(params.items()))
        sig = hmac.new(self.secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        return qs + "&signature=" + sig

    async def _get(self, path: str, params: dict = None, signed: bool = False):
        p       = dict(params or {})
        headers = {"X-BX-APIKEY": self.api_key}
        qs      = self._sign(p) if signed else (urllib.parse.urlencode(p) if p else "")
        url     = f"{BASE}{path}?{qs}" if qs else f"{BASE}{path}"

        for attempt in range(MAX_RETRIES):
            try:
                s = await self._session_()
                async with s.get(url, headers=headers) as r:
                    data = await r.json(content_type=None)
                if data.get("code", 0) != 0:
                    raise RuntimeError(f"BingX GET {path}: {data}")
                return data["data"]
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == MAX_RETRIES - 1:
                    raise
                await asyncio.sleep(RETRY_BASE * (2 ** attempt))
                logger.warning(f"GET {path} retry {attempt+1}: {e}")

    async def _post(self, path: str, params: dict):
        headers = {
            "X-BX-APIKEY":  self.api_key,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        qs = self._sign(dict(params))

        for attempt in range(MAX_RETRIES):
            try:
                s = await self._session_()
                async with s.post(f"{BASE}{path}", data=qs, headers=headers) as r:
                    data = await r.json(content_type=None)
                if data.get("code", 0) != 0:
                    raise RuntimeError(f"BingX POST {path}: {data}")
                return data["data"]
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == MAX_RETRIES - 1:
                    raise
                await asyncio.sleep(RETRY_BASE * (2 ** attempt))
                logger.warning(f"POST {path} retry {attempt+1}: {e}")

    # ── Market data ───────────────────────────────────────────────── #

    async def top_symbols_by_volume(self, n: int = 20) -> list[str]:
        data    = await self._get("/openApi/swap/v2/quote/ticker")
        tickers = data if isinstance(data, list) else data.get("tickers", [])
        usdt    = [t for t in tickers if str(t.get("symbol", "")).endswith("-USDT")]
        usdt.sort(key=lambda t: float(t.get("quoteVolume", 0)), reverse=True)
        symbols = [t["symbol"] for t in usdt[:n]]
        logger.info(f"Top {n} pares: {symbols}")
        return symbols

    async def klines(self, symbol: str, interval: str, limit: int = 100) -> list[dict]:
        """
        Devuelve velas en orden cronológico (más antigua → más reciente).
        BingX entrega más reciente primero — se invierte aquí.
        """
        raw = await self._get("/openApi/swap/v3/quote/klines", {
            "symbol": symbol, "interval": interval, "limit": limit,
        })
        candles = [
            {
                "o": float(c["open"]),
                "h": float(c["high"]),
                "l": float(c["low"]),
                "c": float(c["close"]),
                "v": float(c["volume"]),
                "t": int(c.get("time", 0)),
            }
            for c in raw
        ]
        candles.reverse()   # ← orden cronológico correcto
        return candles

    async def last_price(self, symbol: str) -> float:
        data   = await self._get("/openApi/swap/v2/quote/ticker", {"symbol": symbol})
        ticker = data[0] if isinstance(data, list) else data
        return float(ticker.get("lastPrice") or ticker.get("price", 0))

    # ── Account ───────────────────────────────────────────────────── #

    async def balance_usdt(self) -> float:
        data = await self._get("/openApi/swap/v2/user/balance", signed=True)
        for asset in data.get("balance", []):
            if asset["asset"] == "USDT":
                return float(asset["availableMargin"])
        return 0.0

    async def get_open_positions(self) -> list[dict]:
        try:
            data = await self._get("/openApi/swap/v2/user/positions", signed=True)
            return [p for p in (data or []) if float(p.get("positionAmt", 0)) != 0]
        except Exception as e:
            logger.warning(f"get_open_positions: {e}")
            return []

    # ── Trading ───────────────────────────────────────────────────── #

    async def set_leverage(self, symbol: str, leverage: int = 5):
        for side in ("LONG", "SHORT"):
            try:
                await self._post("/openApi/swap/v2/trade/leverage", {
                    "symbol": symbol, "side": side, "leverage": leverage,
                })
            except Exception as e:
                logger.warning(f"set_leverage {symbol}/{side}: {e}")

    async def open_order(
        self, symbol: str, side: str, qty: float,
        tp_price: float, sl_price: float, leverage: int = 5,
    ) -> str:
        await self.set_leverage(symbol, leverage)

        action = "BUY"  if side == "LONG" else "SELL"
        close  = "SELL" if side == "LONG" else "BUY"

        order    = await self._post("/openApi/swap/v2/trade/order", {
            "symbol": symbol, "side": action,
            "positionSide": side, "type": "MARKET", "quantity": qty,
        })
        order_id = order["order"]["orderId"]
        logger.info(f"Orden {order_id}: {symbol} {side} qty={qty}")

        for label, order_type, stop in [
            ("TP", "TAKE_PROFIT_MARKET", tp_price),
            ("SL", "STOP_MARKET",        sl_price),
        ]:
            try:
                await self._post("/openApi/swap/v2/trade/order", {
                    "symbol": symbol, "side": close, "positionSide": side,
                    "type": order_type, "quantity": qty,
                    "stopPrice": round(stop, 6),
                    "workingType": "MARK_PRICE", "reduceOnly": "true",
                })
            except Exception as e:
                logger.error(f"{label} {symbol}: {e}")

        return order_id

    async def close_position(self, symbol: str, side: str, qty: float):
        close = "SELL" if side == "LONG" else "BUY"
        await self._post("/openApi/swap/v2/trade/order", {
            "symbol": symbol, "side": close, "positionSide": side,
            "type": "MARKET", "quantity": qty, "reduceOnly": "true",
        })
        logger.info(f"Cerrado: {symbol} {side} qty={qty}")
