"""
bt_runner_100_v2.py
===================
Backtest calibrado para conta real $100.
Simbolos: 6 forex majors + XAUUSD+
XAGUSD removido.

Calibracao real por ativo (valores direto do broker MT5):
  - pip_size e pip_value extraidos de symbol_info (nao hardcoded)
  - SL/TP em pips ajustados ao range real H1 de cada ativo
    XAUUSD+ range medio H1 = 1838 pips -> SL 800p / TP 1600p
    EURUSD  range medio H1 = 8 pips    -> SL 12p  / TP 24p
    USDJPY  range medio H1 = 15 pips   -> SL 12p  / TP 24p
  - Sizing: risco fixo 1% do NAV por trade (mais conservador que Kelly puro)
  - Comissao RAW ECN: $1.50/lote (round-turn $3.00)
"""
import sys, os, warnings, traceback
warnings.filterwarnings("ignore")
sys.path.insert(0, r"C:\Users\ACS\Downloads\Trade\vantage_quant")

OUT_LOG    = r"C:\Users\ACS\Downloads\Trade\vantage_quant\logs\bt100v2_output.txt"
OUT_TRADES = r"C:\Users\ACS\Downloads\Trade\vantage_quant\logs\bt100v2_trades.csv"
OUT_EQUITY = r"C:\Users\ACS\Downloads\Trade\vantage_quant\logs\bt100v2_equity.csv"
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

    # ================================================================
    # CONFIGURACAO BASE
    # ================================================================
    CAPITAL      = 100.0
    RISK_PCT     = 0.01      # 1% do NAV por trade = $1.00 inicial
    MIN_LOT      = 0.01
    MAX_LOT      = 0.10
    COMM_LOT     = 1.50      # half-turn
    N_BARS       = 5000      # barras H1

    # Candidatos — estrategia decide quais tem sinal, nao nos
    CANDIDATES = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
                  "USDCAD", "NZDUSD", "XAUUSD+"]

    # ================================================================
    # CONECTAR MT5 E COLETAR PARAMETROS REAIS
    # ================================================================
    log("=" * 62)
    log("  VANTAGE QUANT — BACKTEST v2 — CONTA $100")
    log(f"  {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC")
    log(f"  Capital: ${CAPITAL:.2f} | Risk/trade: {RISK_PCT*100:.0f}% NAV")
    log(f"  Candidatos: {CANDIDATES}")
    log("=" * 62)

    ok = mt5.initialize(server="VantageMarkets-Demo", login=26018171,
                        password=os.getenv("MT5_PASSWORD","!y2%U2dD"))
    if ok:
        acc = mt5.account_info()
        log(f"\nMT5 conectado | {acc.server} | Balance=${acc.balance:.2f}")
    else:
        raise RuntimeError(f"MT5 falhou: {mt5.last_error()}")

    # ================================================================
    # DESCOBERTA AUTOMATICA DE PARAMETROS POR SIMBOLO
    # ================================================================
    log("\n[1/4] Descobrindo parametros e coletando dados reais...")

    asset_cfg  = {}   # parametros por simbolo
    price_dict = {}   # Series de close H1

    for sym in CANDIDATES:
        mt5.symbol_select(sym, True)
        info = mt5.symbol_info(sym)
        tick = mt5.symbol_info_tick(sym)

        if not info or not tick or tick.bid == 0:
            log(f"  SKIP {sym}: sem tick")
            continue

        # Pip size real do broker
        pip_size  = info.point * (10 if info.digits in (5, 3) else 1)
        # Para XAUUSD+ digits=2 e point=0.01 -> pip=0.01 (1 pip = $0.01 de preco)
        # Correcao especial: se digits<=2, pip = point (sem multiplicar por 10)
        if info.digits <= 2:
            pip_size = info.point

        # Pip value real (USD por pip por lote standard)
        pip_value = info.trade_tick_value * (pip_size / info.trade_tick_size)

        # SL/TP baseado no range real H1 do ativo
        # Coleta 500 barras para calcular ATR(14) como referencia de SL
        rates_tmp = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 200)
        if rates_tmp is not None and len(rates_tmp) > 20:
            df_tmp = pd.DataFrame(rates_tmp)
            # ATR simples = media de range H1 ultimas 14 barras
            ranges = (df_tmp["high"] - df_tmp["low"]) / pip_size
            atr14  = ranges.tail(14).mean()
            # SL = 0.5 * ATR14, TP = 1.0 * ATR14 (R:R = 2:1)
            sl_pips = max(round(atr14 * 0.5), 5)
            tp_pips = sl_pips * 2
        else:
            # Fallback conservador
            sl_pips, tp_pips = 12, 24

        # Risco real por trade em USD (1% NAV)
        # lots = risk_usd / (sl_pips * pip_value_per_lot)
        risk_usd      = CAPITAL * RISK_PCT
        lots_for_risk = risk_usd / max(sl_pips * pip_value, 0.001)
        lots_for_risk = round(max(MIN_LOT, min(MAX_LOT, lots_for_risk)), 2)

        asset_cfg[sym] = {
            "pip_size":  pip_size,
            "pip_value": pip_value,       # por lote standard
            "sl":        int(sl_pips),
            "tp":        int(tp_pips),
            "vol_min":   info.volume_min,
            "vol_step":  info.volume_step,
        }

        log(f"\n  {sym}")
        log(f"    bid={tick.bid:.{info.digits}f}  pip_size={pip_size}  pip_value/lot=${pip_value:.4f}")
        log(f"    ATR14={atr14:.0f}p  SL={sl_pips}p  TP={tp_pips}p")
        log(f"    Risco 1% NAV=$1.00 -> {lots_for_risk}L  "
            f"(SL=${ sl_pips*pip_value*lots_for_risk:.4f})")

        # Coleta historico completo
        rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, N_BARS)
        if rates is not None and len(rates) > 500:
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            price_dict[sym] = df.set_index("time")["close"]
            log(f"    H1 barras: {len(price_dict[sym])}")
        else:
            log(f"    Sem historico suficiente — removendo")
            del asset_cfg[sym]

    SYMBOLS = list(asset_cfg.keys())
    prices_df = pd.DataFrame(price_dict).ffill().dropna()
    SYMBOLS = [s for s in SYMBOLS if s in prices_df.columns]

    log(f"\n  Simbolos com dados: {SYMBOLS}")
    log(f"  Periodo: {prices_df.index[0].date()} a {prices_df.index[-1].date()}")
    log(f"  Barras : {len(prices_df)}")

    # ================================================================
    # SINAIS — ESTRATEGIA DECIDE (nao nos)
    # ================================================================
    log("\n[2/4] Gerando sinais via SignalAggregator...")

    # Headlines rotativas refletindo macro ago/set 2026
    HEADLINES = [
        ["Fed holds rates steady amid soft data", "EUR edges higher"],
        ["BoJ rate hike imminent", "JPY strengthens sharply"],
        ["Iran threatens Hormuz closure", "Oil spikes on supply fears"],
        ["PLA military exercises near Taiwan", "Safe haven demand rises"],
        ["German fiscal expansion boosts EUR", "EUR/USD bullish"],
        ["Fed emergency meeting speculation", "Dollar weakens broadly"],
        ["Middle East ceasefire progress", "Risk appetite improves"],
        ["OPEC+ output cut surprise", "Commodity currencies rally"],
        ["US CPI above expectations", "Fed hawkish repricing"],
        ["China stimulus package announced", "AUD NZD rally"],
        ["BoJ intervention warning", "JPY volatility spikes"],
        ["Gold demand surges on uncertainty", "XAU breaks resistance"],
    ]

    WARMUP = 500
    aggs = {}
    for sym in SYMBOLS:
        # SignalAggregator usa o nome sem "+" internamente — alias para XAUUSD+
        agg_sym = sym.replace("+","").replace(".crp","")
        aggs[sym] = SignalAggregator(agg_sym)
        aggs[sym].fit_garch(prices_df[sym].iloc[:WARMUP])
        log(f"  GARCH fitado: {sym} (alias={agg_sym})")

    sig_d = {s: ["FLAT"] * len(prices_df) for s in SYMBOLS}
    scl_d = {s: [1.0]   * len(prices_df) for s in SYMBOLS}
    wpr_d = {s: [0.5]   * len(prices_df) for s in SYMBOLS}
    conf_d= {s: [0.0]   * len(prices_df) for s in SYMBOLS}

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

    log("\n  Distribuicao de sinais (estrategia):")
    log(f"  {'SIMBOLO':<12} {'BUY':>6} {'SELL':>6} {'FLAT':>6}  {'SINAL%':>7}")
    for sym in SYMBOLS:
        c    = signals_df[sym].value_counts().to_dict()
        buy  = c.get("BUY", 0)
        sell = c.get("SELL", 0)
        flat = c.get("FLAT", 0)
        pct  = (buy+sell) / len(signals_df) * 100
        log(f"  {sym:<12} {buy:>6} {sell:>6} {flat:>6}  {pct:>6.1f}%")

    # ================================================================
    # BACKTEST BARRA A BARRA
    # ================================================================
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

            # --- Gerenciar posicao aberta ---
            if sym in open_pos:
                pos     = open_pos[sym]
                diff    = price - pos["entry"]
                if pos["direction"] == "SELL":
                    diff = -diff
                pips_pl = diff / cfg["pip_size"]

                closed = False; reason_out = ""
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
                        volume=pos["vol"], pips=round(pips_pl, 1),
                        gross_pnl=round(gross, 4), commission=round(comm, 4),
                        net_pnl=round(net, 4), reason_out=reason_out,
                        sl_pips=pos["sl"], tp_pips=pos["tp"],
                        pip_value=cfg["pip_value"], confidence=pos["conf"],
                    ))
                    del open_pos[sym]

            # --- Abrir nova posicao ---
            elif sig in ["BUY", "SELL"] and sym not in open_pos:
                if nav < 5.0:
                    continue

                sl_p = cfg["sl"]
                tp_p = cfg["tp"]

                # Sizing: risco fixo 1% NAV / (sl * pip_value)
                risk_usd = nav * RISK_PCT * scl
                lots = risk_usd / max(sl_p * cfg["pip_value"], 1e-6)
                lots = round(max(MIN_LOT, min(MAX_LOT, lots)), 2)

                # Custo de entrada (comissao)
                cost = lots * COMM_LOT
                nav -= cost

                open_pos[sym] = {
                    "direction": sig, "entry": price,
                    "vol": lots, "time": ts,
                    "sl": sl_p, "tp": tp_p, "conf": cf,
                }

    # Fechar posicoes restantes
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
            volume=pos["vol"], pips=round(pips_pl, 1),
            gross_pnl=round(gross, 4), commission=round(comm, 4),
            net_pnl=round(net, 4), reason_out="EOD",
            sl_pips=pos["sl"], tp_pips=pos["tp"],
            pip_value=cfg["pip_value"], confidence=pos["conf"],
        ))
    equity.append(nav)

    # ================================================================
    # METRICAS
    # ================================================================
    eq       = pd.Series(equity)
    rets     = eq.pct_change().dropna()
    net_pnls = [t.net_pnl for t in trades]
    wins     = [p for p in net_pnls if p > 0]
    losses   = [p for p in net_pnls if p <= 0]
    gross_pnl   = sum(t.gross_pnl for t in trades)
    total_comm  = sum(t.commission for t in trades)
    net_pnl     = sum(net_pnls)
    win_rate    = len(wins)/len(trades) if trades else 0
    pf          = sum(wins)/abs(sum(losses)) if losses and sum(losses) != 0 else 0
    sharpe      = rets.mean()/rets.std()*np.sqrt(252*24) if rets.std()>0 else 0
    neg         = rets[rets<0]
    sortino     = rets.mean()/neg.std()*np.sqrt(252*24) if len(neg)>1 else 0
    rolling_mx  = eq.cummax()
    dd_series   = (eq - rolling_mx)/rolling_mx*100
    max_dd_pct  = float(dd_series.min())
    max_dd_usd  = float((eq - rolling_mx).min())

    # ================================================================
    # RELATORIO
    # ================================================================
    log("\n[4/4] Resultado\n")
    log("=" * 62)
    log("  BACKTEST v2 — $100 — 6 FOREX + XAUUSD+ — DADOS REAIS MT5")
    log("=" * 62)
    log(f"  Periodo          : {prices_df.index[0].date()} a {prices_df.index[-1].date()}")
    log(f"  Capital inicial  : ${CAPITAL:.2f}")
    log(f"  Capital final    : ${nav:.4f}")
    log(f"  Net PnL          : ${net_pnl:+.4f}")
    log(f"  Retorno total    : {net_pnl/CAPITAL*100:+.2f}%")
    log(f"  Gross PnL        : ${gross_pnl:+.4f}")
    log(f"  Total comissoes  : ${total_comm:.4f}")
    log(f"  Total trades     : {len(trades)}")
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
        log(f"  Avg pips (win)   : {np.mean(wt):.1f}p")
        log(f"  Avg pips (loss)  : {np.mean(lt):.1f}p")

    log("\n  BREAKDOWN POR SIMBOLO:")
    log(f"  {'SIMBOLO':<12} {'N':>5} {'WIN%':>7} {'NET PNL':>10} "
        f"{'AVG LOT':>8} {'SL':>5} {'TP':>5} {'COMISS':>8}")
    log("  " + "-" * 64)
    for sym in SYMBOLS:
        ts_sym = [t for t in trades if t.symbol == sym]
        if not ts_sym:
            log(f"  {sym:<12}     0  sem trades"); continue
        n    = len(ts_sym)
        w    = sum(1 for t in ts_sym if t.net_pnl > 0)
        net  = sum(t.net_pnl for t in ts_sym)
        alot = np.mean([t.volume for t in ts_sym])
        comm = sum(t.commission for t in ts_sym)
        sl   = ts_sym[0].sl_pips
        tp   = ts_sym[0].tp_pips
        log(f"  {sym:<12} {n:>5} {w/n*100:>6.1f}% {net:>+10.4f} "
            f"{alot:>8.3f} {sl:>5} {tp:>5} {comm:>8.4f}")

    log("\n  MELHORES TRADES:")
    log(f"  {'SIM':<10} {'DIR':<5} {'LOT':>5} {'PIPS':>8} "
        f"{'NET':>10} {'CONF':>6}  SAIDA")
    log("  " + "-" * 58)
    srt = sorted(trades, key=lambda t: t.net_pnl, reverse=True)
    for t in srt[:8]:
        log(f"  {t.symbol:<10} {t.direction:<5} {t.volume:>5.2f} {t.pips:>8.1f} "
            f"{t.net_pnl:>+10.4f} {t.confidence:>6.3f}  {t.reason_out}")

    log("\n  PIORES TRADES:")
    log(f"  {'SIM':<10} {'DIR':<5} {'LOT':>5} {'PIPS':>8} "
        f"{'NET':>10} {'CONF':>6}  SAIDA")
    log("  " + "-" * 58)
    for t in srt[-8:]:
        log(f"  {t.symbol:<10} {t.direction:<5} {t.volume:>5.2f} {t.pips:>8.1f} "
            f"{t.net_pnl:>+10.4f} {t.confidence:>6.3f}  {t.reason_out}")

    log("\n  XAUUSD+ ANALISE ESPECIFICA:")
    xau_trades = [t for t in trades if t.symbol == "XAUUSD+"]
    if xau_trades:
        log(f"  Trades        : {len(xau_trades)}")
        log(f"  Win rate      : {sum(1 for t in xau_trades if t.net_pnl>0)/len(xau_trades)*100:.1f}%")
        log(f"  Net PnL       : ${sum(t.net_pnl for t in xau_trades):+.4f}")
        log(f"  SL/TP         : {xau_trades[0].sl_pips}p / {xau_trades[0].tp_pips}p")
        log(f"  pip_value/lot : ${xau_trades[0].pip_value:.4f}")
        log(f"  Risco/trade   : ~${xau_trades[0].sl_pips * xau_trades[0].pip_value * xau_trades[0].volume:.4f}")
    else:
        log("  Nenhum trade executado em XAUUSD+")

    # ================================================================
    # CSV
    # ================================================================
    tdf = pd.DataFrame([vars(t) for t in trades])
    tdf.to_csv(OUT_TRADES, index=False)
    eq.to_csv(OUT_EQUITY, header=["equity"])
    log(f"\n  Trades: {OUT_TRADES}")
    log(f"  Equity: {OUT_EQUITY}")

    mt5.shutdown()
    log("\n  BACKTEST v2 CONCLUIDO")

except Exception:
    log("\n!!! ERRO !!!")
    log(traceback.format_exc())
finally:
    open(OUT_LOG,"w",encoding="utf-8").write("\n".join(lines))
    print(f"\nLog: {OUT_LOG}")
