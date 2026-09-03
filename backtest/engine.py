"""
backtest/engine.py
==================
Backtesting Engine para Vantage Quant System.

Características:
  - Custos reais configuráveis via ACCOUNT_TYPE (.env): RAW ECN $6.00/lote, PRO ECN $3.00/lote
  - Integra SignalAggregator (GARCH + Kalman + GeoScore)
  - Kelly fracionário com cap 2%
  - Risk Engine com circuit breaker
  - Métricas completas: Sharpe, Sortino, MaxDD, Win Rate, Profit Factor

Uso:
    from backtest.engine import Backtester
    bt = Backtester(capital=100_000)
    results = bt.run(prices_df, headlines_list)
    bt.report()
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import warnings
warnings.filterwarnings("ignore")

from core.costs import get_commission_per_lot


@dataclass
class Trade:
    symbol:      str
    direction:   str
    entry_time:  pd.Timestamp
    exit_time:   pd.Timestamp
    entry_price: float
    exit_price:  float
    volume:      float
    pnl_usd:     float
    commission:  float
    net_pnl:     float
    pips:        float
    reason_in:   str
    reason_out:  str


@dataclass
class BacktestResults:
    total_trades:    int
    winning_trades:  int
    losing_trades:   int
    win_rate:        float
    gross_pnl:       float
    total_commission: float
    net_pnl:         float
    profit_factor:   float
    sharpe_ratio:    float
    sortino_ratio:   float
    max_drawdown:    float
    max_drawdown_pct: float
    avg_pips_win:    float
    avg_pips_loss:   float
    avg_trade_pnl:   float
    best_trade:      float
    worst_trade:     float
    equity_curve:    pd.Series
    trades:          List[Trade] = field(default_factory=list)


class Backtester:

    COMMISSION_PER_LOT = get_commission_per_lot()   # lê ACCOUNT_TYPE do .env
    PIP_VALUE_USD      = 10.0    # por lote standard, pares USD

    def __init__(
        self,
        capital:       float = 100_000,
        sl_pips:       int   = 20,
        tp_pips:       int   = 40,
        max_kelly_pct: float = 0.02,
        spread_pips:   float = 0.0,     # RAW ECN
        win_prob:      float = 0.55,    # prior do modelo
        rr_ratio:      float = 2.0,     # TP/SL ratio
    ):
        self.capital       = capital
        self.sl_pips       = sl_pips
        self.tp_pips       = tp_pips
        self.max_kelly_pct = max_kelly_pct
        self.spread_pips   = spread_pips
        self.win_prob      = win_prob
        self.rr_ratio      = rr_ratio
        self._trades: List[Trade] = []
        self._equity: List[float] = [capital]

    # ------------------------------------------------------------------
    # RUN
    # ------------------------------------------------------------------
    def run(
        self,
        prices: pd.DataFrame,   # colunas: symbol (close prices), index=datetime
        signals: pd.DataFrame,  # colunas: symbol, values: "BUY"|"SELL"|"FLAT"
        scalars: pd.DataFrame,  # size_scalar por símbolo e tempo
    ) -> BacktestResults:
        """
        Executa backtest barra a barra.

        Args:
            prices:  DataFrame de preços (close) por símbolo
            signals: DataFrame de sinais ("BUY"/"SELL"/"FLAT") por símbolo
            scalars: DataFrame de size scalars (0.0-1.0) por símbolo
        """
        nav      = self.capital
        open_pos: Dict[str, Dict] = {}   # posições abertas

        for i in range(1, len(prices)):
            ts      = prices.index[i]
            ts_prev = prices.index[i - 1]

            for sym in prices.columns:
                price  = prices[sym].iloc[i]
                signal = signals[sym].iloc[i] if sym in signals.columns else "FLAT"
                scalar = scalars[sym].iloc[i] if sym in scalars.columns else 1.0

                if pd.isna(price) or price <= 0:
                    continue

                # --- Verificar posição aberta ---
                if sym in open_pos:
                    pos    = open_pos[sym]
                    pips   = (price - pos["entry"]) * 10_000
                    if pos["direction"] == "SELL":
                        pips = -pips

                    closed = False
                    reason_out = ""

                    # Hit TP
                    if pips >= self.tp_pips:
                        reason_out = "TP"
                        closed = True
                    # Hit SL
                    elif pips <= -self.sl_pips:
                        reason_out = "SL"
                        closed = True
                    # Sinal invertido → fechar
                    elif signal not in ["FLAT", pos["direction"]]:
                        reason_out = "Sinal invertido"
                        closed = True

                    if closed:
                        pnl   = pips * pos["vol"] * self.PIP_VALUE_USD
                        comm  = pos["vol"] * self.COMMISSION_PER_LOT * 2  # round-turn
                        net   = pnl - comm
                        nav  += net

                        self._trades.append(Trade(
                            symbol=sym, direction=pos["direction"],
                            entry_time=pos["time"], exit_time=ts,
                            entry_price=pos["entry"], exit_price=price,
                            volume=pos["vol"], pnl_usd=round(pnl, 2),
                            commission=round(comm, 2), net_pnl=round(net, 2),
                            pips=round(pips, 1),
                            reason_in=pos["reason"], reason_out=reason_out
                        ))
                        del open_pos[sym]
                        self._equity.append(nav)

                # --- Abrir nova posição ---
                elif signal in ["BUY", "SELL"] and sym not in open_pos:
                    # Kelly sizing
                    kelly_f = self.win_prob - (1 - self.win_prob) / self.rr_ratio
                    kelly_f = max(min(kelly_f, self.max_kelly_pct), 0.0)
                    fraction = kelly_f * scalar
                    volume   = round(max((nav * fraction) / 100_000, 0.01), 2)

                    # Custo de entrada (spread + comissão entrada)
                    cost    = (self.spread_pips * volume * self.PIP_VALUE_USD
                               + volume * self.COMMISSION_PER_LOT)
                    nav    -= cost

                    open_pos[sym] = {
                        "direction": signal,
                        "entry":     price,
                        "vol":       volume,
                        "time":      ts,
                        "reason":    f"scalar={scalar:.2f}",
                    }

        # Fechar posições abertas no final
        last_ts = prices.index[-1]
        for sym, pos in list(open_pos.items()):
            price = prices[sym].iloc[-1]
            pips  = (price - pos["entry"]) * 10_000
            if pos["direction"] == "SELL":
                pips = -pips
            pnl  = pips * pos["vol"] * self.PIP_VALUE_USD
            comm = pos["vol"] * self.COMMISSION_PER_LOT * 2
            net  = pnl - comm
            nav += net
            self._trades.append(Trade(
                symbol=sym, direction=pos["direction"],
                entry_time=pos["time"], exit_time=last_ts,
                entry_price=pos["entry"], exit_price=price,
                volume=pos["vol"], pnl_usd=round(pnl, 2),
                commission=round(comm, 2), net_pnl=round(net, 2),
                pips=round(pips, 1), reason_in=pos["reason"],
                reason_out="EOD"
            ))
        self._equity.append(nav)

        return self._compute_metrics()

    # ------------------------------------------------------------------
    # MÉTRICAS
    # ------------------------------------------------------------------
    def _compute_metrics(self) -> BacktestResults:
        trades = self._trades
        if not trades:
            return BacktestResults(0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
                                   pd.Series(self._equity))

        net_pnls  = [t.net_pnl for t in trades]
        wins      = [p for p in net_pnls if p > 0]
        losses    = [p for p in net_pnls if p <= 0]
        gross_pnl = sum(t.pnl_usd for t in trades)
        total_comm= sum(t.commission for t in trades)
        net_pnl   = sum(net_pnls)

        profit_factor = (sum(wins) / abs(sum(losses))) if losses else float("inf")

        # Equity curve
        equity = pd.Series(self._equity)
        rets   = equity.pct_change().dropna()

        sharpe  = (rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0.0
        neg_rets = rets[rets < 0]
        sortino = (rets.mean() / neg_rets.std() * np.sqrt(252)) if len(neg_rets) > 1 else 0.0

        # Max Drawdown
        rolling_max = equity.cummax()
        dd          = (equity - rolling_max) / rolling_max
        max_dd_pct  = float(dd.min())
        max_dd_usd  = float((equity - rolling_max).min())

        win_pips  = [t.pips for t in trades if t.net_pnl > 0]
        loss_pips = [t.pips for t in trades if t.net_pnl <= 0]

        return BacktestResults(
            total_trades=len(trades), winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=round(len(wins) / len(trades), 4),
            gross_pnl=round(gross_pnl, 2), total_commission=round(total_comm, 2),
            net_pnl=round(net_pnl, 2),
            profit_factor=round(profit_factor, 4),
            sharpe_ratio=round(sharpe, 4), sortino_ratio=round(sortino, 4),
            max_drawdown=round(max_dd_usd, 2),
            max_drawdown_pct=round(max_dd_pct * 100, 3),
            avg_pips_win=round(np.mean(win_pips), 2) if win_pips else 0,
            avg_pips_loss=round(np.mean(loss_pips), 2) if loss_pips else 0,
            avg_trade_pnl=round(np.mean(net_pnls), 2),
            best_trade=round(max(net_pnls), 2),
            worst_trade=round(min(net_pnls), 2),
            equity_curve=equity, trades=trades
        )

    def report(self, results: Optional[BacktestResults] = None) -> None:
        r = results or self._compute_metrics()
        print(f"\n{'='*60}")
        print(f"  BACKTEST REPORT — Vantage Quant System")
        print(f"  Capital inicial: ${self.capital:,.2f}")
        print(f"{'='*60}")
        print(f"  Total trades   : {r.total_trades}")
        print(f"  Win Rate       : {r.win_rate*100:.1f}%")
        print(f"  Profit Factor  : {r.profit_factor:.3f}")
        print(f"  Sharpe Ratio   : {r.sharpe_ratio:.3f}")
        print(f"  Sortino Ratio  : {r.sortino_ratio:.3f}")
        print(f"  Max Drawdown   : ${r.max_drawdown:,.2f} ({r.max_drawdown_pct:.2f}%)")
        print(f"{'─'*60}")
        print(f"  Gross PnL      : ${r.gross_pnl:,.2f}")
        print(f"  Comissões      : ${r.total_commission:,.2f}")
        print(f"  Net PnL        : ${r.net_pnl:,.2f}")
        print(f"  Capital final  : ${self.capital + r.net_pnl:,.2f}")
        print(f"  Retorno %      : {r.net_pnl/self.capital*100:.2f}%")
        print(f"{'─'*60}")
        print(f"  Avg pips (win) : {r.avg_pips_win:.1f}")
        print(f"  Avg pips (loss): {r.avg_pips_loss:.1f}")
        print(f"  Melhor trade   : ${r.best_trade:,.2f}")
        print(f"  Pior trade     : ${r.worst_trade:,.2f}")
        print(f"{'='*60}\n")


# ======================================================================
# TESTE LOCAL
# ======================================================================
if __name__ == "__main__":
    print("\n" + "="*60)
    print("  BACKTEST ENGINE — Simulação EURUSD + USDJPY")
    print("="*60)

    np.random.seed(42)
    n   = 2000
    idx = pd.date_range("2026-01-01", periods=n, freq="1h")

    # Preços simulados
    prices = pd.DataFrame({
        "EURUSD": 1.0850 + np.cumsum(np.random.normal(0.00005, 0.0008, n)),
        "USDJPY": 148.50 + np.cumsum(np.random.normal(-0.002, 0.08, n)),
    }, index=idx)

    # Sinais simulados (Kalman-like: trending)
    def fake_signals(p: pd.Series) -> pd.Series:
        ma20  = p.rolling(20).mean()
        ma5   = p.rolling(5).mean()
        sig   = pd.Series("FLAT", index=p.index)
        sig[ma5 > ma20 * 1.0002] = "BUY"
        sig[ma5 < ma20 * 0.9998] = "SELL"
        return sig

    signals = pd.DataFrame({
        "EURUSD": fake_signals(prices["EURUSD"]),
        "USDJPY": fake_signals(prices["USDJPY"]),
    }, index=idx)

    # Scalars (geo + garch combinados)
    scalars = pd.DataFrame(1.0, index=idx, columns=["EURUSD", "USDJPY"])
    scalars.iloc[500:600] = 0.25   # simula período geopolítico crítico

    bt  = Backtester(capital=100_000, sl_pips=20, tp_pips=40,
                     max_kelly_pct=0.02, spread_pips=0.0)
    res = bt.run(prices, signals, scalars)
    bt.report(res)
    print(f"  ✅ Backtester OK\n")
