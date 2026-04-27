"""POSITION MONITOR — Trailing SL y cierre automático"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import config
from bot_logger import get_logger

log = get_logger("Monitor")
TRAILING_ACTIVATION = 0.5
TRAILING_DISTANCE   = 0.3

@dataclass
class TrackedPosition:
    symbol: str; direction: str; entry: float; qty: float; sl: float; tp: float
    trailing_active: bool = False; best_price: float = 0.0
    opened_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class PositionMonitor:
    def __init__(self, client, risk, tg):
        self._client = client; self._risk = risk; self._tg = tg
        self._positions: dict = {}; self._lock = asyncio.Lock()

    def track(self, symbol: str, direction: str, entry: float, qty: float, sl: float, tp: float):
        self._positions[symbol] = TrackedPosition(symbol, direction, entry, qty, sl, tp, best_price=entry)
        log.info(f"Monitor: {symbol} {direction} @ {entry}")

    async def _update(self, pos: TrackedPosition) -> Optional[str]:
        try:
            ticker = await self._client.get_ticker(pos.symbol)
            price  = float(ticker.get("lastPrice", ticker.get("price", 0)))
            if price == 0: return None
        except Exception as e:
            log.warning(f"Ticker {pos.symbol}: {e}"); return None

        if pos.direction == "LONG":
            if price <= pos.sl: return "SL"
            if price >= pos.tp: return "TP"
        else:
            if price >= pos.sl: return "SL"
            if price <= pos.tp: return "TP"

        pnl_pct = (price - pos.entry) / pos.entry * 100
        if pos.direction == "SHORT": pnl_pct = -pnl_pct
        if pnl_pct >= TRAILING_ACTIVATION: pos.trailing_active = True
        if pos.trailing_active:
            if pos.direction == "LONG":
                new_sl = price * (1 - TRAILING_DISTANCE / 100)
                if new_sl > pos.sl:
                    log.info(f"Trailing {pos.symbol}: {pos.sl:.4f} → {new_sl:.4f}")
                    pos.sl = new_sl; return "TRAIL"
            else:
                new_sl = price * (1 + TRAILING_DISTANCE / 100)
                if new_sl < pos.sl:
                    log.info(f"Trailing {pos.symbol}: {pos.sl:.4f} → {new_sl:.4f}")
                    pos.sl = new_sl; return "TRAIL"
        return None

    async def _close(self, pos: TrackedPosition, reason: str):
        log.info(f"Cerrando {pos.symbol} motivo={reason}")
        try:
            if not config.DRY_RUN:
                await self._client.close_position(pos.symbol, pos.direction, pos.qty)
        except Exception as e:
            log.error(f"Error cerrando {pos.symbol}: {e}")
        try:
            ticker     = await self._client.get_ticker(pos.symbol)
            exit_price = float(ticker.get("lastPrice", pos.entry))
        except Exception:
            exit_price = pos.entry
        await self._risk.register_close(pos.symbol, exit_price)
        emoji = "✅" if reason == "TP" else "🛑"
        self._tg.error_alert(f"{emoji} {pos.symbol} cerrado ({reason}) @ {exit_price:.4f}")

    async def run(self):
        log.info("PositionMonitor iniciado")
        while True:
            try:
                async with self._lock:
                    symbols = list(self._positions.keys())
                for sym in symbols:
                    async with self._lock:
                        pos = self._positions.get(sym)
                    if not pos: continue
                    result = await self._update(pos)
                    if result in ("SL", "TP"):
                        await self._close(pos, result)
                        async with self._lock:
                            self._positions.pop(sym, None)
            except Exception as e:
                log.error(f"Monitor error: {e}")
            await asyncio.sleep(config.POLL_INTERVAL)
