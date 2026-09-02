"""
backtest/run_backtest.py
========================
Executa backtest completo com:
  - Dados reais do MT5 (se disponivel) ou GBM calibrado
  - SignalAggregator (GARCH + Kalman + GeoScore)
  - RiskEngine + Kelly sizing
  - Relatorio completo com metricas e CSV de trades
"""

import sys, os, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd
from datetime import datetime

from backtest.engine           import Backtester
from signals.signal_aggregator import SignalAggregator

# ── MT5 opcional ──────────────────────────────────────────────────────
try:
    import MetaTrader5 as mt5
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "config", ".env"))
    ok = mt5.initialize(
        server=os.getenv("MT5_SERVER", "VantageMarkets-Demo"),
        login=int(os.getenv("MT5_LOGIN", "26018171")),
        password=os.getenv("MT5_PASSWORD", ""),
    )
    MT5_OK = ok
    if ok:
        acc = mt5.account_info()
        print(f"  MT5 conectado | {acc.server} | Balance=${acc.balance:,.2f}")
    else:
        print(f"  MT5 falhou ({mt5.last_error()}) — usando dados sinteticos")
except Exception as e:
    MT5_OK = False
    print(f"  MT5 nao disponivel: {e}")


# ══════════════════════════════════════════════════════════════════════
# 1. COLETA DE DADOS
# ══════════════════════════════════════════════════════════════════════

N_BARS = 5000

def get_mt5_data(symbol: str, n: int):
    try:
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, n)
        if rates is None or len(rates) == 0:
            return None
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        s = df.set_index("time")["close"]
        if len(s) < 200:
            return None
        return s
    except Exception:
        return None

def synthetic_prices(symbol: str, n: int) -> pd.Series:
    params = {
        "EURUSD": (1.0850, 0.0003, 0.00002),
        "USDJPY": (148.50, 0.035,  0.002),
        "GBPUSD": (1.2720, 0.0004, 0.00001),
        "XAUUSD": (2450.0, 0.80,   0.05),
        "AUDUSD": (0.6480, 0.0003, 0.00001),
        "USDCAD": (1.3540, 0.0003, 0.00001),
    }
    S0, sigma, drift = params.get(symbol, (1.0, 0.0003, 0.00001))
    np.random.seed(abs(hash(symbol)) % (2**31))
    r = np.random.normal(drift / n, sigma / np.sqrt(252 * 24), n)
    prices = S0 * np.exp(np.cumsum(r))
    idx = pd.date_range(end=datetime.utcnow(), periods=n, freq="1h")
    return pd.Series(prices, index=idx, name=symbol)

# Descobrir simbolos disponiveis via MT5
DESIRED = ["EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "USDCAD"]

print(f"\n{'='*60}")
print(f"  VANTAGE QUANT — BACKTEST ENGINE")
print(f"  {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
print(f"  Capital: $100,000 | SL=20p | TP=40p | Kelly<=2% | RAW ECN")
print(f"{'='*60}\n")

print("  [1/4] Coletando dados historicos...")
price_dict = {}
SYMBOLS = []
for sym in DESIRED:
    if MT5_OK:
        s = get_mt5_data(sym, N_BARS)
        if s is not None and len(s) > 500:
            price_dict[sym] = s
            SYMBOLS.append(sym)
            print(f"    {sym}: {len(s)} barras REAIS (MT5)")
            continue
    s = synthetic_prices(sym, N_BARS)
    price_dict[sym] = s
    SYMBOLS.append(sym)
    print(f"    {sym}: {len(s)} barras sinteticas (GBM calibrado)")

# Alinhar pelo indice mais curto — sem dropna agressivo
prices_df = pd.DataFrame(price_dict)
# Preenche NaN internos com forward fill, remove apenas cabeca/cauda vazia
prices_df = prices_df.ffill().dropna()

if len(prices_df) < 200:
    print("  AVISO: menos de 200 barras comuns — usando sintetico completo")
    for sym in DESIRED:
        price_dict[sym] = synthetic_prices(sym, N_BARS)
        SYMBOLS = DESIRED
    prices_df = pd.DataFrame(price_dict)

print(f"\n  Simbolos ativos: {SYMBOLS}")
print(f"  Periodo : {prices_df.index[0].strftime('%Y-%m-%d')} → {prices_df.index[-1].strftime('%Y-%m-%d')}")
print(f"  Barras  : {len(prices_df)}")


# ══════════════════════════════════════════════════════════════════════
# 2. GERAR SINAIS
# ══════════════════════════════════════════════════════════════════════

print(f"\n  [2/4] Gerando sinais (GARCH + Kalman + GeoScore)...")

HEADLINE_POOL = [
    ["Fed holds rates steady", "EUR GDP grows 0.2%"],
    ["BoJ signals rate hike possibility", "JPY strengthens"],
    ["Iran threatens Hormuz", "Oil surges on supply fears"],
    ["PLA exercises near Taiwan", "Risk-off sentiment grows"],
    ["German fiscal expansion approved", "EUR bullish outlook"],
    ["Fed emergency meeting speculation", "USD weakens broadly"],
    ["Ceasefire progress Middle East", "Risk appetite returns"],
    ["Oil embargo concerns", "Commodity currencies rally"],
]

WARMUP = min(500, len(prices_df) // 4)
aggregators = {sym: SignalAggregator(sym) for sym in SYMBOLS}
for sym in SYMBOLS:
    aggregators[sym].fit_garch(prices_df[sym].iloc[:WARMUP])
    print(f"    GARCH fitado: {sym}")

signals_list = {sym: ["FLAT"] * len(prices_df) for sym in SYMBOLS}
scalars_list  = {sym: [1.0]   * len(prices_df) for sym in SYMBOLS}

print(f"    Processando {len(prices_df) - WARMUP} barras pos-warmup...")
for i in range(WARMUP, len(prices_df)):
    headlines = HEADLINE_POOL[i % len(HEADLINE_POOL)]
    for sym in SYMBOLS:
        history = prices_df[sym].iloc[max(0, i-500):i]
        current = float(prices_df[sym].iloc[i])
        pkt = aggregators[sym].evaluate(current, history, headlines)
        signals_list[sym][i] = pkt.direction
        scalars_list[sym][i] = pkt.size_scalar

signals_df = pd.DataFrame(signals_list, index=prices_df.index)
scalars_df  = pd.DataFrame(scalars_list,  index=prices_df.index)

print(f"  Distribuicao de sinais:")
for sym in SYMBOLS:
    c = signals_df[sym].value_counts().to_dict()
    print(f"    {sym}: BUY={c.get('BUY',0):4d}  SELL={c.get('SELL',0):4d}  FLAT={c.get('FLAT',0):4d}")


# ══════════════════════════════════════════════════════════════════════
# 3. BACKTEST
# ══════════════════════════════════════════════════════════════════════

print(f"\n  [3/4] Rodando backtest barra a barra...")

bt = Backtester(
    capital=100_000,
    sl_pips=20,
    tp_pips=40,
    max_kelly_pct=0.02,
    spread_pips=0.0,
    win_prob=0.55,
    rr_ratio=2.0,
)
results = bt.run(prices_df, signals_df, scalars_df)


# ══════════════════════════════════════════════════════════════════════
# 4. RELATORIO
# ══════════════════════════════════════════════════════════════════════

print(f"\n  [4/4] Resultado\n")
bt.report(results)

# Breakdown por simbolo
print(f"  BREAKDOWN POR SIMBOLO:")
print(f"  {'PAR':<10} {'TRADES':>7} {'WIN%':>7} {'NET PNL':>10} {'COMISS':>9}")
print(f"  {'─'*47}")
for sym in SYMBOLS:
    t_sym = [t for t in results.trades if t.symbol == sym]
    if not t_sym:
        print(f"  {sym:<10} {'0':>7} {'—':>7} {'—':>10} {'—':>9}")
        continue
    n    = len(t_sym)
    wins = sum(1 for t in t_sym if t.net_pnl > 0)
    net  = sum(t.net_pnl   for t in t_sym)
    comm = sum(t.commission for t in t_sym)
    print(f"  {sym:<10} {n:>7} {wins/n*100:>6.1f}% {net:>10,.2f} {comm:>9,.2f}")

# Top trades
if results.trades:
    srt = sorted(results.trades, key=lambda t: t.net_pnl, reverse=True)
    print(f"\n  MELHORES TRADES:")
    print(f"  {'PAR':<8} {'DIR':<5} {'PIPS':>7} {'NET PNL':>10}  SAIDA")
    print(f"  {'─'*46}")
    for t in srt[:5]:
        print(f"  {t.symbol:<8} {t.direction:<5} {t.pips:>7.1f} {t.net_pnl:>10,.2f}  {t.reason_out}")

    print(f"\n  PIORES TRADES:")
    print(f"  {'PAR':<8} {'DIR':<5} {'PIPS':>7} {'NET PNL':>10}  SAIDA")
    print(f"  {'─'*46}")
    for t in srt[-5:]:
        print(f"  {t.symbol:<8} {t.direction:<5} {t.pips:>7.1f} {t.net_pnl:>10,.2f}  {t.reason_out}")

# Salvar outputs
out_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(out_dir, exist_ok=True)
ts_str = datetime.utcnow().strftime('%Y%m%d_%H%M')

csv_trades = os.path.join(out_dir, f"backtest_trades_{ts_str}.csv")
pd.DataFrame([vars(t) for t in results.trades]).to_csv(csv_trades, index=False)

csv_equity = os.path.join(out_dir, f"backtest_equity_{ts_str}.csv")
results.equity_curve.to_csv(csv_equity, header=["equity"])

print(f"\n  Trades CSV : {csv_trades}")
print(f"  Equity CSV : {csv_equity}")

if MT5_OK:
    mt5.shutdown()
print(f"\n  BACKTEST CONCLUIDO\n")
