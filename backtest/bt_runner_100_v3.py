"""
bt_runner_100_v3.py
===================
Backtest v3 — conta $100 — correcoes aplicadas:
  [FIX 1] Kalman threshold: 0.5 -> 0.6 + confirmacao de tendencia (kalman_filter.py)
  [FIX 2] Filtro confianca minima: 0.15 (signal_aggregator.py)
  [FIX 3] XAGUSD removido, XAUUSD+ com parametros reais do broker
  [FIX 4] SL/TP baseado em ATR14 real por simbolo (nao hardcoded)
  [FIX 5] Risco fixo 1% NAV por trade com sizing correto

Objetivo: reduzir trades de ~778 para ~250-300 mantendo edge positivo.
"""
import sys, os, warnings, traceback
warnings.filterwarnings("ignore")
sys.path.insert(0, r"C:\Users\ACS\Downloads\Trade\vantage_quant")

OUT_LOG    = r"C:\Users\ACS\Downloads\Trade\vantage_quant\logs\bt100v3_output.txt"
OUT_TRADES = r"C:\Users\ACS\Downloads\Trade\vantage_quant\logs\bt100v3_trades.csv"
OUT_EQUITY = r"C:\Users\ACS\Downloads\Trade\vantage_quant\logs\bt100v3_equity.csv"
os.makedirs(os.path.dirname(OUT_LOG), exist_ok=True)

lines = []
def log(m=""): lines.append(str(m)); print(str(m))

try:
    import numpy as np
    import pandas as pd
    from datetime import datetime
    from dotenv import load_dotenv
    import MetaTrader5 as mt5
    from signals.signal_aggregator import SignalAggregator

    load_dotenv(r"C:\Users\ACS\Downloads\Trade\vantage_quant\config\.env")

    CAPITAL      = 100.0
    RISK_PCT     = 0.01       # 1% NAV por trade
    MIN_LOT      = 0.01
    MAX_LOT      = 0.10
    COMM_LOT     = 1.50       # half-turn RAW ECN
    N_BARS       = 5000

    CANDIDATES = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
                  "USDCAD", "NZDUSD", "XAUUSD+"]

    log("=" * 62)
    log("  VANTAGE QUANT — BACKTEST v3 — CONTA $100")
    log(f"  {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC")
    log("  CORRECOES v3:")
    log("    [1] Kalman threshold 0.5->0.6 + confirmacao tendencia")
    log("    [2] Filtro confianca minima 0.15")
    log("    [3] XAGUSD removido / XAUUSD+ calibrado")
    log("    [4] SL/TP por ATR14 real / Risco 1% NAV")
    log("=" * 62)

    ok = mt5.initialize(server="VantageMarkets-Demo", login=26018171,
                        password=os.getenv("MT5_PASSWORD","!y2%U2dD"))
    if not ok:
        raise RuntimeError(f"MT5 falhou: {mt5.last_error()}")
    acc = mt5.account_info()
    log(f"\nMT5 | {acc.server} | Balance=${acc.balance:.2f}")

    # ── 1. PARAMETROS E DADOS ────────────────────────────────────
    log("\n[1/4] Parametros reais do broker + dados H1...")

    asset_cfg  = {}
    price_dict = {}

    for sym in CANDIDATES:
        mt5.symbol_select(sym, True)
        info = mt5.symbol_info(sym)
        tick = mt5.symbol_info_tick(sym)
        if not info or not tick or tick.bid == 0:
            log(f"  SKIP {sym}"); continue

        pip_size  = info.point * (10 if info.digits in (5, 3) else 1)
        if info.digits <= 2:
            pip_size = info.point
        pip_value = info.trade_tick_value * (pip_size / info.trade_tick_size)

        rates_tmp = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 200)
        if rates_tmp is not None and len(rates_tmp) > 20:
            df_tmp    = pd.DataFrame(rates_tmp)
            atr14     = ((df_tmp["high"] - df_tmp["low"]) / pip_size).tail(14).mean()
            sl_pips   = max(round(atr14 * 0.5), 3)
            tp_pips   = sl_pips * 2
        else:
            sl_pips, tp_pips = 10, 20

        asset_cfg[sym] = dict(pip_size=pip_size, pip_value=pip_value,
                              sl=int(sl_pips), tp=int(tp_pips),
                              vol_min=info.volume_min)

        rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, N_BARS)
        if rates is not None and len(rates) > 500:
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            price_dict[sym] = df.set_index("time")["close"]
            log(f"  {sym:<12} pip={pip_size} pv/lot=${pip_value:.4f} "
                f"SL={sl_pips}p TP={tp_pips}p | {len(price_dict[sym])} barras")
        else:
            log(f"  {sym}: historico insuficiente — removendo")
            del asset_cfg[sym]

    SYMBOLS   = list(asset_cfg.keys())
    prices_df = pd.DataFrame(price_dict).ffill().dropna()
    SYMBOLS   = [s for s in SYMBOLS if s in prices_df.columns]
    log(f"\n  Simbolos: {SYMBOLS}")
    log(f"  Periodo : {prices_df.index[0].date()} a {prices_df.index[-1].date()}")
    log(f"  Barras  : {len(prices_df)}")

    # ── 2. SINAIS (com correcoes aplicadas nos arquivos fonte) ────
    log("\n[2/4] Gerando sinais (Kalman v3 + confianca >= 0.15)...")

    HEADLINES = [
        ["Fed holds rates amid soft data",         "EUR edges higher"],
        ["BoJ rate hike imminent",                 "JPY strengthens sharply"],
        ["Iran threatens Hormuz closure",          "Oil spikes, safe haven bid"],
        ["PLA exercises near Taiwan strait",       "Gold surges on risk-off"],
        ["German fiscal expansion boosts EUR",     "EUR/USD bullish outlook"],
        ["Fed emergency meeting speculation",      "Dollar weakens broadly"],
        ["Middle East ceasefire progress",         "Risk appetite improves"],
        ["OPEC+ output cut surprise",              "CAD AUD rally on oil"],
        ["US CPI above expectations",              "Fed hawkish repricing"],
        ["China stimulus package announced",       "AUD NZD outperform"],
        ["BoJ FX intervention warning",            "JPY volatility spikes"],
        ["Gold breaks record on USD weakness",     "XAU demand surges"],
    ]

    WARMUP = 500
    aggs = {}
    for sym in SYMBOLS:
        alias = sym.replace("+","").replace(".crp","")
        aggs[sym] = SignalAggregator(alias)
        aggs[sym].fit_garch(prices_df[sym].iloc[:WARMUP])

    sig_d  = {s: ["FLAT"] * len(prices_df) for s in SYMBOLS}
    scl_d  = {s: [1.0]   * len(prices_df) for s in SYMBOLS}
    wpr_d  = {s: [0.5]   * len(prices_df) for s in SYMBOLS}
    conf_d = {s: [0.0]   * len(prices_df) for s in SYMBOLS}

    for i in range(WARMUP, len(prices_df)):
        hl = HEADLINES[i % len(HEADLINES)]
        for sym in SYMBOLS:
            hist    = prices_df[sym].iloc[max(0, i-500):i]
            current = float(prices_df[sym].iloc[i])
            pkt = aggs[sym].evaluate(current, hist, hl)
            sig_d[sym][i]  = pkt.direction
            scl_d[sym][i]  = pkt.size_scalar
            wpr_d[sym][i]  = max(pkt.confidence, 0.51) if pkt.direction != "FLAT" else 0.5
            conf_d[sym][i] = pkt.confidence

    signals_df = pd.DataFrame(sig_d,  index=prices_df.index)
    scalars_df = pd.DataFrame(scl_d,  index=prices_df.index)
    winprb_df  = pd.DataFrame(wpr_d,  index=prices_df.index)

    log(f"\n  Distribuicao de sinais (estrategia v3):")
    log(f"  {'SIMBOLO':<12} {'BUY':>6} {'SELL':>6} {'FLAT':>6}  {'SINAL%':>7}  {'vs v2':>8}")
    v2_pct = 46.5   # referencia do backtest anterior
    for sym in SYMBOLS:
        c    = signals_df[sym].value_counts().to_dict()
        buy  = c.get("BUY",0); sell = c.get("SELL",0); flat = c.get("FLAT",0)
        pct  = (buy+sell)/len(signals_df)*100
        delta = pct - v2_pct
        log(f"  {sym:<12} {buy:>6} {sell:>6} {flat:>6}  {pct:>6.1f}%  {delta:>+7.1f}%")

    total_signals = sum(
        signals_df[s].isin(["BUY","SELL"]).sum() for s in SYMBOLS
    )
    log(f"\n  Total sinais gerados: {total_signals} "
        f"(v2 era ~{778*2} contando ambos os lados)")

    # ── 3. BACKTEST ───────────────────────────────────────────────
    log("\n[3/4] Rodando backtest barra a barra...")

    from dataclasses import dataclass
    from typing import List

    @dataclass
    class Trade:
        symbol: str; direction: str
        entry_time: object; exit_time: object
        entry_price: float; exit_price: float
        volume: float; pips: float
        gross_pnl: float; commission: float
        net_pnl: float; reason_out: str
        sl_pips: int; tp_pips: int
        pip_value: float; confidence: float

    nav      = CAPITAL
    peak_nav = CAPITAL
    trades: List[Trade] = []
    equity   = [CAPITAL]
    open_pos = {}

    for i in range(WARMUP, len(prices_df)):
        ts = prices_df.index[i]
        for sym in SYMBOLS:
            cfg   = asset_cfg[sym]
            price = float(prices_df[sym].iloc[i])
            sig   = signals_df[sym].iloc[i]
            scl   = scalars_df[sym].iloc[i]
            wp    = winprb_df[sym].iloc[i]
            cf    = conf_d[sym][i]

            # Gerenciar posicao aberta
            if sym in open_pos:
                pos     = open_pos[sym]
                diff    = price - pos["entry"]
                if pos["direction"] == "SELL": diff = -diff
                pips_pl = diff / cfg["pip_size"]
                closed  = False; reason_out = ""
                if pips_pl >= pos["tp"]:
                    reason_out = "TP"; closed = True
                elif pips_pl <= -pos["sl"]:
                    reason_out = "SL"; closed = True
                elif sig not in ["FLAT", pos["direction"]]:
                    reason_out = "Reversao"; closed = True
                if closed:
                    gross  = pips_pl * pos["vol"] * cfg["pip_value"]
                    comm   = pos["vol"] * COMM_LOT * 2
                    net    = gross - comm
                    nav   += net
                    peak_nav = max(peak_nav, nav)
                    equity.append(nav)
                    trades.append(Trade(
                        symbol=sym, direction=pos["direction"],
                        entry_time=pos["time"], exit_time=ts,
                        entry_price=pos["entry"], exit_price=price,
                        volume=pos["vol"], pips=round(pips_pl,1),
                        gross_pnl=round(gross,4), commission=round(comm,4),
                        net_pnl=round(net,4), reason_out=reason_out,
                        sl_pips=pos["sl"], tp_pips=pos["tp"],
                        pip_value=cfg["pip_value"], confidence=pos["conf"],
                    ))
                    del open_pos[sym]

            # Abrir nova posicao
            elif sig in ["BUY","SELL"] and sym not in open_pos:
                if nav < 5.0: continue
                sl_p     = cfg["sl"]; tp_p = cfg["tp"]
                risk_usd = nav * RISK_PCT * scl
                lots     = risk_usd / max(sl_p * cfg["pip_value"], 1e-6)
                lots     = round(max(MIN_LOT, min(MAX_LOT, lots)), 2)
                cost     = lots * COMM_LOT
                nav     -= cost
                open_pos[sym] = dict(direction=sig, entry=price, vol=lots,
                                     time=ts, sl=sl_p, tp=tp_p, conf=cf)

    # Fechar remanescentes
    for sym, pos in list(open_pos.items()):
        cfg   = asset_cfg[sym]
        price = float(prices_df[sym].iloc[-1])
        diff  = price - pos["entry"]
        if pos["direction"] == "SELL": diff = -diff
        pips_pl = diff / cfg["pip_size"]
        gross   = pips_pl * pos["vol"] * cfg["pip_value"]
        comm    = pos["vol"] * COMM_LOT * 2
        net     = gross - comm
        nav    += net
        trades.append(Trade(
            symbol=sym, direction=pos["direction"],
            entry_time=pos["time"], exit_time=prices_df.index[-1],
            entry_price=pos["entry"], exit_price=price,
            volume=pos["vol"], pips=round(pips_pl,1),
            gross_pnl=round(gross,4), commission=round(comm,4),
            net_pnl=round(net,4), reason_out="EOD",
            sl_pips=pos["sl"], tp_pips=pos["tp"],
            pip_value=cfg["pip_value"], confidence=pos["conf"],
        ))
    equity.append(nav)

    # ── 4. METRICAS E RELATORIO ───────────────────────────────────
    eq        = pd.Series(equity)
    rets      = eq.pct_change().dropna()
    net_pnls  = [t.net_pnl for t in trades]
    wins      = [p for p in net_pnls if p > 0]
    losses    = [p for p in net_pnls if p <= 0]
    gross_pnl = sum(t.gross_pnl for t in trades)
    total_comm= sum(t.commission for t in trades)
    net_pnl   = sum(net_pnls)
    win_rate  = len(wins)/len(trades) if trades else 0
    pf        = sum(wins)/abs(sum(losses)) if losses and sum(losses)!=0 else 0
    sharpe    = rets.mean()/rets.std()*np.sqrt(252*24) if rets.std()>0 else 0
    neg       = rets[rets<0]
    sortino   = rets.mean()/neg.std()*np.sqrt(252*24) if len(neg)>1 else 0
    rolling_mx= eq.cummax()
    dd_series = (eq - rolling_mx)/rolling_mx*100
    max_dd_pct= float(dd_series.min())
    max_dd_usd= float((eq-rolling_mx).min())

    log("\n[4/4] Resultado\n")
    log("=" * 62)
    log("  BACKTEST v3 — $100 — 6 FOREX + XAUUSD+ — DADOS REAIS MT5")
    log("=" * 62)
    log(f"  Periodo          : {prices_df.index[0].date()} a "
        f"{prices_df.index[-1].date()}")
    log(f"  Capital inicial  : ${CAPITAL:.2f}")
    log(f"  Capital final    : ${nav:.4f}")
    log(f"  Net PnL          : ${net_pnl:+.4f}")
    log(f"  Retorno total    : {net_pnl/CAPITAL*100:+.2f}%")
    log(f"  Gross PnL        : ${gross_pnl:+.4f}")
    log(f"  Total comissoes  : ${total_comm:.4f}")
    log(f"  Comissao/Gross   : {abs(total_comm/gross_pnl*100) if gross_pnl else 0:.1f}%")
    log(f"  Total trades     : {len(trades)}  (v2: 778)")
    log(f"  Winning          : {len(wins)} ({win_rate*100:.1f}%)")
    log(f"  Losing           : {len(losses)}")
    log(f"  Profit Factor    : {pf:.4f}")
    log(f"  Sharpe Ratio     : {sharpe:.4f}")
    log(f"  Sortino Ratio    : {sortino:.4f}")
    log(f"  Max Drawdown     : {max_dd_pct:.2f}%  (${max_dd_usd:.4f})")
    if trades:
        log(f"  Melhor trade     : ${max(net_pnls):+.4f}")
        log(f"  Pior trade       : ${min(net_pnls):+.4f}")
        wt = [t.pips for t in trades if t.net_pnl > 0]
        lt = [t.pips for t in trades if t.net_pnl <= 0]
        if wt: log(f"  Avg pips (win)   : {np.mean(wt):.1f}p")
        if lt: log(f"  Avg pips (loss)  : {np.mean(lt):.1f}p")

    log("\n  BREAKDOWN POR SIMBOLO:")
    log(f"  {'SIMBOLO':<12} {'N':>5} {'WIN%':>7} {'NET PNL':>10} "
        f"{'SL':>5} {'TP':>5} {'COMISS':>8}  vs v2")
    log("  " + "-" * 64)

    v2_net = {"EURUSD":-38.04,"GBPUSD":1.17,"USDJPY":-19.76,
              "AUDUSD":23.32,"USDCAD":-61.83,"NZDUSD":7.52,"XAUUSD+":1.79}
    for sym in SYMBOLS:
        ts_sym = [t for t in trades if t.symbol == sym]
        if not ts_sym:
            log(f"  {sym:<12}     0  sem trades"); continue
        n    = len(ts_sym)
        w    = sum(1 for t in ts_sym if t.net_pnl > 0)
        net  = sum(t.net_pnl for t in ts_sym)
        comm = sum(t.commission for t in ts_sym)
        sl   = ts_sym[0].sl_pips; tp = ts_sym[0].tp_pips
        diff = net - v2_net.get(sym, 0)
        arrow = "+" if diff > 0 else ""
        log(f"  {sym:<12} {n:>5} {w/n*100:>6.1f}% {net:>+10.4f} "
            f"{sl:>5} {tp:>5} {comm:>8.4f}  {arrow}{diff:+.4f}")

    log("\n  MELHORES TRADES:")
    log(f"  {'SIM':<10} {'DIR':<5} {'LOT':>5} {'PIPS':>8} "
        f"{'NET':>10} {'CONF':>6}  SAIDA")
    log("  " + "-" * 56)
    srt = sorted(trades, key=lambda t: t.net_pnl, reverse=True)
    for t in srt[:8]:
        log(f"  {t.symbol:<10} {t.direction:<5} {t.volume:>5.2f} "
            f"{t.pips:>8.1f} {t.net_pnl:>+10.4f} {t.confidence:>6.3f}  {t.reason_out}")

    log("\n  PIORES TRADES:")
    log(f"  {'SIM':<10} {'DIR':<5} {'LOT':>5} {'PIPS':>8} "
        f"{'NET':>10} {'CONF':>6}  SAIDA")
    log("  " + "-" * 56)
    for t in srt[-8:]:
        log(f"  {t.symbol:<10} {t.direction:<5} {t.volume:>5.2f} "
            f"{t.pips:>8.1f} {t.net_pnl:>+10.4f} {t.confidence:>6.3f}  {t.reason_out}")

    # Curva de equity mensal
    log("\n  EQUITY MENSAL:")
    eq_series = pd.Series(equity, name="equity")
    # timestamps aproximados (1 ponto por trade fechado + inicio)
    if len(trades) > 0:
        monthly = {}
        for t in trades:
            try:
                m = pd.Timestamp(t.exit_time).strftime("%Y-%m")
                if m not in monthly: monthly[m] = []
                monthly[m].append(t.net_pnl)
            except: pass
        running = CAPITAL
        log(f"  {'MES':<10} {'TRADES':>7} {'PNL MES':>10} {'NAV':>10}")
        log("  " + "-" * 40)
        for m in sorted(monthly.keys()):
            mpnl = sum(monthly[m])
            running += mpnl
            log(f"  {m:<10} {len(monthly[m]):>7} {mpnl:>+10.4f} {running:>10.4f}")

    # CSV
    tdf = pd.DataFrame([vars(t) for t in trades])
    tdf.to_csv(OUT_TRADES, index=False)
    eq_series.to_csv(OUT_EQUITY, header=["equity"])
    log(f"\n  Trades: {OUT_TRADES}")
    log(f"  Equity: {OUT_EQUITY}")

    mt5.shutdown()
    log("\n  BACKTEST v3 CONCLUIDO")

except Exception:
    log("\n!!! ERRO !!!")
    log(traceback.format_exc())
finally:
    open(OUT_LOG,"w",encoding="utf-8").write("\n".join(lines))
    print(f"\nLog: {OUT_LOG}")
