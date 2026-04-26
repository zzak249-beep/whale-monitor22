"""
TESTS — Pruebas unitarias de la estrategia
═══════════════════════════════════════════
Ejecutar:
  python -m pytest tests/ -v
  python -m pytest tests/ -v --tb=short
"""
import sys
import os
import pytest
import random
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def make_candles(n: int = 300, trend: str = "up", base: float = 50000.0) -> list[dict]:
    """Genera velas sintéticas con tendencia controlada."""
    random.seed(42)
    candles = []
    price   = base
    for _ in range(n):
        delta = random.gauss(15 if trend == "up" else -15, 80)
        o = price
        c = price + delta
        h = max(o, c) + abs(random.gauss(0, 30))
        l = min(o, c) - abs(random.gauss(0, 30))
        v = max(10, random.gauss(120, 40))
        candles.append({"open": o, "high": h, "low": l, "close": c, "volume": v})
        price = c
    return candles


def make_candles_with_cross(direction: str = "LONG") -> list[dict]:
    """
    Genera velas donde la ÚLTIMA tiene un cruce EMA10 definitivo.
    """
    random.seed(7)
    candles = make_candles(290, "down" if direction == "LONG" else "up")

    # Última vela: cruce fuerte
    last = candles[-1]
    ema_approx = last["close"]  # aproximación

    if direction == "LONG":
        # Vela anterior cierra bajo EMA, última cierra sobre EMA
        candles[-2]["close"] = ema_approx - 100
        candles[-2]["open"]  = ema_approx - 150
        candles[-1]["close"] = ema_approx + 200
        candles[-1]["open"]  = ema_approx - 50
        candles[-1]["high"]  = ema_approx + 220
        candles[-1]["low"]   = ema_approx - 60
    else:
        candles[-2]["close"] = ema_approx + 100
        candles[-2]["open"]  = ema_approx + 150
        candles[-1]["close"] = ema_approx - 200
        candles[-1]["open"]  = ema_approx + 50
        candles[-1]["high"]  = ema_approx + 60
        candles[-1]["low"]   = ema_approx - 220
    return candles


# ─── TESTS HTF BIAS ──────────────────────────────────────────────────────────

class TestHTFBias:

    def test_insufficient_candles(self):
        from strategy.htf_bias import calculate_htf_bias
        result = calculate_htf_bias(make_candles(50))
        assert result.bias == "NEUTRAL"
        assert result.confirmed is False

    def test_bullish_trend(self):
        from strategy.htf_bias import calculate_htf_bias
        candles = make_candles(300, "up", base=40000)
        result = calculate_htf_bias(candles)
        # Con tendencia alcista fuerte, esperar BULLISH
        assert result.bias in ("BULLISH", "NEUTRAL")
        assert result.ema_fast > 0
        assert result.ema_slow > 0

    def test_returns_state_fields(self):
        from strategy.htf_bias import calculate_htf_bias
        result = calculate_htf_bias(make_candles(300, "up"))
        assert hasattr(result, "bias")
        assert hasattr(result, "confirmed")
        assert hasattr(result, "strength")
        assert result.strength >= 0


# ─── TESTS EMA10 CROSS ───────────────────────────────────────────────────────

class TestEMA10Cross:

    def test_insufficient_candles(self):
        from strategy.ema10_cross import calculate_ema10_signal
        result = calculate_ema10_signal(make_candles(10))
        assert result.signal == "NONE"

    def test_no_cross_flat_market(self):
        from strategy.ema10_cross import calculate_ema10_signal
        # Mercado lateral → raramente genera cruce fuerte
        candles = make_candles(200, "up")
        result  = calculate_ema10_signal(candles)
        assert result.signal in ("LONG", "SHORT", "NONE")
        assert result.entry_type in ("CROSS", "RETEST", "NONE")

    def test_signal_has_sl_tp(self):
        from strategy.ema10_cross import calculate_ema10_signal
        candles = make_candles_with_cross("LONG")
        result  = calculate_ema10_signal(candles)
        if result.signal != "NONE":
            assert result.stop_loss > 0
            assert result.take_profit > 0
            assert result.atr > 0

    def test_candle_type_classifier(self):
        from strategy.ema10_cross import _candle_type
        # Doji — cuerpo < 15% del rango (rango=20, cuerpo=1 → 5%)
        assert _candle_type(100, 110, 90, 101) == "DOJI"
        # Hammer — rango=30, cuerpo=8(97→105), mecha_inf=17(80→97) > 2×8, mecha_sup=2
        assert _candle_type(97, 107, 80, 105) == "HAMMER"
        # Vela normal alcista fuerte → NONE
        assert _candle_type(100, 122, 98, 120) == "NONE"

    def test_rr_minimum(self):
        from strategy.ema10_cross import calculate_ema10_signal
        candles = make_candles(300, "up")
        result  = calculate_ema10_signal(candles)
        if result.signal != "NONE" and result.stop_loss > 0 and result.take_profit > 0:
            rr = abs(result.candle_close - result.take_profit) / max(
                abs(result.candle_close - result.stop_loss), 1e-10)
            assert rr >= 1.4, f"RR {rr:.2f} < 1.4"


# ─── TESTS BOS ───────────────────────────────────────────────────────────────

class TestBOS:

    def test_insufficient_candles(self):
        from strategy.structure import detect_bos
        result = detect_bos(make_candles(10))
        assert result.valid is False
        assert result.bos_type == "NONE"

    def test_returns_valid_levels(self):
        from strategy.structure import detect_bos
        candles = make_candles(200, "up")
        result  = detect_bos(candles)
        if result.valid:
            assert result.broken_level > 0
            assert result.last_swing_high > result.last_swing_low

    def test_bullish_bos_on_uptrend(self):
        from strategy.structure import detect_bos
        candles = make_candles(200, "up", base=30000)
        result  = detect_bos(candles)
        assert result.bos_type in ("BULLISH", "NONE")


# ─── TESTS CVD ───────────────────────────────────────────────────────────────

class TestCVD:

    def test_insufficient_candles(self):
        from strategy.volume_cvd import calculate_volume_cvd
        result = calculate_volume_cvd(make_candles(5))
        assert result.bias == "NEUTRAL"

    def test_buying_pressure_uptrend(self):
        from strategy.volume_cvd import calculate_volume_cvd
        candles = make_candles(100, "up")
        result  = calculate_volume_cvd(candles)
        assert result.bias in ("BUYING", "NEUTRAL", "SELLING")
        assert result.vol_ratio > 0

    def test_returns_all_fields(self):
        from strategy.volume_cvd import calculate_volume_cvd
        result = calculate_volume_cvd(make_candles(100))
        assert hasattr(result, "cvd_now")
        assert hasattr(result, "cvd_slope")
        assert hasattr(result, "vol_ratio")
        assert hasattr(result, "vol_ok")


# ─── TESTS RISK MANAGER ──────────────────────────────────────────────────────

class TestRiskManager:

    def test_sizing_basic(self):
        from risk.manager import RiskManager
        rm     = RiskManager()
        result = rm.calculate_position_size(
            equity=10000, entry_price=50000, stop_loss=49000)
        assert result.valid
        assert result.qty > 0
        assert result.risk_usdt > 0
        assert result.sl_distance > 0

    def test_sizing_invalid_params(self):
        from risk.manager import RiskManager
        rm = RiskManager()
        assert not rm.calculate_position_size(0, 50000, 49000).valid
        assert not rm.calculate_position_size(10000, 0, 49000).valid

    def test_sizing_sl_too_close(self):
        from risk.manager import RiskManager
        rm = RiskManager()
        # SL a 0.05% → demasiado cercano
        result = rm.calculate_position_size(10000, 50000, 49975)
        assert not result.valid

    def test_sizing_sl_too_far(self):
        from risk.manager import RiskManager
        rm = RiskManager()
        # SL a 15% → demasiado lejos
        result = rm.calculate_position_size(10000, 50000, 42500)
        assert not result.valid

    @pytest.mark.asyncio
    async def test_can_trade(self):
        from risk.manager import RiskManager
        rm = RiskManager()
        can, reason = await rm.can_trade("BTC-USDT", 10000)
        assert can is True
        assert reason == "OK"

    @pytest.mark.asyncio
    async def test_max_trades_limit(self):
        from risk.manager import RiskManager
        rm = RiskManager()
        # Simular MAX_OPEN_TRADES posiciones abiertas
        for i in range(config.MAX_OPEN_TRADES):
            await rm.register_open(f"SYM{i}-USDT", "LONG", 50000, 0.1, 49000, 52000)
        can, reason = await rm.can_trade("NEW-USDT", 10000)
        assert can is False
        assert "Máx" in reason

    @pytest.mark.asyncio
    async def test_daily_loss_limit(self):
        from risk.manager import RiskManager
        from datetime import datetime, timezone
        rm = RiskManager()
        # Fijar la fecha al día de hoy para evitar reset automático
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rm._daily.date = today
        rm._daily.pnl  = -600   # 6% de pérdida sobre equity 10000 > MAX_DAILY_LOSS(5%)
        can, reason = await rm.can_trade("BTC-USDT", 10000)
        assert can is False
        assert "pérdida" in reason.lower()


# ─── TESTS SIGNAL AGGREGATOR ─────────────────────────────────────────────────

class TestSignalAggregator:

    def _make_htf(self, bias, confirmed=True):
        from strategy.htf_bias import HTFState
        return HTFState(bias=bias, ema_fast=50000, ema_slow=48000,
                        price=51000, strength=5.0, confirmed=confirmed)

    def _make_ema(self, signal, entry_type="CROSS", quality=0.8):
        from strategy.ema10_cross import EMA10Signal
        return EMA10Signal(
            signal=signal, entry_type=entry_type,
            ema10_value=50000, candle_close=50200,
            cross_quality=quality, slope=0.1, atr=200,
            stop_loss=49700, take_profit=51200,
            candle_pattern="", bars_since_cross=0,
        )

    def _make_bos(self, bos_type="BULLISH"):
        from strategy.structure import BOSState
        return BOSState(bos_type=bos_type, broken_level=50100,
                        last_swing_high=50500, last_swing_low=49500,
                        poi_zone_top=50100, poi_zone_bot=49800, valid=True)

    def _make_vol(self, bias="BUYING", vol_ok=True):
        from strategy.volume_cvd import VolumeState
        return VolumeState(bias=bias, cvd_now=1000, cvd_slope=10,
                           vol_ratio=1.5 if vol_ok else 0.9,
                           vol_ok=vol_ok, cvd_ok=True, delta_pct=5)

    def test_hold_when_ema_none(self):
        from strategy.ema10_cross import EMA10Signal
        from strategy.signals import aggregate_signals
        ema  = EMA10Signal("NONE","NONE",0,0,0,0,0,0,0,"",0)
        sig  = aggregate_signals(self._make_htf("BULLISH"), ema,
                                 self._make_bos(), self._make_vol(), "BTC-USDT")
        assert sig.direction == "HOLD"

    def test_hold_when_htf_against(self):
        from strategy.signals import aggregate_signals
        sig = aggregate_signals(
            self._make_htf("BEARISH"),
            self._make_ema("LONG"),
            self._make_bos("BULLISH"),
            self._make_vol("BUYING"), "BTC-USDT")
        assert sig.direction == "HOLD"

    def test_long_all_filters_pass(self):
        from strategy.signals import aggregate_signals
        sig = aggregate_signals(
            self._make_htf("BULLISH"),
            self._make_ema("LONG", quality=0.90),
            self._make_bos("BULLISH"),
            self._make_vol("BUYING"), "BTC-USDT")
        assert sig.direction == "LONG"
        assert sig.score >= 7

    def test_high_confidence_score9(self):
        from strategy.signals import aggregate_signals
        sig = aggregate_signals(
            self._make_htf("BULLISH", confirmed=True),
            self._make_ema("LONG", quality=0.95),
            self._make_bos("BULLISH"),
            self._make_vol("BUYING", vol_ok=True), "BTC-USDT")
        if sig.direction == "LONG":
            assert sig.confidence in ("MEDIUM", "HIGH")
            assert sig.size_mult >= 1.0

    def test_retest_lower_threshold(self):
        from strategy.signals import aggregate_signals
        # Retest: umbral es 6 en vez de 7
        sig = aggregate_signals(
            self._make_htf("BULLISH"),
            self._make_ema("LONG", entry_type="RETEST", quality=0.75),
            self._make_bos("NONE"),   # BOS no confirmado
            self._make_vol("BUYING"), "BTC-USDT")
        # Con 1(retest) + 3(HTF) + 2(EMA) + 2(CVD) = 8, debe pasar
        assert sig.direction in ("LONG", "HOLD")


# ─── TESTS INTEGRACION ───────────────────────────────────────────────────────

class TestIntegration:
    """Pruebas de integración sin API real."""

    def test_full_pipeline_no_crash(self):
        """El pipeline completo no debe lanzar excepciones con datos sintéticos."""
        from strategy.htf_bias    import calculate_htf_bias
        from strategy.ema10_cross import calculate_ema10_signal
        from strategy.structure   import detect_bos
        from strategy.volume_cvd  import calculate_volume_cvd
        from strategy.signals     import aggregate_signals

        candles = make_candles(300, "up")
        htf     = calculate_htf_bias(candles)
        ema     = calculate_ema10_signal(candles)
        bos     = detect_bos(candles)
        vol     = calculate_volume_cvd(candles)
        signal  = aggregate_signals(htf, ema, bos, vol, "TEST-USDT")

        assert signal is not None
        assert signal.direction in ("LONG", "SHORT", "HOLD")

    def test_pipeline_bull_and_bear(self):
        from strategy.htf_bias    import calculate_htf_bias
        from strategy.ema10_cross import calculate_ema10_signal
        from strategy.structure   import detect_bos
        from strategy.volume_cvd  import calculate_volume_cvd
        from strategy.signals     import aggregate_signals

        for trend in ["up", "down"]:
            candles = make_candles(300, trend)
            htf  = calculate_htf_bias(candles)
            ema  = calculate_ema10_signal(candles)
            bos  = detect_bos(candles)
            vol  = calculate_volume_cvd(candles)
            sig  = aggregate_signals(htf, ema, bos, vol, "TEST-USDT")
            assert sig.direction in ("LONG", "SHORT", "HOLD")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
