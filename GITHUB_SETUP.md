# 🚀 Subir UltraBot v3 a GitHub

## Paso 1: Crear repositorio en GitHub

1. Ve a https://github.com/new
2. Nombre: `whale-monitor22` (o el que prefieras)
3. Descripción: "UltraBot v3 - Automated Crypto Trading Bot"
4. **NO** inicialices con README, .gitignore ni LICENSE
5. Click "Create repository"

## Paso 2: Preparar repositorio local

Abre terminal en la carpeta del proyecto:

```bash
cd whale-monitor22
```

## Paso 3: Inicializar Git (si no está ya inicializado)

```bash
git init
git config user.name "Tu Nombre"
git config user.email "tu@email.com"
```

## Paso 4: Agregar archivos

```bash
git add .
git status  # Verifica que todo esté listo
```

Deberías ver algo como:
```
On branch master

Initial commit

Changes to be committed:
  new file:   .env.example
  new file:   .gitignore
  new file:   Procfile
  new file:   README.md
  new file:   bot.py
  new file:   core/__init__.py
  new file:   core/config.py
  new file:   core/database.py
  new file:   core/risk.py
  ... etc
```

## Paso 5: Primer commit

```bash
git commit -m "Initial commit: UltraBot v3 complete setup"
```

## Paso 6: Conectar con GitHub

Copia la URL de tu repositorio (ej: `https://github.com/tu-usuario/whale-monitor22.git`)

```bash
git branch -M main
git remote add origin https://github.com/tu-usuario/whale-monitor22.git
git push -u origin main
```

Si pide autenticación:
- Usuario: tu-usuario-github
- Password: tu-token-personal (genera en Settings → Developer settings → Personal access tokens)

## ✅ Verifica en GitHub

Ve a tu repositorio en GitHub y deberías ver todos los archivos:

```
whale-monitor22/
├── .env.example
├── .gitignore
├── Procfile
├── README.md
├── bot.py
├── requirements.txt
├── core/
│   ├── __init__.py
│   ├── config.py
│   ├── database.py
│   └── risk.py
├── exchange/
│   ├── __init__.py
│   └── client.py
├── strategies/
│   ├── __init__.py
│   └── indicators.py
├── notifications/
│   ├── __init__.py
│   └── telegram.py
└── dashboard/
    ├── __init__.py
    └── server.py
```

## 🚂 Conectar con Railway

1. Ve a https://railway.app
2. New Project → GitHub repo
3. Selecciona `whale-monitor22`
4. Elige rama `main`
5. Railway empezará a desplegar

### Agregar variables de entorno en Railway

En el dashboard de Railway:
1. Variables → Add Variable
2. Agrega todas estas:

```
EXCHANGE_KEY=your_binance_api_key
EXCHANGE_SECRET=your_binance_api_secret
TELEGRAM_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_chat_id
LEVERAGE=10
MAX_OPEN_TRADES=5
SCAN_INTERVAL=5
MAX_RISK_PER_TRADE=1.0
MAX_DAILY_LOSS=500
MIN_CONFIDENCE=65
DASHBOARD_ENABLED=true
```

3. Deploy

## 🔄 Actualizar desde local

Cada vez que hagas cambios locales:

```bash
git add .
git commit -m "Descripción del cambio"
git push origin main
```

Railway se redeploy automáticamente

## 📝 Archivo .env local (para desarrollo)

Crea `.env` en tu carpeta local:

```bash
cp .env.example .env
# Edita con tus valores
nano .env
```

⚠️ **NO** subas `.env` a GitHub (está en .gitignore)

---

**¿Problemas?** 
- Verifica que Git está instalado: `git --version`
- Verifica conexión con GitHub: `git remote -v`
- Revisa logs de Railway en dashboard
