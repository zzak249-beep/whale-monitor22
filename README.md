# Maki Bot — BingX Futures

Estrategia de @makitofx adaptada a crypto: ZigZag + 20MA en 4H.

| Parámetro | Valor |
|-----------|-------|
| Marco temporal | 15m (señal) + 4H (filtro) |
| Take Profit | +0.45% del precio de entrada |
| Stop Loss | -0.30% del precio de entrada |
| Pares | Top 20 por volumen en BingX, actualizado cada hora |
| Exchange | BingX Perpetual Futures |

## Deploy en Railway (5 pasos)

**1. Claves BingX**
- bingx.com → Perfil → Gestión de API → Nueva clave
- Permisos: Futuros (lectura + trading). **Sin permisos de retiro.**

**2. Bot de Telegram**
- Habla con `@BotFather` → `/newbot` → copia el token
- Habla con `@userinfobot` → copia tu Chat ID

**3. Sube a GitHub**
```bash
git init && git add . && git commit -m "maki-bot"
git remote add origin https://github.com/TU_USUARIO/maki-bot.git
git push -u origin main
```

**4. Nuevo proyecto en Railway**
- [railway.app](https://railway.app) → New Project → Deploy from GitHub repo

**5. Variables de entorno en Railway**

| Variable | Valor |
|----------|-------|
| `BINGX_API_KEY` | Tu API Key |
| `BINGX_API_SECRET` | Tu API Secret |
| `TELEGRAM_BOT_TOKEN` | Token de @BotFather |
| `TELEGRAM_CHAT_ID` | Tu Chat ID |
| `TRADE_AMOUNT_USDT` | `10` |
| `MAX_OPEN_TRADES` | `3` |
| `SCAN_INTERVAL_SECONDS` | `60` |
| `TOP_N_SYMBOLS` | `20` |

Deploy → el bot arranca en ~2 minutos y te manda un mensaje a Telegram.

## Archivos

```
src/
  bot.py        # Loop principal (~80 líneas)
  bingx.py      # Cliente API BingX
  strategy.py   # Lógica ZigZag + 20MA 4H
  telegram.py   # Envío de mensajes
```

## Aviso

Opera con dinero real. Empieza con `TRADE_AMOUNT_USDT=10` y revisa los logs en Railway → Logs antes de subir el monto.
