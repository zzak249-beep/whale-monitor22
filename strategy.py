"""
Phantom Edge V3 — Fusión Institucional
══════════════════════════════════════
= Pine Script V17.1 (EMA Pullback + ADX + RVOL)
+ Phantom Edge     (HMA + Volume Delta + ATR dinámico)

Lógica: Pullback a EMA17 en mercado tendencial (ADX>20)
        confirmado por HMA, rechazo de vela y volumen.
        SL bajo el mínimo reciente, TP en R:R 1:3.
        H1 simulado con EMA68 sobre 15m (17×4 = 68 velas).
"""

import numpy as np
from typing import Optional

# ── Parámetros ajustables ─────────────────────────────────────────── #
EMA_FAST        = 7
EMA_SLOW        = 17
EMA_H1          = 68        # EMA17 en H1 ≈ EMA68 en 15m
HMA_LEN         = 50
ATR_LEN         = 14
ADX_LEN         = 14
VOL_MULT        = 1.2       # RVOL: volumen 20 % sobre media
PULLBACK_BARS   = 5         # Velas atrás para buscar toque de EMA17
BODY_RATIO      = 0.75      # Cuerpo mínimo vs media (relajado del 100 %)
RR_TARGET       = 3.0       # Take Profit en 1:3
MIN_RR          = 2.5       # Descarta si R:R calculado < 2.5
ATR_SL_BUFFER   = 0.4       # Multiplicador ATR sobre el mínimo/máximo reciente
MIN_ATR_PCT     = 0.05      # Volatilidad mínima (ignora monedas dormidas)
MAX_ATR_PCT     = 5.0       # Volatilidad máxima (ignora explosiones)


# ── Indicadores ───────────────────────────────────────────────────── #

def _ema(arr: np.ndarray, n: int) -> np.ndarray:
    alpha = 2.0 / (n + 1)
    out   = np.empty(len(arr))
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1.0 - alpha) * out[i - 1]
    return out


def _wma(arr: np.ndarray, n: int) -> np.ndarray:
    if len(arr) < n:
        return np.full(len(arr), arr[-1] if len(arr) else 0.0)
    w    = np.arange(1, n + 1, dtype=np.float64)
    conv = np.convolve(arr, w[::-1] / w.sum(), mode="valid")
    return np.concatenate([np.full(n - 1, conv[0]), conv])


def _calc_hma(closes: np.ndarray, n: int = HMA_LEN) -> np.ndarray:
    half = max(2, n // 2)
    sq   = max(2, int(np.sqrt(n)))
    return _wma(2 * _wma(closes, half) - _wma(closes, n), sq)


def _calc_atr(h: np.ndarray, l: np.ndarray, c: np.ndarray,
              n: int = ATR_LEN) -> float:
    if len(c) < n + 1:
        return float(np.mean(h - l))
    tr = np.maximum(h[1:] - l[1:],
         np.maximum(np.abs(h[1:] - c[:-1]),
                    np.abs(l[1:] - c[:-1])))
    tr = np.concatenate([[h[0] - l[0]], tr])
    a  = np.zeros(len(tr))
    a[n - 1] = np.mean(tr[:n])
    for i in range(n, len(tr)):
        a[i] = (a[i - 1] * (n - 1) + tr[i]) / n
    return float(a[-1])


def _calc_adx(h: np.ndarray, l: np.ndarray, c: np.ndarray,
              n: int = ADX_LEN) -> float:
    if len(c) < n * 2 + 1:
        return 0.0
    size   = len(c)
    tr     = np.zeros(size)
    dm_p   = np.zeros(size)
    dm_m   = np.zeros(size)
    for i in range(1, size):
        tr[i]   = max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
        up, dn  = h[i] - h[i-1], l[i-1] - l[i]
        dm_p[i] = up   if up > dn and up > 0 else 0.0
        dm_m[i] = dn   if dn > up and dn > 0 else 0.0

    atr_s = np.zeros(size); dp_s = np.zeros(size); dm_s = np.zeros(size)
    atr_s[n] = tr[1:n+1].sum()
    dp_s[n]  = dm_p[1:n+1].sum()
    dm_s[n]  = dm_m[1:n+1].sum()
    for i in range(n + 1, size):
        atr_s[i] = atr_s[i-1] - atr_s[i-1] / n + tr[i]
        dp_s[i]  = dp_s[i-1]  - dp_s[i-1]  / n + dm_p[i]
        dm_s[i]  = dm_s[i-1]  - dm_s[i-1]  / n + dm_m[i]

    di_p  = np.where(atr_s > 0, 100 * dp_s / atr_s, 0.0)
    di_m  = np.where(atr_s > 0, 100 * dm_s / atr_s, 0.0)
    dx    = np.where((di_p + di_m) > 0,
                     100 * np.abs(di_p - di_m) / (di_p + di_m), 0.0)
    adx   = np.zeros(size)
    adx[2 * n] = dx[n:2*n+1].mean()
    for i in range(2 * n + 1, size):
        adx[i] = (adx[i-1] * (n - 1) + dx[i]) / n
    return float(adx[-1])


def _calc_vdelta(c: np.ndarray, o: np.ndarray, v: np.ndarray,
                 period: int = 10) -> float:
    """Volume Delta simplificado (últimas `period` velas)."""
    delta = np.where(c > o, v, np.where(c < o, -v, 0.0))
    return float(delta[-period:].sum())


# ── Utilidades públicas (usadas en main.py) ───────────────────────── #

def risk_reward(tp: float, sl: float, entry: float, side: str) -> float:
    if side in ("BUY", "LONG"):
        reward, risk = tp - entry, entry - sl
    else:
        reward, risk = entry - tp, sl - entry
    return round(reward / risk, 2) if risk > 0 else 0.0


def qty_by_risk(entry: float, sl: float, risk_usdt: float,
                leverage: int, step: float = 0.001) -> float:
    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return 0.0
    qty = risk_usdt / sl_dist
    if step > 0:
        qty = int(qty / step) * step
    return round(qty, 4)


# ── Señal principal ───────────────────────────────────────────────── #

def signal(candles: list) -> Optional[dict]:
    """
    Devuelve un dict con la señal o None si no hay setup.

    Condiciones LONG (todas deben cumplirse):
      1. Tendencia H1 alcista  → precio > EMA68
      2. Mercado en tendencia  → ADX > 20
      3. EMAs alineadas        → EMA7 > EMA17
      4. HMA alcista           → HMA[-1] > HMA[-2]
      5. Pullback a EMA17      → mínimo reciente tocó EMA17, cierre encima
      6. Vela de rechazo bull  → cierre arriba, cuerpo > 75 % media
      7. Volumen institucional → volumen actual o anterior > 1.2× media
      8. R:R calculado         → ≥ MIN_RR tras colocar SL/TP

    Condiciones SHORT: espejo exacto.
    """
    if len(candles) < 160:
        return None

    h = np.array([x["h"] for x in candles], dtype=np.float64)
    l = np.array([x["l"] for x in candles], dtype=np.float64)
    c = np.array([x["c"] for x in candles], dtype=np.float64)
    o = np.array([x["o"] for x in candles], dtype=np.float64)
    v = np.array([x["v"] for x in candles], dtype=np.float64)

    close = float(c[-1])
    if close <= 0:
        return None

    # ── 1. ATR y filtro de volatilidad ── #
    atr     = _calc_atr(h, l, c)
    atr_pct = atr / close * 100.0
    if not (MIN_ATR_PCT <= atr_pct <= MAX_ATR_PCT):
        return None

    # ── 2. EMAs ── #
    ema7_a  = _ema(c, EMA_FAST)
    ema17_a = _ema(c, EMA_SLOW)
    ema68_a = _ema(c, EMA_H1)
    ema7    = float(ema7_a[-1])
    ema17   = float(ema17_a[-1])
    ema68   = float(ema68_a[-1])

    # ── 3. HMA ── #
    hma_a    = _calc_hma(c)
    hma_cur  = float(hma_a[-1])
    hma_prev = float(hma_a[-2])

    # ── 4. ADX ── #
    adx         = _calc_adx(h, l, c)
    is_trending = adx > 20

    # ── 5. Volumen institucional (RVOL) ── #
    vol_ma     = float(np.mean(v[-20:])) if len(v) >= 20 else float(v[-1])
    has_volume = (float(v[-1]) > vol_ma * VOL_MULT or
                  float(v[-2]) > vol_ma * VOL_MULT)

    # ── 6. Volume Delta (confirmación flujo) ── #
    vdelta     = _calc_vdelta(c, o, v)
    flow_bull  = vdelta > 0
    flow_bear  = vdelta < 0

    # ── 7. Cuerpo medio de vela ── #
    body     = np.abs(c - o)
    avg_body = float(np.mean(body[-20:])) if len(body) >= 20 else float(body[-1])
    cur_body = float(body[-1])

    # ── 8. Pullback a EMA17 ── #
    low_recent  = float(np.min(l[-PULLBACK_BARS:]))
    high_recent = float(np.max(h[-PULLBACK_BARS:]))
    pb_long     = low_recent  <= ema17 and close > ema17
    pb_short    = high_recent >= ema17 and close < ema17

    # ── 9. Vela de rechazo ── #
    bull_rej = (close > float(o[-1]) and
                cur_body > avg_body * BODY_RATIO and
                close > (float(h[-1]) + float(l[-1])) / 2)
    bear_rej = (close < float(o[-1]) and
                cur_body > avg_body * BODY_RATIO and
                close < (float(h[-1]) + float(l[-1])) / 2)

    # ── 10. Señales compuestas ── #
    h1_bull  = close > ema68
    h1_bear  = close < ema68
    ema_bull = ema7 > ema17
    ema_bear = ema7 < ema17
    hma_bull = hma_cur > hma_prev
    hma_bear = hma_cur < hma_prev

    long_ok  = (h1_bull and is_trending and ema_bull and hma_bull and
                pb_long  and bull_rej and has_volume and flow_bull)
    short_ok = (h1_bear and is_trending and ema_bear and hma_bear and
                pb_short and bear_rej and has_volume and flow_bear)

    if not long_ok and not short_ok:
        return None

    # ── 11. SL / TP con R:R 1:3 ── #
    if long_ok:
        side    = "BUY"
        sl      = float(np.min(l[-3:])) - atr * ATR_SL_BUFFER
        tp      = close + (close - sl) * RR_TARGET
        reasons = [
            f"H1_bull(EMA68={ema68:.5f})",
            f"ADX={adx:.1f}",
            f"EMA{EMA_FAST}>{EMA_SLOW}",
            f"HMA_bull({hma_cur:.5f})",
            "Pullback_EMA17",
            "BullReject",
            f"VD+{vdelta:.0f}",
        ]
    else:
        side    = "SELL"
        sl      = float(np.max(h[-3:])) + atr * ATR_SL_BUFFER
        tp      = close - (sl - close) * RR_TARGET
        reasons = [
            f"H1_bear(EMA68={ema68:.5f})",
            f"ADX={adx:.1f}",
            f"EMA{EMA_FAST}<{EMA_SLOW}",
            f"HMA_bear({hma_cur:.5f})",
            "Pullback_EMA17",
            "BearReject",
            f"VD{vdelta:.0f}",
        ]

    rr = risk_reward(tp, sl, close, side)
    if rr < MIN_RR:
        return None

    return {
        "side":     side,
        "entry":    close,
        "tp":       round(tp,  8),
        "sl":       round(sl,  8),
        "atr":      round(atr, 8),
        "atr_pct":  round(atr_pct, 3),
        "hma":      hma_cur,
        "adx":      round(adx, 2),
        "vdelta":   round(vdelta, 2),
        "rr":       rr,
        "reasons":  reasons,
    }
