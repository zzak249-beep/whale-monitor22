# CryptoBot v3 — EMA10 × HTF × BOS × FVG × CVD

Bot de trading automático para BingX Futures.

## Estructura
```
main.py              ← Orquestador principal
config.py            ← Configuración (lee .env)
exchange/            ← Cliente BingX REST + WebSocket
strategy/            ← Indicadores: HTF, EMA10, Structure, Volume
risk/                ← Risk manager + Position monitor
notifications/       ← Telegram
utils/               ← Logger
```

## Variables de entorno en Railway
```
BINGX_API_KEY=...
BINGX_SECRET_KEY=...
TELEGRAM_TOKEN=...
TELEGRAM_CHAT_ID=...
SYMBOLS=BTC-USDT,ETH-USDT
DRY_RUN=true
LEVERAGE=10
RISK_PER_TRADE=1.0
```
