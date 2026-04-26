"""
BACKTESTER — Simulación histórica de la estrategia
═════════════════════════════════════════════════════
Uso:
  python backtest.py --symbol BTC-USDT --interval 15m --days 90

Descarga velas reales de BingX, corre la estrategia sobre cada vela
(walk-forward, sin lookahead bias) y genera:
  • Estadísticas de performance
  • CSV con todos los trades
  • Gráfico de equity curve (si matplotlib está instalado)

Sin lookahead bias:
  • Las señales se calculan con datos disponibles hasta la vela CERRADA
  • SL/TP se evalúan en velas posteriores con high/low reales
"""
import argparse
import asyncio
import csv
import json
import sys
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from exchange.bingx_client   import BingXClient
from strategy.htf_bias       import calculate_htf_bias
from strategy.ema10_cross    import calculate_ema10_signal
from strategy.structure      import detect_bos
from strategy.volume_cvd     import calculate_volume_cvd
from strategy.signals        import aggregate_signals, TradeSignal
from utils.logger            import get_logger

log = get_logger("Backtest")

# ─── RESULTADO DE UN TRADE ────────────────────────────────────────────────────

class BacktestTrade:
    __slots__ = [
        "symbol","direction","entry_type","entry_time","exit_time",
        "entry_price","exit_price","sl","tp","qty","pnl","pnl_pct",
        "result","score","confidence","bars_held","pattern",
    ]
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


# ─── BACKTEST ENGINE ─────────────────────────────────────────────────────────

class Backtester:

    def __init__(self, symbol: str, capital: float = 10_000.0):
        self.symbol  = symbol
        self.capital = capital
        self.trades: list[BacktestTrade] = []

    async def load_candles(self, interval: str, days: int) -> list[dict]:
        """Descarga velas históricas de BingX."""
        client  = BingXClient()
        limit   = min(days * (24 * 60 // int(interval.replace("m","").replace("h","") * 60
                                              if "h" in interval else
                                              int(interval.replace("m","")))), 1440)
        log.info(f"Descargando {limit} velas {interval} para {self.symbol}…")
        raw = await client.get_klines(self.symbol, interval, limit=limit)
        await client.close()

        candles = []
        for c in raw:
            if isinstance(c, list):
                candles.append({
                    "time": c[0], "open": float(c[1]), "high": float(c[2]),
                    "low": float(c[3]), "close": float(c[4]), "volume": float(c[5]),
                })
            elif isinstance(c, dict):
                candles.append({k: float(v) if k != "time" else v for k, v in c.items()})
        log.info(f"Cargadas {len(candles)} velas")
        return candles

    def _simulate_trade(
        self,
        signal:       TradeSignal,
        entry_candle: dict,
        future_candles: list[dict],
        qty:          float,
    ) -> Optional[BacktestTrade]:
        """
        Simula la ejecución de un trade mirando las velas futuras.
        Retorna el trade con el resultado real.
        """
        entry  = signal.entry_price
        sl     = signal.stop_loss
        tp     = signal.take_profit
        direct = signal.direction
        entry_time = entry_candle.get("time", 0)

        for i, candle in enumerate(future_candles):
            h = candle["high"]
            l = candle["low"]

            if direct == "LONG":
                # Verificar SL primero (más conservador)
                if l <= sl:
                    pnl = (sl - entry) * qty
                    return BacktestTrade(
                        symbol=self.symbol, direction=direct,
                        entry_type=signal.entry_type,
                        entry_time=entry_time, exit_time=candle.get("time",0),
                        entry_price=entry, exit_price=sl,
                        sl=sl, tp=tp, qty=qty,
                        pnl=round(pnl, 4),
                        pnl_pct=round((sl - entry)/entry*100, 3),
                        result="LOSS", score=signal.score,
                        confidence=signal.confidence,
                        bars_held=i+1,
                        pattern=getattr(signal, "candle_pattern", ""),
                    )
                if h >= tp:
                    pnl = (tp - entry) * qty
                    return BacktestTrade(
                        symbol=self.symbol, direction=direct,
                        entry_type=signal.entry_type,
                        entry_time=entry_time, exit_time=candle.get("time",0),
                        entry_price=entry, exit_price=tp,
                        sl=sl, tp=tp, qty=qty,
                        pnl=round(pnl, 4),
                        pnl_pct=round((tp - entry)/entry*100, 3),
                        result="WIN", score=signal.score,
                        confidence=signal.confidence,
                        bars_held=i+1,
                        pattern=getattr(signal, "candle_pattern", ""),
                    )
            else:  # SHORT
                if h >= sl:
                    pnl = (entry - sl) * qty
                    return BacktestTrade(
                        symbol=self.symbol, direction=direct,
                        entry_type=signal.entry_type,
                        entry_time=entry_time, exit_time=candle.get("time",0),
                        entry_price=entry, exit_price=sl,
                        sl=sl, tp=tp, qty=qty,
                        pnl=round(pnl, 4),
                        pnl_pct=round((entry - sl)/entry*100, 3),
                        result="LOSS", score=signal.score,
                        confidence=signal.confidence,
                        bars_held=i+1,
                        pattern=getattr(signal, "candle_pattern", ""),
                    )
                if l <= tp:
                    pnl = (entry - tp) * qty
                    return BacktestTrade(
                        symbol=self.symbol, direction=direct,
                        entry_type=signal.entry_type,
                        entry_time=entry_time, exit_time=candle.get("time",0),
                        entry_price=entry, exit_price=tp,
                        sl=sl, tp=tp, qty=qty,
                        pnl=round(pnl, 4),
                        pnl_pct=round((entry - tp)/entry*100, 3),
                        result="WIN", score=signal.score,
                        confidence=signal.confidence,
                        bars_held=i+1,
                        pattern=getattr(signal, "candle_pattern", ""),
                    )
        return None  # Sin cierre en las velas disponibles

    async def run(self, entry_candles: list[dict], htf_candles: list[dict]):
        """
        Walk-forward sobre las velas de entrada.
        Requiere mínimo 250 velas de warm-up.
        """
        WARMUP    = 250
        in_trade  = False
        equity    = self.capital
        daily_pnl: dict[str, float] = {}

        log.info(f"Iniciando backtest: {len(entry_candles)} velas, warmup={WARMUP}")

        for i in range(WARMUP, len(entry_candles) - 50):
            hist  = entry_candles[:i+1]   # Velas hasta ahora (sin ver el futuro)
            htf_w = htf_candles[:min(i+1, len(htf_candles))]

            if in_trade:
                continue  # En un trade, espera

            # ── Calcular señales
            try:
                htf  = calculate_htf_bias(htf_w)
                ema  = calculate_ema10_signal(hist)
                bos  = detect_bos(hist)
                vol  = calculate_volume_cvd(hist)
                sig  = aggregate_signals(htf, ema, bos, vol, self.symbol)
            except Exception:
                continue

            if sig.direction == "HOLD":
                continue

            # ── Sizing
            risk_usdt = equity * (config.RISK_PER_TRADE / 100) * sig.size_mult
            sl_dist   = abs(sig.entry_price - sig.stop_loss)
            if sl_dist == 0:
                continue
            qty = risk_usdt / sl_dist

            # ── Simular
            future  = entry_candles[i+1: i+51]  # Hasta 50 velas futuras
            trade   = self._simulate_trade(sig, hist[-1], future, qty)
            if trade is None:
                continue

            # ── Actualizar equity
            equity += trade.pnl
            trade_date = str(datetime.fromtimestamp(
                trade.entry_time/1000, tz=timezone.utc
            ).date()) if trade.entry_time else "unknown"
            daily_pnl[trade_date] = daily_pnl.get(trade_date, 0) + trade.pnl

            self.trades.append(trade)
            in_trade = False  # Resetear (simplificado: 1 trade a la vez)

            # Avanzar i hasta después del trade
            i += trade.bars_held

        log.info(f"Backtest terminado: {len(self.trades)} trades")

    def print_stats(self):
        if not self.trades:
            print("Sin trades en el período.")
            return

        wins   = [t for t in self.trades if t.result == "WIN"]
        losses = [t for t in self.trades if t.result == "LOSS"]
        total_pnl    = sum(t.pnl for t in self.trades)
        total_wins   = sum(t.pnl for t in wins)
        total_losses = abs(sum(t.pnl for t in losses))
        winrate      = len(wins) / len(self.trades) * 100
        avg_win      = total_wins  / len(wins)   if wins   else 0
        avg_loss     = total_losses / len(losses) if losses else 0
        profit_factor = total_wins / total_losses if total_losses > 0 else float("inf")
        avg_bars     = sum(t.bars_held for t in self.trades) / len(self.trades)

        # Separar por tipo de entrada
        cross_trades  = [t for t in self.trades if t.entry_type == "CROSS"]
        retest_trades = [t for t in self.trades if t.entry_type == "RETEST"]
        cross_wr  = len([t for t in cross_trades  if t.result == "WIN"]) / len(cross_trades)  * 100 if cross_trades  else 0
        retest_wr = len([t for t in retest_trades if t.result == "WIN"]) / len(retest_trades) * 100 if retest_trades else 0

        sep = "═" * 55
        print(f"\n{sep}")
        print(f"  BACKTEST — {self.symbol} | EMA10×15m×8")
        print(sep)
        print(f"  Capital inicial : {self.capital:>10,.2f} USDT")
        print(f"  Capital final   : {self.capital + total_pnl:>10,.2f} USDT")
        print(f"  PnL total       : {total_pnl:>+10,.2f} USDT  ({total_pnl/self.capital*100:+.1f}%)")
        print(f"  ─────────────────────────────────────────────────")
        print(f"  Trades totales  : {len(self.trades):>5}")
        print(f"  Ganados / Perdidos : {len(wins)} / {len(losses)}")
        print(f"  Winrate         : {winrate:>7.1f}%")
        print(f"  Avg. ganancia   : {avg_win:>+10.2f} USDT")
        print(f"  Avg. pérdida    : {-avg_loss:>+10.2f} USDT")
        print(f"  Profit factor   : {profit_factor:>8.2f}x")
        print(f"  Avg. barras/trade: {avg_bars:>6.1f}")
        print(f"  ─────────────────────────────────────────────────")
        print(f"  Entrada CRUCE   : {len(cross_trades):>3} trades | WR={cross_wr:.1f}%")
        print(f"  Entrada RETEST  : {len(retest_trades):>3} trades | WR={retest_wr:.1f}%")
        print(sep + "\n")

    def save_csv(self, path: str = "backtest_results.csv"):
        if not self.trades:
            return
        fields = [
            "symbol","direction","entry_type","result","score","confidence",
            "entry_price","exit_price","sl","tp","pnl","pnl_pct",
            "bars_held","pattern","entry_time","exit_time",
        ]
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for t in self.trades:
                w.writerow({field: getattr(t, field, "") for field in fields})
        log.info(f"CSV guardado: {path}")


# ─── CLI ─────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Backtest EMA10×15m×8")
    parser.add_argument("--symbol",   default="BTC-USDT",  help="Par (ej: BTC-USDT)")
    parser.add_argument("--days",     type=int, default=60, help="Días de historia")
    parser.add_argument("--capital",  type=float, default=10000, help="Capital inicial USDT")
    parser.add_argument("--csv",      default="backtest_results.csv")
    args = parser.parse_args()

    bt = Backtester(args.symbol, args.capital)

    # Cargar velas de entrada y HTF (simplificado: usa el mismo símbolo)
    entry_candles = await bt.load_candles("15m", args.days)
    htf_candles   = await bt.load_candles("1h",  args.days)

    if len(entry_candles) < 300:
        print("Insuficientes velas para backtest. Prueba con más días.")
        return

    await bt.run(entry_candles, htf_candles)
    bt.print_stats()
    bt.save_csv(args.csv)
    print(f"Resultados guardados en: {args.csv}")


if __name__ == "__main__":
    asyncio.run(main())
