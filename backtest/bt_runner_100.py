"""
bt_runner_100.py
================
Backtest calibrado para conta real de $100.
- Capital: $100
- Micro-lotes: 0.01L minimo
- Pip values corretos por simbolo (JPY, metais, crypto)
- Kelly fracionario cap 2% = $2 max risk por trade
- SL/TP em pips ajustados por tipo de ativo
- RAW ECN: $1.50/lote comissao (round-turn $3.00)
- Dados reais MT5: Nov 2025 -> Set 2026
"""
import sys, os, warnings, traceback
warnings.filterwarnings("ignore")
sys.path.insert(0, r"C:\Users\ACS\Downloads\Trade\vantage_quant")

OUT_LOG    = r"C:\Users\ACS\Downloads\Trade\vantage_quant\logs\bt100_output.txt"
OUT_TRADES = r"C:\Users\ACS\Downloads\Trade\vantage_quant\logs\bt100_trades.csv"
OUT_EQUITY = r"C:\Users\ACS\Downloads\Trade\vantage_quant\logs\bt100_equity.csv"
os.makedirs(os.path.dirname(OUT_LOG), exist_ok=True)

lines = []
def log(m=""): lines.append(str(m)); print(str(m))

try:
    import numpy as np
    import pandas as pd
    from datetime import datetime
    from dotenv import load_dotenv
    import MetaTrader5 as mt5

    load_dotenv(r"C:\Users\ACS\Downloads\Trade\vantage_quant\config\.env")
    from signals.signal_aggregator import SignalAggregator

    # ================================================================
    # CONFIGURACAO DO SISTEMA PARA $100
    # ================================================================

    CAPITAL      = 100.0          # conta real $100
    MAX_KELLY    = 0.02           # 2% max risk = $2 por trade
    MIN_LOT      = 0.01           # micro-lote minimo Vantage
    MAX_LOT      = 0.10           # max 0.10L com $100 (seguranca)
    COMM_PER_LOT = 1.50           # RAW ECN half-turn

    # Configuracao por tipo de ativo
    # pip_size: valor de 1 pip em preco
    # pip_value_per_lot: USD por pip por lote standard
    # sl_pips / tp_pips: stops em pips
    ASSET_CONFIG = {
        # Forex major (cotacao x.xxxxx, pip=0.0001)
        "EURUSD": dict(pip=0.0001, pv_lot=10.0,  sl=15, tp=30),
        "GBPUSD": dict(pip=0.0001, pv_lot=10.0,  sl=15, tp=30),
        "AUDUSD": dict(pip=0.0001, pv_lot=10.0,  sl=15, tp=30),
        "USDCAD": dict(pip=0.0001, pv_lot=10.0,  sl=15, tp=30),
        "NZDUSD": dict(pip=0.0001, pv_lot=10.0,  sl=15, tp=30),
        # JPY (cotacao xxx.xxx, pip=0.01)
        "USDJPY": dict(pip=0.01,   pv_lot=6.25,  sl=15, tp=30),
        # Prata (cotacao xx.xxx, pip=0.01)
        "XAGUSD": dict(pip=0.01,   pv_lot=50.0,  sl=20, tp=40),
        # Crypto (muito volatil — stops maiores, sizing minimo)
        "BTCUSD": dict(pip=1.0,    pv_lot=1.0,   sl=200,tp=400),
        "ETHUSD": dict(pip=0.1,    pv_lot=1.0,   sl=50, tp=100),
    }

    # Apenas ativos forex+prata para este backtest
    # (crypto tem margem muito alta para $100)
    SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "NZDUSD", "XAGUSD"]
    N_BARS  = 5000   # H1 barras

    # ================================================================
    # SIZING CORRETO PARA $100
    # ================================================================
    def kelly_lots(capital, nav, win_prob, rr, scalar, cfg):
        """
        Calcula lotes via Kelly fracionario com risk em USD.
        Risk por trade = nav * kelly_f * scalar
        Lotes = risk_usd / (sl_pips * pip_value_per_lot)
        """
        kelly_f  = win_prob - (1 - win_prob) / max(rr, 0.01)
        kelly_f  = max(min(kelly_f, MAX_KELLY), 0.0) * scalar
        risk_usd = nav * kelly_f                        # ex: $100 * 0.01 = $1.00
        sl_usd   = cfg["sl"] * cfg["pv_lot"]            # risco por lote no SL
        if sl_usd <= 0:
            return MIN_LOT
        lots = risk_usd / sl_usd
        lots = round(max(MIN_LOT, min(MAX_LOT, lots)), 2)
        return lots

    def commission(lots):
        return round(lots * COMM_PER_LOT * 2, 4)       # round-turn

    # ================================================================
    # COLETA DE DADOS MT5
    # ================================================================
    log("=" * 60)
    log("  VANTAGE QUANT — BACKTEST $100 CONTA REAL")
    log(f"  {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC")
    log(f"  Capital: ${CAPITAL:.2f} | Kelly<=2% | SL/TP por ativo")
    log(f"  RAW ECN: $1.50/lote comissao | Min lot: {MIN_LOT}")
    log("=" * 60)

    ok = mt5.initialize(server="VantageMarkets-Demo", login=26018171,
                        password=os.getenv("MT5_PASSWORD","!y2%U2dD"))
    log(f"\nMT5: {'conectado' if ok else 'FALHOU'}")
    if ok:
        acc = mt5.account_info()
        log(f"Conta: {acc.login} | Balance: ${acc.balance:.2f}")

    log("\n[1/4] Coletando dados historicos (H1)...")
    price_dict = {}
    for sym in SYMBOLS:
        mt5.symbol_select(sym, True)
        rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, N_BARS)
        if rates is not None and len(rates) > 500:
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            s = df.set_index("time")["close"]
            price_dict[sym] = s
            log(f"  {sym}: {len(s)} barras reais MT5")
        else:
            log(f"  {sym}: sem dados MT5 — pulando")
            SYMBOLS = [s for s in SYMBOLS if s != sym]

    prices_df = pd.DataFrame(price_dict).ffill().dropna()
    SYMBOLS = list(prices_df.columns)
    log(f"\n  Simbolos finais: {SYMBOLS}")
    log(f"  Periodo: {prices_df.index[0].date()} a {prices_df.index[-1].date()}")
    log(f"  Barras : {len(prices_df)}")

    # ================================================================
    # SINAIS
    # ================================================================
    log("\n[2/4] Gerando sinais (GARCH + Kalman + GeoScore)...")

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

    sig_d = {sym: ["FLAT"] * len(prices_df) for sym in SYMBOLS}
    scl_d = {sym: [1.0]   * len(prices_df) for sym in SYMBOLS}
    wpr_d = {sym: [0.5]   * len(prices_df) for sym in SYMBOLS}

    for i in range(WARMUP, len(prices_df)):
        hl = HEADLINES[i % len(HEADLINES)]
        for sym in SYMBOLS:
            hist    = prices_df[sym].iloc[max(0, i-500):i]
            current = float(prices_df[sym].iloc[i])
            pkt = aggs[sym].evaluate(current, hist, hl)
            sig_d[sym][i] = pkt.direction
            scl_d[sym][i] = pkt.size_scalar
            wpr_d[sym][i] = max(pkt.confidence, 0.51) if pkt.direction != "FLAT" else 0.5

    signals_df = pd.DataFrame(sig_d, index=prices_df.index)
    scalars_df = pd.DataFrame(scl_d, index=prices_df.index)
    winprb_df  = pd.DataFrame(wpr_d, index=prices_df.index)

    log("\n  Distribuicao de sinais:")
    for sym in SYMBOLS:
        c = signals_df[sym].value_counts().to_dict()
        log(f"    {sym}: BUY={c.get('BUY',0):4d} SELL={c.get('SELL',0):4d} FLAT={c.get('FLAT',0):4d}")

    # ================================================================
    # BACKTEST BARRA A BARRA — ENGINE PROPRIA (pip-aware)
    # ================================================================
    log("\n[3/4] Rodando backtest barra a barra (pip-aware)...")

    from dataclasses import dataclass, field
    from typing import List

    @dataclass
    class Trade:
        symbol:      str
        direction:   str
        entry_time:  object
        exit_time:   object
        entry_price: float
        exit_price:  float
        volume:      float
        pips:        float
        gross_pnl:   float
        commission:  float
        net_pnl:     float
        reason_out:  str

    nav       = CAPITAL
    peak_nav  = CAPITAL
    max_dd    = 0.0
    trades: List[Trade] = []
    equity  = [CAPITAL]
    open_pos = {}      # sym -> dict

    for i in range(WARMUP, len(prices_df)):
        ts    = prices_df.index[i]
        for sym in SYMBOLS:
            cfg   = ASSET_CONFIG[sym]
            price = float(prices_df[sym].iloc[i])
            sig   = signals_df[sym].iloc[i]
            scl   = scalars_df[sym].iloc[i]
            wp    = winprb_df[sym].iloc[i]

            # --- Gerenciar posicao aberta ---
            if sym in open_pos:
                pos   = open_pos[sym]
                diff  = price - pos["entry"]
                if pos["direction"] == "SELL":
                    diff = -diff
                pips_pl = diff / cfg["pip"]

                closed     = False
                reason_out = ""
                if pips_pl >= cfg["tp"]:
                    reason_out = "TP"
                    closed = True
                elif pips_pl <= -cfg["sl"]:
                    reason_out = "SL"
                    closed = True
                elif sig not in ["FLAT", pos["direction"]]:
                    reason_out = "Reversao"
                    closed = True

                if closed:
                    gross = pips_pl * pos["vol"] * cfg["pv_lot"]
                    comm  = commission(pos["vol"])
                    net   = gross - comm
                    nav  += net
                    equity.append(nav)
                    peak_nav = max(peak_nav, nav)
                    dd = (peak_nav - nav) / peak_nav * 100
                    max_dd = max(max_dd, dd)
                    trades.append(Trade(
                        symbol=sym, direction=pos["direction"],
                        entry_time=pos["time"], exit_time=ts,
                        entry_price=pos["entry"], exit_price=price,
                        volume=pos["vol"], pips=round(pips_pl,1),
                        gross_pnl=round(gross,4), commission=round(comm,4),
                        net_pnl=round(net,4), reason_out=reason_out
                    ))
                    del open_pos[sym]

            # --- Abrir nova posicao ---
            elif sig in ["BUY","SELL"] and sym not in open_pos:
                # Nao abrir se nav < $10 (margem insuficiente)
                if nav < 10.0:
                    continue
                lots  = kelly_lots(CAPITAL, nav, wp, 2.0, scl, cfg)
                cost  = lots * COMM_PER_LOT   # comissao de entrada
                nav  -= cost
                open_pos[sym] = {
                    "direction": sig,
                    "entry":     price,
                    "vol":       lots,
                    "time":      ts,
                }

        # Equity snapshot a cada barra mesmo sem fechamento
        # (apenas quando ha mudanca — ja capturado nos fechamentos)

    # Fechar posicoes remanescentes no fim
    last_ts = prices_df.index[-1]
    for sym, pos in list(open_pos.items()):
        cfg   = ASSET_CONFIG[sym]
        price = float(prices_df[sym].iloc[-1])
        diff  = price - pos["entry"]
        if pos["direction"] == "SELL":
            diff = -diff
        pips_pl = diff / cfg["pip"]
        gross   = pips_pl * pos["vol"] * cfg["pv_lot"]
        comm    = commission(pos["vol"])
        net     = gross - comm
        nav    += net
        trades.append(Trade(
            symbol=sym, direction=pos["direction"],
            entry_time=pos["time"], exit_time=last_ts,
            entry_price=pos["entry"], exit_price=price,
            volume=pos["vol"], pips=round(pips_pl,1),
            gross_pnl=round(gross,4), commission=round(comm,4),
            net_pnl=round(net,4), reason_out="EOD"
        ))
    equity.append(nav)

    # ================================================================
    # METRICAS
    # ================================================================
    eq = pd.Series(equity)
    rets = eq.pct_change().dropna()

    net_pnls   = [t.net_pnl for t in trades]
    wins       = [p for p in net_pnls if p > 0]
    losses     = [p for p in net_pnls if p <= 0]
    gross_pnl  = sum(t.gross_pnl for t in trades)
    total_comm = sum(t.commission for t in trades)
    net_pnl    = sum(net_pnls)

    win_rate   = len(wins)/len(trades) if trades else 0
    pf         = sum(wins)/abs(sum(losses)) if losses and sum(losses) != 0 else 0
    sharpe     = rets.mean()/rets.std()*np.sqrt(252*24) if rets.std()>0 else 0
    neg        = rets[rets<0]
    sortino    = rets.mean()/neg.std()*np.sqrt(252*24) if len(neg)>1 else 0
    rolling_mx = eq.cummax()
    dd_series  = (eq - rolling_mx)/rolling_mx*100
    max_dd_pct = float(dd_series.min())

    # ================================================================
    # RELATORIO
    # ================================================================
    log("\n[4/4] Resultado\n")
    log("=" * 60)
    log("  BACKTEST REPORT — CONTA $100 — VANTAGE MARKETS DEMO")
    log("=" * 60)
    log(f"  Capital inicial  : ${CAPITAL:,.2f}")
    log(f"  Capital final    : ${nav:,.2f}")
    log(f"  Retorno total    : {net_pnl/CAPITAL*100:+.2f}%")
    log(f"  Net PnL          : ${net_pnl:+,.4f}")
    log(f"  Gross PnL        : ${gross_pnl:+,.4f}")
    log(f"  Total comissoes  : ${total_comm:,.4f}")
    log(f"  Total trades     : {len(trades)}")
    log(f"  Winning          : {len(wins)} ({win_rate*100:.1f}%)")
    log(f"  Losing           : {len(losses)}")
    log(f"  Profit Factor    : {pf:.4f}")
    log(f"  Sharpe Ratio     : {sharpe:.4f}")
    log(f"  Sortino Ratio    : {sortino:.4f}")
    log(f"  Max Drawdown     : {max_dd_pct:.2f}%  (${(max_dd_pct/100)*CAPITAL:.2f})")
    if trades:
        log(f"  Melhor trade     : ${max(net_pnls):+,.4f}")
        log(f"  Pior trade       : ${min(net_pnls):+,.4f}")
        log(f"  Avg pips (win)   : {np.mean([t.pips for t in trades if t.net_pnl>0]):.1f}p")
        log(f"  Avg pips (loss)  : {np.mean([t.pips for t in trades if t.net_pnl<=0]):.1f}p")

    log("\n  BREAKDOWN POR SIMBOLO:")
    log(f"  {'PAR':<10} {'TRADES':>7} {'WIN%':>7} {'NET PNL':>10} {'AVG LOT':>8} {'COMISS':>8}")
    log("  " + "-" * 54)
    for sym in SYMBOLS:
        ts_sym = [t for t in trades if t.symbol == sym]
        if not ts_sym:
            log(f"  {sym:<10}       0"); continue
        n    = len(ts_sym)
        w    = sum(1 for t in ts_sym if t.net_pnl > 0)
        net  = sum(t.net_pnl for t in ts_sym)
        alot = np.mean([t.volume for t in ts_sym])
        comm = sum(t.commission for t in ts_sym)
        log(f"  {sym:<10} {n:>7} {w/n*100:>6.1f}% {net:>+10.4f} {alot:>8.3f} {comm:>8.4f}")

    log("\n  MELHORES TRADES:")
    log(f"  {'PAR':<8} {'DIR':<5} {'LOT':>5} {'PIPS':>7} {'NET PNL':>10}  SAIDA")
    log("  " + "-" * 50)
    srt = sorted(trades, key=lambda t: t.net_pnl, reverse=True)
    for t in srt[:8]:
        log(f"  {t.symbol:<8} {t.direction:<5} {t.volume:>5.2f} {t.pips:>7.1f} {t.net_pnl:>+10.4f}  {t.reason_out}")

    log("\n  PIORES TRADES:")
    log(f"  {'PAR':<8} {'DIR':<5} {'LOT':>5} {'PIPS':>7} {'NET PNL':>10}  SAIDA")
    log("  " + "-" * 50)
    for t in srt[-8:]:
        log(f"  {t.symbol:<8} {t.direction:<5} {t.volume:>5.2f} {t.pips:>7.1f} {t.net_pnl:>+10.4f}  {t.reason_out}")

    # ================================================================
    # ANALISE DE VIABILIDADE PARA $100
    # ================================================================
    log("\n  ANALISE DE VIABILIDADE PARA CONTA $100:")
    log(f"  Risk por trade (Kelly 2%): ~${CAPITAL*0.02:.2f}")
    log(f"  Lote tipico EURUSD       : ~0.01L (SL 15p = ${0.01*10.0*15:.2f} risco)")
    log(f"  Comissao round-turn 0.01L: ${0.01*COMM_PER_LOT*2:.2f}")
    pct_comm = (total_comm / max(abs(gross_pnl),0.01))*100 if gross_pnl else 0
    log(f"  Comissao / Gross PnL     : {pct_comm:.1f}%")
    log(f"  Trades com volume 0.01L  : {sum(1 for t in trades if t.volume<=0.01)}/{len(trades)}")

    # ================================================================
    # CSV
    # ================================================================
    tdf = pd.DataFrame([{
        "symbol": t.symbol, "direction": t.direction,
        "entry_time": t.entry_time, "exit_time": t.exit_time,
        "entry_price": t.entry_price, "exit_price": t.exit_price,
        "volume": t.volume, "pips": t.pips,
        "gross_pnl": t.gross_pnl, "commission": t.commission,
        "net_pnl": t.net_pnl, "reason_out": t.reason_out,
    } for t in trades])
    tdf.to_csv(OUT_TRADES, index=False)
    eq.to_csv(OUT_EQUITY, header=["equity"])

    log(f"\n  Trades CSV : {OUT_TRADES}")
    log(f"  Equity CSV : {OUT_EQUITY}")

    if ok: mt5.shutdown()
    log("\n  BACKTEST CONCLUIDO")

except Exception:
    log("\n!!! ERRO !!!")
    log(traceback.format_exc())
finally:
    open(OUT_LOG, "w", encoding="utf-8").write("\n".join(lines))
    print(f"\nLog salvo: {OUT_LOG}")
