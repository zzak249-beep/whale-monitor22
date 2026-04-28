"""
BACKTESTER — Simulación histórica walk-forward sin lookahead bias
═════════════════════════════════════════════════════════════════
Uso:
  python backtest.py --symbol BTC-USDT --days 90 --capital 10000

Genera:
  • Estadísticas completas (winrate, PF, RR, drawdown)
  • CSV con todos los trades
"""
import argparse
import asyncio
import csv
import sys
import os
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from exchange.bingx_client import BingXClient
from strategy.htf_bias     import calculate_htf_bias
from strategy.ema10_cross  import calculate_ema10_signal
from strategy.structure    import detect_bos
from strategy.volume_cvd   import calculate_volume_cvd
from strategy.signals      import aggregate_signals, TradeSignal
from utils.logger          import get_logger

log = get_logger("Backtest")


class BacktestTrade:
    __slots__ = [
        "symbol", "direction", "entry_type", "entry_time", "exit_time",
        "entry_price", "exit_price", "sl", "tp", "qty", "pnl", "pnl_pct",
        "result", "score", "confidence", "bars_held", "pattern",
    ]
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class Backtester:
    def __init__(self, symbol: str, capital: float = 10_000.0):
        self.symbol  = symbol
        self.capital = capital
        self.trades: list[BacktestTrade] = []

    async def load_candles(self, interval: str, days: int) -> list[dict]:
        mins_per_bar = (60 if interval.endswith("h") else int(interval.replace("m", "")))
        limit = min(days * 24 * 60 // mins_per_bar, 1440)
        log.info(f"Descargando {limit} velas {interval} para {self.symbol}…")
        client  = BingXClient()
        raw     = await client.get_klines(self.symbol, interval, limit=limit)
        await client.close()
        log.info(f"Cargadas {len(raw)} velas {interval}")
        return raw

    def _simulate_trade(
        self, signal: TradeSignal, entry_candle: dict,
        future_candles: list[dict], qty: float,
    ) -> Optional[BacktestTrade]:
        entry      = signal.entry_price
        sl, tp     = signal.stop_loss, signal.take_profit
        direct     = signal.direction
        entry_time = entry_candle.get("time", 0)

        for i, c in enumerate(future_candles):
            h, l = c["high"], c["low"]
            if direct == "LONG":
                if l <= sl:
                    return self._make_trade(direct, signal, entry_time, c, entry, sl, qty, i, "LOSS")
                if h >= tp:
                    return self._make_trade(direct, signal, entry_time, c, entry, tp, qty, i, "WIN")
            else:
                if h >= sl:
                    return self._make_trade(direct, signal, entry_time, c, entry, sl, qty, i, "LOSS")
                if l <= tp:
                    return self._make_trade(direct, signal, entry_time, c, entry, tp, qty, i, "WIN")
        return None

    def _make_trade(self, direct, signal, entry_time, candle, entry, exit_price, qty, i, result):
        if direct == "LONG":
            pnl     = (exit_price - entry) * qty
            pnl_pct = (exit_price - entry) / entry * 100
        else:
            pnl     = (entry - exit_price) * qty
            pnl_pct = (entry - exit_price) / entry * 100
        return BacktestTrade(
            symbol=self.symbol, direction=direct,
            entry_type=signal.entry_type,
            entry_time=entry_time, exit_time=candle.get("time", 0),
            entry_price=entry, exit_price=exit_price,
            sl=signal.stop_loss, tp=signal.take_profit, qty=qty,
            pnl=round(pnl, 4), pnl_pct=round(pnl_pct, 3),
            result=result, score=signal.score, confidence=signal.confidence,
            bars_held=i + 1,
            pattern=getattr(signal, "candle_pattern", ""),
        )

    async def run(self, entry_candles: list[dict], htf_candles: list[dict]):
        WARMUP   = 250
        equity   = self.capital
        i        = WARMUP

        log.info(f"Backtest: {len(entry_candles)} velas, warmup={WARMUP}")

        while i < len(entry_candles) - 50:
            hist    = entry_candles[:i+1]
            htf_w   = htf_candles[:min(i+1, len(htf_candles))]

            try:
                htf  = calculate_htf_bias(htf_w)
                ema  = calculate_ema10_signal(hist)
                bos  = detect_bos(hist)
                vol  = calculate_volume_cvd(hist)
                sig  = aggregate_signals(htf, ema, bos, vol, self.symbol)
            except Exception:
                i += 1
                continue

            if sig.direction == "HOLD":
                i += 1
                continue

            sl_dist = abs(sig.entry_price - sig.stop_loss)
            if sl_dist == 0:
                i += 1
                continue

            risk_usd = equity * (config.RISK_PER_TRADE / 100) * sig.size_mult
            qty      = (risk_usd / sl_dist) * config.LEVERAGE

            future = entry_candles[i+1: i+51]
            trade  = self._simulate_trade(sig, hist[-1], future, qty)
            if trade is None:
                i += 1
                continue

            equity += trade.pnl
            self.trades.append(trade)
            i += trade.bars_held  # Avanzar hasta después del trade

        log.info(f"Backtest terminado: {len(self.trades)} trades")

    def print_stats(self):
        if not self.trades:
            print("Sin trades en el período.")
            return

        wins   = [t for t in self.trades if t.result == "WIN"]
        losses = [t for t in self.trades if t.result == "LOSS"]
        total_pnl     = sum(t.pnl for t in self.trades)
        gross_win     = sum(t.pnl for t in wins)
        gross_loss    = abs(sum(t.pnl for t in losses))
        winrate       = len(wins) / len(self.trades) * 100
        avg_win       = gross_win  / len(wins)   if wins   else 0
        avg_loss      = gross_loss / len(losses) if losses else 0
        profit_factor = gross_win / gross_loss   if gross_loss > 0 else float("inf")
        avg_bars      = sum(t.bars_held for t in self.trades) / len(self.trades)

        # Max drawdown
        equity = self.capital
        peak   = equity
        dd     = 0.0
        for t in self.trades:
            equity += t.pnl
            peak    = max(peak, equity)
            dd      = max(dd, (peak - equity) / peak * 100)

        # Por tipo de entrada
        cross_t  = [t for t in self.trades if t.entry_type == "CROSS"]
        retest_t = [t for t in self.trades if t.entry_type == "RETEST"]
        cwr = len([t for t in cross_t  if t.result == "WIN"]) / len(cross_t)  * 100 if cross_t  else 0
        rwr = len([t for t in retest_t if t.result == "WIN"]) / len(retest_t) * 100 if retest_t else 0

        # Por confianza
        high_t = [t for t in self.trades if t.confidence == "HIGH"]
        hwr    = len([t for t in high_t if t.result == "WIN"]) / len(high_t) * 100 if high_t else 0

        sep = "═" * 58
        print(f"\n{sep}")
        print(f"  BACKTEST — {self.symbol} | CryptoBot v3")
        print(sep)
        print(f"  Capital inicial  : {self.capital:>12,.2f} USDT")
        print(f"  Capital final    : {self.capital + total_pnl:>12,.2f} USDT")
        print(f"  PnL total        : {total_pnl:>+12,.2f} USDT  ({total_pnl/self.capital*100:+.1f}%)")
        print(f"  Max Drawdown     : {dd:>10.1f}%")
        print(f"  ──────────────────────────────────────────────────────")
        print(f"  Trades totales   : {len(self.trades):>5}")
        print(f"  Ganados / Perdidos: {len(wins)} / {len(losses)}")
        print(f"  Winrate          : {winrate:>8.1f}%")
        print(f"  Avg. ganancia    : {avg_win:>+11.2f} USDT")
        print(f"  Avg. pérdida     : {-avg_loss:>+11.2f} USDT")
        print(f"  Profit factor    : {profit_factor:>9.2f}x")
        print(f"  Avg. barras/trade: {avg_bars:>7.1f}")
        print(f"  ──────────────────────────────────────────────────────")
        print(f"  Entrada CRUCE    : {len(cross_t):>3} trades | WR={cwr:.1f}%")
        print(f"  Entrada RETEST   : {len(retest_t):>3} trades | WR={rwr:.1f}%")
        print(f"  Confianza HIGH   : {len(high_t):>3} trades | WR={hwr:.1f}%")
        print(sep + "\n")

    def save_csv(self, path: str = "backtest_results.csv"):
        if not self.trades:
            return
        fields = [
            "symbol", "direction", "entry_type", "result", "score", "confidence",
            "entry_price", "exit_price", "sl", "tp", "pnl", "pnl_pct",
            "bars_held", "pattern", "entry_time", "exit_time",
        ]
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for t in self.trades:
                w.writerow({field: getattr(t, field, "") for field in fields})
        log.info(f"CSV guardado: {path}")


async def main():
    parser = argparse.ArgumentParser(description="Backtest CryptoBot v3")
    parser.add_argument("--symbol",  default="BTC-USDT")
    parser.add_argument("--days",    type=int,   default=60)
    parser.add_argument("--capital", type=float, default=10_000)
    parser.add_argument("--csv",     default="backtest_results.csv")
    args = parser.parse_args()

    bt = Backtester(args.symbol, args.capital)
    entry_c = await bt.load_candles("15m", args.days)
    htf_c   = await bt.load_candles("1h",  args.days)

    if len(entry_c) < 300:
        print("Insuficientes velas. Aumenta --days.")
        return

    await bt.run(entry_c, htf_c)
    bt.print_stats()
    bt.save_csv(args.csv)
    print(f"CSV guardado: {args.csv}")


if __name__ == "__main__":
    asyncio.run(main())
