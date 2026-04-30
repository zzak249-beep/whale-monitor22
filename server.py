"""dashboard/server.py — FastAPI + WebSocket live dashboard."""
from __future__ import annotations
import asyncio
import json
import time
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import uvicorn
from loguru import logger

app = FastAPI(title="UltraBot Dashboard")

_state: dict[str, Any] = {
    "status": "starting",
    "balance": 0.0,
    "positions": {},
    "scan_stats": {},
    "risk": {},
    "perf": {},
    "last_signals": [],
    "trade_metrics": {},
    "updated_at": time.time(),
}
_clients: list[WebSocket] = []


def update_state(**kwargs: Any) -> None:
    _state.update(kwargs)
    _state["updated_at"] = time.time()


_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>⚡ UltraBot v3</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Space+Grotesk:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #070b10;
    --surface: #0d1520;
    --surface2: #131c2a;
    --border: #1e2d40;
    --accent: #00d4ff;
    --green: #00e676;
    --red: #ff3d57;
    --text: #e0eaf5;
    --muted: #5a7a9a;
    --warn: #ffb300;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Space Grotesk', sans-serif; font-size: 14px; }
  code, .mono { font-family: 'JetBrains Mono', monospace; }

  header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 14px 24px;
    display: flex;
    align-items: center;
    gap: 14px;
    position: sticky; top: 0; z-index: 10;
  }
  .logo { font-size: 20px; font-weight: 600; color: var(--accent); letter-spacing: -0.5px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1;box-shadow:0 0 0 0 rgba(0,230,118,.4)} 50%{opacity:.8;box-shadow:0 0 0 6px rgba(0,230,118,0)} }
  .badge { padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 600; letter-spacing: .05em; }
  .badge.running { background: rgba(0,212,255,.15); color: var(--accent); border: 1px solid rgba(0,212,255,.3); }
  .badge.halted  { background: rgba(255,61,87,.15);  color: var(--red);   border: 1px solid rgba(255,61,87,.3); }
  .ts { margin-left: auto; color: var(--muted); font-size: 12px; font-family: 'JetBrains Mono', monospace; }

  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px,1fr)); gap: 12px; padding: 20px 24px 0; }
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px;
    position: relative;
    overflow: hidden;
  }
  .card::after { content:''; position:absolute; top:0; left:0; right:0; height:2px; background: linear-gradient(90deg, var(--accent), transparent); opacity:.4; }
  .card .lbl { font-size: 10px; text-transform: uppercase; letter-spacing: .1em; color: var(--muted); }
  .card .val { font-size: 26px; font-weight: 600; margin-top: 6px; font-family: 'JetBrains Mono', monospace; }
  .card .sub { font-size: 11px; color: var(--muted); margin-top: 3px; }
  .green { color: var(--green) !important; }
  .red   { color: var(--red) !important; }
  .accent{ color: var(--accent) !important; }

  section { margin: 16px 24px; background: var(--surface); border: 1px solid var(--border); border-radius: 10px; }
  section h2 {
    padding: 12px 16px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: .1em;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
  }
  table { width: 100%; border-collapse: collapse; }
  th { padding: 10px 14px; font-size: 10px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); font-weight: 500; text-align: left; }
  td { padding: 10px 14px; border-top: 1px solid var(--border); font-size: 13px; }
  tr:hover td { background: var(--surface2); }
  .empty { text-align: center; color: var(--muted); padding: 24px; font-size: 13px; }

  .spark { display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-right: 4px; }
  .spark.buy  { background: var(--green); box-shadow: 0 0 4px var(--green); }
  .spark.sell { background: var(--red);   box-shadow: 0 0 4px var(--red); }
</style>
</head>
<body>
<header>
  <div class="dot" id="status-dot"></div>
  <div class="logo">⚡ UltraBot v3</div>
  <span id="badge" class="badge running">RUNNING</span>
  <span class="ts" id="updated"></span>
</header>

<div class="grid" id="metrics"></div>

<section>
  <h2>Open Positions</h2>
  <table><thead>
    <tr><th>Symbol</th><th>Side</th><th>Entry</th><th>Mark</th><th>PnL</th><th>Conf%</th><th>ADX</th></tr>
  </thead><tbody id="positions-body"></tbody></table>
</section>

<section>
  <h2>Latest Signals</h2>
  <table><thead>
    <tr><th>Symbol</th><th>Signal</th><th>Conf%</th><th>ADX</th><th>RSI</th><th>ATR%</th><th>Vol</th></tr>
  </thead><tbody id="signals-body"></tbody></table>
</section>

<script>
const ws = new WebSocket((location.protocol==='https:'?'wss':'ws') + '://' + location.host + '/ws');
ws.onmessage = e => render(JSON.parse(e.data));
ws.onclose = () => {
  document.getElementById('status-dot').style.background = 'var(--red)';
  setTimeout(() => location.reload(), 5000);
};

function pC(v) { return parseFloat(v) >= 0 ? 'green' : 'red'; }
function fmt(v, d=4) { return parseFloat(v||0).toFixed(d); }

function render(d) {
  const r = d.risk || {}, s = d.scan_stats || {}, p = d.perf || {};
  document.getElementById('updated').textContent = new Date(d.updated_at*1000).toLocaleTimeString();

  const badge = document.getElementById('badge');
  badge.textContent = r.halted ? 'HALTED' : 'RUNNING';
  badge.className   = 'badge ' + (r.halted ? 'halted' : 'running');

  const metrics = [
    { lbl:'Balance',      val:'$'+(d.balance||0).toFixed(2), sub:'USDT available' },
    { lbl:'Day PnL',      val:(r.daily_pnl_usdt>=0?'+':'')+((r.daily_pnl_usdt)||0).toFixed(2), cls:pC(r.daily_pnl_usdt), sub:'USDT today' },
    { lbl:'Total PnL',    val:(r.total_pnl>=0?'+':'')+(r.total_pnl||0).toFixed(2), cls:pC(r.total_pnl), sub:'all time' },
    { lbl:'Win Rate',     val:(r.win_rate||0)+'%', sub:(r.wins||0)+'W / '+(r.losses||0)+'L' },
    { lbl:'Open Trades',  val:Object.keys(d.positions||{}).length, sub:'of '+(r.max_open||3)+' max' },
    { lbl:'Scan Speed',   val:(s.last_ms||0).toFixed(0)+'ms', sub:(s.n_scanned||0)+' symbols' },
    { lbl:'Signals',      val:'↑'+(s.n_buy||0)+' ↓'+(s.n_sell||0), sub:'this scan', cls:'accent' },
    { lbl:'Trades',       val:p.total_trades||0, sub:'avg '+(p.avg_duration_m||0).toFixed(0)+'m hold' },
  ];

  document.getElementById('metrics').innerHTML = metrics.map(m =>
    `<div class="card"><div class="lbl">${m.lbl}</div>
     <div class="val mono ${m.cls||''}">${m.val}</div>
     <div class="sub">${m.sub||''}</div></div>`
  ).join('');

  const positions = d.positions || {};
  const posRows = Object.entries(positions).map(([sym, pos]) => {
    const pnl  = parseFloat(pos.unrealizedProfit||0);
    const amt  = parseFloat(pos.positionAmt||0);
    const side = amt > 0 ? 'LONG' : 'SHORT';
    const m    = (d.trade_metrics||{})[sym] || {};
    return `<tr>
      <td class="mono">${sym}</td>
      <td><span class="spark ${amt>0?'buy':'sell'}"></span>${side}</td>
      <td class="mono">${fmt(pos.entryPrice,4)}</td>
      <td class="mono">${fmt(pos.markPrice||pos.entryPrice,4)}</td>
      <td class="mono ${pC(pnl)}">${pnl>=0?'+':''}${pnl.toFixed(2)}</td>
      <td>${(m.confidence||0).toFixed(0)}%</td>
      <td>${(m.adx||0).toFixed(1)}</td>
    </tr>`;
  }).join('') || `<tr><td colspan="7" class="empty">No open positions</td></tr>`;
  document.getElementById('positions-body').innerHTML = posRows;

  const sigRows = (d.last_signals||[]).slice(0,15).map(s =>
    `<tr>
      <td class="mono">${s.symbol}</td>
      <td><span class="spark ${s.signal==='BUY'?'buy':'sell'}"></span><span class="${s.signal==='BUY'?'green':'red'}">${s.signal}</span></td>
      <td>${(s.confidence||0).toFixed(0)}%</td>
      <td>${(s.adx||0).toFixed(1)}</td>
      <td>${(s.rsi||0).toFixed(1)}</td>
      <td>${(s.atr_pct||0).toFixed(2)}%</td>
      <td>${s.vol_spike?'<span class="accent">SPIKE</span>':'-'}</td>
    </tr>`
  ).join('') || `<tr><td colspan="7" class="empty">No signals yet</td></tr>`;
  document.getElementById('signals-body').innerHTML = sigRows;
}
</script>
</body>
</html>"""


@app.get("/")
async def index() -> HTMLResponse:
    return HTMLResponse(_HTML)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "updated_at": _state["updated_at"]}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    _clients.append(ws)
    try:
        await ws.send_text(json.dumps(_state, default=str))
        while True:
            await asyncio.sleep(2)
            await ws.send_text(json.dumps(_state, default=str))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if ws in _clients:
            _clients.remove(ws)


async def start_dashboard() -> None:
    from core.config import cfg
    port = cfg.effective_port
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    asyncio.create_task(server.serve())
    logger.info(f"Dashboard → http://0.0.0.0:{port}")
