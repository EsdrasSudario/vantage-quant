"""
bt_runner_100_v4.py
===================
Backtest v4 — conta $100 — fiel ao sistema real

Mudancas vs v3:
  [v4-1] Comissao via core/costs.py (RAW ECN $6.00/lot round turn)
  [v4-2] SL=20p, TP=40p fixos — igual ao main.py
  [v4-3] Kelly identico ao KellySizer do OrderManager (cap 2% NAV)
  [v4-4] Simbolos alinhados com conta demo Vantage (sem sufixo exceto XAUUSD+)
  [v4-5] Paths corrigidos para usuario atual
"""
import sys, os, warnings, traceback
warnings.filterwarnings("ignore")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT_LOG    = os.path.join(ROOT, "logs", "bt100v4_output.txt")
OUT_TRADES = os.path.join(ROOT, "logs", "bt100v4_trades.csv")
OUT_EQUITY = os.path.join(ROOT, "logs", "bt100v4_equity.csv")
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
    from core.costs import get_commission_per_lot

    load_dotenv(os.path.join(ROOT, "config", ".env"))

    # ── Parametros identicos ao main.py ──────────────────────────────
    CAPITAL        = 100.0
    SL_PIPS        = 20          # fixo, igual ao main.py
    TP_PIPS        = 40          # fixo, igual ao main.py
    WIN_LOSS_RATIO = 2.0         # R:R usado no Kelly (main.py linha 243)
    KELLY_CAP      = 0.02        # max_fraction do KellySizer
    MIN_LOT        = 0.01
    MAX_LOT        = 10.0
    COMM_ROUND     = get_commission_per_lot()   # $6.00 para RAW ECN
    N_BARS         = 5000
    WARMUP         = 500

    # Simbolos alinhados com a conta demo (verificado via check_symbols.py)
    # Principais: sem sufixo | XAUUSD: apenas com +
    CANDIDATES = [
        "EURUSD", "GBPUSD", "USDJPY",
        "AUDUSD", "NZDUSD", "USDCAD",
        "XAUUSD+",
    ]

    log("=" * 66)
    log("  VANTAGE QUANT — BACKTEST v4 — CONTA $100")
    log(f"  {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC")
    log(f"  Conta     : {os.getenv('ACCOUNT_TYPE','RAW_ECN')}")
    log(f"  Comissao  : ${COMM_ROUND:.2f}/lot round turn")
    log(f"  SL/TP     : {SL_PIPS}p / {TP_PIPS}p (fixos, igual main.py)")
    log(f"  Kelly cap : {KELLY_CAP*100:.0f}% NAV")
    log("  [v4-1] Comissao real via core/costs.py")
    log("  [v4-2] SL/TP fixos identicos ao main.py")
    log("  [v4-3] Kelly identico ao KellySizer do OrderManager")
    log("  [v4-4] Simbolos alinhados com conta demo Vantage")
    log("=" * 66)

    ok = mt5.initialize(
        server="VantageMarkets-Demo",
        login=26018171,
        password=os.getenv("MT5_PASSWORD", ""),
    )
    if not ok:
        raise RuntimeError(f"MT5 falhou: {mt5.last_error()}")
    acc = mt5.account_info()
    log(f"\nMT5 | {acc.server} | Balance=${acc.balance:.2f}")

    # ── 1. DADOS HISTORICOS ──────────────────────────────────────────
    log("\n[1/4] Carregando dados H1 + pip values do broker...")

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

        rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, N_BARS)
        if rates is None or len(rates) < WARMUP + 100:
            log(f"  {sym}: historico insuficiente — removendo"); continue

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        price_dict[sym] = df.set_index("time")["close"]
        asset_cfg[sym]  = dict(pip_size=pip_size, pip_value=pip_value)

        log(f"  {sym:<12} pip={pip_size} pv/lot=${pip_value:.4f} "
            f"SL={SL_PIPS}p TP={TP_PIPS}p | {len(price_dict[sym])} barras")

    SYMBOLS   = list(asset_cfg.keys())
    prices_df = pd.DataFrame(price_dict).ffill().dropna()
    SYMBOLS   = [s for s in SYMBOLS if s in prices_df.columns]
    log(f"\n  Simbolos ativos: {SYMBOLS}")
    log(f"  Periodo : {prices_df.index[0].date()} a {prices_df.index[-1].date()}")
    log(f"  Barras  : {len(prices_df)}")

    # ── 2. SINAIS ────────────────────────────────────────────────────
    log("\n[2/4] Gerando sinais via SignalAggregator...")

    # Headlines rotativas (mesmo padrao do bt_runner_100_v3)
    HEADLINES = [
        ["Fed holds rates amid soft data",        "EUR edges higher"],
        ["BoJ rate hike imminent",                "JPY strengthens sharply"],
        ["Iran threatens Hormuz closure",         "Oil spikes, safe haven bid"],
        ["PLA exercises near Taiwan strait",      "Gold surges on risk-off"],
        ["German fiscal expansion boosts EUR",    "EUR/USD bullish outlook"],
        ["Fed emergency meeting speculation",     "Dollar weakens broadly"],
        ["Middle East ceasefire progress",        "Risk appetite improves"],
        ["OPEC+ output cut surprise",             "CAD AUD rally on oil"],
        ["US CPI above expectations",             "Fed hawkish repricing"],
        ["China stimulus package announced",      "AUD NZD outperform"],
        ["BoJ FX intervention warning",           "JPY volatility spikes"],
        ["Gold breaks record on USD weakness",    "XAU demand surges"],
    ]

    aggs = {}
    for sym in SYMBOLS:
        alias = sym.replace("+", "").replace(".crp", "")
        aggs[sym] = SignalAggregator(alias)
        aggs[sym].fit_garch(prices_df[sym].iloc[:WARMUP])

    sig_d  = {s: ["FLAT"] * len(prices_df) for s in SYMBOLS}
    scl_d  = {s: [1.0]   * len(prices_df) for s in SYMBOLS}
    conf_d = {s: [0.0]   * len(prices_df) for s in SYMBOLS}

    for i in range(WARMUP, len(prices_df)):
        hl = HEADLINES[i % len(HEADLINES)]
        for sym in SYMBOLS:
            hist    = prices_df[sym].iloc[max(0, i - 500):i]
            current = float(prices_df[sym].iloc[i])
            pkt = aggs[sym].evaluate(current, hist, hl)
            sig_d[sym][i]  = pkt.direction
            scl_d[sym][i]  = pkt.size_scalar
            conf_d[sym][i] = pkt.confidence

    signals_df = pd.DataFrame(sig_d, index=prices_df.index)
    scalars_df = pd.DataFrame(scl_d, index=prices_df.index)

    log(f"\n  Distribuicao de sinais:")
    log(f"  {'SIMBOLO':<12} {'BUY':>6} {'SELL':>6} {'FLAT':>6}  {'SINAL%':>7}")
    for sym in SYMBOLS:
        c   = signals_df[sym].value_counts().to_dict()
        buy = c.get("BUY", 0); sell = c.get("SELL", 0)
        pct = (buy + sell) / len(signals_df) * 100
        log(f"  {sym:<12} {buy:>6} {sell:>6} {c.get('FLAT',0):>6}  {pct:>6.1f}%")

    # ── 3. BACKTEST ──────────────────────────────────────────────────
    log("\n[3/4] Rodando backtest barra a barra (Kelly identico ao main.py)...")

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
        pip_value: float; confidence: float

    def kelly_size(win_prob: float, size_scalar: float, nav: float) -> float:
        """Replica exata do KellySizer.size() do OrderManager."""
        kelly_f  = win_prob - (1 - win_prob) / max(WIN_LOSS_RATIO, 0.01)
        kelly_f  = max(kelly_f, 0.0)
        fraction = min(kelly_f, KELLY_CAP) * size_scalar
        notional = nav * fraction
        lots     = notional / 100_000
        return round(max(MIN_LOT, min(MAX_LOT, lots)), 2)

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
            cf    = conf_d[sym][i]

            # Gerenciar posicao aberta: checar SL/TP
            if sym in open_pos:
                pos     = open_pos[sym]
                diff    = price - pos["entry"]
                if pos["direction"] == "SELL": diff = -diff
                pips_pl = diff / cfg["pip_size"]

                closed = False; reason_out = ""
                if pips_pl >= TP_PIPS:
                    reason_out = "TP"; closed = True
                elif pips_pl <= -SL_PIPS:
                    reason_out = "SL"; closed = True
                elif sig not in ["FLAT", pos["direction"]]:
                    reason_out = "Reversao"; closed = True

                if closed:
                    gross = pips_pl * pos["vol"] * cfg["pip_value"]
                    # Metade da comissao round turn na saida
                    comm  = round(pos["vol"] * COMM_ROUND / 2, 4)
                    net   = gross - comm
                    nav  += net
                    peak_nav = max(peak_nav, nav)
                    equity.append(nav)
                    trades.append(Trade(
                        symbol=sym, direction=pos["direction"],
                        entry_time=pos["time"], exit_time=ts,
                        entry_price=pos["entry"], exit_price=price,
                        volume=pos["vol"], pips=round(pips_pl, 1),
                        gross_pnl=round(gross, 4), commission=round(pos["entry_comm"] + comm, 4),
                        net_pnl=round(net - pos["entry_comm"], 4),
                        reason_out=reason_out,
                        pip_value=cfg["pip_value"], confidence=pos["conf"],
                    ))
                    del open_pos[sym]

            # Abrir nova posicao
            elif sig in ["BUY", "SELL"] and sym not in open_pos:
                if nav < 5.0: continue
                # Kelly identico ao main.py: win_prob = max(conf, 0.51)
                win_prob = max(cf, 0.51)
                lots     = kelly_size(win_prob, scl, nav)
                # Metade da comissao round turn na entrada
                entry_comm = round(lots * COMM_ROUND / 2, 4)
                nav -= entry_comm
                open_pos[sym] = dict(
                    direction=sig, entry=price, vol=lots,
                    time=ts, conf=cf, entry_comm=entry_comm,
                )

    # Fechar posicoes remanescentes ao EOD
    for sym, pos in list(open_pos.items()):
        cfg     = asset_cfg[sym]
        price   = float(prices_df[sym].iloc[-1])
        diff    = price - pos["entry"]
        if pos["direction"] == "SELL": diff = -diff
        pips_pl = diff / cfg["pip_size"]
        gross   = pips_pl * pos["vol"] * cfg["pip_value"]
        comm    = round(pos["vol"] * COMM_ROUND / 2, 4)
        net     = gross - comm
        nav    += net
        trades.append(Trade(
            symbol=sym, direction=pos["direction"],
            entry_time=pos["time"], exit_time=prices_df.index[-1],
            entry_price=pos["entry"], exit_price=price,
            volume=pos["vol"], pips=round(pips_pl, 1),
            gross_pnl=round(gross, 4),
            commission=round(pos["entry_comm"] + comm, 4),
            net_pnl=round(net - pos["entry_comm"], 4),
            reason_out="EOD",
            pip_value=cfg["pip_value"], confidence=pos["conf"],
        ))
    equity.append(nav)

    # ── 4. METRICAS E RELATORIO ──────────────────────────────────────
    eq        = pd.Series(equity)
    rets      = eq.pct_change().dropna()
    net_pnls  = [t.net_pnl for t in trades]
    wins      = [p for p in net_pnls if p > 0]
    losses    = [p for p in net_pnls if p <= 0]
    gross_pnl = sum(t.gross_pnl for t in trades)
    total_comm = sum(t.commission for t in trades)
    net_pnl   = sum(net_pnls)
    win_rate  = len(wins) / len(trades) if trades else 0
    pf        = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else 0
    sharpe    = rets.mean() / rets.std() * np.sqrt(252 * 24) if rets.std() > 0 else 0
    neg       = rets[rets < 0]
    sortino   = rets.mean() / neg.std() * np.sqrt(252 * 24) if len(neg) > 1 else 0
    rolling_mx = eq.cummax()
    dd_series  = (eq - rolling_mx) / rolling_mx * 100
    max_dd_pct = float(dd_series.min())
    max_dd_usd = float((eq - rolling_mx).min())

    log("\n[4/4] Resultado\n")
    log("=" * 66)
    log("  BACKTEST v4 — $100 — RAW ECN $6.00/lot — SL20p TP40p — Kelly")
    log("=" * 66)
    log(f"  Periodo          : {prices_df.index[0].date()} a {prices_df.index[-1].date()}")
    log(f"  Capital inicial  : ${CAPITAL:.2f}")
    log(f"  Capital final    : ${nav:.4f}")
    log(f"  Net PnL          : ${net_pnl:+.4f}")
    log(f"  Retorno total    : {net_pnl / CAPITAL * 100:+.2f}%")
    log(f"  Gross PnL        : ${gross_pnl:+.4f}")
    log(f"  Total comissoes  : ${total_comm:.4f}")
    log(f"  Comissao/Gross   : {abs(total_comm / gross_pnl * 100) if gross_pnl else 0:.1f}%")
    log(f"  Total trades     : {len(trades)}")
    log(f"  Winning          : {len(wins)} ({win_rate * 100:.1f}%)")
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

    # Criterio de aprovacao: Net > 0, PF > 1.3
    log("\n  BREAKDOWN POR SIMBOLO:")
    log(f"  {'SIMBOLO':<12} {'N':>5} {'WIN%':>7} {'NET PNL':>10} {'COMISS':>8}  STATUS")
    log("  " + "-" * 60)
    aprovados = []
    for sym in SYMBOLS:
        ts_sym = [t for t in trades if t.symbol == sym]
        if not ts_sym:
            log(f"  {sym:<12}     0  sem trades"); continue
        n    = len(ts_sym)
        w    = sum(1 for t in ts_sym if t.net_pnl > 0)
        net  = sum(t.net_pnl for t in ts_sym)
        comm = sum(t.commission for t in ts_sym)
        sym_w = [p for p in [t.net_pnl for t in ts_sym] if p > 0]
        sym_l = [p for p in [t.net_pnl for t in ts_sym] if p <= 0]
        sym_pf = sum(sym_w) / abs(sum(sym_l)) if sym_l and sum(sym_l) != 0 else 0
        ok_sym = net > 0 and sym_pf > 1.3
        status = "APROVADO" if ok_sym else "reprovado"
        if ok_sym: aprovados.append(sym)
        log(f"  {sym:<12} {n:>5} {w/n*100:>6.1f}% {net:>+10.4f} {comm:>8.4f}  {status}")

    log(f"\n  Pares aprovados (Net>0, PF>1.3): {aprovados}")

    log("\n  TOP 8 MELHORES TRADES:")
    log(f"  {'SIM':<10} {'DIR':<5} {'LOT':>5} {'PIPS':>7} {'NET':>10} {'CONF':>6}  SAIDA")
    log("  " + "-" * 54)
    srt = sorted(trades, key=lambda t: t.net_pnl, reverse=True)
    for t in srt[:8]:
        log(f"  {t.symbol:<10} {t.direction:<5} {t.volume:>5.2f} "
            f"{t.pips:>7.1f} {t.net_pnl:>+10.4f} {t.confidence:>6.3f}  {t.reason_out}")

    log("\n  TOP 8 PIORES TRADES:")
    log(f"  {'SIM':<10} {'DIR':<5} {'LOT':>5} {'PIPS':>7} {'NET':>10} {'CONF':>6}  SAIDA")
    log("  " + "-" * 54)
    for t in srt[-8:]:
        log(f"  {t.symbol:<10} {t.direction:<5} {t.volume:>5.2f} "
            f"{t.pips:>7.1f} {t.net_pnl:>+10.4f} {t.confidence:>6.3f}  {t.reason_out}")

    log("\n  EQUITY MENSAL:")
    if trades:
        monthly = {}
        for t in trades:
            try:
                m = pd.Timestamp(t.exit_time).strftime("%Y-%m")
                monthly.setdefault(m, []).append(t.net_pnl)
            except: pass
        running = CAPITAL
        log(f"  {'MES':<10} {'TRADES':>7} {'PNL MES':>10} {'NAV':>10}")
        log("  " + "-" * 40)
        for m in sorted(monthly.keys()):
            mpnl = sum(monthly[m])
            running += mpnl
            log(f"  {m:<10} {len(monthly[m]):>7} {mpnl:>+10.4f} {running:>10.4f}")

    pd.DataFrame([vars(t) for t in trades]).to_csv(OUT_TRADES, index=False)
    pd.Series(equity).to_csv(OUT_EQUITY, header=["equity"])
    log(f"\n  Trades CSV : {OUT_TRADES}")
    log(f"  Equity CSV : {OUT_EQUITY}")
    log(f"  Log        : {OUT_LOG}")

    mt5.shutdown()
    log("\n  BACKTEST v4 CONCLUIDO")

except Exception:
    log("\n!!! ERRO !!!")
    log(traceback.format_exc())
finally:
    open(OUT_LOG, "w", encoding="utf-8").write("\n".join(lines))
    print(f"\nLog salvo: {OUT_LOG}")
