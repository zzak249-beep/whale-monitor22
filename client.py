"""
THREE STEP BOT — client.py
===========================
Cliente BingX REST para futuros perpetuos.
Soporta: balance, leverage, market order con SL/TP automático, posiciones.
Todo a 10x (configurable via LEVERAGE env).
"""
from __future__ import annotations
import asyncio
import hashlib
import hmac
import time
from typing import Optional
from urllib.parse import urlencode

import aiohttp
from loguru import logger

from config import cfg

_SESSION: Optional[aiohttp.ClientSession] = None


def _sign(params: dict) -> str:
    query = urlencode(sorted(params.items()))
    return hmac.new(cfg.secret_key.encode(), query.encode(), hashlib.sha256).hexdigest()


def _headers() -> dict:
    return {"X-BX-APIKEY": cfg.api_key, "Content-Type": "application/json"}


async def _session() -> aiohttp.ClientSession:
    global _SESSION
    if _SESSION is None or _SESSION.closed:
        timeout = aiohttp.ClientTimeout(total=15)
        _SESSION = aiohttp.ClientSession(timeout=timeout)
    return _SESSION


async def _get(path: str, params: dict | None = None) -> dict:
    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 5000
    params["signature"] = _sign(params)
    url = cfg.base_url + path
    s = await _session()
    async with s.get(url, params=params, headers=_headers()) as r:
        return await r.json(content_type=None)


async def _post(path: str, params: dict) -> dict:
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 5000
    params["signature"] = _sign(params)
    url = cfg.base_url + path
    s = await _session()
    async with s.post(url, params=params, headers=_headers()) as r:
        return await r.json(content_type=None)


# ── Public helpers ────────────────────────────────────────────────────────────

async def get_balance() -> float:
    """Retorna USDT disponible en cuenta de futuros."""
    try:
        data = await _get("/openApi/swap/v2/user/balance")
        balance_list = data.get("data", {}).get("balance", [])
        if isinstance(balance_list, list):
            for b in balance_list:
                if b.get("asset") == "USDT":
                    return float(b.get("availableMargin", 0) or 0)
        elif isinstance(balance_list, dict):
            return float(balance_list.get("availableMargin", 0) or 0)
        return 0.0
    except Exception as e:
        logger.warning(f"get_balance error: {e}")
        return 0.0


async def set_leverage(symbol: str, leverage: int) -> None:
    """Fija el apalancamiento para un símbolo en ambas direcciones."""
    for side in ("LONG", "SHORT"):
        try:
            await _post("/openApi/swap/v2/trade/leverage", {
                "symbol": symbol,
                "side": side,
                "leverage": leverage,
            })
        except Exception as e:
            logger.warning(f"set_leverage {symbol} {side}: {e}")
    logger.debug(f"Leverage {symbol} → {leverage}x")


async def place_market_order(
    symbol: str,
    side: str,              # BUY | SELL
    size_usdt: float,
    sl: float,
    tp: float,
    leverage: int | None = None,
) -> dict:
    """
    Coloca orden de mercado con SL y TP automáticos.
    - leverage: si None usa cfg.leverage (10x)
    - sl / tp: precios absolutos
    """
    lev = leverage or cfg.leverage
    pos_side = "LONG" if side == "BUY" else "SHORT"

    # Obtener precio actual para calcular qty
    try:
        ticker = await _get("/openApi/swap/v2/quote/price", {"symbol": symbol})
        price = float(ticker["data"]["price"])
    except Exception as e:
        logger.error(f"No se pudo obtener precio de {symbol}: {e}")
        return {"code": -1, "msg": str(e)}

    qty = round((size_usdt * lev) / price, 4)
    if qty <= 0:
        return {"code": -1, "msg": "qty calculada es 0"}

    # ── Orden de entrada ──────────────────────────────────────────────────────
    entry_params = {
        "symbol":       symbol,
        "side":         side,
        "positionSide": pos_side,
        "type":         "MARKET",
        "quantity":     qty,
    }

    logger.info(
        f"[ORDER] {symbol} {side} qty={qty} @ ~{price:.4f} "
        f"SL={sl:.4f} TP={tp:.4f} ({lev}x)"
    )

    resp = await _post("/openApi/swap/v2/trade/order", entry_params)
    code = resp.get("code", -1)

    if code not in (0, 200, None):
        logger.warning(f"[ORDER FAIL] {symbol} code={code} msg={resp.get('msg','')}")
        return resp

    order_data = resp.get("data", {}).get("order", resp.get("data", {}))

    # ── Stop Loss automático ──────────────────────────────────────────────────
    sl_side  = "SELL" if side == "BUY" else "BUY"
    sl_params = {
        "symbol":       symbol,
        "side":         sl_side,
        "positionSide": pos_side,
        "type":         "STOP_MARKET",
        "quantity":     qty,
        "stopPrice":    round(sl, 6),
        "workingType":  "MARK_PRICE",
    }
    try:
        sl_resp = await _post("/openApi/swap/v2/trade/order", sl_params)
        if sl_resp.get("code") not in (0, 200, None):
            logger.warning(f"SL order failed: {sl_resp.get('msg','')}")
        else:
            logger.info(f"  SL colocado @ {sl:.4f}")
    except Exception as e:
        logger.warning(f"SL placement error: {e}")

    # ── Take Profit automático ────────────────────────────────────────────────
    tp_side  = "SELL" if side == "BUY" else "BUY"
    tp_params = {
        "symbol":       symbol,
        "side":         tp_side,
        "positionSide": pos_side,
        "type":         "TAKE_PROFIT_MARKET",
        "quantity":     qty,
        "stopPrice":    round(tp, 6),
        "workingType":  "MARK_PRICE",
    }
    try:
        tp_resp = await _post("/openApi/swap/v2/trade/order", tp_params)
        if tp_resp.get("code") not in (0, 200, None):
            logger.warning(f"TP order failed: {tp_resp.get('msg','')}")
        else:
            logger.info(f"  TP colocado @ {tp:.4f}")
    except Exception as e:
        logger.warning(f"TP placement error: {e}")

    return resp


async def close_position(symbol: str, side: str, qty: float) -> dict:
    """Cierra posición con orden de mercado reduce-only."""
    close_side = "SELL" if side == "LONG" else "BUY"
    pos_side   = side  # LONG | SHORT
    params = {
        "symbol":       symbol,
        "side":         close_side,
        "positionSide": pos_side,
        "type":         "MARKET",
        "quantity":     round(qty, 4),
        "reduceOnly":   True,
    }
    return await _post("/openApi/swap/v2/trade/order", params)


async def cancel_all_orders(symbol: str) -> dict:
    """Cancela todas las órdenes abiertas de un símbolo (SL/TP pendientes)."""
    try:
        return await _post("/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol})
    except Exception as e:
        logger.warning(f"cancel_all_orders {symbol}: {e}")
        return {}


async def get_open_positions() -> list:
    """Retorna lista de posiciones abiertas."""
    try:
        data = await _get("/openApi/swap/v2/user/positions")
        return data.get("data", []) or []
    except Exception as e:
        logger.warning(f"get_open_positions: {e}")
        return []


async def get_klines(symbol: str, interval: str, limit: int = 100) -> list:
    """Retorna velas OHLCV."""
    try:
        data = await _get("/openApi/swap/v3/quote/klines", {
            "symbol":   symbol,
            "interval": interval,
            "limit":    limit,
        })
        raw = data.get("data", [])
        candles = []
        for c in raw:
            try:
                if isinstance(c, dict):
                    candles.append({
                        "time":   int(c.get("time", c.get("t", 0))),
                        "open":   float(c.get("open",   c.get("o", 0))),
                        "high":   float(c.get("high",   c.get("h", 0))),
                        "low":    float(c.get("low",    c.get("l", 0))),
                        "close":  float(c.get("close",  c.get("c", 0))),
                        "volume": float(c.get("volume", c.get("v", 0))),
                    })
                elif isinstance(c, list) and len(c) >= 6:
                    candles.append({
                        "time": int(c[0]), "open": float(c[1]),
                        "high": float(c[2]), "low": float(c[3]),
                        "close": float(c[4]), "volume": float(c[5]),
                    })
            except Exception:
                continue
        candles.sort(key=lambda x: x["time"])
        return candles
    except Exception as e:
        logger.warning(f"get_klines {symbol}: {e}")
        return []


async def get_recent_trades(symbol: str) -> list:
    """Obtiene los últimos trades cerrados de un símbolo."""
    try:
        data = await _get("/openApi/swap/v2/trade/allOrders", {
            "symbol": symbol,
            "limit": 20,
        })
        return data.get("data", {}).get("orders", []) or []
    except Exception as e:
        logger.warning(f"get_recent_trades {symbol}: {e}")
        return []
