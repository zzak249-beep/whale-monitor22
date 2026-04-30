# UltraBot v3 - Automated Crypto Trading Bot

Advanced algorithmic trading bot for Binance futures with multi-timeframe technical analysis, risk management, and Telegram alerts.

## Features

✅ **Multi-Timeframe Analysis**
- 1-hour (primary), 4-hour, and 1-day timeframes
- ADX + RSI + Volume Delta indicators
- Confidence-based signal filtering

✅ **Risk Management**
- Dynamic position sizing
- Trailing stop-loss
- Daily loss limits
- Max open trades limit

✅ **Trading**
- One-way mode compatible (no ReduceOnly conflicts)
- Market orders with automatic SL/TP
- Real-time position monitoring
- Graceful shutdown

✅ **Notifications**
- Telegram alerts for trades
- Performance reports (6-hourly)
- Error notifications

✅ **Dashboard**
- Real-time web UI
- Performance metrics
- Open positions tracking
- Signal monitoring

## Installation

### 1. Clone the repository

```bash
git clone <repo-url>
cd whale-monitor22
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
# Edit .env with your API keys and preferences
```

### 4. Run locally

```bash
python bot.py
```

## Configuration

Edit `.env` with your settings:

```env
# Binance API
EXCHANGE_KEY=your_api_key
EXCHANGE_SECRET=your_api_secret

# Trading
LEVERAGE=10
MAX_OPEN_TRADES=5
SCAN_INTERVAL=5

# Risk
MAX_RISK_PER_TRADE=1.0
MAX_DAILY_LOSS=500

# Telegram
TELEGRAM_TOKEN=bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

## Deployment on Railway

### 1. Push to GitHub

```bash
git add .
git commit -m "Initial commit"
git push origin main
```

### 2. Connect Railway to GitHub

1. Go to [railway.app](https://railway.app)
2. Create new project
3. Connect your GitHub repository
4. Select the branch to deploy

### 3. Add Environment Variables

In Railway dashboard:
1. Go to Variables
2. Add all variables from `.env.example`
3. Set production values

### 4. Deploy

Railway auto-deploys on push to main branch

## Architecture

```
whale-monitor22/
├── bot.py                 # Main entry point
├── core/
│   ├── config.py         # Configuration management
│   ├── database.py       # SQLite operations
│   └── risk.py           # Risk management
├── exchange/
│   └── client.py         # Binance API client
├── strategies/
│   └── indicators.py     # Technical indicators
├── notifications/
│   └── telegram.py       # Telegram alerts
└── dashboard/
    └── server.py         # Web dashboard
```

## How It Works

### Main Loop (5-second cycle)

1. **Scan** - Fetch OHLCV for top 50 symbols
2. **Generate Signals** - Run multi-timeframe analysis
3. **Execute** - Place up to 3 new trades per cycle
4. **Monitor** - Check SL/TP on open positions
5. **Dashboard** - Update real-time metrics

### Signal Generation

- ADX > 25 = Trend strength ✅
- RSI < 30 = Oversold (BUY) 🟢
- RSI > 70 = Overbought (SELL) 🔴
- Volume delta confirmation
- Confidence scoring (0-100%)

### Risk Management

- Position size = (Balance × Risk%) / Max Trades
- Adjusted by confidence and volatility (ATR)
- SL = 2× ATR or 2% (whichever larger)
- TP = 3× SL (1:3 risk/reward)
- Trailing SL moves with price

## Monitoring

### Telegram Alerts

- 🟢 BUY entry with SL/TP
- ❌ SELL entry with SL/TP
- ✅ TP/SL hit (close reason)
- 📊 6-hour performance report
- ⚠️ Daily loss limit hit
- 🛑 Bot halted

### Web Dashboard

- Balance and positions
- Win rate and PnL
- Open trades monitoring
- Signal history
- Real-time updates

**Access**: `http://localhost:8000` (or Railway domain)

## Troubleshooting

### ImportError: No module named 'core'

Ensure all `__init__.py` files exist:
```bash
touch core/__init__.py
touch exchange/__init__.py
touch strategies/__init__.py
touch notifications/__init__.py
touch dashboard/__init__.py
```

### Telegram not sending messages

1. Check `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`
2. Verify bot has permission to post in chat
3. Test manually: `curl "https://api.telegram.org/botYOUR_TOKEN/sendMessage?chat_id=YOUR_ID&text=test"`

### Low signal frequency

1. Check `MIN_CONFIDENCE` setting (default 65%)
2. Lower threshold to catch more signals
3. Verify symbol volume meets `MIN_VOLUME_USDT`

## API Keys Setup

### Binance

1. Create API key at https://www.binance.com/en/account/api-management
2. Enable Futures Trading permission
3. Do **NOT** enable withdrawal
4. Copy Key and Secret to `.env`

### Telegram

1. Create bot with @BotFather
2. Copy bot token to `TELEGRAM_TOKEN`
3. Send message to bot, get chat ID from logs or use `/start`
4. Copy chat ID to `TELEGRAM_CHAT_ID`

## Performance Metrics

After first trades, check performance:

```bash
sqlite3 data/trades.db
SELECT * FROM trades ORDER BY closed_at DESC LIMIT 10;
SELECT * FROM daily_stats;
```

## Development

Run with debug logging:

```python
# In bot.py, change logger level
logger.add(sys.stdout, level="DEBUG")
```

Database schema available in `core/database.py`

## License

Proprietary - All rights reserved

## Support

For issues or questions, refer to logs:

```bash
tail -f data/bot.log
```

---

**⚠️ WARNING**: This is an automated trading bot. Start with small position sizes and monitor carefully. Past performance does not guarantee future results. Trade at your own risk.
