"""
main.py
=======
Loop principal do Vantage Quant System.

Orquestra:
  1. Conexão MT5 → Vantage Demo
  2. Data ingestion (tick data em tempo real)
  3. SignalAggregator (GARCH + Kalman + GeoScore)
  4. RiskEngine pre-trade
  5. OrderManager → execução
  6. Monitoramento de posições abertas
  7. Logging e relatório periódico

Execução:
    python main.py
    python main.py --symbols AUDUSD NZDUSD USDJPY --interval 60
"""

import os
import sys
import time
import argparse
import logging
import pandas as pd
import numpy as np
from datetime import datetime
from dotenv import load_dotenv

# Fix encoding Windows CP1252 — suporte a caracteres Unicode no terminal
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"logs/vantage_quant_{datetime.now().strftime('%Y%m%d')}.log"),
    ]
)
log = logging.getLogger("MAIN")

# Carrega .env
load_dotenv("config/.env")

# Imports do sistema
sys.path.insert(0, os.path.dirname(__file__))
from signals.signal_aggregator import SignalAggregator
from signals.kalman_filter     import KalmanPairs
from signals.pair_screener     import PairScreener
from signals.geo_score         import GeoScorer
from risk.risk_engine          import RiskEngine, RiskLimits
from execution.order_manager   import OrderManager
from core.symbol_resolver      import resolve_mapping

# Pares correlacionados para Stat Arb (KalmanPairs).
# Apenas os pares cujos dois símbolos estejam na lista --symbols serão ativados.
_KALMAN_PAIRS = [
    ("AUDUSD", "NZDUSD"),   # correlação histórica > 0.90 — ambos aprovados no backtest
    ("EURUSD", "GBPUSD"),   # correlação histórica > 0.85
]

# MT5 (opcional — Windows)
try:
    import MetaTrader5 as mt5
    MT5_OK = True
except ImportError:
    MT5_OK = False
    log.warning("MT5 não disponível — modo paper trading ativo")


# ======================================================================
# FUNÇÕES DE DATA
# ======================================================================

def connect_mt5() -> bool:
    if not MT5_OK:
        return False
    ok = mt5.initialize(
        server=os.getenv("MT5_SERVER", "VantageMarkets-Demo"),
        login=int(os.getenv("MT5_LOGIN", "26018171")),
        password=os.getenv("MT5_PASSWORD", "")
    )
    if ok:
        acc = mt5.account_info()
        log.info(f"MT5 conectado | {acc.server} | Login={acc.login} "
                 f"| Balance=${acc.balance:,.2f} | Equity=${acc.equity:,.2f}")
    else:
        log.error(f"MT5 falhou: {mt5.last_error()}")
    return ok


def get_prices(symbol: str, n_bars: int = 500) -> pd.Series:
    """Retorna últimas n_bars de preços de fechamento."""
    if MT5_OK:
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, n_bars)
        if rates is not None:
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            return df.set_index("time")["close"]
    # Fallback simulado (paper trading)
    idx   = pd.date_range(end=datetime.utcnow(), periods=n_bars, freq="5min")
    base  = {"EURUSD": 1.0850, "USDJPY": 148.50, "GBPUSD": 1.2720,
             "XAUUSD": 2450.0, "AUDUSD": 0.6480, "USDCAD": 1.3540}
    price = base.get(symbol, 1.0)
    return pd.Series(
        price + np.cumsum(np.random.normal(0, 0.0003, n_bars)),
        index=idx, name=symbol
    )


def get_ohlc(symbol: str, n_bars: int = 200) -> pd.DataFrame:
    """Retorna DataFrame OHLC real (open, high, low, close) para o screener."""
    if MT5_OK:
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, n_bars)
        if rates is not None and len(rates) > 0:
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            return df.set_index("time")[["open", "high", "low", "close"]]
    # Fallback paper trading — gera OHLC sintético com variação realista
    idx = pd.date_range(end=datetime.utcnow(), periods=n_bars, freq="1h")
    base = {"EURUSD": 1.0850, "USDJPY": 148.50, "GBPUSD": 1.2720,
            "XAUUSD": 2450.0, "XAUUSD+": 2450.0, "AUDUSD": 0.6480,
            "NZDUSD": 0.5950, "USDCAD": 1.3540, "USDCHF+": 0.8960,
            "EURGBP+": 0.8540, "XAGUSD": 29.50}
    price = base.get(symbol, 1.0)
    closes = price + np.cumsum(np.random.normal(0, price * 0.0003, n_bars))
    noise  = np.abs(np.random.normal(0, price * 0.0002, n_bars))
    return pd.DataFrame({
        "open":  closes - noise * 0.3,
        "high":  closes + noise,
        "low":   closes - noise,
        "close": closes,
    }, index=idx)


def get_current_price(symbol: str) -> float:
    if MT5_OK:
        tick = mt5.symbol_info_tick(symbol)
        if tick:
            return (tick.bid + tick.ask) / 2
    prices = get_prices(symbol, 10)
    return float(prices.iloc[-1])


def sync_startup_positions(
    open_positions: dict,
    open_stat_arb: dict,
    risk: "RiskEngine",
    symbols: list,
    sym_map: dict,
    magic: int = 0,
) -> None:
    """Popula open_positions e risk.positions com posições já abertas no MT5.

    Chamado uma única vez no startup, após inicializar os componentes e antes
    do loop principal.  Sem isso, o RiskEngine subestima a exposição real e
    pode abrir ordens além do limite quando o processo é reiniciado com
    posições vivas no terminal.

    Parâmetros
    ----------
    magic : int
        Magic number usado pelo sistema (0 = aceita qualquer magic, útil para
        contas demo onde as ordens podem ter magic=0).
    """
    if not MT5_OK:
        return

    # Inverte sym_map para resolver mt5_sym → sym_original
    rev_map = {v: k for k, v in sym_map.items()}

    all_positions = mt5.positions_get()
    if not all_positions:
        log.info("[STARTUP SYNC] Nenhuma posição aberta no MT5.")
        return

    count = 0
    for p in all_positions:
        if magic != 0 and p.magic != magic:
            continue  # filtra por magic quando especificado

        mt5_sym = p.symbol
        sym = rev_map.get(mt5_sym, mt5_sym)

        # Ignora símbolos fora do universo operado
        if sym not in symbols:
            log.debug(f"[STARTUP SYNC] {mt5_sym} fora do universo — ignorado")
            continue

        direction = "BUY" if p.type == 0 else "SELL"   # 0=ORDER_TYPE_BUY

        if sym not in open_positions:
            open_positions[sym] = {
                "direction": direction,
                "volume":    p.volume,
                "entry":     p.price_open,
                "ticket":    p.ticket,
            }

        from risk.risk_engine import Position
        risk.positions[sym] = Position(
            symbol=sym,
            direction=direction,
            volume=p.volume,
            entry_price=p.price_open,
            open_time=datetime.utcfromtimestamp(p.time),
            current_pnl=p.profit,
        )
        count += 1
        log.info(
            f"[STARTUP SYNC] {sym} ({mt5_sym}) {direction} "
            f"{p.volume:.2f}L @ {p.price_open:.5f} "
            f"ticket={p.ticket} profit={p.profit:.2f}"
        )

    if count:
        log.info(f"[STARTUP SYNC] {count} posição(ões) restaurada(s) no RiskEngine.")
    else:
        log.info("[STARTUP SYNC] Nenhuma posição do universo ativa no MT5.")


def sync_closed_positions(
    open_positions: dict,
    open_stat_arb: dict,
    risk: "RiskEngine",
    sym_map: dict,
) -> None:
    """Remove de open_positions/open_stat_arb entradas que o MT5 fechou via SL/TP.

    Sem isso, após um SL ou TP ser atingido pelo broker o loop nunca mais abre
    ordem naquele símbolo porque open_positions[sym] continua existindo.
    """
    if not MT5_OK:
        return

    # Posições diretas
    for sym in list(open_positions):
        mt5_sym = sym_map.get(sym, sym)
        positions_mt5 = mt5.positions_get(symbol=mt5_sym)
        if positions_mt5 is None or len(positions_mt5) == 0:
            tick = mt5.symbol_info_tick(mt5_sym)
            exit_price = (tick.bid + tick.ask) / 2 if tick else 0.0
            risk.close_position(sym, exit_price)
            del open_positions[sym]
            log.info(f"  [SYNC] {sym} fechado pelo MT5 (SL/TP) — removido do estado interno")

    # Legs de stat arb
    for pair_key in list(open_stat_arb):
        sym_a, sym_b = pair_key
        mt5_a = sym_map.get(sym_a, sym_a)
        mt5_b = sym_map.get(sym_b, sym_b)
        pos_a = mt5.positions_get(symbol=mt5_a)
        pos_b = mt5.positions_get(symbol=mt5_b)
        closed_a = pos_a is None or len(pos_a) == 0
        closed_b = pos_b is None or len(pos_b) == 0
        if closed_a or closed_b:
            for sym, mt5_sym in [(sym_a, mt5_a), (sym_b, mt5_b)]:
                tick = mt5.symbol_info_tick(mt5_sym)
                exit_price = (tick.bid + tick.ask) / 2 if tick else 0.0
                risk.close_position(sym, exit_price)
            del open_stat_arb[pair_key]
            log.info(
                f"  [SYNC] KP {sym_a}/{sym_b} fechado pelo MT5 (SL/TP)"
                f" — a={closed_a} b={closed_b} — removido do estado interno"
            )


_GEO_RSS_FEEDS = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://rss.ft.com/rss/time/sections/96da3bc2-eeb4-4a87-a790-e5e7e6b84a51",
]

_FALLBACK_HEADLINES = [
    "Market conditions normal — no geopolitical alerts",
]

def get_geo_headlines(max_items: int = 20) -> list:
    """Busca headlines reais via RSS. Fallback para lista fixa se offline."""
    try:
        import feedparser
    except ImportError:
        log.warning("feedparser nao instalado — usando headlines fallback. "
                    "Execute: pip install feedparser")
        return _FALLBACK_HEADLINES

    headlines = []
    per_feed  = max(1, max_items // len(_GEO_RSS_FEEDS))
    for url in _GEO_RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:per_feed]:
                title = getattr(entry, "title", "").strip()
                if title:
                    headlines.append(title)
        except Exception as exc:
            log.debug(f"RSS falhou ({url}): {exc}")

    if headlines:
        log.debug(f"GeoScore: {len(headlines)} headlines carregadas via RSS")
        return headlines

    log.warning("Todos os RSS falharam — usando headlines fallback")
    return _FALLBACK_HEADLINES


# ======================================================================
# LOOP PRINCIPAL
# ======================================================================

_SCREENER_INTERVAL_SEC = 4 * 3600   # re-executa screening a cada 4h


def run(symbols: list, interval_sec: int, max_iterations: int):
    log.info("="*60)
    log.info("  VANTAGE QUANT SYSTEM — Iniciando")
    log.info(f"  Símbolos : {symbols}")
    log.info(f"  Intervalo: {interval_sec}s")
    log.info(f"  Modo     : {'LIVE MT5' if MT5_OK else 'PAPER TRADING'}")
    log.info("="*60)

    # Inicializar componentes
    nav    = 100_000.0
    limits = RiskLimits(
        max_daily_loss_pct=0.02,
        max_position_loss_pct=0.015,
        var_95_limit_pct=0.015,
        geo_score_threshold=0.85,
        max_open_positions=6,
    )
    risk   = RiskEngine(nav=nav, limits=limits)
    oms    = OrderManager(nav=nav, mt5_connected=MT5_OK, risk_engine=risk)

    # PairScreener — seleciona automaticamente os melhores pares por sessão
    # resolve_fn: mapeia nome canônico → nome real MT5 (ex: XAUUSD → XAUUSD+)
    _sym_map_cache: dict[str, str] = resolve_mapping(symbols)
    def _resolve_symbol(sym: str) -> str:
        return _sym_map_cache.get(sym, sym)

    screener    = PairScreener(universe=symbols, max_pairs=4, symbol_resolve_fn=_resolve_symbol)
    geo_screener = GeoScorer()
    last_screen_time = 0.0   # força execução imediata no startup
    active_symbols = list(symbols)   # começa com todos; screener refina no primeiro ciclo

    # Aggregators por símbolo
    aggregators = {sym: SignalAggregator(sym) for sym in symbols}

    # KalmanPairs — ativa apenas pares cujos dois símbolos estejam em `symbols`
    active_kp_pairs = [
        (a, b) for (a, b) in _KALMAN_PAIRS
        if a in symbols and b in symbols
    ]
    kalman_pairs: dict[tuple, KalmanPairs] = {
        (a, b): KalmanPairs(delta=1e-4, z_entry=2.0, z_exit=0.5, window=100)
        for (a, b) in active_kp_pairs
    }
    open_stat_arb: dict[tuple, dict] = {}   # (sym_a, sym_b) → posição aberta

    if active_kp_pairs:
        log.info(f"KalmanPairs ativo para: {active_kp_pairs}")
    else:
        log.info("KalmanPairs: nenhum par ativo (símbolos ausentes na lista --symbols)")

    # Pré-fit GARCH com dados históricos
    log.info("Pré-carregando dados históricos para GARCH...")
    histories = {}
    for sym in symbols:
        hist = get_prices(sym, n_bars=1000)
        aggregators[sym].fit_garch(hist)
        histories[sym] = hist
        log.info(f"  GARCH fitado para {sym} ({len(hist)} barras)")

    # Warm-up dos KalmanPairs com dados históricos (min. 100 barras para z-score estável)
    if active_kp_pairs:
        log.info("Warm-up dos KalmanPairs com dados históricos...")
        for (sym_a, sym_b), kp in kalman_pairs.items():
            hist_a = histories.get(sym_a, get_prices(sym_a, 200))
            hist_b = histories.get(sym_b, get_prices(sym_b, 200))
            n = min(len(hist_a), len(hist_b))
            for i in range(n):
                kp.update(float(hist_a.iloc[i]), float(hist_b.iloc[i]))
            log.info(f"  KalmanPairs {sym_a}/{sym_b} warm-up: {n} barras")

    # Loop de trading
    iteration = 0
    open_positions = {}

    # Sync de startup: restaura posições já abertas no terminal MT5.
    # Deve ocorrer após sym_map estar disponível, mas antes do loop.
    startup_sym_map = resolve_mapping(symbols) if MT5_OK else {s: s for s in symbols}
    sync_startup_positions(
        open_positions, open_stat_arb, risk,
        symbols, startup_sym_map, magic=0,
    )

    while iteration < max_iterations:
        iteration += 1
        ts = datetime.utcnow()
        log.info(f"\n{'-'*50}")
        log.info(f"  Iteração {iteration}/{max_iterations} — {ts.strftime('%H:%M:%S UTC')}")

        headlines = get_geo_headlines()

        # PairScreener — re-executa no startup e a cada 4h
        now_ts = time.time()
        if now_ts - last_screen_time >= _SCREENER_INTERVAL_SEC:
            log.info("  [SCREENER] Executando screening de pares...")
            _screen_sym_map = resolve_mapping(symbols) if MT5_OK else {s: s for s in symbols}
            bars_for_screen = {
                sym: get_ohlc(_screen_sym_map.get(sym, sym), 200)
                for sym in symbols
            }
            # Quando MT5 disponível, usa barras H1 via _fetch_bars_mt5 interno
            screen_report = screener.screen(geo_screener, bars_for_screen, headlines)
            active_symbols = screen_report.selected if screen_report.selected else list(symbols)
            last_screen_time = now_ts
            log.info(f"  [SCREENER] Pares ativos: {active_symbols}")
            if screen_report.rejected_correlation:
                log.info(f"  [SCREENER] Rejeitados (correlação): {screen_report.rejected_correlation}")

        # Resolve símbolos habilitados na corretora neste ciclo.
        # sym_map: {original: resolvido} — original indexa aggregators/histories,
        # resolvido é usado nas chamadas MT5 (ticks, ordens).
        sym_map = resolve_mapping(symbols) if MT5_OK else {s: s for s in symbols}
        if not sym_map:
            log.warning("Nenhum símbolo habilitado neste ciclo — aguardando próximo intervalo")
            time.sleep(interval_sec)
            continue

        # Restringe ao subconjunto aprovado pelo screener
        screened_map = {s: v for s, v in sym_map.items() if s in active_symbols}

        # Sincroniza posições fechadas pelo MT5 (SL/TP) antes de processar sinais
        sync_closed_positions(open_positions, open_stat_arb, risk, sym_map)

        for sym, mt5_sym in screened_map.items():
            # sym     → chave interna (aggregators, histories, open_positions)
            # mt5_sym → símbolo enviado ao MT5 (pode ter sufixo '+')
            try:
                current_price = get_current_price(mt5_sym)
                prices_hist   = get_prices(mt5_sym, n_bars=500)

                # Atualiza histórico local (indexado pelo nome original)
                histories[sym] = prices_hist

                # Gera sinal (aggregator indexado pelo nome original)
                pkt = aggregators[sym].evaluate(
                    current_price, prices_hist, headlines
                )

                log.info(
                    f"  {sym:<8} price={current_price:.5f} | "
                    f"signal={pkt.direction:<5} conf={pkt.confidence:.3f} | "
                    f"geo={pkt.geo_level} | garch={pkt.garch_regime}"
                )

                # Verificar posições abertas: stop por posição
                if sym in open_positions:
                    action = risk.update_position_pnl(sym, current_price)
                    if action == "CLOSE":
                        pos = open_positions[sym]
                        oms.close_position(mt5_sym, pos["direction"], pos["volume"])
                        risk.close_position(sym, current_price)
                        del open_positions[sym]
                        log.warning(f"  STOP atingido — {sym} fechado")
                        continue

                # Executar novo sinal
                if pkt.direction in ["BUY", "SELL"] and sym not in open_positions:
                    res = oms.execute(
                        symbol=mt5_sym,
                        direction=pkt.direction,
                        win_prob=max(pkt.confidence, 0.51),
                        win_loss_ratio=2.0,
                        geo_score=pkt.geo_score,
                        size_scalar=pkt.size_scalar,
                        sl_pips=20, tp_pips=40,
                    )
                    if res.success:
                        open_positions[sym] = {
                            "direction": pkt.direction,
                            "volume":    res.volume,
                            "entry":     res.filled_price,
                        }
                        # B2 fix: registra posição no RiskEngine para circuit
                        # breaker, max_positions e position-stop funcionarem
                        from risk.risk_engine import Position
                        risk.open_position(Position(
                            symbol=sym,
                            direction=pkt.direction,
                            volume=res.volume,
                            entry_price=res.filled_price,
                        ))
                        risk.record_fill_slippage(res.requested_price,
                                                  res.filled_price,
                                                  symbol=sym)
                        log.info(
                            f"  [OK] ORDEM {sym} {pkt.direction} "
                            f"{res.volume:.2f}L @ {res.filled_price:.5f} "
                            f"slip={res.slippage_pips:.1f}p "
                            f"comm=${res.commission:.2f}"
                        )
                    else:
                        log.warning(f"  [REJEITADA] Ordem {sym}: {res.error_msg}")

            except Exception as e:
                log.error(f"  Erro em {sym}: {e}", exc_info=True)

        # ------------------------------------------------------------------
        # STAT ARB — KalmanPairs
        # ------------------------------------------------------------------
        for (sym_a, sym_b), kp in kalman_pairs.items():
            # Resolve nomes MT5 para cada leg (fallback para o original se não estiver no sym_map)
            mt5_a = sym_map.get(sym_a, sym_a)
            mt5_b = sym_map.get(sym_b, sym_b)
            try:
                price_a = get_current_price(mt5_a)
                price_b = get_current_price(mt5_b)
                kp_result = kp.update(price_a, price_b)
                kp_signal = kp_result["signal"]
                z = kp_result["z_score"]

                log.info(
                    f"  KP {sym_a}/{sym_b} z={z:+.3f} beta={kp_result['beta']:.4f}"
                    f" signal={kp_signal}"
                )

                pair_key = (sym_a, sym_b)

                # Fechar posição stat arb existente
                if pair_key in open_stat_arb and kp_signal == "EXIT":
                    pos = open_stat_arb[pair_key]
                    oms.close_position(mt5_a, pos["side_a"], pos["volume"])
                    oms.close_position(mt5_b, pos["side_b"], pos["volume"])
                    risk.close_position(sym_a, price_a)
                    risk.close_position(sym_b, price_b)
                    del open_stat_arb[pair_key]
                    log.info(f"  [KP EXIT] {sym_a}/{sym_b} fechado (z={z:+.3f})")
                    # Fechar posições diretas abertas nos mesmos símbolos para
                    # evitar legs órfãs quando sinal direto e stat arb coexistem.
                    for sym, mt5_sym, price in [(sym_a, mt5_a, price_a), (sym_b, mt5_b, price_b)]:
                        if sym in open_positions:
                            direct = open_positions[sym]
                            oms.close_position(mt5_sym, direct["direction"], direct["volume"])
                            risk.close_position(sym, price)
                            del open_positions[sym]
                            log.info(f"  [KP EXIT] posição direta {sym} fechada junto ao stat arb")
                    continue

                # Abrir nova posição stat arb
                if pair_key not in open_stat_arb and kp_signal in ("BUY_A_SELL_B", "SELL_A_BUY_B"):
                    side_a = "BUY"  if kp_signal == "BUY_A_SELL_B"  else "SELL"
                    side_b = "SELL" if kp_signal == "BUY_A_SELL_B"  else "BUY"

                    # Sizing conservador: 0.01 lot fixo por leg (micro)
                    vol = 0.01

                    res_a = oms.execute(
                        symbol=mt5_a, direction=side_a,
                        win_prob=0.55, win_loss_ratio=2.0,
                        geo_score=0.0, size_scalar=0.5,
                        sl_pips=20, tp_pips=40,
                    )
                    res_b = oms.execute(
                        symbol=mt5_b, direction=side_b,
                        win_prob=0.55, win_loss_ratio=2.0,
                        geo_score=0.0, size_scalar=0.5,
                        sl_pips=20, tp_pips=40,
                    )

                    if res_a.success and res_b.success:
                        open_stat_arb[pair_key] = {
                            "side_a": side_a, "side_b": side_b,
                            "volume": min(res_a.volume, res_b.volume),
                        }
                        from risk.risk_engine import Position
                        risk.open_position(Position(
                            symbol=sym_a, direction=side_a,
                            volume=res_a.volume, entry_price=res_a.filled_price,
                        ))
                        risk.open_position(Position(
                            symbol=sym_b, direction=side_b,
                            volume=res_b.volume, entry_price=res_b.filled_price,
                        ))
                        log.info(
                            f"  [KP OPEN] {sym_a} {side_a} / {sym_b} {side_b}"
                            f" z={z:+.3f} vol={res_a.volume:.2f}L"
                        )
                    else:
                        log.warning(
                            f"  [KP REJEITADO] {sym_a}/{sym_b}: "
                            f"a={res_a.error_msg} b={res_b.error_msg}"
                        )

            except Exception as e:
                log.error(f"  Erro KalmanPairs {sym_a}/{sym_b}: {e}", exc_info=True)

        # Status do Risk Engine
        status = risk.status()
        log.info(
            f"\n  [RISK] Daily PnL: ${status['Daily PnL']:+.2f} "
            f"({status['Daily PnL %']:+.3f}%) | "
            f"Posições: {status['Open Positions']} | "
            f"Halted: {status['Halted']}"
        )

        # Relatório de trades a cada 10 iterações
        if iteration % 10 == 0:
            oms.trade_log()

        # Aguardar próximo ciclo
        log.info(f"  [WAIT] Aguardando {interval_sec}s...")
        time.sleep(interval_sec)

    # Relatório final
    log.info("\n" + "="*60)
    log.info("  SESSÃO ENCERRADA")
    oms.trade_log()
    if MT5_OK:
        mt5.shutdown()
        log.info("  MT5 desconectado.")


# ======================================================================
# ENTRY POINT
# ======================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Vantage Quant System")
    parser.add_argument("--symbols",   nargs="+",
                        default=["AUDUSD", "NZDUSD", "USDJPY"],
                        help="Símbolos a operar")
    parser.add_argument("--interval",  type=int, default=60,
                        help="Intervalo entre ciclos em segundos")
    parser.add_argument("--max-iter",  type=int, default=0,
                        help="Número máximo de iterações (0 = infinito)")
    args = parser.parse_args()

    os.makedirs("logs", exist_ok=True)

    if MT5_OK:
        if not connect_mt5():
            log.error("Falha ao conectar MT5. Continuando em paper trading.")

    max_it = args.max_iter if args.max_iter > 0 else float("inf")

    try:
        run(args.symbols, args.interval, max_it)
    except KeyboardInterrupt:
        log.info("\n  Sistema encerrado pelo usuário (Ctrl+C)")
        if MT5_OK:
            mt5.shutdown()
