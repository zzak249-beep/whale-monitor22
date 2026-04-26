# 🤖 CryptoBot — EMA10 × 15m × 8

Bot de trading algorítmico para **BingX Perpetual Futures**.
Deploy en Railway. Notificaciones por Telegram. 28 tests unitarios.

---

## 🧠 Estrategia (4 filtros en cascada)

```
┌─────────────────────────────────────────────────────┐
│  1. HTF BIAS (1H)    EMA50 > EMA200 → BULLISH        │
│         ↓                                            │
│  2. EMA10 CRUCE      Vela cierra SOBRE EMA10         │  ← ENTRADA 1
│         ↓         o  Retest EMA (Doji/Hammer/Pin)   │  ← ENTRADA 2
│         ↓                                            │
│  3. BOS (15m)        Ruptura de swing high/low       │
│         ↓                                            │
│  4. CVD + VOLUMEN    Presión compradora > 1.3× media │
│         ↓                                            │
│   SCORE ≥ 7/10  →  EJECUTAR ORDEN                   │
└─────────────────────────────────────────────────────┘
```

### Las dos entradas del método

| Entrada | Condición | Umbral |
|---------|-----------|--------|
| **① CRUCE** | `close[-2] < EMA10` Y `close[-1] > EMA10` — vela CERRADA cruza de abajo hacia arriba | Score ≥ 7 |
| **② RETEST** | Precio sobre EMA10 → toca EMA con mecha → patrón válido (Doji, Hammer, Pin bar, Engulfing) → entrada en la siguiente vela | Score ≥ 6 (+1 bonus) |

**Ventana retest**: máximo 8 velas tras el cruce inicial.

**SL**: bajo el último mínimo swing • **TP**: último máximo swing

### Sistema de scoring (0–10)

| Filtro | Peso | Condición LONG |
|--------|------|----------------|
| HTF Bias | 3 pts | EMA50 > EMA200 confirmado 2+ velas |
| EMA10 Cross | 3 pts | Calidad cruce ≥ 60% |
| BOS | 2 pts | Ruptura swing high |
| CVD/Volumen | 2 pts | Buying + vol > 1.3× media |
| Retest bonus | +1 pt | Automático en Entrada 2 |

- Score 7-8 → tamaño normal, RR 2.5×
- Score 9-10 → tamaño +50%, RR 3.0×

### Ventaja matemática

```
Breakeven winrate = 1 / (1 + RR) = 1 / 3.5 = 28.6%
Objetivo real con filtros: 45–55% WR → edge consistente
Profit Factor objetivo: > 1.8
```

---

## 📁 Estructura del proyecto

```
cryptobot/
├── main.py                          # Orquestador principal
├── config.py                        # Configuración central
├── backtest.py                      # Backtester walk-forward
├── requirements.txt
├── Procfile                         # Para Railway
├── railway.toml
├── pyproject.toml                   # Config pytest
├── .env.example
├── .gitignore
│
├── .github/
│   └── workflows/ci.yml             # GitHub Actions CI/CD
│
├── exchange/
│   └── bingx_client.py             # REST firmado + WebSocket BingX
│
├── strategy/
│   ├── htf_bias.py                 # Sesgo HTF — EMA50/200 en 1H
│   ├── ema10_cross.py              # Entrada 1 (cruce) + Entrada 2 (retest)
│   ├── structure.py                # Break of Structure swing H/L
│   ├── volume_cvd.py               # CVD + filtro de volumen
│   └── signals.py                  # Agregador scoring 0–10
│
├── risk/
│   ├── manager.py                  # Sizing, límites diarios, anti-duplicado
│   └── monitor.py                  # Monitor posiciones + trailing SL
│
├── notifications/
│   └── telegram_notifier.py        # Alertas Telegram con cola asíncrona
│
├── utils/
│   └── logger.py                   # Logger con colores
│
├── tests/
│   └── test_strategy.py            # 28 tests unitarios (pytest)
│
└── logs/
    └── bot.log
```

---

## 🚀 Setup local

```bash
# 1. Clonar
git clone https://github.com/TU_USUARIO/cryptobot.git
cd cryptobot

# 2. Entorno virtual
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# 3. Dependencias
pip install -r requirements.txt
pip install pytest pytest-asyncio   # Solo para tests

# 4. Configurar
cp .env.example .env
nano .env                     # Añade tus keys

# 5. Tests (primero verifica que todo funciona)
python -m pytest tests/ -v

# 6. Ejecutar en PAPER (DRY_RUN=true)
python main.py
```

---

## ☁️ Deploy en Railway

### Paso a paso

1. Sube el código a **GitHub** (repositorio privado recomendado)
2. Ve a [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub Repo**
3. Selecciona tu repositorio
4. Ve a **Variables** y añade todas las de abajo
5. Railway detecta el `Procfile` → deploy automático

### Variables de entorno en Railway

```
BINGX_API_KEY       = tu_api_key
BINGX_SECRET_KEY    = tu_secret_key
TELEGRAM_TOKEN      = 123456:ABCdef...
TELEGRAM_CHAT_ID    = -100123456789
SYMBOLS             = BTC-USDT,ETH-USDT
DRY_RUN             = true          ← empieza SIEMPRE aquí
RISK_PER_TRADE      = 1.0
MAX_DAILY_LOSS      = 5.0
MAX_OPEN_TRADES     = 2
LEVERAGE            = 10
RISK_REWARD         = 2.5
TRADE_HOURS_UTC     = 7-23
LOG_LEVEL           = INFO
POLL_INTERVAL       = 15
```

---

## 🔑 Obtener API Keys de BingX

1. [bingx.com](https://bingx.com) → Perfil → **API Management**
2. Crear API Key → permisos: **Read + Trade** (NO retirada)
3. Whitelist IP de Railway (en Settings → Networking → Public Networking)

---

## 📱 Configurar Telegram

```
1. Habla con @BotFather en Telegram
2. /newbot → ponle nombre → copia el TOKEN
3. Habla con @userinfobot → copia tu CHAT_ID
4. Añade ambos en las variables de Railway
```

---

## 🧪 Backtest

```bash
# Descarga velas reales de BingX y simula la estrategia
python backtest.py --symbol BTC-USDT --days 90 --capital 10000

# Resultado:
# ═══════════════════════════════════════════════════════
#   BACKTEST — BTC-USDT | EMA10×15m×8
# ═══════════════════════════════════════════════════════
#   Capital inicial :  10,000.00 USDT
#   Capital final   :  12,340.00 USDT
#   PnL total       :  +2,340.00 USDT  (+23.4%)
#   Trades totales  :   47
#   Winrate         :    51.1%
#   Profit factor   :    1.94x
#   Entrada CRUCE   :   31 trades | WR=48.4%
#   Entrada RETEST  :   16 trades | WR=56.3%
```

---

## 📊 Mensajes Telegram

| Evento | Emoji |
|--------|-------|
| Bot iniciado | 🚀 / 🧪 |
| Señal detectada | 🟢 LONG / 🔴 SHORT + score |
| Orden ejecutada | 📈 / 📉 + entry/SL/TP |
| Trade cerrado | ✅ GANADA / ❌ PERDIDA + PnL |
| Reporte diario | 📈 23:55 UTC |
| Error | ⚠️ |

---

## 🛡️ Gestión de riesgo

| Control | Valor default | Descripción |
|---------|--------------|-------------|
| `RISK_PER_TRADE` | 1% | Capital arriesgado por operación |
| `MAX_DAILY_LOSS` | 5% | Pausa automática al superar |
| `MAX_OPEN_TRADES` | 2 | Máximo simultáneos |
| `LEVERAGE` | 10× | Apalancamiento BingX |
| Trailing SL | 0.4% ganancia | Activa trailing automático |
| Cooldown | 3 velas | Evita señales consecutivas |

---

## ⚠️ Importante antes de activar LIVE

1. **`DRY_RUN=true` durante mínimo 2 semanas** — observa las señales
2. **Ejecuta el backtest** con tus datos reales
3. **Empieza con capital pequeño** — el bot usa apalancamiento
4. **Monitorea los logs** diariamente al principio
5. **Ningún bot garantiza ganancias** — el método tiene ventaja matemática, pero el mercado puede hacer cualquier cosa

---

## 🧬 Tests

```bash
python -m pytest tests/ -v
# 28 passed ✅
```

Cubre: HTF Bias, EMA10 cruce, clasificador de velas, BOS, CVD,
Risk Manager (sizing, límites, cooldown), Signal Aggregator (todos los casos), integración completa.
