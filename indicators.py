"""Technical indicators and signal generation for UltraBot v3."""
from typing import Tuple, Dict, Optional
from loguru import logger


def calculate_atr(highs: list, lows: list, closes: list, period: int = 14) -> float:
    """Calculate Average True Range."""
    if len(highs) < period:
        return 0.0
    
    tr_values = []
    for i in range(len(highs)):
        high = highs[i]
        low = lows[i]
        close_prev = closes[i - 1] if i > 0 else closes[i]
        
        tr = max(
            high - low,
            abs(high - close_prev),
            abs(low - close_prev)
        )
        tr_values.append(tr)
    
    # SMA of TR
    atr = sum(tr_values[-period:]) / period
    return atr


def calculate_rsi(prices: list, period: int = 14) -> float:
    """Calculate Relative Strength Index."""
    if len(prices) < period + 1:
        return 50.0
    
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    return rsi


def calculate_adx(highs: list, lows: list, closes: list, period: int = 14) -> float:
    """Calculate Average Directional Index (simplified)."""
    if len(highs) < period * 2:
        return 0.0
    
    # Calculate +DM and -DM
    plus_dm = []
    minus_dm = []
    
    for i in range(1, len(highs)):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        
        if up_move > down_move and up_move > 0:
            plus_dm.append(up_move)
            minus_dm.append(0)
        elif down_move > up_move and down_move > 0:
            plus_dm.append(0)
            minus_dm.append(down_move)
        else:
            plus_dm.append(0)
            minus_dm.append(0)
    
    # Calculate ATR
    atr = calculate_atr(highs, lows, closes, period)
    if atr == 0:
        return 0.0
    
    # Calculate +DI and -DI
    plus_di = (sum(plus_dm[-period:]) / period) / atr * 100
    minus_di = (sum(minus_dm[-period:]) / period) / atr * 100
    
    # Calculate ADX
    dx_values = []
    for i in range(period):
        di_sum = plus_di + minus_di
        if di_sum == 0:
            dx = 0
        else:
            dx = abs(plus_di - minus_di) / di_sum * 100
        dx_values.append(dx)
    
    adx = sum(dx_values) / len(dx_values) if dx_values else 0
    return adx


def calculate_volume_delta(volumes: list) -> Tuple[float, float, float]:
    """Calculate volume deltas across timeframes."""
    if len(volumes) < 3:
        return 0.0, 0.0, 0.0
    
    # Short-term (last 5 candles)
    delta1 = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else sum(volumes) / len(volumes)
    
    # Medium-term (last 20 candles)
    delta2 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else sum(volumes) / len(volumes)
    
    # Long-term (average)
    delta3 = sum(volumes) / len(volumes)
    
    return delta1, delta2, delta3


def generate_signal(
    high: float, low: float, close: float, open_: float, volume: float,
    h_high: Optional[float] = None, h_low: Optional[float] = None,
    h_close: Optional[float] = None, h_open: Optional[float] = None,
    h_volume: Optional[float] = None,
    t_high: Optional[float] = None, t_low: Optional[float] = None,
    t_close: Optional[float] = None,
    cfg=None
) -> Tuple[Optional[str], Dict]:
    """
    Generate BUY/SELL signal based on multi-timeframe analysis.
    
    Returns: (signal, metrics_dict)
    """
    
    if cfg is None:
        return None, {}
    
    # Initialize metrics
    metrics = {
        "adx": 0,
        "rsi": 0,
        "atr_pct": 0,
        "confidence": 0,
        "delta1": 0,
        "delta2": 0,
        "delta3": 0,
    }
    
    try:
        # Simple logic: trend + momentum
        # In a real bot, use proper OHLCV arrays
        
        # RSI analysis (momentum)
        # Simulate with simple calculation
        price_change = close - open_
        rsi = 50 + (price_change / (high - low) * 100) if (high - low) > 0 else 50
        metrics["rsi"] = rsi
        
        # ATR analysis (volatility)
        atr_range = high - low
        atr_pct = (atr_range / close * 100) if close > 0 else 0
        metrics["atr_pct"] = atr_pct
        
        # Volume analysis
        volume_multiplier = volume / (volume * 0.8) if volume > 0 else 1.0  # Simplified
        delta1 = volume * 0.95
        delta2 = volume * 1.0
        delta3 = volume * 1.05
        metrics["delta1"] = delta1
        metrics["delta2"] = delta2
        metrics["delta3"] = delta3
        
        # Confidence score
        confidence = 50
        
        # BUY signal logic
        if rsi < cfg.rsi_oversold:
            confidence += 20
            signal = "BUY"
        elif rsi > cfg.rsi_overbought:
            confidence += 20
            signal = "SELL"
        else:
            confidence += 10
            if close > open_:
                signal = "BUY"
            else:
                signal = "SELL"
        
        # Volume confirmation
        if volume_multiplier > 1.1:
            confidence += 10
        
        # Clamp confidence
        confidence = min(100, max(0, confidence))
        metrics["confidence"] = confidence
        
        # Only return signal if confidence meets threshold
        if confidence >= cfg.min_confidence:
            return signal, metrics
        
        return None, metrics
    
    except Exception as e:
        logger.debug(f"Signal generation error: {e}")
        return None, metrics
