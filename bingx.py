"""
Cliente BingX Perpetual Futures
Solo usa stdlib + aiohttp (en requirements.txt)
"""
import hashlib
import hmac
import time
import urllib.parse
import asyncio
import logging
from typing import Optional

import aiohttp

logger     = logging.getLogger("bingx")
BASE       = "https://open-api.bingx.com"
RETRIES    = 3
RETRY_BASE = 1.5   # segundos, se dobla en cada reintento


class BingXClient:
    def __init__(self, api_key: str, secret: str):
        self.api_key = api_key
        self.secret  = secret
        self._sess: Optional[aiohttp.ClientSession] = None

    # ── sesión reutilizable ────────────────────────────────────────── #

    async def _get_sess(self) -> aiohttp.ClientSession:
        if self._sess is None or self._sess.closed:
            self._sess = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20)
            )
        return self._sess

    async def close(self):
        if self._sess and not self._sess.closed:
            await self._sess.close()

    # ── firma HMAC-SHA256 ─────────────────────────────────────────── #

    def _sign(self, params: dict) -> str:
        # Copia para no mutar el dict original
        p = dict(params)
        p["timestamp"] = int(time.time() * 1000)
        qs  = urllib.parse.urlencode(sorted(p.items()))
        sig = hmac.new(
            self.secret.encode("utf-8"), qs.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return qs + "&signature=" + sig

    # ── helpers HTTP con retry ────────────────────────────────────── #

    async def _get(self, path: str, params: dict = None, signed: bool = False):
        p       = dict(params or {})
        headers = {"X-BX-APIKEY": self.api_key}
        qs      = self._sign(p) if signed else (urllib.parse.urlencode(p) if p else "")
        url     = f"{BASE}{path}?{qs}" if qs else f"{BASE}{path}"

        last_err = None
        for attempt in range(RETRIES):
            try:
                sess = await self._get_sess()
                async with sess.get(url, headers=headers) as r:
                    data = await r.json(content_type=None)
                code = data.get("code", 0)
                if code != 0:
                    raise RuntimeError(f"API error {code}: {data.get('msg', data)}")
                return data["data"]
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_err = e
                wait = RETRY_BASE * (2 ** attempt)
                logger.warning(f"GET {path} intento {attempt+1}/{RETRIES} fallido: {e} — espera {wait:.1f}s")
                await asyncio.sleep(wait)
        raise RuntimeError(f"GET {path} falló tras {RETRIES} intentos: {last_err}")

    async def _post(self, path: str, params: dict):
        headers = {
            "X-BX-APIKEY":  self.api_key,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        qs = self._sign(params)   # _sign ya copia internamente

        last_err = None
        for attempt in range(RETRIES):
            try:
                sess = await self._get_sess()
                async with sess.post(f"{BASE}{path}", data=qs, headers=headers) as r:
                    data = await r.json(content_type=None)
                code = data.get("code", 0)
                if code != 0:
                    raise RuntimeError(f"API error {code}: {data.get('msg', data)}")
                return data["data"]
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_err = e
                wait = RETRY_BASE * (2 ** attempt)
                logger.warning(f"POST {path} intento {attempt+1}/{RETRIES} fallido: {e} — espera {wait:.1f}s")
                await asyncio.sleep(wait)
        raise RuntimeError(f"POST {path} falló tras {RETRIES} intentos: {last_err}")

    # ── market data ───────────────────────────────────────────────── #

    async def top_symbols_by_volume(self, n: int = 20) -> list:
        """Top N pares *-USDT por volumen 24h."""
        data    = await self._get("/openApi/swap/v2/quote/ticker")
        tickers = data if isinstance(data, list) else data.get("tickers", [])
        usdt    = [t for t in tickers if str(t.get("symbol", "")).endswith("-USDT")]
        usdt.sort(key=lambda t: float(t.get("quoteVolume") or 0), reverse=True)
        symbols = [t["symbol"] for t in usdt[:n]]
        logger.info(f"Top {n} pares: {symbols}")
        return symbols

    async def klines(self, symbol: str, interval: str, limit: int = 100) -> list:
        """
        Devuelve velas OHLCV en orden CRONOLÓGICO (más antigua primero).
        BingX puede devolver más reciente primero → ordenamos por timestamp.
        """
        raw = await self._get("/openApi/swap/v3/quote/klines", {
            "symbol": symbol, "interval": interval, "limit": limit,
        })
        candles = []
        for c in raw:
            candles.append({
                "t": int(c.get("time", 0)),
                "o": float(c["open"]),
                "h": float(c["high"]),
                "l": float(c["low"]),
                "c": float(c["close"]),
                "v": float(c["volume"]),
            })
        # Ordenar por timestamp garantiza orden cronológico sin importar
        # cómo devuelva BingX los datos en cada versión de la API
        candles.sort(key=lambda x: x["t"])
        return candles

    async def last_price(self, symbol: str) -> float:
        """Precio mark más reciente."""
        data   = await self._get("/openApi/swap/v2/quote/ticker", {"symbol": symbol})
        ticker = data[0] if isinstance(data, list) else data
        price  = ticker.get("lastPrice") or ticker.get("price") or 0
        return float(price)

    # ── account ───────────────────────────────────────────────────── #

    async def balance_usdt(self) -> float:
        data = await self._get("/openApi/swap/v2/user/balance", signed=True)
        # data puede ser dict con key "balance" (lista) o directamente lista
        assets = data.get("balance", []) if isinstance(data, dict) else data
        for asset in assets:
            if asset.get("asset") == "USDT":
                return float(asset.get("availableMargin", 0))
        return 0.0

    async def get_open_positions(self) -> list:
        """Posiciones con cantidad distinta de 0."""
        try:
            data = await self._get("/openApi/swap/v2/user/positions", signed=True)
            positions = data if isinstance(data, list) else []
            return [p for p in positions if float(p.get("positionAmt", 0)) != 0]
        except Exception as e:
            logger.warning(f"get_open_positions error: {e}")
            return []

    # ── trading ───────────────────────────────────────────────────── #

    async def set_leverage(self, symbol: str, leverage: int):
        """Establece apalancamiento para LONG y SHORT."""
        for side in ("LONG", "SHORT"):
            try:
                await self._post("/openApi/swap/v2/trade/leverage", {
                    "symbol": symbol, "side": side, "leverage": str(leverage),
                })
                logger.info(f"Leverage {leverage}x OK: {symbol} {side}")
            except Exception as e:
                logger.warning(f"set_leverage {symbol}/{side}: {e}")

    async def open_order(
        self,
        symbol:   str,
        side:     str,       # "LONG" o "SHORT"
        qty:      float,     # cantidad en moneda base (no en USDT)
        tp_price: float,
        sl_price: float,
        leverage: int = 5,
    ) -> str:
        """Abre posición MARKET + TP + SL. Retorna orderId."""
        await self.set_leverage(symbol, leverage)

        action = "BUY"  if side == "LONG" else "SELL"
        close  = "SELL" if side == "LONG" else "BUY"

        # Orden principal
        resp     = await self._post("/openApi/swap/v2/trade/order", {
            "symbol":       symbol,
            "side":         action,
            "positionSide": side,
            "type":         "MARKET",
            "quantity":     str(qty),
        })
        order_id = str(resp.get("order", {}).get("orderId", ""))
        logger.info(f"Orden abierta {order_id}: {symbol} {side} qty={qty}")

        # TP
        try:
            await self._post("/openApi/swap/v2/trade/order", {
                "symbol":       symbol,
                "side":         close,
                "positionSide": side,
                "type":         "TAKE_PROFIT_MARKET",
                "quantity":     str(qty),
                "stopPrice":    str(round(tp_price, 6)),
                "workingType":  "MARK_PRICE",
                "reduceOnly":   "true",
            })
        except Exception as e:
            logger.error(f"TP falló {symbol}: {e}")

        # SL
        try:
            await self._post("/openApi/swap/v2/trade/order", {
                "symbol":       symbol,
                "side":         close,
                "positionSide": side,
                "type":         "STOP_MARKET",
                "quantity":     str(qty),
                "stopPrice":    str(round(sl_price, 6)),
                "workingType":  "MARK_PRICE",
                "reduceOnly":   "true",
            })
        except Exception as e:
            logger.error(f"SL falló {symbol}: {e}")

        return order_id

    async def close_position(self, symbol: str, side: str, qty: float):
        """Cierra posición con orden de mercado."""
        close = "SELL" if side == "LONG" else "BUY"
        await self._post("/openApi/swap/v2/trade/order", {
            "symbol":       symbol,
            "side":         close,
            "positionSide": side,
            "type":         "MARKET",
            "quantity":     str(qty),
            "reduceOnly":   "true",
        })
        logger.info(f"Posición cerrada: {symbol} {side} qty={qty}")
