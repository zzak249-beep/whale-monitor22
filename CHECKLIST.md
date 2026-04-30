# ✅ Checklist Pre-GitHub

## Antes de subir, verifica esto:

### 📁 Estructura de carpetas

- [x] `core/__init__.py`
- [x] `core/config.py`
- [x] `core/database.py`
- [x] `core/risk.py`
- [x] `exchange/__init__.py`
- [x] `exchange/client.py`
- [x] `strategies/__init__.py`
- [x] `strategies/indicators.py`
- [x] `notifications/__init__.py`
- [x] `notifications/telegram.py`
- [x] `dashboard/__init__.py`
- [x] `dashboard/server.py`
- [x] `bot.py`
- [x] `requirements.txt`
- [x] `.env.example`
- [x] `.gitignore`
- [x] `Procfile`
- [x] `README.md`

### 🔑 Archivos importantes

- [ ] `.env` **NO** está en git (debe estar en `.gitignore`)
- [ ] `requirements.txt` tiene todas las dependencias
- [ ] `Procfile` especifica cómo ejecutar el bot
- [ ] `.env.example` tiene estructura correcta
- [ ] `bot.py` importa correctamente

### 🧪 Prueba local

```bash
# 1. Instalar dependencias
pip install -r requirements.txt

# 2. Crear .env
cp .env.example .env
# Edita con valores de prueba

# 3. Prueba los imports (opcional)
python -c "from core.config import cfg; print('✅ Imports OK')"
```

### 📤 Antes de git push

```bash
# Verifica que .env NO esté en la siguiente lista
git status

# Debería mostrar "nothing to commit" si .env está ignorado
# Si .env aparece, debes agregar a .gitignore:
echo ".env" >> .gitignore
git add .gitignore
git commit -m "Update gitignore"
```

### 🚀 Railway Configuration

Antes de conectar con Railway, ten listo:

- [ ] Binance API key (no habilites withdrawal)
- [ ] Binance API secret
- [ ] Telegram Bot token (de @BotFather)
- [ ] Telegram chat ID (tu ID privado)

### 📋 Variables de entorno necesarias

Copia estas en Railway → Variables:

```
EXCHANGE_KEY
EXCHANGE_SECRET
TELEGRAM_TOKEN
TELEGRAM_CHAT_ID
LEVERAGE
MAX_OPEN_TRADES
SCAN_INTERVAL
MAX_RISK_PER_TRADE
MAX_DAILY_LOSS
MIN_CONFIDENCE
DASHBOARD_ENABLED
DASHBOARD_PORT=8000
```

### 🔐 Seguridad

- [ ] ❌ NO subas archivos `.env` o con secrets
- [ ] ❌ NO incluyas API keys en código
- [ ] ✅ Usa variables de entorno (dotenv)
- [ ] ✅ Revisa `.gitignore` antes de push

### 🎯 Último paso

```bash
# Desde carpeta del proyecto
git status  # Verifica todo
git add .
git commit -m "Initial commit: UltraBot v3"
git push origin main
```

---

## ⚠️ Si algo falla

### Error: "No such file or directory: bot.py"

```bash
# Verifica que estás en la carpeta correcta
ls bot.py  # Debe existir
```

### Error: "fatal: not a git repository"

```bash
git init
```

### Error: "remote origin already exists"

```bash
git remote remove origin
git remote add origin https://github.com/usuario/repo.git
```

### ImportError después de deploy

1. Verifica que `.py` files tienen encoding UTF-8
2. Verifica que `__init__.py` existen en todas las carpetas
3. Revisa logs en Railway dashboard

---

**¿Listo?** Sigue `GITHUB_SETUP.md` 🚀
