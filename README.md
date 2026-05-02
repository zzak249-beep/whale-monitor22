# Maki Bot v2 — BingX Futures

Estrategia de @makitofx adaptada a crypto: **ZigZag + 20MA en 4H + RSI como filtro**.

| Parámetro | Valor |
|-----------|-------|
| Marco temporal | 15m (señal) + 4H (filtro de tendencia) |
| Indicadores | Pivotes ZigZag · 20MA · RSI(14) |
| Take Profit | +0.45% del precio de entrada |
| Stop Loss | -0.30% del precio de entrada |
| Pares | Top 20 por volumen en BingX, actualizado cada hora |
| Exchange | BingX Perpetual Futures |
| Apalancamiento | 5x (configurable) |

---

## Mejoras v2

- **Retry automático** en todas las llamadas a la API (backoff exponencial)
- **Sesión HTTP reutilizada** — reduce latencia y conexiones abiertas
- **RSI como filtro**: no abre LONG si RSI > 65, no abre SHORT si RSI < 35
- **Sincronización con la API**: detecta si BingX cerró la posición por TP/SL sin depender sólo del estado interno
- **Cooldown por símbolo**: espera 5 min antes de reabrir el mismo par
- **Balance mínimo**: no opera si el balance cae por debajo de `MIN_BALANCE_USDT`
- **Heartbeat periódico**: resumen de estado a Telegram cada hora
- **Cola de mensajes Telegram**: evita rate-limiting al enviar muchas notificaciones seguidas
- **Shutdown limpio**: captura SIGTERM (Railway/Docker) y avisa antes de cerrar

---

## Deploy en Railway (5 pasos)

**1. Claves BingX**
- bingx.com → Perfil → Gestión de API → Nueva clave
- Permisos: Futuros (lectura + trading). **Sin permisos de retiro.**

**2. Bot de Telegram**
- Habla con `@BotFather` → `/newbot` → copia el token
- Habla con `@userinfobot` → copia tu Chat ID

**3. Sube a GitHub**
```bash
git init && git add . && git commit -m "maki-bot-v2"
git remote add origin https://github.com/TU_USUARIO/maki-bot.git
git push -u origin main
```

**4. Nuevo proyecto en Railway**
- [railway.app](https://railway.app) → New Project → Deploy from GitHub repo

**5. Variables de entorno en Railway**

| Variable | Valor por defecto | Descripción |
|----------|-------------------|-------------|
| `BINGX_API_KEY` | — | Tu API Key |
| `BINGX_API_SECRET` | — | Tu API Secret |
| `TELEGRAM_BOT_TOKEN` | — | Token de @BotFather |
| `TELEGRAM_CHAT_ID` | — | Tu Chat ID |
| `TRADE_AMOUNT_USDT` | `10` | USDT por trade |
| `MAX_OPEN_TRADES` | `3` | Posiciones simultáneas |
| `LEVERAGE` | `5` | Apalancamiento |
| `MIN_BALANCE_USDT` | `20` | Balance mínimo para operar |
| `SCAN_INTERVAL_SECONDS` | `60` | Frecuencia de escaneo |
| `TOP_N_SYMBOLS` | `20` | Pares a analizar |
| `COOLDOWN_SECONDS` | `300` | Pausa entre trades del mismo par |
| `HEARTBEAT_MINUTES` | `60` | Frecuencia del resumen Telegram |

Deploy → el bot arranca en ~2 minutos y te manda un mensaje a Telegram.

---

## Estructura

```
src/
  bot.py        # Loop principal, monitor, heartbeat
  bingx.py      # Cliente API BingX con retry
  strategy.py   # ZigZag + 20MA + RSI
  telegram.py   # Notificador con cola
```

## ⚠️ Aviso

Opera con dinero real. Empieza siempre con `TRADE_AMOUNT_USDT=10` y revisa los logs antes de subir el monto. El autor no se hace responsable de pérdidas.
