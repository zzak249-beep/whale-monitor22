# ⚡ UltraBot v3 — BingX Perpetual Futures

Multi-timeframe trading bot: ADX + RSI + 3-Step Volume Delta on 15m/1h/4h.

## CRITICAL: Before deploying

### 1. Switch BingX to One-Way Mode
BingX Futures → Settings → Position Mode → **One-way Mode**
(Fixes error 109400 — Hedge mode conflicts with order parameters)

### 2. API Key Permissions
Enable: **Perpetual Futures Trading** (read + trade)
Disable: withdrawals

---

## Railway Deployment

1. Push this repo to GitHub
2. New Railway project → Deploy from GitHub
3. Add variables from `.env.example` in Railway → Variables tab
4. Railway injects `PORT` automatically — dashboard available at your Railway URL

## Project Structure

```
ultrabot/
├── bot.py                  ← main entrypoint
├── core/
│   ├── config.py           ← all env var config
│   ├── database.py         ← SQLite trade log
│   └── risk.py             ← risk engine
├── exchange/
│   └── client.py           ← BingX API (one-way mode fixed)
├── strategies/
│   └── indicators.py       ← ADX/RSI/ATR/Volume Delta
├── notifications/
│   └── telegram.py         ← Telegram alerts
├── dashboard/
│   └── server.py           ← FastAPI WebSocket dashboard
├── data/                   ← SQLite DB + logs (auto-created)
├── requirements.txt
├── Dockerfile
└── railway.toml
```

## Strategy Logic

1. **Universe**: Top 50 symbols by 24h volume on BingX Perps
2. **Signal**: ADX ≥ 25 + 2/3 Volume Delta steps aligned + RSI not overbought/oversold
3. **HTF Filter**: 1h must not be in opposite trend (±20%)
4. **Trend Filter**: 4h must not be strongly opposed (±50%)
5. **Confidence Gate**: score ≥ 52 (ADX excess + vol spike bonus)
6. **Sizing**: dynamic — confidence-scaled, slot-reduced, volatility-adjusted
7. **SL/TP**: ATR-based dynamic (default 2% SL / 4% TP, ratio 1:2)
8. **Trailing SL**: moves up with price to protect profits

## Risk Parameters (recommended)

| Param | Value | Reason |
|-------|-------|--------|
| LEVERAGE | 5x | Safe for volatile alts |
| SL_PCT | 2.0% | ATR-adaptive minimum |
| TP_PCT | 4.0% | 1:2 risk/reward |
| MAX_OPEN_TRADES | 3 | Diversification |
| DAILY_LOSS_LIMIT | 4.0% | Hard stop per day |
| MAX_DRAWDOWN_PCT | 8.0% | Bot halts at this |
