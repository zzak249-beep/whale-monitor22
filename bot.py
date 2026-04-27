#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  BOT v10.0 — EXPLOSION HUNTER PRO                                           ║
║  ATR dinámico · RSI + BB · Rate-limiter · Paper Trading · Railway ready     ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
import os, re, sys, time, math, logging, threading, collections
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple

from settings import (
    AUTO, PAPER_TRADING, EQUITY, LEVERAGE, RISK_PER_TRADE,
    MAX_OPEN_TRADES, MAX_DAILY, MAX_DRAWDOWN_PCT, DAILY_LOSS, CB_H,
    ATR_SL_MULT, ATR_TP_MULT, SL_MAX, SL_MIN, MIN_RR,
    TP1_R, TP2_R, TP1_PCT, TP2_PCT,
    USE_TRAIL, TRAIL_RATE, TRAIL_ACT,
    MIN_SCORE, KLINE_INTERVAL, SIGNAL_LOOKBACK, VOL_CONFIRM_MULT,
    USE_VWAP, USE_MTF, USE_BB, USE_RSI,
    RSI_OVERSOLD, RSI_OVERBOUGHT, VOL_R_MIN, AUROLO_MIN,
    TOP_SYMBOLS, MIN_VOL, SCAN_INTERVAL_SEC, HOT_CONF, CHECK_INT,
    MIN_MEAN_PNL, MIN_SIGNALS_HIST,
    SCORE_BULL, SCORE_NEUTRAL,
    CD_TP, CD_SL, FEE_TAKER, FEE_COST,
    ML_ENABLED, RL_ENABLED, ML_THRESHOLD, ML_MODEL_PATH, RL_MODEL_PATH,
    BTC_CRASH, BREADTH_BEAR, BREADTH_COINS,
    EXCL, EXCL_PFX,
    BINGX_API_KEY, BINGX_API_SECRET, TG_CHAT, LOG_LEVEL,
)
from indicators import ema, vol_ratio, atr as calc_atr, rsi as calc_rsi, bb as calc_bb, build_features
from aurolo import aurolo, is_explosion_1h
from bingx_client import (
    api, pub, get_klines, get_ticker, get_balance,
    place_order, close_position, set_leverage, get_positions,
    cancel_order, get_open_orders,
)
from scanner import Scanner
from learn import Learn
from telegram_bot import send, get_updates
from ml_model import load_models, ml_predict, rl_action

# ── Logging ───────────────────────────────────────────────────────────────────
os.makedirs('logs', exist_ok=True)
os.makedirs('data', exist_ok=True)
os.makedirs('models', exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('logs/bot.log', encoding='utf-8'),
    ],
)
log = logging.getLogger('BOT')


# ══════════════════════════════════════════════════════════════════════════════
#  Rate Limiter — ventana deslizante, evita 429 de BingX
# ══════════════════════════════════════════════════════════════════════════════
class RateLimiter:
    """Máx `max_calls` llamadas en `window` segundos por endpoint."""

    def __init__(self, max_calls: int = 8, window: float = 1.0):
        self._max   = max_calls
        self._win   = window
        self._times: collections.deque = collections.deque()
        self._lock  = threading.Lock()

    def wait(self):
        with self._lock:
            now = time.monotonic()
            # eliminar timestamps fuera de ventana
            while self._times and now - self._times[0] >= self._win:
                self._times.popleft()
            if len(self._times) >= self._max:
                sleep_ms = self._win - (now - self._times[0]) + 0.05
                if sleep_ms > 0:
                    time.sleep(sleep_ms)
                    # recalcular tras dormir
                    now = time.monotonic()
                    while self._times and now - self._times[0] >= self._win:
                        self._times.popleft()
            self._times.append(time.monotonic())


_rl = RateLimiter(max_calls=8, window=1.2)   # límite global BingX


def safe_get_open_orders(sym: str) -> list:
    _rl.wait()
    try:
        return get_open_orders(sym)
    except Exception as e:
        log.debug(f"[RL] get_open_orders {sym}: {e}")
        return []


def safe_cancel_order(sym: str, oid: str) -> dict:
    _rl.wait()
    try:
        return cancel_order(sym, oid)
    except Exception as e:
        log.debug(f"[RL] cancel_order {sym} {oid}: {e}")
        return {}


def safe_get_ticker(sym: str) -> Optional[dict]:
    _rl.wait()
    try:
        return get_ticker(sym)
    except Exception as e:
        log.debug(f"[RL] get_ticker {sym}: {e}")
        return None


def safe_get_klines(sym: str, tf: str, lim: int) -> Optional[dict]:
    _rl.wait()
    try:
        return get_klines(sym, tf, lim)
    except Exception as e:
        log.debug(f"[RL] get_klines {sym}: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  Paper Wallet — simula ejecuciones sin API real
# ══════════════════════════════════════════════════════════════════════════════
class PaperWallet:
    def __init__(self, equity: float):
        self.equity    = equity
        self.peak      = equity
        self.pnl       = 0.0
        self._lock     = threading.Lock()

    def pos_size_usdt(self) -> float:
        """USDT a arriesgar por trade según RISK_PER_TRADE %."""
        with self._lock:
            return round(self.equity * RISK_PER_TRADE / 100, 4)

    def apply_pnl(self, pnl_usdt: float):
        with self._lock:
            self.equity += pnl_usdt
            self.pnl    += pnl_usdt
            if self.equity > self.peak:
                self.peak = self.equity

    @property
    def drawdown_pct(self) -> float:
        with self._lock:
            if self.peak <= 0:
                return 0.0
            return (self.peak - self.equity) / self.peak * 100


# ══════════════════════════════════════════════════════════════════════════════
#  Indicadores auxiliares (por si indicators.py no los exporta todos)
# ══════════════════════════════════════════════════════════════════════════════
def _atr(highs, lows, closes, period=14) -> float:
    """ATR simplificado — fallback si indicators.py no lo tiene."""
    try:
        return calc_atr(highs, lows, closes, period)
    except Exception:
        if len(closes) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i],
                     abs(highs[i] - closes[i-1]),
                     abs(lows[i]  - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period


def _rsi(closes, period=14) -> float:
    """RSI simplificado — fallback."""
    try:
        return calc_rsi(closes, period)
    except Exception:
        if len(closes) < period + 1:
            return 50.0
        gains = losses = 0.0
        for i in range(1, period + 1):
            d = closes[-period + i] - closes[-period + i - 1]
            if d > 0: gains  += d
            else:     losses -= d
        if losses == 0:
            return 100.0
        rs = (gains / period) / (losses / period)
        return 100 - 100 / (1 + rs)


def _bb(closes, period=20, dev=2.0) -> Tuple[float, float, float]:
    """Bollinger Bands — (upper, mid, lower)."""
    try:
        return calc_bb(closes, period, dev)
    except Exception:
        if len(closes) < period:
            p = closes[-1]
            return p, p, p
        sl = closes[-period:]
        mid = sum(sl) / period
        std = (sum((x - mid)**2 for x in sl) / period) ** 0.5
        return mid + dev * std, mid, mid - dev * std


# ══════════════════════════════════════════════════════════════════════════════
#  BOT
# ══════════════════════════════════════════════════════════════════════════════
class Bot:
    _opening = False   # mutex simple para evitar dobles entradas

    def __init__(self):
        log.info("=" * 72)
        log.info("  BOT v10.0 — EXPLOSION HUNTER PRO")
        log.info(f"  Modo: {'📄 PAPER' if PAPER_TRADING else '💰 REAL'} | "
                 f"Leverage: {LEVERAGE}x | Max trades: {MAX_OPEN_TRADES}")
        log.info(f"  TF: {KLINE_INTERVAL} | Score mín: {MIN_SCORE} | "
                 f"ATR SL:{ATR_SL_MULT}x TP:{ATR_TP_MULT}x")
        log.info(f"  RSI:{USE_RSI} | BB:{USE_BB} | MTF:{USE_MTF} | VWAP:{USE_VWAP}")
        log.info("=" * 72)

        # Estado
        self.trades: Dict[str, Dict]  = {}
        self._contracts: Dict         = {}
        self._cooldowns: Dict         = {}
        self._last_report             = datetime.now() - timedelta(hours=3)
        self._last_zombie             = 0.0
        self._tg_offset               = 0

        # Macro
        self._btc_1h   = 0.0
        self._btc_4h   = 0.0
        self._regime   = 'neutral'
        self._regime_until: Optional[datetime] = None
        self._breadth  = 0.5

        # Diario
        self._daily_pnl    = 0.0
        self._daily_trades = 0
        self._daily_date   = datetime.utcnow().date()

        # Circuit breaker
        self._cb_active = False
        self._cb_until: Optional[datetime] = None
        self._paused    = False

        # Paper wallet
        self._paper = PaperWallet(EQUITY)

        # Módulos
        self.learn   = Learn()
        self.scanner = Scanner(self._tg)
        self.symbols: List[str] = []

        if ML_ENABLED or RL_ENABLED:
            load_models(ML_MODEL_PATH, RL_MODEL_PATH)

        # Hilos
        threading.Thread(target=self.scanner.run_loop, daemon=True).start()
        threading.Thread(target=self._cmd_loop,        daemon=True).start()

        # Inicio
        if not PAPER_TRADING:
            self._connect()
        self._load_contracts()
        self._refresh_symbols()
        nk = self._nuke_zombies()
        self._recover()

        bal = self._paper.equity if PAPER_TRADING else get_balance()
        self._tg(
            f"<b>🚀 BOT v10.0 — EXPLOSION HUNTER PRO</b>\n"
            f"{'📄 PAPER TRADING' if PAPER_TRADING else '💰 REAL'}\n"
            f"📊 {len(self.symbols)} símbolos | Lev {LEVERAGE}x | Max {MAX_OPEN_TRADES} pos\n"
            f"💵 Equity: ${bal:.2f} | Riesgo: {RISK_PER_TRADE}%/trade\n"
            f"🎯 ATR SL×{ATR_SL_MULT} TP×{ATR_TP_MULT} | Min R:R {MIN_RR}\n"
            f"🧹 Zombies: {nk} | ♻️ {len(self.trades)} recuperadas\n\n"
            f"/status /top /trades /learn /pause /resume /help"
        )

    # ── Telegram helpers ──────────────────────────────────────────────────────

    def _tg(self, txt: str):
        try:
            send(txt)
        except Exception as e:
            log.debug(f"[TG] {e}")

    # ── Comandos Telegram ──────────────────────────────────────────────────────

    def _cmd_loop(self):
        while True:
            try:
                updates = get_updates(self._tg_offset)
                for upd in updates:
                    self._tg_offset = upd['update_id'] + 1
                    msg = upd.get('message', {})
                    txt = msg.get('text', '').strip().lower()
                    cid = str(msg.get('chat', {}).get('id', ''))
                    if txt and cid == str(TG_CHAT):
                        self._cmd(txt)
            except Exception as e:
                log.debug(f"[CMD] {e}")
            time.sleep(3)

    def _cmd(self, cmd: str):
        total = len(self.learn.history)
        wins  = sum(1 for t in self.learn.history if t.get('win'))
        wr    = wins / total * 100 if total else 0

        if '/status' in cmd:
            bal = self._paper.equity if PAPER_TRADING else get_balance()
            dd  = self._paper.drawdown_pct if PAPER_TRADING else 0.0
            self._tg(
                f"<b>📊 STATUS v10.0</b>\n"
                f"{'📄 PAPER' if PAPER_TRADING else '💰 REAL'}\n"
                f"Pos: {len(self.trades)}/{MAX_OPEN_TRADES} | Hoy: {self._daily_trades}/{MAX_DAILY}\n"
                f"PnL hoy: ${self._daily_pnl:+.4f} | WR: {wr:.0f}% ({total}t)\n"
                f"Equity: ${bal:.2f} | DD: {dd:.1f}%\n"
                f"Régimen: {self._regime} | Breadth: {int(self._breadth*100)}%\n"
                f"BTC 1h: {self._btc_1h:+.2f}% | 4h: {self._btc_4h:+.2f}%\n"
                f"Score mín: {self._score_min():.0f}\n"
                f"Estado: {'⏸ PAUSADO' if self._paused else '✅ ACTIVO'}"
            )
        elif '/top' in cmd:
            with self.scanner._lock:
                top = self.scanner.hot[:10]
            if not top:
                self._tg("🔍 Scanner buscando...")
            else:
                lines = [f"{'🔴' if c>=80 else '🟠' if c>=65 else '🟡'} {s}: {c}%"
                         for s, c in top]
                self._tg("<b>🔥 TOP 10 Scanner</b>\n" + "\n".join(lines))
        elif '/trades' in cmd:
            if not self.trades:
                self._tg("Sin posiciones abiertas")
            else:
                lines = []
                for sym, t in self.trades.items():
                    cur = self._current_price(sym, t['entry'])
                    pct = (cur - t['entry']) / t['entry'] * 100
                    dur = str(datetime.now() - t['opened']).split('.')[0]
                    lines.append(f"{'✅' if pct>0 else '❌'} {sym}: {pct:+.2f}% [{dur}]")
                self._tg("<b>📋 POSICIONES</b>\n" + "\n".join(lines))
        elif '/learn' in cmd:
            self._tg(f"<b>🧠 APRENDIZAJE</b>\n{self.learn.stats_str()}")
        elif '/pause' in cmd:
            self._paused = True
            self._tg("⏸ Bot PAUSADO")
        elif '/resume' in cmd:
            self._paused = False
            self._tg("▶️ Bot REANUDADO")
        elif '/help' in cmd or '/start' in cmd:
            self._tg(
                "<b>📖 COMANDOS</b>\n"
                "/status — estado completo\n"
                "/top    — top 10 scanner\n"
                "/trades — posiciones con PnL\n"
                "/learn  — estadísticas\n"
                "/pause  — pausar entradas\n"
                "/resume — reanudar\n"
                "/help   — esta ayuda"
            )

    # ── Conexión BingX ────────────────────────────────────────────────────────

    def _connect(self) -> bool:
        if not BINGX_API_KEY or not BINGX_API_SECRET:
            log.warning("⚠️  Sin API keys → forzando paper trading")
            return False
        try:
            eq = get_balance()
            if eq > 0:
                import settings as S
                S.EQUITY = eq
                self._paper.equity = eq
                self._paper.peak   = eq
                log.info(f"✅ BingX conectado | Equity: ${eq:.2f} USDT")
                return True
            log.error("❌ Balance = 0 — verificar API Key/Secret")
            return False
        except Exception as e:
            log.error(f"❌ BingX error: {e}")
            return False

    def _load_contracts(self):
        try:
            d = pub('/openApi/swap/v2/quote/contracts')
            if d.get('code') == 0:
                for c in d.get('data', []):
                    s = c.get('symbol', '')
                    if s:
                        self._contracts[s] = {
                            'step':  float(c.get('tradeMinQuantity', 1)),
                            'prec':  int(c.get('quantityPrecision', 2)),
                            'ctval': float(c.get('contractSize', 1)),
                        }
            log.info(f"📋 {len(self._contracts)} contratos cargados")
        except Exception as e:
            log.warning(f"[CONTRACTS] {e}")

    def _refresh_symbols(self):
        try:
            d = pub('/openApi/swap/v2/quote/ticker')
            if d.get('code') != 0:
                return
            items = []
            for t in d.get('data', []):
                sym = t.get('symbol', '')
                if not self._sym_ok(sym):
                    continue
                try:
                    price = float(t.get('lastPrice', 0))
                    vol   = float(t.get('volume', 0)) * price
                    if vol >= MIN_VOL and price > 0:
                        items.append((sym, vol))
                except Exception:
                    continue
            items.sort(key=lambda x: x[1], reverse=True)
            self.symbols = [s for s, _ in items[:TOP_SYMBOLS]]
            log.info(f"📊 {len(self.symbols)} símbolos activos (vol≥${MIN_VOL:,.0f})")
        except Exception as e:
            log.warning(f"[REFRESH_SYM] {e}")

    def _sym_ok(self, sym: str) -> bool:
        if not sym.endswith('-USDT'):
            return False
        b = sym.replace('-USDT', '').upper()
        if b in EXCL:
            return False
        if any(b.startswith(p) for p in EXCL_PFX):
            return False
        if re.search(r'[A-Z]{2,}\d{3,}', b):
            return False
        return True

    # ── Macro / Régimen ───────────────────────────────────────────────────────

    def _update_btc(self):
        try:
            c, _, _, _, _ = self._klines('BTC-USDT', '1h', 4)
            if c and len(c) >= 2:
                self._btc_1h = (c[-1] - c[-2]) / c[-2] * 100
            c4, _, _, _, _ = self._klines('BTC-USDT', '4h', 10)
            if c4 and len(c4) >= 4:
                self._btc_4h = (c4[-1] - c4[-4]) / c4[-4] * 100
        except Exception as e:
            log.debug(f"[BTC] {e}")

    def _update_regime(self):
        self._update_btc()

        if self._btc_4h < -3.0:
            if not self._regime_until or datetime.utcnow() > self._regime_until:
                self._regime_until = datetime.utcnow() + timedelta(hours=2)
                self._tg(f"<b>🛑 CRASH GUARD</b> BTC 4h: {self._btc_4h:.1f}% → pausa 2h")

        bulls = total = 0
        for coin in BREADTH_COINS[:10]:
            try:
                c, _, _, _, _ = self._klines(coin, '1h', 25)
                if c and len(c) >= 21:
                    if c[-1] > ema(c, 21):
                        bulls += 1
                    total += 1
            except Exception:
                pass
        if total > 0:
            self._breadth = bulls / total

        if self._breadth < BREADTH_BEAR:
            if self._regime != 'bear':
                self._tg(f"<b>🐻 BEAR</b> Breadth {int(self._breadth*100)}%")
            self._regime = 'bear'
            return

        btc_bear = self._btc_4h < -2.0 or self._btc_1h < -2.0
        low_b    = self._breadth < 0.35

        if   btc_bear and low_b:                                  nuevo = 'bear'
        elif btc_bear or low_b:                                   nuevo = 'caution'
        elif self._btc_4h > 1.0 and self._breadth > 0.60:        nuevo = 'bull'
        else:                                                      nuevo = 'neutral'

        if nuevo != self._regime:
            log.info(f"🔄 Régimen: {self._regime} → {nuevo}")
        self._regime = nuevo

    def _regime_ok(self) -> Tuple[bool, str]:
        if self._regime_until and datetime.utcnow() < self._regime_until:
            return False, "crash_guard"
        if self._regime == 'bear':
            return False, "bear"
        return True, "ok"

    def _score_min(self) -> float:
        base = SCORE_BULL if self._regime == 'bull' else SCORE_NEUTRAL
        base = max(base, self.learn.opt_score, MIN_SCORE)
        if self._regime == 'caution':
            base *= 1.10
        return base

    # ── Utilidades ────────────────────────────────────────────────────────────

    def _klines(self, sym: str, tf: str = None, lim: int = 130):
        tf = tf or KLINE_INTERVAL
        k  = safe_get_klines(sym, tf, lim)
        if k:
            return k['c'], k['h'], k['l'], k['v'], k['o']
        return None, None, None, None, None

    def _current_price(self, sym: str, fallback: float) -> float:
        tk = safe_get_ticker(sym)
        if tk:
            p = float(tk.get('price', 0))
            if p > 0:
                return p
        # fallback: última vela del TF principal
        c, _, _, _, _ = self._klines(sym, KLINE_INTERVAL, 3)
        return c[-1] if c else fallback

    def _pos_size_usdt(self) -> float:
        """USDT por trade según riesgo configurado."""
        if PAPER_TRADING:
            return self._paper.pos_size_usdt()
        eq = get_balance()
        return round(eq * RISK_PER_TRADE / 100, 4) if eq > 0 else 5.0

    def _qty(self, sym: str, price: float, usdt: float) -> float:
        if price <= 0:
            return 0.0
        ct    = self._contracts.get(sym, {})
        prec  = ct.get('prec', 2)
        step  = ct.get('step', 0.01)
        ctval = ct.get('ctval', 1.0)
        raw   = (usdt * LEVERAGE) / (price * ctval)
        qty   = math.floor(raw / step) * step
        return round(qty, prec)

    def _daily_reset(self):
        today = datetime.utcnow().date()
        if today != self._daily_date:
            self._daily_pnl    = 0.0
            self._daily_trades = 0
            self._daily_date   = today

    def _in_cooldown(self, sym: str) -> bool:
        cd = self._cooldowns.get(sym)
        return bool(cd and datetime.utcnow() < cd)

    def _set_cooldown(self, sym: str, minutes: int):
        self._cooldowns[sym] = datetime.utcnow() + timedelta(minutes=minutes)

    # ── Limpieza de zombies — RATE-LIMITED ───────────────────────────────────

    def _nuke_zombies(self) -> int:
        if PAPER_TRADING:
            return 0
        protected = set()
        for sym in list(self.trades.keys()):
            for o in safe_get_open_orders(sym):
                otype = str(o.get('type', '')).upper()
                if 'STOP' in otype or 'TRAILING' in otype:
                    oid = o.get('orderId')
                    if oid:
                        protected.add(str(oid))

        killed  = 0
        now_ms  = int(time.time() * 1000)
        all_sym = set(self.symbols or [])
        try:
            dp = api('GET', '/openApi/swap/v2/user/positions', {})
            for p in (dp.get('data') or []):
                s = p.get('symbol', '')
                if s:
                    all_sym.add(s)
        except Exception:
            pass

        for sym in list(all_sym)[:60]:                 # límite reducido a 60
            try:
                for o in safe_get_open_orders(sym):    # rate-limited
                    oid   = str(o.get('orderId', ''))
                    otype = str(o.get('type', '')).upper()
                    otime = int(o.get('time', now_ms) or now_ms)
                    age   = (now_ms - otime) / 60_000
                    if oid in protected:
                        continue
                    if otype in ('LIMIT', 'TRIGGER', 'STOP', 'TAKE_PROFIT') \
                            and (sym not in self.trades) and age > 20:
                        r = safe_cancel_order(sym, oid)
                        if r.get('code') == 0:
                            killed += 1
            except Exception:
                pass

        if killed:
            log.info(f"🧹 {killed} zombies eliminados")
        self._last_zombie = time.time()
        return killed

    # ── Recuperar posiciones existentes ──────────────────────────────────────

    def _recover(self):
        if PAPER_TRADING:
            return
        try:
            all_pos = get_positions()
            n = 0
            for sym, sides in all_pos.items():
                if sides['short'] > 0:
                    close_position(sym, sides['short'], 'hedge')
                    time.sleep(0.5)
                if sides['long'] > 0 and sym not in self.trades:
                    entry = sides['entry']
                    lv    = sides['lev']
                    if lv > LEVERAGE + 1 or entry <= 0:
                        continue
                    c, h, l, v, _ = self._klines(sym, KLINE_INTERVAL, 50)
                    atr_val = _atr(h, l, c, 14) if c else entry * 0.01
                    sl_pct  = min(max(atr_val / entry * 100 * ATR_SL_MULT, SL_MIN), SL_MAX)
                    sl_r    = entry * (1 - sl_pct / 100)
                    self.trades[sym] = self._build_trade_dict(
                        sym=sym, fill=entry, qty_a=sides['long'],
                        sl_f=sl_r, sl_pct=sl_pct,
                        tp1=entry * (1 + TP1_R * sl_pct / 100),
                        tp2=entry * (1 + TP2_R * sl_pct / 100),
                        score=0, aur={'puntos': 0, 'señal': 'recovered',
                                      'factors': []},
                        signal={'hora': datetime.utcnow().hour},
                        label='recovered', usdt=self._pos_size_usdt(),
                    )
                    n += 1
                    log.info(f"♻️  Recuperada: {sym} @ ${entry:.6f}")
            log.info(f"♻️  {n} posiciones recuperadas")
        except Exception as e:
            log.warning(f"[RECOVER] {e}")

    # ── Análisis de señal ─────────────────────────────────────────────────────

    def _analyze_symbol(self, sym: str) -> Optional[Dict]:
        try:
            c5, h5, l5, v5, o5 = self._klines(sym, KLINE_INTERVAL, 150)
            if not c5 or len(c5) < 80:
                return None

            price = c5[-1]
            if price <= 0:
                return None

            # ── Aurolo ──────────────────────────────────────────────────────
            aur = aurolo(c5, h5, l5, v5, o5)
            if aur['puntos'] < AUROLO_MIN:
                return None

            # ── EMA 55 ──────────────────────────────────────────────────────
            e55 = ema(c5, 55)
            if price <= e55:
                return None

            # ── Volumen ──────────────────────────────────────────────────────
            vr = vol_ratio(v5, 3, 7)
            if vr < VOL_R_MIN:
                return None

            # ── ATR → SL/TP dinámico ─────────────────────────────────────
            atr_val = _atr(h5, l5, c5, 14)
            if atr_val <= 0:
                return None
            sl_pct  = min(max(atr_val / price * 100 * ATR_SL_MULT, SL_MIN), SL_MAX)
            tp1_p   = price + atr_val * ATR_TP_MULT * TP1_R / TP2_R
            tp2_p   = price + atr_val * ATR_TP_MULT
            sl_p    = price - atr_val * ATR_SL_MULT
            rr      = (tp1_p - price) / (price - sl_p) if (price - sl_p) > 0 else 0
            if rr < MIN_RR:
                return None

            # ── RSI ──────────────────────────────────────────────────────────
            rsi_val = _rsi(c5, 14) if USE_RSI else 50.0

            # ── Bollinger Bands ───────────────────────────────────────────────
            bb_upper, bb_mid, bb_lower = _bb(c5, 20, 2.0) if USE_BB else (0, 0, 0)
            bb_width = (bb_upper - bb_lower) / bb_mid if bb_mid > 0 else 0

            # ── MTF 1h (opcional) ────────────────────────────────────────────
            tf_1h_ok = False
            expl1h   = {'activo': False, 'score': 0}
            if USE_MTF:
                c1h, h1h, l1h, v1h, o1h = self._klines(sym, '1h', 80)
                if c1h and len(c1h) >= 60:
                    e55_1h   = ema(c1h, 55)
                    e9_1h    = ema(c1h, 9)
                    e21_1h   = ema(c1h, 21)
                    tf_1h_ok = c1h[-1] > e55_1h and e9_1h > e21_1h
                    expl1h   = is_explosion_1h(c1h, h1h, l1h, v1h, o1h)

            # ── VWAP (opcional) ───────────────────────────────────────────────
            above_vwap = False
            if USE_VWAP:
                try:
                    from indicators import vwap as calc_vwap
                    vwap_val  = calc_vwap(h5, l5, c5, v5)
                    above_vwap = price > vwap_val
                except Exception:
                    pass

            # ── Score compuesto ───────────────────────────────────────────────
            score = 0.0
            score += aur['puntos'] * 10
            score += vr * 3
            score += self.scanner.get_conf(sym) * 0.3

            if USE_RSI:
                if RSI_OVERSOLD <= rsi_val < RSI_OVERBOUGHT:
                    score += 8
                elif rsi_val >= RSI_OVERBOUGHT:
                    score -= 10            # sobrecomprado → penalizar
                elif rsi_val < 35:
                    score -= 5

            if USE_BB:
                if price > bb_lower and price < bb_mid:
                    score += 5             # precio en zona baja de BB → potencial
                if bb_width > 0.04:
                    score += 4             # bandas abiertas → momentum
                if price > bb_upper:
                    score -= 8             # fuera de banda superior → riesgo

            if USE_MTF:
                if tf_1h_ok:    score += 12
                if expl1h['activo']: score += 10

            if above_vwap: score += 5
            if aur.get('p1'): score += 5
            if aur.get('p3'): score += 5

            hora = datetime.utcnow().hour
            if 6 <= hora <= 22:
                score += 3
            if self._btc_1h > 0.5:
                score += 3
            elif self._btc_1h < -1.5:
                score -= 8

            score += self.learn.score_adj(aur.get('factors', []))

            # ── ML / RL ───────────────────────────────────────────────────────
            ml_prob = 1.0
            if ML_ENABLED:
                features = build_features(c5, h5, l5, v5)
                ml_prob, ml_ok = ml_predict(features, ML_THRESHOLD)
                if not ml_ok:
                    return None
                score += (ml_prob - 0.5) * 10
            if RL_ENABLED:
                features = build_features(c5, h5, l5, v5)
                if rl_action(features) != 1:
                    return None

            # Sobreescribimos sl_price en aurolo con el calculado por ATR
            aur['sl_price'] = sl_p
            aur['sl_pct']   = sl_pct

            return {
                'sym':      sym,
                'price':    price,
                'score':    round(score, 1),
                'aurolo':   aur,
                'vr':       vr,
                'rsi':      rsi_val,
                'bb_width': bb_width,
                'tf_1h':    tf_1h_ok,
                'expl1h':   expl1h,
                'ml_prob':  ml_prob,
                'hora':     hora,
                'atr':      atr_val,
                'sl_pct':   sl_pct,
                'tp1_price': tp1_p,
                'tp2_price': tp2_p,
                'sl_price':  sl_p,
                'rr':        rr,
            }

        except Exception as e:
            log.debug(f"[ANALYZE] {sym}: {e}")
            return None

    # ── Apertura ──────────────────────────────────────────────────────────────

    def _open_trade(self, sym: str, signal: Dict) -> bool:
        if Bot._opening:
            return False
        Bot._opening = True
        try:
            price   = signal['price']
            score   = signal['score']
            sl_pct  = signal['sl_pct']
            sl_p    = signal['sl_price']
            tp1_p   = signal['tp1_price']
            tp2_p   = signal['tp2_price']
            rr      = signal['rr']
            aur     = signal['aurolo']
            usdt    = self._pos_size_usdt()
            qty     = self._qty(sym, price, usdt)

            if qty <= 0:
                log.debug(f"[OPEN] {sym} qty=0 → skip")
                return False

            if PAPER_TRADING:
                fill  = price
                qty_a = qty
            else:
                _rl.wait()
                set_leverage(sym, LEVERAGE)
                time.sleep(0.15)
                _rl.wait()
                r = place_order(sym, 'BUY', qty, 'hedge', 'LONG')
                if r.get('code') != 0:
                    log.error(f"[OPEN] {sym}: {r.get('msg', '')}")
                    return False
                fill  = float(r.get('data', {}).get('avgPrice', price) or price)
                qty_a = float(r.get('data', {}).get('executedQty', qty) or qty)

            # Recalcular con precio de fill real
            atr_v    = signal['atr']
            sl_f     = fill - atr_v * ATR_SL_MULT
            sl_pct_f = (fill - sl_f) / fill * 100
            sl_pct_f = min(max(sl_pct_f, SL_MIN), SL_MAX)
            sl_f     = fill * (1 - sl_pct_f / 100)
            tp1_f    = fill + atr_v * ATR_TP_MULT * TP1_R / TP2_R
            tp2_f    = fill + atr_v * ATR_TP_MULT

            self.trades[sym] = self._build_trade_dict(
                sym=sym, fill=fill, qty_a=qty_a,
                sl_f=sl_f, sl_pct=sl_pct_f,
                tp1=tp1_f, tp2=tp2_f,
                score=score, aur=aur, signal=signal,
                label=aur.get('señal', ''), usdt=usdt,
            )
            self._daily_trades += 1
            if not PAPER_TRADING:
                self._save_trade_for_ml(sym, signal, sl_pct_f)

            fees = fill * qty_a * FEE_TAKER * 2
            self._tg(
                f"{'📄' if PAPER_TRADING else '✅'} <b>ENTRADA {sym}</b>\n"
                f"💲 ${fill:.6f} | Qty: {qty_a}\n"
                f"🎯 TP1: ${tp1_f:.6f} | TP2: ${tp2_f:.6f}\n"
                f"🛑 SL: ${sl_f:.6f} (-{sl_pct_f:.2f}%) | R:R {rr:.1f}x\n"
                f"📊 Score: {score:.0f} | RSI: {signal.get('rsi',0):.0f} | "
                f"ATR: {atr_v:.6f}\n"
                f"🕐 {datetime.utcnow().strftime('%H:%M UTC')} | "
                f"Fees≈${fees:.4f}"
            )
            log.info(f"✅ ABIERTO {sym} @ ${fill:.6f} | SL={sl_pct_f:.2f}% | "
                     f"R:R={rr:.1f} | Score={score:.0f}")
            return True

        except Exception as e:
            log.error(f"[OPEN] {sym}: {e}", exc_info=True)
            return False
        finally:
            Bot._opening = False

    def _build_trade_dict(self, sym, fill, qty_a, sl_f, sl_pct,
                          tp1, tp2, score, aur, signal, label, usdt) -> Dict:
        return {
            'entry':              fill,
            'qty_total':          qty_a,
            'qty_runner':         qty_a,
            'qty_tp1':            round(qty_a * TP1_PCT, 6),
            'qty_tp2':            round(qty_a * TP2_PCT, 6),
            'tp1_hit':            False,
            'tp2_hit':            False,
            'tp1_price':          tp1,
            'tp2_price':          tp2,
            'sl':                 sl_f,
            'sl_orig':            sl_f,
            'sl_pct':             sl_pct,
            'trail_sl':           sl_f,
            'highest':            fill,
            'opened':             datetime.now(),
            'score':              score,
            'aurolo_pts':         aur.get('puntos', 0),
            'label':              label,
            'usdt':               usdt,
            'pnl_partial':        0.0,
            'factors':            aur.get('factors', []),
            'hora_utc':           signal.get('hora', datetime.utcnow().hour),
            'btc_dir':            'up' if self._btc_1h > 0 else 'dn',
            'trail_active':       False,
            'scanner_conf':       self.scanner.get_conf(sym),
            'debilidad_alertada': False,
        }

    # ── Gestión de posición ───────────────────────────────────────────────────

    def _manage_trade(self, sym: str, t: Dict):
        try:
            price = self._current_price(sym, t['entry'])
            if price <= 0:
                return

            entry = t['entry']
            pct   = (price - entry) / entry * 100
            t['highest'] = max(t['highest'], price)

            # Trailing stop
            if USE_TRAIL:
                gain_pct = (t['highest'] - entry) / entry * 100
                if gain_pct >= t['sl_pct'] * TRAIL_ACT:
                    t['trail_active'] = True
                if t['trail_active']:
                    new_trail = t['highest'] * (1 - TRAIL_RATE / 100)
                    if new_trail > t['trail_sl']:
                        t['trail_sl'] = new_trail
                        t['sl']       = new_trail

            # SL
            if price <= t['sl']:
                self._close_trade(sym, t, price, 'SL')
                return

            # TP1
            if not t['tp1_hit'] and price >= t['tp1_price']:
                qty_tp1 = t['qty_tp1']
                if qty_tp1 > 0:
                    ok = True
                    if not PAPER_TRADING:
                        r  = close_position(sym, qty_tp1, 'hedge')
                        ok = r.get('code') == 0
                    if ok:
                        partial_pnl = (price - entry) / entry * LEVERAGE * TP1_PCT * t['usdt']
                        t['tp1_hit']      = True
                        t['qty_runner']   = round(t['qty_runner'] - qty_tp1, 6)
                        t['pnl_partial'] += partial_pnl
                        t['sl']           = max(t['sl'], entry * 1.001)
                        if PAPER_TRADING:
                            self._paper.apply_pnl(partial_pnl)
                        self._tg(
                            f"🎯 <b>TP1 {sym}</b>\n"
                            f"💲 ${price:.6f} (+{pct:.2f}%) ✅\n"
                            f"📈 SL → Breakeven | +${partial_pnl:.4f}"
                        )

            # TP2
            if t['tp1_hit'] and not t['tp2_hit'] and price >= t['tp2_price']:
                qty_tp2 = t['qty_tp2']
                if qty_tp2 > 0:
                    ok = True
                    if not PAPER_TRADING:
                        r  = close_position(sym, qty_tp2, 'hedge')
                        ok = r.get('code') == 0
                    if ok:
                        partial_pnl = (price - entry) / entry * LEVERAGE * TP2_PCT * t['usdt']
                        t['tp2_hit']      = True
                        t['qty_runner']   = round(t['qty_runner'] - qty_tp2, 6)
                        t['pnl_partial'] += partial_pnl
                        if PAPER_TRADING:
                            self._paper.apply_pnl(partial_pnl)
                        self._tg(f"🎯 <b>TP2 {sym}</b>\n💲 ${price:.6f} (+{pct:.2f}%) ✅")

            # Alerta debilidad
            if not t.get('debilidad_alertada') and pct > 0.5:
                c5, h5, l5, v5, o5 = self._klines(sym, KLINE_INTERVAL, 100)
                if c5:
                    aur = aurolo(c5, h5, l5, v5, o5)
                    if aur.get('debilidad'):
                        t['debilidad_alertada'] = True
                        self._tg(f"⚠️ <b>DEBILIDAD {sym}</b> @ ${price:.6f} (+{pct:.2f}%)")

        except Exception as e:
            log.debug(f"[MANAGE] {sym}: {e}")

    def _close_trade(self, sym: str, t: Dict, price: float, reason: str):
        try:
            qty = t.get('qty_runner', t.get('qty_total', 0))
            if qty <= 0:
                self.trades.pop(sym, None)
                return

            if not PAPER_TRADING:
                _rl.wait()
                r = close_position(sym, qty, 'hedge')
                if r.get('code') != 0:
                    log.error(f"[CLOSE] {sym}: {r.get('msg', '')}")
                    return

            pnl_pct = (price - t['entry']) / t['entry'] * 100
            fee_est = t['usdt'] * FEE_COST / 100
            pnl_usd = t['usdt'] * pnl_pct / 100 * LEVERAGE - fee_est + t['pnl_partial']
            win     = pnl_usd > 0

            self._daily_pnl += pnl_usd
            if PAPER_TRADING:
                self._paper.apply_pnl(pnl_usd - t['pnl_partial'])  # parciales ya aplicados

            self.learn.record(
                sym=sym, score=t['score'], pnl=pnl_usd, win=win,
                hora=t['hora_utc'], pts=t['aurolo_pts'],
                reason=reason, factors=t['factors'],
            )

            dur = str(datetime.now() - t['opened']).split('.')[0]
            self._tg(
                f"{'✅' if win else '❌'} <b>CIERRE {sym}</b> [{reason}]\n"
                f"💲 Entry: ${t['entry']:.6f} → Exit: ${price:.6f}\n"
                f"📊 PnL: {pnl_pct:+.2f}% | ${pnl_usd:+.4f} USDT\n"
                f"🕐 Duración: {dur}"
            )
            log.info(f"{'✅' if win else '❌'} CIERRE {sym} [{reason}] "
                     f"{pnl_pct:+.2f}% ${pnl_usd:+.4f}")

            self._set_cooldown(sym, CD_SL if reason == 'SL' else CD_TP)
            self.trades.pop(sym, None)
            if not PAPER_TRADING:
                self._update_trade_label(sym, int(win))

        except Exception as e:
            log.error(f"[CLOSE] {sym}: {e}")

    # ── ML Data ───────────────────────────────────────────────────────────────

    def _save_trade_for_ml(self, sym: str, signal: Dict, sl_pct: float):
        try:
            import csv
            c5, h5, l5, v5, _ = self._klines(sym, KLINE_INTERVAL, 50)
            if not c5:
                return
            features = build_features(c5, h5, l5, v5)
            row = {
                'sym': sym, 'ts': datetime.utcnow().isoformat(),
                'price': features[0], 'ret1': features[1],
                'ema9': features[2], 'ema21': features[3], 'ema55': features[4],
                'rsi': features[5], 'atr': features[6], 'bbw': features[7],
                'volume': features[8], 'vol_ratio': features[9],
                'score': signal['score'], 'sl_pct': sl_pct,
                'label': -1, 'future_pnl': 0.0,
            }
            path   = 'data/trades.csv'
            exists = os.path.exists(path)
            with open(path, 'a', newline='') as f:
                w = csv.DictWriter(f, fieldnames=list(row.keys()))
                if not exists:
                    w.writeheader()
                w.writerow(row)
        except Exception:
            pass

    def _update_trade_label(self, sym: str, label: int):
        try:
            import pandas as pd
            path = 'data/trades.csv'
            if not os.path.exists(path):
                return
            df   = pd.read_csv(path)
            mask = (df['sym'] == sym) & (df['label'] == -1)
            if mask.any():
                df.at[df[mask].index[-1], 'label'] = label
            df.to_csv(path, index=False)
        except Exception:
            pass

    # ── Reporte horario ───────────────────────────────────────────────────────

    def _send_report(self):
        if (datetime.now() - self._last_report).total_seconds() < 3600:
            return
        total = len(self.learn.history)
        wins  = sum(1 for t in self.learn.history if t.get('win'))
        pnl   = sum(t.get('pnl', 0) for t in self.learn.history)
        wr    = wins / total * 100 if total else 0
        bal   = self._paper.equity if PAPER_TRADING else get_balance()
        dd    = self._paper.drawdown_pct if PAPER_TRADING else 0.0
        self._tg(
            f"<b>📈 REPORTE HORARIO</b>\n"
            f"Pos abiertas: {len(self.trades)} | WR: {wr:.0f}% ({total}t)\n"
            f"PnL hoy: ${self._daily_pnl:+.4f} | Total: ${pnl:+.2f}\n"
            f"Equity: ${bal:.2f} | DD: {dd:.1f}%\n"
            f"Régimen: {self._regime} | Score opt: {self.learn.opt_score:.0f}\n"
            f"BL: {len(self.learn.blacklist)} | Breadth: {int(self._breadth*100)}%"
        )
        self._last_report = datetime.now()

    # ── Loop principal ────────────────────────────────────────────────────────

    def loop(self):
        cycle = 0
        log.info("🟢 Loop principal iniciado")

        while True:
            try:
                self._daily_reset()
                cycle += 1

                # ── Circuit breaker ───────────────────────────────────────────
                if self._cb_active:
                    if self._cb_until and datetime.utcnow() < self._cb_until:
                        time.sleep(CHECK_INT)
                        continue
                    else:
                        self._cb_active = False
                        self._tg("▶️ Circuit breaker levantado")

                # Drawdown máximo
                dd_pct = self._paper.drawdown_pct if PAPER_TRADING else (
                    -self._daily_pnl / max(EQUITY, 1) * 100)
                if dd_pct >= MAX_DRAWDOWN_PCT or \
                        self._daily_pnl < -(EQUITY * DAILY_LOSS / 100):
                    self._cb_active = True
                    self._cb_until  = datetime.utcnow() + timedelta(hours=CB_H)
                    self._tg(
                        f"🛑 Circuit breaker activado\n"
                        f"DD: {dd_pct:.1f}% | PnL día: ${self._daily_pnl:.2f}"
                    )
                    time.sleep(CHECK_INT)
                    continue

                # ── Actualizar macro cada 5 ciclos ────────────────────────────
                if cycle % 5 == 1:
                    self._update_regime()

                # ── Refrescar símbolos cada 20 ciclos ─────────────────────────
                if cycle % 20 == 1:
                    self._refresh_symbols()

                # ── Limpiar zombies cada 30 min ───────────────────────────────
                if time.time() - self._last_zombie > 1800:
                    self._nuke_zombies()

                # ── Gestionar posiciones abiertas ──────────────────────────────
                for sym in list(self.trades.keys()):
                    self._manage_trade(sym, self.trades[sym])

                # ── BTC crash guard ────────────────────────────────────────────
                if self._btc_1h < -BTC_CRASH:
                    log.warning(f"⚠️ BTC crash 1h: {self._btc_1h:.1f}% → skip entradas")
                    time.sleep(CHECK_INT)
                    continue

                # ── Buscar entradas ────────────────────────────────────────────
                if not self._paused:
                    rok, rreason = self._regime_ok()
                    if not rok:
                        log.debug(f"[LOOP] Régimen: {rreason}")
                    elif len(self.trades) < MAX_OPEN_TRADES and \
                            self._daily_trades < MAX_DAILY:
                        self._scan_for_entries()

                self._send_report()

            except Exception as e:
                log.error(f"[LOOP] {e}", exc_info=True)

            time.sleep(CHECK_INT)

    def _scan_for_entries(self):
        hot       = self.scanner.get_hot(HOT_CONF, 60)
        rest      = [s for s in self.symbols if s not in hot]
        orden     = hot + rest
        score_min = self._score_min()

        for sym in orden:
            if len(self.trades) >= MAX_OPEN_TRADES:
                break
            if sym in self.trades:
                continue
            if self._in_cooldown(sym):
                continue
            if sym in self.learn.blacklist:
                continue
            if sym in self.learn.daily_losers:
                continue

            sig = self._analyze_symbol(sym)
            if sig is None or sig['score'] < score_min:
                continue

            ok, reason = self.learn.ok(sym, sig['score'])
            if not ok:
                log.debug(f"[ENTRY] {sym} Learn: {reason}")
                continue

            log.info(
                f"🔎 Señal: {sym} score={sig['score']:.0f} "
                f"rsi={sig.get('rsi',0):.0f} "
                f"rr={sig.get('rr',0):.1f}x "
                f"aurolo={sig['aurolo'].get('señal','')}"
            )
            if self._open_trade(sym, sig):
                break           # una entrada por ciclo para no saturar la API


if __name__ == '__main__':
    bot = Bot()
    bot.loop()
