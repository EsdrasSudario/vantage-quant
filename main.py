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
    python main.py --symbols EURUSD USDJPY --interval 60
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
from risk.risk_engine          import RiskEngine, RiskLimits
from execution.order_manager   import OrderManager

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


def get_current_price(symbol: str) -> float:
    if MT5_OK:
        tick = mt5.symbol_info_tick(symbol)
        if tick:
            return (tick.bid + tick.ask) / 2
    prices = get_prices(symbol, 10)
    return float(prices.iloc[-1])


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

    # Aggregators por símbolo
    aggregators = {sym: SignalAggregator(sym) for sym in symbols}

    # Pré-fit GARCH com dados históricos
    log.info("Pré-carregando dados históricos para GARCH...")
    histories = {}
    for sym in symbols:
        hist = get_prices(sym, n_bars=1000)
        aggregators[sym].fit_garch(hist)
        histories[sym] = hist
        log.info(f"  GARCH fitado para {sym} ({len(hist)} barras)")

    # Loop de trading
    iteration = 0
    open_positions = {}

    while iteration < max_iterations:
        iteration += 1
        ts = datetime.utcnow()
        log.info(f"\n{'-'*50}")
        log.info(f"  Iteração {iteration}/{max_iterations} — {ts.strftime('%H:%M:%S UTC')}")

        headlines = get_geo_headlines()

        for sym in symbols:
            try:
                current_price = get_current_price(sym)
                prices_hist   = get_prices(sym, n_bars=500)

                # Atualiza histórico local
                histories[sym] = prices_hist

                # Gera sinal
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
                        oms.close_position(sym, pos["direction"], pos["volume"])
                        risk.close_position(sym, current_price)
                        del open_positions[sym]
                        log.warning(f"  STOP atingido — {sym} fechado")
                        continue

                # Executar novo sinal
                if pkt.direction in ["BUY", "SELL"] and sym not in open_positions:
                    res = oms.execute(
                        symbol=sym,
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
                                                  res.filled_price)
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
                        default=["EURUSD", "USDJPY", "GBPUSD"],
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
