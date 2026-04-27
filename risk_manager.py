"""RISK MANAGER — Gestión de riesgo y position sizing"""
import asyncio
from datetime import datetime, timezone
from dataclasses import dataclass
import config
from bot_logger import get_logger

log = get_logger("Risk")

@dataclass
class DailyStats:
    date: str = ""; pnl: float = 0.0; trades: int = 0
    wins: int = 0;  losses: int = 0;  max_drawdown: float = 0.0

@dataclass
class PositionSizing:
    qty: float; risk_usdt: float; notional: float
    sl_distance: float; valid: bool; reason: str

class RiskManager:
    def __init__(self):
        self._open_positions: dict = {}
        self._daily = DailyStats()
        self._lock  = asyncio.Lock()

    def _check_reset_daily(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily.date != today:
            log.info(f"Reset diario: {today}")
            self._daily = DailyStats(date=today)

    def calculate_position_size(self, equity: float, entry_price: float,
                                 stop_loss: float, size_mult: float = 1.0,
                                 min_qty: float = 0.001, qty_step: float = 0.001) -> PositionSizing:
        if equity <= 0 or entry_price <= 0 or stop_loss <= 0:
            return PositionSizing(0,0,0,0,False,"Parámetros inválidos")
        sl_pct = abs(entry_price - stop_loss) / entry_price
        if sl_pct < 0.001: return PositionSizing(0,0,0,0,False,"SL muy cercano")
        if sl_pct > 0.10:  return PositionSizing(0,0,0,0,False,"SL muy lejos")
        risk_usdt = equity * (config.RISK_PER_TRADE / 100) * size_mult
        qty_raw   = risk_usdt / (entry_price * sl_pct)
        qty       = max(min_qty, round(qty_raw / qty_step) * qty_step)
        log.info(f"Sizing: equity={equity:.2f} riesgo={risk_usdt:.2f}USDT SL={sl_pct:.2%} qty={qty:.4f}")
        return PositionSizing(round(qty,6), round(risk_usdt,2), round(qty*entry_price,2), round(sl_pct*100,3), True, "OK")

    async def can_trade(self, symbol: str, equity: float) -> tuple:
        async with self._lock:
            self._check_reset_daily()
            daily_loss_pct = abs(self._daily.pnl) / max(equity, 1) * 100
            if self._daily.pnl < 0 and daily_loss_pct >= config.MAX_DAILY_LOSS:
                return False, f"Límite pérdida diaria ({daily_loss_pct:.1f}%)"
            if len(self._open_positions) >= config.MAX_OPEN_TRADES:
                return False, f"Máx trades ({len(self._open_positions)}/{config.MAX_OPEN_TRADES})"
            if symbol in self._open_positions:
                return False, f"Posición ya abierta en {symbol}"
            return True, "OK"

    async def register_open(self, symbol: str, direction: str, entry: float, qty: float, sl: float, tp: float):
        async with self._lock:
            self._open_positions[symbol] = {"direction": direction, "entry": entry, "qty": qty, "sl": sl, "tp": tp}
            self._daily.trades += 1
            log.info(f"Registrado: {symbol} {direction} @ {entry}")

    async def register_close(self, symbol: str, exit_price: float):
        async with self._lock:
            pos = self._open_positions.pop(symbol, None)
            if not pos: return
            pnl = (exit_price - pos["entry"]) * pos["qty"]
            if pos["direction"] == "SHORT": pnl = -pnl
            self._daily.pnl += pnl
            if pnl > 0: self._daily.wins += 1
            else:       self._daily.losses += 1
            log.info(f"Cerrado: {symbol} PnL={pnl:+.2f}USDT | Día={self._daily.pnl:+.2f}USDT")

    def get_stats(self) -> dict:
        self._check_reset_daily()
        total = self._daily.wins + self._daily.losses
        wr    = self._daily.wins / total * 100 if total > 0 else 0
        return {"date": self._daily.date, "pnl": round(self._daily.pnl,2),
                "trades": self._daily.trades, "wins": self._daily.wins,
                "losses": self._daily.losses, "winrate": round(wr,1),
                "open": len(self._open_positions),
                "open_symbols": list(self._open_positions.keys())}

    @staticmethod
    def is_trading_hour() -> bool:
        try:
            start, end = config.TRADE_HOURS_UTC.split("-")
            return int(start) <= datetime.now(timezone.utc).hour < int(end)
        except Exception:
            return True
