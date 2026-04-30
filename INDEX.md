# 📑 Índice de Archivos - UltraBot v3

## 🎯 Empezar aquí

1. **INICIO_RAPIDO.md** ⚡ - 3 pasos para subir a GitHub
2. **GITHUB_SETUP.md** 📖 - Guía detallada paso a paso
3. **CHECKLIST.md** ✅ - Verificación antes de subir

---

## 🤖 Código principal

### `bot.py` (394 líneas)
**Punto de entrada del bot**
- Main loop (escanea cada 5 segundos)
- Monitor de posiciones abiertas
- Ejecución de órdenes
- Dashboard updates
- Manejo de señales de graceful shutdown

```bash
python bot.py  # Ejecuta el bot
```

---

## 📁 Módulo `core/`

### `core/__init__.py`
Identifica la carpeta como paquete Python

### `core/config.py` (52 líneas)
**Gestión centralizada de configuración**
- Lee variables de `.env`
- Config class con todos los parámetros
- Exchange, trading, risk, signals, telegram
- Global `cfg` instance

```python
from core.config import cfg
print(cfg.leverage)  # 10
```

### `core/database.py` (201 líneas)
**Persistencia en SQLite**
- Inicialización de tablas (trades, signals, daily_stats)
- Guardar trades abiertos y cerrados
- Registrar señales
- Estadísticas de performance
- 4 tablas con índices

```python
await init_db()
trade_id = await save_trade_open(...)
```

### `core/risk.py` (180 líneas)
**Gestión inteligente de riesgo**
- RiskManager class
- Position sizing dinámico (según confidence y ATR)
- SL/TP calculation (1:3 risk/reward)
- Daily loss tracking
- Halt logic si se excede límite diario

```python
size = risk.position_size(balance, n_open, confidence)
sl, tp, sl_pct, tp_pct = risk.dynamic_sl_tp(price, side, atr)
```

---

## 🔌 Módulo `exchange/`

### `exchange/__init__.py`
Identifica la carpeta como paquete Python

### `exchange/client.py` (316 líneas)
**Cliente Binance Futures API**
- BinanceClient class con métodos HTTP
- Firma de requests (HMAC-SHA256)
- Klines, account, positions, orders
- Market orders con SL/TP automático
- Cancelación de órdenes
- Closes position (venta/cobertura)

```python
client = BinanceClient(key, secret)
await client.set_leverage(symbol, 10)
await client.place_market_order("BTCUSDT", "BUY", 100)
```

Funciones high-level:
- `fetch_all_tickers()` - Top 50 symbols por volumen
- `fetch_universe_concurrent()` - OHLCV multi-timeframe (1h, 4h, 1d)
- `get_balance()` - Balance total
- `get_all_positions()` - Posiciones abiertas

---

## 📈 Módulo `strategies/`

### `strategies/__init__.py`
Identifica la carpeta como paquete Python

### `strategies/indicators.py` (289 líneas)
**Indicadores técnicos y generación de señales**
- `calculate_atr()` - Average True Range (volatilidad)
- `calculate_rsi()` - Relative Strength Index (momentum)
- `calculate_adx()` - Average Directional Index (trend strength)
- `calculate_volume_delta()` - Volume analysis
- `generate_signal()` - Multi-timeframe signal

Logic:
```
RSI < 30 (oversold) → BUY 🟢
RSI > 70 (overbought) → SELL 🔴
Volume confirmation → +10% confidence
Result: Confidence 0-100%
```

```python
sig, metrics = generate_signal(high, low, close, open, volume, cfg=cfg)
# Returns: ("BUY", {"confidence": 75, "rsi": 28, "adx": 45, ...})
```

---

## 💬 Módulo `notifications/`

### `notifications/__init__.py`
Identifica la carpeta como paquete Python

### `notifications/telegram.py` (263 líneas)
**Sistema de alertas por Telegram**
- TelegramSender class con queue async
- HTTP requests a API de Telegram
- Message formatters (entry, close, performance, halt, error)
- Global `send()` y `send_now()` functions

Mensajes:
```
🟢 BUY entry with SL/TP
❌ SELL entry with SL/TP
✅ TP hit (trade cerrado)
📊 6-hour performance report
🛑 Bot halted
❌ Error alerts
```

```python
await send(msg_entry(symbol, side, price, size, sl, tp))
```

---

## 🌐 Módulo `dashboard/`

### `dashboard/__init__.py`
Identifica la carpeta como paquete Python

### `dashboard/server.py` (390 líneas)
**Web UI en tiempo real**
- aiohttp web server (puerto 8000)
- DashboardState class
- API endpoint `/api/state` (JSON)
- HTML con auto-refresh cada 5 segundos

Métricas mostradas:
- Balance total
- Performance (wins, losses, win rate, PnL)
- Risk (open positions, daily PnL)
- Scan stats (último tiempo de scan, signals)
- Open positions list

```bash
# Acceso
http://localhost:8000/
```

---

## 📦 Archivos de Configuración

### `requirements.txt` (8 líneas)
Dependencias Python:
```
loguru==0.7.2          # Logging
aiohttp==3.9.1         # HTTP client
asyncio-contextmanager # Async context
python-telegram-bot    # Telegram API
aiosqlite==3.1.0       # SQLite async
numpy==1.24.3          # Números
pandas==2.1.3          # Data
ta-lib==0.4.28         # Indicators (opcional)
python-dotenv==1.0.0   # .env support
```

### `.env.example` (29 líneas)
Template de variables de entorno:
```env
EXCHANGE_KEY=
EXCHANGE_SECRET=
TELEGRAM_TOKEN=
TELEGRAM_CHAT_ID=
LEVERAGE=10
MAX_OPEN_TRADES=5
# ... más variables
```

**Instrucciones:**
```bash
cp .env.example .env
# Edita .env con tus valores reales
```

### `Procfile` (1 línea)
Especifica cómo ejecutar en Railway:
```
worker: python bot.py
```

### `.gitignore` (30 líneas)
Archivos que NO se suben a GitHub:
```
.env              # Variables privadas
data/             # Base de datos
__pycache__/      # Python cache
.vscode/          # IDE settings
.pytest_cache/    # Tests
*.db              # SQLite files
logs/             # Log files
```

---

## 📚 Documentación

### `README.md` (180 líneas)
Documentación principal:
- Features y características
- Installation instructions
- Configuration
- Deployment en Railway
- Architecture overview
- Cómo funciona (main loop, signal generation)
- Risk management
- Troubleshooting

### `INICIO_RAPIDO.md` (Este archivo)
Instrucciones rápidas para empezar (3 pasos)

### `GITHUB_SETUP.md` (Paso a paso)
Guía detallada para subir a GitHub + Railway

### `CHECKLIST.md` (Verificación)
Checklist antes de git push

### `GIT_COMMANDS.sh` (Comandos listos)
Comandos Git para copiar/pegar

### `INDEX.md` (Este índice)
Descripción de cada archivo

---

## 📊 Estadísticas del Proyecto

```
Total de archivos: 22
Líneas de código Python: ~2000
Archivos de documentación: 6
Módulos: 6 (core, exchange, strategies, notifications, dashboard, main)
Tablas base de datos: 3
Indicadores técnicos: 4
```

---

## 🎯 Flujo de Ejecución

```
bot.py main()
  ↓
get_universe() → Top 50 symbols por volumen
  ↓
scan_symbols() → Fetch OHLCV (1h, 4h, 1d)
  ↓
generate_signal() → ADX + RSI + Volume analysis
  ↓
execute_signal() → Position sizing (risk management)
  ↓
place_market_order() → Binance API
  ↓
monitor_positions() → Check SL/TP hit
  ↓
send() → Telegram alerts
  ↓
update_state() → Dashboard update
  ↓
[Repeat every 5 seconds]
```

---

## 🔐 Seguridad

✅ Variables de entorno con dotenv
✅ API keys en `.env` (no en código)
✅ HMAC signatures para Binance API
✅ .gitignore excluye archivos sensibles
✅ No hay hardcoding de secrets

---

## 🚀 Deploy

### Local
```bash
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

### Railway
1. Push a GitHub
2. Connect con Railway
3. Add variables
4. Auto-deploy en push

---

## 📞 Soporte

- Logs: `data/bot.log`
- Debug mode: Cambiar logger level en bot.py
- Database: `data/trades.db` (SQLite)

---

**Última actualización:** May 1, 2026
**Versión:** UltraBot v3
