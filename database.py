"""Database management for trades, signals, and performance tracking."""
import aiosqlite
import os
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple
from loguru import logger

from core.config import cfg


async def init_db() -> None:
    """Initialize database tables."""
    os.makedirs(os.path.dirname(cfg.db_path) or ".", exist_ok=True)
    
    async with aiosqlite.connect(cfg.db_path) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL,
            size REAL NOT NULL,
            sl_price REAL,
            tp_price REAL,
            pnl REAL,
            pnl_pct REAL,
            close_reason TEXT,
            opened_at TEXT NOT NULL,
            closed_at TEXT,
            duration_seconds INTEGER
        );
        
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            signal TEXT NOT NULL,
            confidence REAL,
            adx REAL,
            rsi REAL,
            atr_pct REAL,
            delta1 REAL,
            delta2 REAL,
            delta3 REAL,
            executed BOOLEAN,
            created_at TEXT NOT NULL
        );
        
        CREATE TABLE IF NOT EXISTS daily_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            win_count INTEGER DEFAULT 0,
            loss_count INTEGER DEFAULT 0,
            total_pnl REAL DEFAULT 0,
            max_drawdown REAL DEFAULT 0
        );
        
        CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
        CREATE INDEX IF NOT EXISTS idx_trades_opened_at ON trades(opened_at);
        CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
        CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals(created_at);
        """)
        await db.commit()
    
    logger.info(f"Database initialized: {cfg.db_path}")


async def save_trade_open(
    symbol: str, side: str, entry_price: float, 
    exit_price: float, size: float, sl: float, tp: float,
    metrics: Dict
) -> int:
    """Save a new open trade."""
    opened_at = datetime.now(timezone.utc).isoformat()
    
    async with aiosqlite.connect(cfg.db_path) as db:
        cursor = await db.execute(
            """INSERT INTO trades 
            (symbol, side, entry_price, size, sl_price, tp_price, opened_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (symbol, side, entry_price, size, sl, tp, opened_at)
        )
        await db.commit()
        return cursor.lastrowid


async def save_trade_close(
    trade_id: int, exit_price: float, pnl: float, pnl_pct: float,
    close_reason: str, entry_price: float, opened_at: str
) -> None:
    """Update trade with close information."""
    closed_at = datetime.now(timezone.utc).isoformat()
    
    async with aiosqlite.connect(cfg.db_path) as db:
        await db.execute(
            """UPDATE trades 
            SET exit_price = ?, pnl = ?, pnl_pct = ?, close_reason = ?, closed_at = ?
            WHERE id = ?""",
            (exit_price, pnl, pnl_pct, close_reason, closed_at, trade_id)
        )
        await db.commit()


async def save_signal(
    symbol: str, signal: str, metrics: Dict, executed: bool = False
) -> None:
    """Save a trading signal."""
    created_at = datetime.now(timezone.utc).isoformat()
    
    async with aiosqlite.connect(cfg.db_path) as db:
        await db.execute(
            """INSERT INTO signals 
            (symbol, signal, confidence, adx, rsi, atr_pct, delta1, delta2, delta3, executed, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                symbol, signal,
                metrics.get("confidence", 0),
                metrics.get("adx", 0),
                metrics.get("rsi", 0),
                metrics.get("atr_pct", 0),
                metrics.get("delta1", 0),
                metrics.get("delta2", 0),
                metrics.get("delta3", 0),
                executed,
                created_at
            )
        )
        await db.commit()


async def get_performance_stats() -> Dict:
    """Get overall performance statistics."""
    async with aiosqlite.connect(cfg.db_path) as db:
        # Total trades
        cursor = await db.execute("SELECT COUNT(*) FROM trades WHERE closed_at IS NOT NULL")
        total_trades = (await cursor.fetchone())[0]
        
        # Wins and losses
        cursor = await db.execute(
            "SELECT COUNT(*) FROM trades WHERE closed_at IS NOT NULL AND pnl > 0"
        )
        wins = (await cursor.fetchone())[0]
        losses = total_trades - wins
        
        # Total PnL
        cursor = await db.execute("SELECT SUM(pnl) FROM trades WHERE closed_at IS NOT NULL")
        total_pnl = (await cursor.fetchone())[0] or 0
        
        # Win rate
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        # Average trade
        avg_trade = (total_pnl / total_trades) if total_trades > 0 else 0
        
        return {
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "avg_trade": avg_trade,
        }


async def get_recent_trades(limit: int = 10) -> List[Dict]:
    """Get recent closed trades."""
    async with aiosqlite.connect(cfg.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM trades 
            WHERE closed_at IS NOT NULL 
            ORDER BY closed_at DESC 
            LIMIT ?""",
            (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
