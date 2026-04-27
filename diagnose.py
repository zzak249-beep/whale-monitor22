"""
DIAGNÓSTICO — Verifica conexión BingX y Telegram antes de arrancar el bot
Ejecutar: python diagnose.py
"""
import asyncio
import sys
import os

os.makedirs("logs", exist_ok=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config

async def main():
    print("\n" + "═"*55)
    print("  DIAGNÓSTICO CRYPTOBOT")
    print("═"*55)

    # 1. Config
    print(f"\n📋 Config:")
    print(f"   DRY_RUN     : {config.DRY_RUN}")
    print(f"   SYMBOLS     : {config.SYMBOLS}")
    print(f"   LEVERAGE    : {config.LEVERAGE}x")
    print(f"   RISK/TRADE  : {config.RISK_PER_TRADE}%")
    print(f"   API_KEY     : {'✓ configurada' if config.BINGX_API_KEY else '✗ VACÍA'}")
    print(f"   SECRET_KEY  : {'✓ configurada' if config.BINGX_SECRET_KEY else '✗ VACÍA'}")
    print(f"   TG_TOKEN    : {'✓ configurado' if config.TELEGRAM_TOKEN else '✗ VACÍO'}")

    # 2. BingX klines (endpoint crítico)
    from exchange.bingx_client import BingXClient
    client = BingXClient()

    print(f"\n🔗 Probando BingX klines...")
    for sym in config.SYMBOLS[:2]:
        candles = await client.get_klines(sym, "15m", limit=5)
        status = f"✅ {len(candles)} velas OK" if candles else "❌ FALLO — sin datos"
        print(f"   {sym} 15m: {status}")
        if candles:
            last = candles[-1]
            print(f"      última vela: close={last['close']:.4f} vol={last['volume']:.2f}")

    # 3. BingX ticker
    print(f"\n💰 Probando precios...")
    for sym in config.SYMBOLS[:2]:
        price = await client.get_ticker(sym)
        status = f"✅ ${price:,.2f}" if price > 0 else "❌ FALLO"
        print(f"   {sym}: {status}")

    # 4. BingX balance (requiere API key)
    if config.BINGX_API_KEY:
        print(f"\n💼 Probando balance (API privada)...")
        balance = await client.get_balance()
        if balance.get("equity", 0) > 0:
            print(f"   Equity : ${balance['equity']:,.2f} USDT ✅")
            print(f"   Libre  : ${balance['available']:,.2f} USDT")
        else:
            print(f"   ⚠️  Equity=0 — verifica API keys o que la cuenta tenga fondos")

    # 5. Telegram
    if config.TELEGRAM_TOKEN and config.TELEGRAM_CHAT_ID:
        print(f"\n📱 Probando Telegram...")
        import aiohttp
        url  = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
        body = {"chat_id": config.TELEGRAM_CHAT_ID, "text": "🧪 Diagnóstico OK — bot conectado"}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, json=body, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    d = await r.json()
                    if d.get("ok"):
                        print(f"   ✅ Mensaje enviado correctamente")
                    else:
                        print(f"   ❌ Error Telegram: {d.get('description')}")
        except Exception as e:
            print(f"   ❌ Error Telegram: {e}")
    else:
        print(f"\n📱 Telegram: ⚠️  no configurado")

    await client.close()
    print("\n" + "═"*55 + "\n")

if __name__ == "__main__":
    asyncio.run(main())
