"""Risk management system for position sizing and stop-loss/take-profit calculation."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Tuple, Dict
from loguru import logger

from core.config import cfg


@dataclass
class RiskManager:
    """Manages position sizing, SL/TP, and trading permissions."""
    
    balance: float = 0.0
    open_positions: int = 0
    daily_pnl: float = 0.0
    last_daily_reset: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_halted: bool = False
    halt_reason: str = ""
    correlation_symbols: Dict[str, float] = field(default_factory=dict)
    
    def set_balance(self, balance: float) -> None:
        """Update current balance."""
        self.balance = balance
        
        # Check daily loss limit
        if self.daily_pnl < -cfg.max_daily_loss:
            self.is_halted = True
            self.halt_reason = f"Daily loss limit hit: ${self.daily_pnl:.2f} USDT"
            logger.warning(self.halt_reason)
    
    def record_open(self, symbol: str) -> None:
        """Record opening of a position."""
        self.open_positions += 1
    
    def record_close(self, symbol: str, pnl: float, balance: float) -> None:
        """Record closing of a position."""
        self.open_positions = max(0, self.open_positions - 1)
        self.daily_pnl += pnl
        self.balance = balance
        
        # Check daily loss limit
        if self.daily_pnl < -cfg.max_daily_loss:
            self.is_halted = True
            self.halt_reason = f"Daily loss limit hit: ${self.daily_pnl:.2f} USDT"
    
    def can_trade(self, balance: float) -> Tuple[bool, str]:
        """Check if trading is allowed."""
        # Check halt status
        if self.is_halted:
            return False, "Bot halted due to daily loss limit"
        
        # Check balance
        if balance <= 0:
            return False, "Insufficient balance"
        
        # Check max open positions
        if self.open_positions >= cfg.max_open_trades:
            return False, f"Max open positions reached ({self.open_positions})"
        
        return True, "OK"
    
    def correlation_ok(self, symbol: str) -> bool:
        """Check if symbol is not too correlated with open positions."""
        # Simple correlation check - can be enhanced
        # For now, accept all
        return True
    
    def position_size(
        self, balance: float, n_open: int, confidence: float = 50, atr_pct: float = 1.0
    ) -> float:
        """Calculate position size based on risk management rules."""
        
        # Base size: equal weight per max open trades
        base_size = (balance * cfg.max_risk_per_trade / 100) / cfg.max_open_trades
        
        # Adjust by confidence (higher confidence = larger position)
        confidence_multiplier = (confidence - 40) / 30  # Map 40-70% to 0-1
        confidence_multiplier = max(0.5, min(1.5, confidence_multiplier))  # Clamp 0.5-1.5
        
        # Adjust by ATR volatility (higher volatility = smaller position)
        atr_multiplier = 1.0 / (1 + atr_pct / 100)
        
        size = base_size * confidence_multiplier * atr_multiplier
        
        # Minimum viable position
        min_size = 10  # $10 minimum
        return max(min_size, size)
    
    def dynamic_sl_tp(
        self, entry_price: float, side: str, atr: float = 0.0
    ) -> Tuple[float, float, float, float]:
        """Calculate dynamic stop-loss and take-profit levels based on ATR."""
        
        # SL: 2x ATR or 2% - whichever is larger
        sl_distance = max(atr, entry_price * 0.02)
        sl_pct = (sl_distance / entry_price) * 100
        
        # TP: 3x SL distance (risk/reward 1:3)
        tp_distance = sl_distance * 3
        tp_pct = (tp_distance / entry_price) * 100
        
        if side == "BUY":
            sl = entry_price - sl_distance
            tp = entry_price + tp_distance
        else:  # SELL
            sl = entry_price + sl_distance
            tp = entry_price - tp_distance
        
        return sl, tp, sl_pct, tp_pct
    
    def summary(self) -> Dict:
        """Get current risk status summary."""
        return {
            "balance": self.balance,
            "open_positions": self.open_positions,
            "daily_pnl": self.daily_pnl,
            "is_halted": self.is_halted,
            "halt_reason": self.halt_reason,
            "max_daily_loss": cfg.max_daily_loss,
        }
