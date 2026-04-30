# ⚡ Inicio Rápido - UltraBot v3

## 🎯 3 pasos para subir a GitHub

### 1️⃣ Descarga todos los archivos

Descarga la carpeta `whale-monitor22` desde aquí. Contiene:
```
✅ 22 archivos Python
✅ Documentación completa
✅ Instrucciones GitHub
✅ Variables de ejemplo
```

### 2️⃣ Crea repositorio en GitHub

- Ve a https://github.com/new
- Nombre: `whale-monitor22`
- Crea el repo (vacío)

### 3️⃣ Sube los archivos

Abre terminal en `whale-monitor22`:

```bash
git init
git add .
git commit -m "Initial commit: UltraBot v3"
git branch -M main
git remote add origin https://github.com/TU-USUARIO/whale-monitor22.git
git push -u origin main
```

---

## 🚂 Luego en Railway

1. Ve a https://railway.app
2. New Project → GitHub repo → whale-monitor22
3. Agrega variables de entorno:
   - `EXCHANGE_KEY` y `EXCHANGE_SECRET` (Binance)
   - `TELEGRAM_TOKEN` y `TELEGRAM_CHAT_ID`
4. Railway despliega automáticamente ✅

---

## 📂 Archivos principales

| Archivo | Descripción |
|---------|-------------|
| `bot.py` | 🤖 Punto de entrada principal |
| `core/config.py` | ⚙️ Configuración centralizada |
| `core/database.py` | 💾 Base de datos SQLite |
| `core/risk.py` | 📊 Gestión de riesgo |
| `exchange/client.py` | 🔌 API Binance |
| `strategies/indicators.py` | 📈 Indicadores técnicos |
| `notifications/telegram.py` | 💬 Alertas Telegram |
| `dashboard/server.py` | 🌐 Web UI |
| `requirements.txt` | 📦 Dependencias |
| `.env.example` | 🔐 Variables de ejemplo |

---

## 🔧 Variables que necesitas

### Binance API
1. Ve a https://www.binance.com/en/account/api-management
2. Crea API Key con permisos Futures Trading
3. Copia Key y Secret

### Telegram
1. Habla con @BotFather en Telegram
2. Crea nuevo bot
3. Copia el token
4. Envía `/start` al bot y copia el chat ID

---

## 📚 Documentos incluidos

1. **README.md** - Documentación completa
2. **GITHUB_SETUP.md** - Paso a paso GitHub
3. **CHECKLIST.md** - Verificación pre-upload
4. **GIT_COMMANDS.sh** - Comandos Git listos
5. **INICIO_RAPIDO.md** - Este archivo

---

## 🚀 Después del deploy

Accede al dashboard:
- **Railway URL**: Tu bot aparecerá en el dashboard
- **Dashboard web**: http://tu-railway-url:8000
- **Telegram**: Recibirás alertas en tiempo real

---

## ⚠️ Importante

- ❌ NO subas `.env` (está en `.gitignore`)
- ✅ Usa `.env.example` como referencia
- 🔐 Nunca compartas API keys
- 💾 Railway guarda logs automáticamente

---

**¿Preguntas?** Lee `GITHUB_SETUP.md` para instrucciones detalladas.
