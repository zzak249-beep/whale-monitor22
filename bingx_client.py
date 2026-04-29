"""BINGX CLIENT — REST + WebSocket para futuros perpetuos"""
import asyncio, gzip, hashlib, hmac, json, time
from typing import Any, Callable, Optional

import aiohttp
import websockets

import config
from bot_logger import get_logger

log = get_logger("BingX")


def _sign(params: dict, secret: str) -> str:
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


class BingXClient:
    BASE = "https://open-api.bingx.com"

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws_callbacks: dict[str, Callable] = {}

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=30, ttl_dns_cache=300, use_dns_cache=True)
            self._session = aiohttp.ClientSession(
                connector=connector,
                headers={"X-BX-APIKEY": config.BINGX_API_KEY},
                timeout=aiohttp.ClientTimeout(total=10, connect=5),
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _build_signed_params(self, params: dict) -> dict:
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = _sign(params, config.BINGX_SECRET_KEY)
        return params

    async def _get(self, path: str, params: dict = None, signed: bool = False) -> Any:
        params = params or {}
        if signed:
            params = self._build_signed_params(params)
        for attempt in range(3):
            try:
                async with self._get_session().get(self.BASE + path, params=params) as r:
                    if r.status == 429:
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    data = await r.json(content_type=None)
                    if isinstance(data, dict) and data.get("code", 0) != 0:
                        log.warning(f"API {path}: {data.get('msg', data)}")
                    return data
            except asyncio.TimeoutError:
                await asyncio.sleep(0.5 * (attempt + 1))
            except Exception as e:
                log.error(f"GET {path}: {e}")
                if attempt < 2:
                    await asyncio.sleep(0.5)
        return {}

    async def _post(self, path: str, params: dict = None) -> Any:
        params = self._build_signed_params(params or {})
        for attempt in range(3):
            try:
                async with self._get_session().post(self.BASE + path, params=params) as r:
                    return await r.json(content_type=None)
            except Exception as e:
                log.error(f"POST {path}: {e}")
                await asyncio.sleep(0.5)
        return {}

    async def get_klines(self, symbol: str, interval: str, limit: int = 300) -> list:
        params = {"symbol": symbol, "interval": interval, "limit": min(limit, 1440)}
        data = await self._get("/openApi/swap/v2/quote/klines", params)
        candles = []
        for c in (data.get("data", []) if isinstance(data, dict) else []):
            if isinstance(c, dict):
                candles.append({
                    "time":   int(c.get("time",   c.get("t",  0))),
                    "open":   float(c.get("open",  c.get("o",  0))),
                    "high":   float(c.get("high",  c.get("h",  0))),
                    "low":    float(c.get("low",   c.get("l",  0))),
                    "close":  float(c.get("close", c.get("c",  0))),
                    "volume": float(c.get("volume",c.get("v",  0))),
                })
            elif isinstance(c, list) and len(c) >= 6:
                candles.append({
                    "time": int(c[0]), "open": float(c[1]), "high": float(c[2]),
                    "low":  float(c[3]), "close": float(c[4]), "volume": float(c[5]),
                })
        candles.sort(key=lambda x: x["time"])
        return candles

    async def get_klines_batch(self, requests: list) -> dict:
        tasks = [self.get_klines(s, i, l) for s, i, l in requests]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {
            f"{req[0]}_{req[1]}": (res if not isinstance(res, Exception) else [])
            for req, res in zip(requests, results)
        }

    async def get_ticker(self, symbol: str) -> dict:
        data = await self._get("/openApi/swap/v2/quote/ticker", {"symbol": symbol})
        return data.get("data", {}) if isinstance(data, dict) else {}

    async def get_balance(self) -> dict:
        if config.DRY_RUN:
            return {"equity": 10_000.0, "available": 10_000.0, "unrealizedProfit": 0.0}
        data = await self._get("/openApi/swap/v2/user/balance", {}, signed=True)
        bal  = ((data.get("data", {}) or {}).get("balance", {})) if isinstance(data, dict) else {}
        return {
            "equity":           float(bal.get("equity", 0)),
            "available":        float(bal.get("availableMargin", 0)),
            "unrealizedProfit": float(bal.get("unrealizedProfit", 0)),
        }

    async def get_open_positions(self, symbol: str = "") -> list:
        if config.DRY_RUN:
            return []
        params = {"symbol": symbol} if symbol else {}
        data = await self._get("/openApi/swap/v2/user/positions", params, signed=True)
        return data.get("data", []) if isinstance(data, dict) else []

    async def set_leverage(self, symbol: str, leverage: int) -> dict:
        if config.DRY_RUN:
            log.info(f"[DRY] Leverage {symbol} → {leverage}x")
            return {}
        for side in ("LONG", "SHORT"):
            await self._post("/openApi/swap/v2/trade/leverage",
                             {"symbol": symbol, "leverage": leverage, "side": side})
        return {}

    async def place_order(self, symbol: str, side: str, position_side: str,
                          qty: float, sl_price: float = 0.0, tp_price: float = 0.0) -> dict:
        if config.DRY_RUN:
            log.info(f"[DRY] {symbol} {side}/{position_side} qty={qty:.4f} SL={sl_price:.4f} TP={tp_price:.4f}")
            return {"orderId": f"DRY_{int(time.time())}", "status": "FILLED"}
        params: dict = {
            "symbol": symbol, "side": side, "positionSide": position_side,
            "type": "MARKET", "quantity": qty,
        }
        if sl_price:
            params["stopLoss"] = json.dumps({
                "type": "STOP_MARKET", "stopPrice": sl_price,
                "price": sl_price, "workingType": "MARK_PRICE",
            })
        if tp_price:
            params["takeProfit"] = json.dumps({
                "type": "TAKE_PROFIT_MARKET", "stopPrice": tp_price,
                "price": tp_price, "workingType": "MARK_PRICE",
            })
        data  = await self._post("/openApi/swap/v2/trade/order", params)
        order = ((data.get("data", {}) or {}) if isinstance(data, dict) else {})
        log.info(f"Orden: {symbol} {side} id={order.get('orderId','?')}")
        return order

    async def close_position(self, symbol: str, position_side: str, qty: float) -> dict:
        side = "SELL" if position_side == "LONG" else "BUY"
        return await self.place_order(symbol, side, position_side, qty)

    def register_ws_callback(self, key: str, callback: Callable):
        self._ws_callbacks[key] = callback

    async def stream_klines(self, symbol: str, interval: str):
        """
        BingX Perpetual Swap WebSocket.
        Endpoint correcto: wss://open-api.bingx.com/market
        Protocolo: suscripción con dataType market.<symbol>.kline.<interval>
        """
        key     = f"{symbol}_{interval}"
        topic   = f"market.{symbol}.kline.{interval}"
        # BingX swap perpetual market data WebSocket
        ws_url  = "wss://open-api.bingx.com/market"
        backoff = 10
        fails   = 0

        while True:
            if fails >= 5:
                log.info(f"WS {key}: demasiados fallos → solo polling (5min)")
                await asyncio.sleep(300)
                fails = 0
                backoff = 10
                continue
            try:
                async with websockets.connect(
                    ws_url,
                    ping_interval=None,   # BingX gestiona su propio heartbeat
                    open_timeout=20,
                    close_timeout=5,
                    max_size=2**20,
                ) as ws:
                    sub = json.dumps({
                        "id":       key,
                        "reqType":  "sub",
                        "dataType": topic,
                    })
                    await ws.send(sub)
                    log.info(f"WS ✓ suscrito: {topic}")
                    backoff = 10
                    fails   = 0
                    last_ping = asyncio.get_event_loop().time()

                    async for raw in ws:
                        try:
                            # BingX envía datos comprimidos con gzip
                            if isinstance(raw, bytes):
                                try:    raw = gzip.decompress(raw).decode("utf-8")
                                except: raw = raw.decode("utf-8", errors="ignore")

                            msg = json.loads(raw)

                            # Heartbeat BingX
                            if "ping" in msg:
                                await ws.send(json.dumps({"pong": msg["ping"]}))
                                last_ping = asyncio.get_event_loop().time()
                                continue

                            # Datos de vela
                            dt = msg.get("dataType", "")
                            if "kline" in dt and "data" in msg:
                                cb = self._ws_callbacks.get(key)
                                if cb:
                                    items = msg["data"] if isinstance(msg["data"], list) else [msg["data"]]
                                    for kline in items:
                                        await cb(kline)

                            # Timeout manual si no hay ping en 60s
                            if asyncio.get_event_loop().time() - last_ping > 60:
                                log.warning(f"WS {key}: sin ping 60s, reconectando")
                                break

                        except Exception as e:
                            log.debug(f"WS parse {key}: {e}")

            except (websockets.exceptions.InvalidStatusCode,
                    websockets.exceptions.InvalidHandshake) as e:
                fails += 1
                log.warning(f"WS {key} rechazado (fallo {fails}/5): {e} → retry {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)
            except (websockets.exceptions.ConnectionClosed,
                    OSError, asyncio.TimeoutError) as e:
                fails += 1
                log.warning(f"WS {key} caído (fallo {fails}/5): {e} → retry {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            except Exception as e:
                fails += 1
                log.error(f"WS {key} error inesperado: {e}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)
