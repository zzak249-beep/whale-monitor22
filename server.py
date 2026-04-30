"""Dashboard web server for real-time monitoring."""
import asyncio
import json
from typing import Dict, Any, Optional
from datetime import datetime
from loguru import logger

from core.config import cfg


class DashboardState:
    """Current state of the dashboard."""
    
    def __init__(self):
        self.status: str = "initializing"
        self.balance: float = 0.0
        self.positions: Dict = {}
        self.scan_stats: Dict = {}
        self.risk: Dict = {}
        self.perf: Dict = {}
        self.last_signals: list = []
        self.trade_metrics: Dict = {}
        self.last_update: str = datetime.now().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "status": self.status,
            "balance": self.balance,
            "positions": self.positions,
            "scan_stats": self.scan_stats,
            "risk": self.risk,
            "perf": self.perf,
            "last_signals": self.last_signals,
            "trade_metrics": self.trade_metrics,
            "last_update": self.last_update,
        }


# Global dashboard state
_state = DashboardState()


def update_state(
    status: str = None,
    balance: float = None,
    positions: Dict = None,
    scan_stats: Dict = None,
    risk: Dict = None,
    perf: Dict = None,
    last_signals: list = None,
    trade_metrics: Dict = None,
) -> None:
    """Update dashboard state."""
    global _state
    
    if status is not None:
        _state.status = status
    if balance is not None:
        _state.balance = balance
    if positions is not None:
        _state.positions = positions
    if scan_stats is not None:
        _state.scan_stats = scan_stats
    if risk is not None:
        _state.risk = risk
    if perf is not None:
        _state.perf = perf
    if last_signals is not None:
        _state.last_signals = last_signals
    if trade_metrics is not None:
        _state.trade_metrics = trade_metrics
    
    _state.last_update = datetime.now().isoformat()


def get_state() -> Dict[str, Any]:
    """Get current state."""
    return _state.to_dict()


async def start_dashboard() -> None:
    """Start the dashboard server."""
    try:
        from aiohttp import web
        
        async def handle_api(request):
            """API endpoint for dashboard data."""
            return web.json_response(get_state())
        
        async def handle_home(request):
            """Serve dashboard HTML."""
            html = _get_dashboard_html()
            return web.Response(text=html, content_type="text/html")
        
        app = web.Application()
        app.router.add_get("/", handle_home)
        app.router.add_get("/api/state", handle_api)
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", cfg.dashboard_port)
        await site.start()
        
        logger.info(f"Dashboard started on http://0.0.0.0:{cfg.dashboard_port}")
    
    except ImportError:
        logger.warning("aiohttp not installed, dashboard disabled")
    except Exception as e:
        logger.error(f"Dashboard start failed: {e}")


def _get_dashboard_html() -> str:
    """Generate dashboard HTML."""
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>UltraBot v3 Dashboard</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #0a0e27 0%, #1a1a2e 100%);
            color: #e0e0e0;
            padding: 20px;
            min-height: 100vh;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        
        header {
            text-align: center;
            margin-bottom: 30px;
            border-bottom: 2px solid #00ff88;
            padding-bottom: 20px;
        }
        
        h1 {
            font-size: 2.5em;
            color: #00ff88;
            text-shadow: 0 0 10px rgba(0, 255, 136, 0.5);
        }
        
        .status {
            display: inline-block;
            padding: 8px 16px;
            border-radius: 20px;
            margin-top: 10px;
            font-weight: bold;
        }
        
        .status.running {
            background: #00ff88;
            color: #000;
        }
        
        .status.halted {
            background: #ff4444;
            color: #fff;
        }
        
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        
        .card {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(0, 255, 136, 0.3);
            border-radius: 10px;
            padding: 20px;
            backdrop-filter: blur(10px);
        }
        
        .card h3 {
            color: #00ff88;
            margin-bottom: 15px;
            font-size: 1.2em;
        }
        
        .metric {
            display: flex;
            justify-content: space-between;
            margin-bottom: 10px;
            padding: 8px 0;
            border-bottom: 1px solid rgba(0, 255, 136, 0.1);
        }
        
        .metric-label {
            color: #999;
        }
        
        .metric-value {
            color: #00ff88;
            font-weight: bold;
        }
        
        .metric-value.negative {
            color: #ff4444;
        }
        
        .metric-value.positive {
            color: #00ff88;
        }
        
        .last-update {
            text-align: center;
            color: #666;
            font-size: 0.9em;
            margin-top: 20px;
        }
        
        .refresh-button {
            background: #00ff88;
            color: #000;
            border: none;
            padding: 10px 20px;
            border-radius: 5px;
            cursor: pointer;
            font-weight: bold;
            margin-top: 10px;
            transition: all 0.3s;
        }
        
        .refresh-button:hover {
            background: #00dd77;
            transform: scale(1.05);
        }
        
        .positions-list {
            max-height: 300px;
            overflow-y: auto;
        }
        
        .position-item {
            background: rgba(0, 255, 136, 0.1);
            padding: 10px;
            margin-bottom: 10px;
            border-radius: 5px;
            font-size: 0.9em;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🤖 UltraBot v3</h1>
            <div class="status" id="status">Loading...</div>
        </header>
        
        <div class="grid">
            <div class="card">
                <h3>💰 Balance</h3>
                <div class="metric">
                    <span class="metric-label">Total USDT</span>
                    <span class="metric-value" id="balance">0.00</span>
                </div>
            </div>
            
            <div class="card">
                <h3>📊 Performance</h3>
                <div class="metric">
                    <span class="metric-label">Total Trades</span>
                    <span class="metric-value" id="total_trades">0</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Win Rate</span>
                    <span class="metric-value" id="win_rate">0%</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Total PnL</span>
                    <span class="metric-value" id="total_pnl">0.00</span>
                </div>
            </div>
            
            <div class="card">
                <h3>⚠️ Risk</h3>
                <div class="metric">
                    <span class="metric-label">Open Positions</span>
                    <span class="metric-value" id="open_positions">0</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Daily PnL</span>
                    <span class="metric-value" id="daily_pnl">0.00</span>
                </div>
            </div>
            
            <div class="card">
                <h3>🔍 Scan</h3>
                <div class="metric">
                    <span class="metric-label">Last Scan</span>
                    <span class="metric-value" id="last_scan">0ms</span>
                </div>
                <div class="metric">
                    <span class="metric-label">BUY Signals</span>
                    <span class="metric-value positive" id="buy_signals">0</span>
                </div>
                <div class="metric">
                    <span class="metric-label">SELL Signals</span>
                    <span class="metric-value negative" id="sell_signals">0</span>
                </div>
            </div>
        </div>
        
        <div class="card">
            <h3>📈 Open Positions</h3>
            <div class="positions-list" id="positions_list">
                <p style="color: #666;">No open positions</p>
            </div>
        </div>
        
        <div class="last-update">
            Last updated: <span id="last_update">-</span>
            <button class="refresh-button" onclick="refreshData()">Refresh Now</button>
        </div>
    </div>
    
    <script>
        async function refreshData() {
            try {
                const response = await fetch('/api/state');
                const data = await response.json();
                updateDashboard(data);
            } catch (error) {
                console.error('Error fetching data:', error);
            }
        }
        
        function updateDashboard(data) {
            // Status
            const statusEl = document.getElementById('status');
            statusEl.textContent = data.status.toUpperCase();
            statusEl.className = 'status ' + data.status;
            
            // Balance
            document.getElementById('balance').textContent = data.balance.toFixed(2);
            
            // Performance
            document.getElementById('total_trades').textContent = data.perf.total_trades || 0;
            document.getElementById('win_rate').textContent = (data.perf.win_rate || 0).toFixed(1) + '%';
            const totalPnL = document.getElementById('total_pnl');
            totalPnL.textContent = (data.perf.total_pnl || 0).toFixed(2);
            totalPnL.className = 'metric-value ' + (data.perf.total_pnl >= 0 ? 'positive' : 'negative');
            
            // Risk
            document.getElementById('open_positions').textContent = data.risk.open_positions || 0;
            const dailyPnL = document.getElementById('daily_pnl');
            dailyPnL.textContent = (data.risk.daily_pnl || 0).toFixed(2);
            dailyPnL.className = 'metric-value ' + (data.risk.daily_pnl >= 0 ? 'positive' : 'negative');
            
            // Scan
            document.getElementById('last_scan').textContent = (data.scan_stats.last_ms || 0).toFixed(0) + 'ms';
            document.getElementById('buy_signals').textContent = data.scan_stats.n_buy || 0;
            document.getElementById('sell_signals').textContent = data.scan_stats.n_sell || 0;
            
            // Positions
            const positionsEl = document.getElementById('positions_list');
            if (Object.keys(data.positions).length === 0) {
                positionsEl.innerHTML = '<p style="color: #666;">No open positions</p>';
            } else {
                positionsEl.innerHTML = Object.entries(data.positions).map(([sym, pos]) => `
                    <div class="position-item">
                        <strong>${sym}</strong><br>
                        Size: ${pos.positionAmt} | Mark Price: ${pos.markPrice}
                    </div>
                `).join('');
            }
            
            // Last update
            document.getElementById('last_update').textContent = new Date(data.last_update).toLocaleString();
        }
        
        // Auto-refresh every 5 seconds
        setInterval(refreshData, 5000);
        refreshData();
    </script>
</body>
</html>
    """
