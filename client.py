"""Exchange API client for Binance futures trading."""
import aiohttp
import json
import hashlib
import hmac
import time
from typing import Dict, List, Optional, Tuple, Any
from loguru import logger

from core.config import cfg


class BinanceClient:
    """Binance Futures API client."""
    
    def __init__(self, api_key: str, api_secret: str, base_url: str = "https://fapi.binance.com"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.session: Optional[aiohttp.ClientSession] = None
    
    async def _init_session(self) -> None:
        """Initialize HTTP session if not already done."""
        if self.session is None:
            self.session = aiohttp.ClientSession()
    
    async def _request(
        self, method: str, endpoint: str, 
        params: Optional[Dict] = None, 
        signed: bool = False
    ) -> Dict:
        """Make API request."""
        await self._init_session()
        
        url = f"{self.base_url}{endpoint}"
        headers = {"X-MBX-APIKEY": self.api_key}
        
        if signed:
            params = params or {}
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = 5000
            
            # Create signature
            query_string = "&".join(f"{k}={v}" for k, v in params.items())
            signature = hmac.new(
                self.api_secret.encode(),
                query_string.encode(),
                hashlib.sha256
            ).hexdigest()
            params["signature"] = signature
        
        try:
            async with self.session.request(method, url, params=params, headers=headers) as resp:
                return await resp.json()
        except Exception as e:
            logger.error(f"API request failed: {e}")
            return {"code": -1, "msg": str(e)}
    
    async def get_tickers(self) -> Dict:
        """Get all ticker information."""
        return await self._request("GET", "/fapi/v1/ticker/24hr")
    
    async def get_klines(
        self, symbol: str, interval: str = "1h", limit: int = 100
    ) -> List[List]:
        """Get candlestick data."""
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        resp = await self._request("GET", "/fapi/v1/klines", params=params)
        if isinstance(resp, list):
            return resp
        return []
    
    async def get_account(self) -> Dict:
        """Get account information."""
        return await self._request("GET", "/fapi/v2/account", signed=True)
    
    async def get_open_positions(self) -> List[Dict]:
        """Get all open positions."""
        resp = await self._request("GET", "/fapi/v2/positionRisk", signed=True)
        if isinstance(resp, list):
            return [pos for pos in resp if float(pos.get("positionAmt", 0)) != 0]
        return []
    
    async def set_leverage(self, symbol: str, leverage: int) -> Dict:
        """Set leverage for a symbol."""
        params = {"symbol": symbol, "leverage": leverage}
        return await self._request("POST", "/fapi/v1/leverage", params=params, signed=True)
    
    async def place_market_order(
        self, symbol: str, side: str, quantity: float,
        stop_loss: float = 0, take_profit: float = 0
    ) -> Dict:
        """Place a market order with SL and TP."""
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": quantity,
        }
        
        result = await self._request("POST", "/fapi/v1/order", params=params, signed=True)
        
        # Set stop loss and take profit orders separately
        if result.get("orderId") and (stop_loss > 0 or take_profit > 0):
            order_id = result["orderId"]
            
            if stop_loss > 0:
                await self._set_stop_loss(symbol, stop_loss, side, quantity)
            
            if take_profit > 0:
                await self._set_take_profit(symbol, take_profit, side, quantity)
        
        return result
    
    async def _set_stop_loss(
        self, symbol: str, price: float, side: str, quantity: float
    ) -> Dict:
        """Set stop loss order."""
        close_side = "SELL" if side == "BUY" else "BUY"
        params = {
            "symbol": symbol,
            "side": close_side,
            "type": "STOP_MARKET",
            "quantity": quantity,
            "stopPrice": price,
            "timeInForce": "GTE_GTC",
        }
        return await self._request("POST", "/fapi/v1/order", params=params, signed=True)
    
    async def _set_take_profit(
        self, symbol: str, price: float, side: str, quantity: float
    ) -> Dict:
        """Set take profit order."""
        close_side = "SELL" if side == "BUY" else "BUY"
        params = {
            "symbol": symbol,
            "side": close_side,
            "type": "TAKE_PROFIT_MARKET",
            "quantity": quantity,
            "stopPrice": price,
            "timeInForce": "GTE_GTC",
        }
        return await self._request("POST", "/fapi/v1/order", params=params, signed=True)
    
    async def close_position(self, symbol: str, position: Dict) -> Dict:
        """Close a position."""
        side_to_close = "SELL" if float(position.get("positionAmt", 0)) > 0 else "BUY"
        quantity = abs(float(position.get("positionAmt", 0)))
        
        params = {
            "symbol": symbol,
            "side": side_to_close,
            "type": "MARKET",
            "quantity": quantity,
        }
        return await self._request("POST", "/fapi/v1/order", params=params, signed=True)
    
    async def cancel_all_orders(self, symbol: str) -> Dict:
        """Cancel all open orders for a symbol."""
        params = {"symbol": symbol}
        return await self._request("DELETE", "/fapi/v1/allOpenOrders", params=params, signed=True)
    
    async def get_price(self, symbol: str) -> float:
        """Get current price for a symbol."""
        params = {"symbol": symbol}
        resp = await self._request("GET", "/fapi/v1/ticker/price", params=params)
        return float(resp.get("price", 0))
    
    async def close_session(self) -> None:
        """Close HTTP session."""
        if self.session:
            await self.session.close()


# Global client instance
_client = BinanceClient(cfg.exchange_key, cfg.exchange_secret, cfg.exchange_url)


# High-level API functions
async def fetch_all_tickers() -> List[Dict]:
    """Get all tickers."""
    return await _client.get_tickers()


async def fetch_universe_concurrent(symbols: List[str]) -> Dict[str, Dict]:
    """Fetch OHLCV data for all symbols concurrently."""
    data = {}
    
    for sym in symbols:
        try:
            # Fetch 1h, 4h, and 1d candles
            klines_1h = await _client.get_klines(sym, "1h", 100)
            klines_4h = await _client.get_klines(sym, "4h", 50)
            klines_1d = await _client.get_klines(sym, "1d", 30)
            
            # Parse primary timeframe (1h)
            if klines_1h and len(klines_1h) > 0:
                k = klines_1h[-1]
                price_data = {
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "open": float(k[1]),
                    "volume": float(k[7]),
                }
                
                # Higher timeframes
                h_data = None
                t_data = None
                
                if klines_4h and len(klines_4h) > 0:
                    k = klines_4h[-1]
                    h_data = {
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "open": float(k[1]),
                        "volume": float(k[7]),
                    }
                
                if klines_1d and len(klines_1d) > 0:
                    k = klines_1d[-1]
                    t_data = {
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "open": float(k[1]),
                        "volume": float(k[7]),
                    }
                
                data[sym] = {
                    "p": price_data,  # 1h primary
                    "h": h_data,      # 4h
                    "t": t_data,      # 1d
                }
        except Exception as e:
            logger.debug(f"Error fetching {sym}: {e}")
    
    return data


async def get_balance() -> float:
    """Get total wallet balance in USDT."""
    account = await _client.get_account()
    balance = account.get("totalWalletBalance", 0)
    return float(balance)


async def get_all_positions() -> Dict[str, Dict]:
    """Get all open positions."""
    positions = await _client.get_open_positions()
    result = {}
    for pos in positions:
        symbol = pos.get("symbol")
        if symbol:
            result[symbol] = pos
    return result


async def set_leverage(symbol: str, leverage: int) -> Dict:
    """Set leverage."""
    return await _client.set_leverage(symbol, leverage)


async def place_market_order(
    symbol: str, side: str, size: float, sl: float = 0, tp: float = 0
) -> Dict:
    """Place a market order."""
    return await _client.place_market_order(symbol, side, size, sl, tp)


async def close_position(symbol: str, position: Dict) -> Dict:
    """Close a position."""
    return await _client.close_position(symbol, position)


async def cancel_all_orders(symbol: str) -> Dict:
    """Cancel all orders for a symbol."""
    return await _client.cancel_all_orders(symbol)


async def get_price(symbol: str) -> float:
    """Get current price."""
    return await _client.get_price(symbol)


async def close_session() -> None:
    """Close the client session."""
    await _client.close_session()
