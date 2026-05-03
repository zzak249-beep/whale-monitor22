"""
Estrategia Phantom Edge — ZigZag++ 15m
HMA · Volume Delta · ATR dinámico · Filtros anti-fakeout

Señal LONG:
  1. Ruptura alcista confirmada del último pivot high
  2. HMA con pendiente alcista y precio sobre HMA
  3. Volume Delta positivo (presión compradora)
  4. Volumen de la vela ≥ media×VOL_MULT (confirma la ruptura)
  5. ATR dentro del rango de volatilidad aceptable
  6. Gap de ruptura < 1.5×ATR (no entrar tarde)

Señal SHORT: simétricas.

TP = entrada ± ATR × ATR_TP_MULT
SL = entrada ∓ ATR × ATR_SL_MULT
RR mínimo configurable (filtro final)
"""
import numpy as np
from typing import Optional

# ── Parámetros de estrategia ──────────────────────────────────────────────────
PIVOT_LEN    = 5      # velas a cada lado para confirmar pivot
HMA_LEN      = 50     # longitud HMA (filtro de tendencia)
FT_PERIOD    = 25     # ventana Volume Delta
ATR_LEN      = 14     # período ATR
ATR_TP_MULT  = 1.5    # TP = entrada ± ATR × multiplicador
ATR_SL_MULT  = 1.0    # SL = entrada ∓ ATR × multiplicador
MIN_ATR_PCT  = 0.08   # ATR % mínimo respecto al precio (filtra inactivos)
MAX_ATR_PCT  = 4.0    # ATR % máximo (filtra demasiado volátiles)
VOL_MULT     = 1.2    # multiplicador volumen spike anti-fakeout
MIN_RR       = 1.3    # ratio reward/risk mínimo para aceptar señal
MAX_GAP_ATR  = 1.5    # ruptura máxima permitida en múltiplos de ATR

# Porcentajes fijos de fallback (si ATR no disponible)
TP_PCT_FIXED = 0.0045
SL_PCT_FIXED = 0.0030


# ── WMA interna ───────────────────────────────────────────────────────────────

def _wma(arr: np.ndarray, n: int) -> np.ndarray:
    """Weighted Moving Average vectorizada."""
    if len(arr) < n:
        return np.full(len(arr), arr[-1] if len(arr) else 0.0)
    w    = np.arange(1, n + 1, dtype=np.float64)
    conv = np.convolve(arr, w[::-1] / w.sum(), mode="valid")
    return np.concatenate([np.full(n - 1, conv[0]), conv])


# ── Indicadores ───────────────────────────────────────────────────────────────

def calc_hma(closes: np.ndarray, n: int = HMA_LEN) -> np.ndarray:
    """
    Hull Moving Average — 3× más reactivo que EMA, sin lag.
    HMA(n) = WMA(2·WMA(n/2) − WMA(n), √n)
    """
    half = max(2, n // 2)
    sq   = max(2, int(np.sqrt(n)))
    return _wma(2 * _wma(closes, half) - _wma(closes, n), sq)


def calc_atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = ATR_LEN) -> float:
    """ATR de Wilder — mide volatilidad real del mercado."""
    if len(c) < n + 1:
        return float(np.mean(h - l))
    tr = np.maximum(
        h[1:] - l[1:],
        np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1]))
    )
    tr = np.concatenate([[h[0] - l[0]], tr])
    atr_arr = np.zeros(len(tr))
    atr_arr[n - 1] = np.mean(tr[:n])
    for i in range(n, len(tr)):
        atr_arr[i] = (atr_arr[i - 1] * (n - 1) + tr[i]) / n
    return float(atr_arr[-1])


def calc_pivots(h: np.ndarray, l: np.ndarray, pivot_len: int = PIVOT_LEN) -> tuple[float, float]:
    """
    Pivot High / Pivot Low (ZigZag++ equivalente).
    Retorna (último pico confirmado, último valle confirmado).
    Excluye las últimas `pivot_len` velas (en formación, no confirmadas).
    """
    n = len(h)
    if n < 2 * pivot_len + 2:
        return np.nan, np.nan

    last_peak = last_valley = np.nan

    for i in range(n - pivot_len - 2, pivot_len - 1, -1):
        win_h = h[i - pivot_len: i + pivot_len + 1]
        win_l = l[i - pivot_len: i + pivot_len + 1]

        if np.isnan(last_peak) and h[i] == win_h.max():
            last_peak = float(h[i])

        if np.isnan(last_valley) and l[i] == win_l.min():
            last_valley = float(l[i])

        if not np.isnan(last_peak) and not np.isnan(last_valley):
            break

    return last_peak, last_valley


def calc_volume_delta(
    c: np.ndarray, o: np.ndarray, v: np.ndarray,
    period: int = FT_PERIOD,
) -> float:
    """
    Future-Trend Volume Delta.
    Positivo = presión compradora · Negativo = presión vendedora.
    """
    delta = np.where(c > o, v, np.where(c < o, -v, 0.0))
    n = len(delta)
    if n < period * 3:
        return 0.0

    total = 0.0
    for i in range(period):
        d0 = delta[n - 1 - i]
        d1 = delta[n - 1 - i - period]     if n - 1 - i - period     >= 0 else 0.0
        d2 = delta[n - 1 - i - period * 2] if n - 1 - i - period * 2 >= 0 else 0.0
        total += (d0 + d1 + d2) / 3.0
    return total / period


# ── Señal principal ───────────────────────────────────────────────────────────

def signal(candles: list[dict]) -> Optional[dict]:
    """
    Analiza una lista de velas 15m y retorna un dict con la señal,
    o None si no hay entrada.

    El dict incluye:
      side, entry, tp, sl, atr, atr_pct, pip_val,
      peak, valley, hma, vdelta, rr, reasons
    """
    if len(candles) < max(HMA_LEN * 2, 120):
        return None

    h = np.array([c["h"] for c in candles])
    l = np.array([c["l"] for c in candles])
    c = np.array([c["c"] for c in candles])
    o = np.array([c["o"] for c in candles])
    v = np.array([c["v"] for c in candles])

    close = float(c[-1])
    prev  = float(c[-2])

    if close <= 0:
        return None

    # ── ATR ───────────────────────────────────────────────────────────────────
    atr     = calc_atr(h, l, c)
    atr_pct = atr / close * 100
    if atr_pct < MIN_ATR_PCT or atr_pct > MAX_ATR_PCT:
        return None   # demasiado quieto o demasiado volátil

    pip_val = atr / ATR_LEN   # unidad de precio (proxy de 1 pip)

    # ── Pivotes ZigZag++ ─────────────────────────────────────────────────────
    peak, valley = calc_pivots(h, l)
    if np.isnan(peak) or np.isnan(valley):
        return None

    # ── HMA ───────────────────────────────────────────────────────────────────
    hma         = calc_hma(c)
    hma_cur     = float(hma[-1])
    hma_prev    = float(hma[-2])
    hma_bullish = close > hma_cur and hma_cur > hma_prev
    hma_bearish = close < hma_cur and hma_cur < hma_prev

    # ── Volume Delta ─────────────────────────────────────────────────────────
    vdelta     = calc_volume_delta(c, o, v)
    flow_bull  = vdelta > 0
    flow_bear  = vdelta < 0

    # ── Volumen spike (anti-fakeout) ──────────────────────────────────────────
    vol_ma    = float(np.mean(v[-20:])) if len(v) >= 20 else float(v[-1])
    vol_spike = float(v[-2]) > vol_ma * VOL_MULT  # vela anterior (cerrada)

    # ── Filtro de gap máximo ──────────────────────────────────────────────────
    within_long  = (close - peak)   < atr * MAX_GAP_ATR
    within_short = (valley - close) < atr * MAX_GAP_ATR

    # ── Condiciones de ruptura ────────────────────────────────────────────────
    long_break  = prev <= peak   < close  # crossover del pico
    short_break = prev >= valley > close  # crossunder del valle

    long_ok  = long_break  and hma_bullish and flow_bull and vol_spike and within_long
    short_ok = short_break and hma_bearish and flow_bear and vol_spike and within_short

    if not long_ok and not short_ok:
        return None

    # ── TP / SL dinámicos ─────────────────────────────────────────────────────
    if long_ok:
        tp = close + atr * ATR_TP_MULT
        sl = close - atr * ATR_SL_MULT
        side = "BUY"
        reasons = [
            "ZZ++_break_peak",
            f"HMA_bull({hma_cur:.4f})",
            f"VDelta+{vdelta:.0f}",
            "VolSpike",
        ]
    else:
        tp = close - atr * ATR_TP_MULT
        sl = close + atr * ATR_SL_MULT
        side = "SELL"
        reasons = [
            "ZZ++_break_valley",
            f"HMA_bear({hma_cur:.4f})",
            f"VDelta{vdelta:.0f}",
            "VolSpike",
        ]

    # ── Filtro R/R mínimo ─────────────────────────────────────────────────────
    rr = risk_reward(tp, sl, close, side)
    if rr < MIN_RR:
        return None

    return {
        "side":    side,
        "entry":   close,
        "tp":      round(tp, 8),
        "sl":      round(sl, 8),
        "atr":     atr,
        "atr_pct": atr_pct,
        "pip_val": pip_val,
        "peak":    peak,
        "valley":  valley,
        "hma":     hma_cur,
        "vdelta":  vdelta,
        "rr":      rr,
        "reasons": reasons,
    }


# ── TP / SL de fallback ───────────────────────────────────────────────────────

def tp_sl_fixed(entry: float, side: str) -> tuple[float, float]:
    """Calcula TP y SL con porcentajes fijos (fallback sin ATR)."""
    if side in ("BUY", "LONG"):
        return entry * (1 + TP_PCT_FIXED), entry * (1 - SL_PCT_FIXED)
    return entry * (1 - TP_PCT_FIXED), entry * (1 + SL_PCT_FIXED)


# ── Utilidades ────────────────────────────────────────────────────────────────

def risk_reward(tp: float, sl: float, entry: float, side: str) -> float:
    """Calcula ratio reward/risk. Retorna 0.0 si el riesgo es nulo."""
    if side in ("BUY", "LONG"):
        reward, risk = tp - entry, entry - sl
    else:
        reward, risk = entry - tp, sl - entry
    return round(reward / risk, 2) if risk > 0 else 0.0


def qty_by_risk(
    entry: float,
    sl: float,
    risk_usdt: float,
    leverage: int,
    step: float = 0.001,
) -> float:
    """
    Calcula la cantidad de contratos según riesgo fijo en USDT.
    risk_usdt = capital que se acepta perder si toca el SL.
    """
    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return 0.0
    # Con apalancamiento: cantidad = riesgo / (distancia_sl / entrada × margen)
    # Simplificado: qty = (risk_usdt × leverage) / (sl_dist × leverage / 1) ≈ risk_usdt / sl_dist
    qty = risk_usdt / sl_dist
    if step > 0:
        qty = int(qty / step) * step
    return round(qty, 4)
