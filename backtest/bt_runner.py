"""Runner isolado — captura erros e escreve output em arquivo."""
import sys, os, traceback, warnings
warnings.filterwarnings("ignore")

OUT = r"C:\Users\ACS\Downloads\Trade\vantage_quant\logs\bt_output.txt"
os.makedirs(os.path.dirname(OUT), exist_ok=True)

log_lines = []
def log(msg=""):
    log_lines.append(str(msg))

try:
    sys.path.insert(0, r"C:\Users\ACS\Downloads\Trade\vantage_quant")

    import numpy as np
    import pandas as pd
    from datetime import datetime

    log("=== BACKTEST START ===")
    log(f"Python: {sys.version}")

    # MT5
    import MetaTrader5 as mt5
    from dotenv import load_dotenv
    load_dotenv(r"C:\Users\ACS\Downloads\Trade\vantage_quant\config\.env")

    pw = os.getenv("MT5_PASSWORD", "!y2%U2dD")
    ok = mt5.initialize(server="VantageMarkets-Demo", login=26018171, password=pw)
    log(f"MT5 init: {ok} | error: {mt5.last_error()}")

    if ok:
        acc = mt5.account_info()
        log(f"Balance: ${acc.balance:.2f} | Equity: ${acc.equity:.2f}")

    # Dados
    SYMBOLS = ["EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "USDCAD"]
    N = 5000
    price_dict = {}

    def synth(sym, n):
        params = {
            "EURUSD": (1.0850, 0.0003),
            "USDJPY": (148.50, 0.035),
            "GBPUSD": (1.2720, 0.0004),
            "AUDUSD": (0.6480, 0.0003),
            "USDCAD": (1.3540, 0.0003),
        }
        S0, sig = params.get(sym, (1.0, 0.0003))
        np.random.seed(abs(hash(sym)) % (2**31))
        r = np.random.normal(0, sig / np.sqrt(252 * 24), n)
        p = S0 * np.exp(np.cumsum(r))
        idx = pd.date_range(end=datetime.utcnow(), periods=n, freq="1h")
        return pd.Series(p, index=idx, name=sym)

    for sym in SYMBOLS:
        s = None
        if ok:
            try:
                rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, N)
                if rates is not None and len(rates) > 500:
                    df = pd.DataFrame(rates)
                    df["time"] = pd.to_datetime(df["time"], unit="s")
                    s = df.set_index("time")["close"]
                    log(f"  {sym}: {len(s)} barras reais MT5")
            except Exception as e:
                log(f"  {sym}: MT5 erro {e}")
        if s is None:
            s = synth(sym, N)
            log(f"  {sym}: {len(s)} barras sinteticas")
        price_dict[sym] = s

    prices_df = pd.DataFrame(price_dict).ffill().dropna()
    log(f"Barras alinhadas: {len(prices_df)}")
    log(f"Periodo: {prices_df.index[0].date()} a {prices_df.index[-1].date()}")

    # Sinais
    from signals.signal_aggregator import SignalAggregator

    HEADLINES = [
        ["Fed holds rates steady", "EUR GDP grows 0.2%"],
        ["BoJ signals rate hike", "JPY strengthens on hawkish tone"],
        ["Iran threatens Hormuz", "Oil surges on supply fears"],
        ["PLA exercises near Taiwan", "Risk-off sentiment"],
        ["German fiscal expansion", "EUR bullish outlook"],
        ["Fed emergency meeting", "USD weakens broadly"],
        ["Ceasefire progress", "Risk appetite returns"],
        ["Oil embargo concerns", "Commodity currencies rally"],
    ]

    WARMUP = 500
    aggs = {sym: SignalAggregator(sym) for sym in SYMBOLS}
    for sym in SYMBOLS:
        aggs[sym].fit_garch(prices_df[sym].iloc[:WARMUP])
        log(f"  GARCH fitado: {sym}")

    sig_dict  = {sym: ["FLAT"] * len(prices_df) for sym in SYMBOLS}
    scl_dict  = {sym: [1.0]   * len(prices_df) for sym in SYMBOLS}

    for i in range(WARMUP, len(prices_df)):
        hl = HEADLINES[i % len(HEADLINES)]
        for sym in SYMBOLS:
            hist    = prices_df[sym].iloc[max(0, i-500):i]
            current = float(prices_df[sym].iloc[i])
            pkt = aggs[sym].evaluate(current, hist, hl)
            sig_dict[sym][i]  = pkt.direction
            scl_dict[sym][i]  = pkt.size_scalar

    signals_df = pd.DataFrame(sig_dict, index=prices_df.index)
    scalars_df = pd.DataFrame(scl_dict, index=prices_df.index)

    log("\n--- SINAIS ---")
    for sym in SYMBOLS:
        c = signals_df[sym].value_counts().to_dict()
        log(f"  {sym}: BUY={c.get('BUY',0)} SELL={c.get('SELL',0)} FLAT={c.get('FLAT',0)}")

    # Backtest
    from backtest.engine import Backtester
    bt = Backtester(capital=100_000, sl_pips=20, tp_pips=40,
                    max_kelly_pct=0.02, spread_pips=0.0,
                    win_prob=0.55, rr_ratio=2.0)
    results = bt.run(prices_df, signals_df, scalars_df)

    log("\n=== BACKTEST REPORT ===")
    log(f"Capital inicial : $100,000.00")
    log(f"Total trades    : {results.total_trades}")
    log(f"Winning trades  : {results.winning_trades}")
    log(f"Losing trades   : {results.losing_trades}")
    log(f"Win Rate        : {results.win_rate*100:.1f}%")
    log(f"Profit Factor   : {results.profit_factor:.4f}")
    log(f"Sharpe Ratio    : {results.sharpe_ratio:.4f}")
    log(f"Sortino Ratio   : {results.sortino_ratio:.4f}")
    log(f"Max Drawdown    : ${results.max_drawdown:,.2f} ({results.max_drawdown_pct:.2f}%)")
    log(f"Gross PnL       : ${results.gross_pnl:,.2f}")
    log(f"Total Commission: ${results.total_commission:,.2f}")
    log(f"Net PnL         : ${results.net_pnl:,.2f}")
    capital_final = 100_000 + results.net_pnl
    log(f"Capital final   : ${capital_final:,.2f}")
    log(f"Retorno total   : {results.net_pnl/100_000*100:.2f}%")
    log(f"Avg pips (win)  : {results.avg_pips_win:.1f}")
    log(f"Avg pips (loss) : {results.avg_pips_loss:.1f}")
    log(f"Melhor trade    : ${results.best_trade:,.2f}")
    log(f"Pior trade      : ${results.worst_trade:,.2f}")

    log("\n--- BREAKDOWN POR SIMBOLO ---")
    log(f"{'PAR':<10} {'TRADES':>7} {'WIN%':>7} {'NET PNL':>11} {'COMISS':>9}")
    for sym in SYMBOLS:
        t_sym = [t for t in results.trades if t.symbol == sym]
        if not t_sym:
            log(f"  {sym:<10} 0 trades")
            continue
        n    = len(t_sym)
        wins = sum(1 for t in t_sym if t.net_pnl > 0)
        net  = sum(t.net_pnl for t in t_sym)
        comm = sum(t.commission for t in t_sym)
        log(f"{sym:<10} {n:>7} {wins/n*100:>6.1f}% {net:>11,.2f} {comm:>9,.2f}")

    if results.trades:
        srt = sorted(results.trades, key=lambda t: t.net_pnl, reverse=True)
        log("\n--- TOP 5 MELHORES ---")
        for t in srt[:5]:
            log(f"  {t.symbol} {t.direction} {t.pips:+.1f}p => ${t.net_pnl:,.2f} [{t.reason_out}]")
        log("\n--- TOP 5 PIORES ---")
        for t in srt[-5:]:
            log(f"  {t.symbol} {t.direction} {t.pips:+.1f}p => ${t.net_pnl:,.2f} [{t.reason_out}]")

    # CSV
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M")
    tdf = pd.DataFrame([{k: getattr(t,k) for k in
           ["symbol","direction","entry_time","exit_time","entry_price",
            "exit_price","volume","pips","pnl_usd","commission","net_pnl",
            "reason_in","reason_out"]} for t in results.trades])
    tp = fr"C:\Users\ACS\Downloads\Trade\vantage_quant\logs\trades_{ts}.csv"
    ep = fr"C:\Users\ACS\Downloads\Trade\vantage_quant\logs\equity_{ts}.csv"
    tdf.to_csv(tp, index=False)
    results.equity_curve.to_csv(ep, header=["equity"])
    log(f"\nTrades CSV : {tp}")
    log(f"Equity CSV : {ep}")

    if ok:
        mt5.shutdown()
    log("\n=== BACKTEST CONCLUIDO ===")

except Exception:
    log("\n!!! ERRO !!!")
    log(traceback.format_exc())

finally:
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))
    print(f"Output salvo em: {OUT}")
    print("Linhas:", len(log_lines))
