"""
signals/pair_screener.py
========================
Seleção algorítmica de pares de trading por sessão.

Filtra o universo de pares em 5 etapas em cascata:
  1. Liquidez      — spread médio < 2 pips (últimas N ticks)
  2. Vol mínima    — ATR(14) > 5 pips (par não está dormindo)
  3. Vol máxima    — GARCH anualizado < 30% (evitar pares em crise)
  4. Correlação    — remove pares com correlação > 0.80 entre si
  5. GeoScore      — remove pares cujas moedas estão sob score CRITICAL

Uso:
    screener = PairScreener()
    pares = screener.screen(geo_scorer, bars_dict)
    # → ["AUDUSD", "NZDUSD", "USDJPY", "EURGBP"]  (máx 4)
"""

import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("PairScreener")

try:
    import MetaTrader5 as mt5
    _MT5_OK = True
except ImportError:
    _MT5_OK = False


# ---------------------------------------------------------------------------
# Mapeamento: moeda → pares do universo que a contêm
# Usado no filtro GeoScore CRITICAL por moeda individual
# ---------------------------------------------------------------------------
_CURRENCY_PAIRS: dict[str, list[str]] = {
    "USD": ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF"],
    "EUR": ["EURUSD", "EURGBP"],
    "GBP": ["GBPUSD", "EURGBP"],
    "JPY": ["USDJPY"],
    "AUD": ["AUDUSD"],
    "NZD": ["NZDUSD"],
    "CAD": ["USDCAD"],
    "CHF": ["USDCHF"],
    "XAU": ["XAUUSD"],
}

# Exposição geopolítica de cada par: quais regiões impactam cada símbolo
# Usado para correlacionar GeoScore temático com o par específico
_GEO_EXPOSURE: dict[str, list[str]] = {
    "USDJPY": ["monetary_shock", "taiwan_china"],   # BoJ + tensão Ásia
    "XAUUSD": ["war_conflict", "energy_supply"],    # safe haven
    "USDCAD": ["energy_supply"],                    # petróleo
    "AUDUSD": ["taiwan_china", "trade_sanctions"],  # China dependency
    "NZDUSD": ["taiwan_china"],
    "EURUSD": ["political_instability", "energy_supply"],
    "GBPUSD": ["political_instability"],
    "EURGBP": ["political_instability"],
    "USDCHF": ["war_conflict", "monetary_shock"],   # safe haven
}


@dataclass
class ScreenResult:
    symbol:         str
    passed:         bool
    fail_reason:    str = ""
    spread_pips:    float = 0.0
    atr_pips:       float = 0.0
    garch_vol_pct:  float = 0.0
    score:          float = 0.0   # score composto para ranking


@dataclass
class ScreenReport:
    selected:   list[str]
    all_results: list[ScreenResult]
    rejected_correlation: list[str] = field(default_factory=list)

    def log_summary(self):
        log.info(f"PairScreener: {len(self.selected)} pares selecionados: {self.selected}")
        for r in self.all_results:
            if not r.passed:
                log.debug(f"  REJEITADO {r.symbol}: {r.fail_reason}")


class PairScreener:
    """
    Seleciona automaticamente os N melhores pares a operar em cada sessão.

    Parâmetros:
        universe        Lista de símbolos candidatos (sem resolver sufixo)
        max_pairs       Máximo de pares simultâneos retornados
        max_spread_pips Spread médio máximo (filtro liquidez)
        min_atr_pips    ATR(14) mínimo em pips (filtro vol mínima)
        max_garch_pct   Vol anualizada GARCH máxima em % (filtro vol máxima)
        max_correlation Correlação máxima entre pares no portfólio
        tick_history    Número de ticks usados para calcular spread médio
    """

    UNIVERSE: list[str] = [
        "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD",
        "USDCAD", "USDCHF", "EURGBP", "XAUUSD",
    ]

    # pip size por símbolo (padrão 0.0001; JPY e XAU são exceção)
    _PIP: dict[str, float] = {
        "USDJPY": 0.01, "EURJPY": 0.01, "GBPJPY": 0.01,
        "AUDJPY": 0.01, "NZDJPY": 0.01, "CADJPY": 0.01,
        "XAUUSD": 1.00, "XAGUSD": 0.01,
    }
    _DEFAULT_PIP = 0.0001

    def __init__(
        self,
        universe: Optional[list[str]] = None,
        max_pairs: int = 4,
        max_spread_pips: float = 2.0,
        min_atr_pips: float = 5.0,
        max_garch_pct: float = 30.0,
        max_correlation: float = 0.80,
        tick_history: int = 100,
    ):
        self.universe        = universe or self.UNIVERSE
        self.max_pairs       = max_pairs
        self.max_spread_pips = max_spread_pips
        self.min_atr_pips    = min_atr_pips
        self.max_garch_pct   = max_garch_pct
        self.max_correlation = max_correlation
        self.tick_history    = tick_history

    # ------------------------------------------------------------------
    # Ponto de entrada principal
    # ------------------------------------------------------------------
    def screen(
        self,
        geo_scorer,
        bars: dict[str, pd.DataFrame],
        geo_headlines: Optional[list[str]] = None,
    ) -> ScreenReport:
        """
        Executa o screening completo.

        Args:
            geo_scorer:    Instância de GeoScorer (já importada pelo caller)
            bars:          dict {symbol: DataFrame com colunas open/high/low/close}
                           Usado para ATR, GARCH e correlação.
                           Pode ser vazio — o screener tenta buscar do MT5 como fallback.
            geo_headlines: Lista de headlines para o GeoScore (opcional; usa [] se None)

        Returns:
            ScreenReport com lista de símbolos selecionados e detalhes de cada candidato.
        """
        headlines = geo_headlines or []
        geo_result = geo_scorer.score(headlines) if headlines else None

        results: list[ScreenResult] = []

        for sym in self.universe:
            r = self._evaluate(sym, bars.get(sym), geo_result)
            results.append(r)

        # Filtro 4: correlação (opera sobre os aprovados até aqui)
        passed = [r for r in results if r.passed]
        rejected_corr: list[str] = []

        if len(passed) > 1:
            passed, rejected_corr = self._filter_correlation(passed, bars)

        # Ranking: menor spread + maior ATR + menor GARCH vol → melhor score
        passed.sort(key=lambda r: r.score, reverse=True)

        selected = [r.symbol for r in passed[: self.max_pairs]]

        report = ScreenReport(
            selected=selected,
            all_results=results,
            rejected_correlation=rejected_corr,
        )
        report.log_summary()
        return report

    # ------------------------------------------------------------------
    # Avaliação individual por símbolo (filtros 1–3 + GeoScore)
    # ------------------------------------------------------------------
    def _evaluate(
        self,
        symbol: str,
        bars: Optional[pd.DataFrame],
        geo_result,
    ) -> ScreenResult:
        pip = self._PIP.get(symbol, self._DEFAULT_PIP)

        # --- Filtro 1: Liquidez (spread médio) ---
        spread_pips = self._spread_pips(symbol, pip)
        if spread_pips > self.max_spread_pips:
            return ScreenResult(
                symbol=symbol, passed=False,
                fail_reason=f"Spread {spread_pips:.2f}p > {self.max_spread_pips}p",
                spread_pips=spread_pips,
            )

        # --- Filtro 2: Volatilidade mínima (ATR) ---
        atr_pips = self._atr_pips(symbol, bars, pip)
        if atr_pips < self.min_atr_pips:
            return ScreenResult(
                symbol=symbol, passed=False,
                fail_reason=f"ATR {atr_pips:.2f}p < {self.min_atr_pips}p (par dormindo)",
                spread_pips=spread_pips, atr_pips=atr_pips,
            )

        # --- Filtro 3: Volatilidade máxima (GARCH) ---
        garch_vol = self._garch_vol(symbol, bars)
        if garch_vol > self.max_garch_pct:
            return ScreenResult(
                symbol=symbol, passed=False,
                fail_reason=f"GARCH vol {garch_vol:.1f}% > {self.max_garch_pct}% (crise)",
                spread_pips=spread_pips, atr_pips=atr_pips, garch_vol_pct=garch_vol,
            )

        # --- Filtro 5: GeoScore CRITICAL ---
        if geo_result is not None and geo_result.level == "CRITICAL":
            exposures = _GEO_EXPOSURE.get(symbol, [])
            if geo_result.dominant_theme in exposures:
                return ScreenResult(
                    symbol=symbol, passed=False,
                    fail_reason=(
                        f"GeoScore CRITICAL ({geo_result.score:.3f}) — "
                        f"tema '{geo_result.dominant_theme}' expõe {symbol}"
                    ),
                    spread_pips=spread_pips, atr_pips=atr_pips, garch_vol_pct=garch_vol,
                )

        # Score composto: normaliza cada métrica em [0,1] e combina
        # Quanto menor o spread e maior o ATR, melhor
        spread_score = max(0.0, 1.0 - spread_pips / self.max_spread_pips)
        atr_score    = min(1.0, atr_pips / 20.0)   # referência: 20 pips = ótimo
        vol_score    = max(0.0, 1.0 - garch_vol / self.max_garch_pct)
        composite    = round(0.4 * spread_score + 0.4 * atr_score + 0.2 * vol_score, 4)

        return ScreenResult(
            symbol=symbol, passed=True,
            spread_pips=round(spread_pips, 4),
            atr_pips=round(atr_pips, 4),
            garch_vol_pct=round(garch_vol, 4),
            score=composite,
        )

    # ------------------------------------------------------------------
    # Filtro 4: Correlação — remove pares altamente correlacionados
    # ------------------------------------------------------------------
    def _filter_correlation(
        self,
        candidates: list[ScreenResult],
        bars: dict[str, pd.DataFrame],
    ) -> tuple[list[ScreenResult], list[str]]:
        """
        Remove pares com correlação > max_correlation entre si.
        Quando dois pares conflitam, mantém o de maior score composto.
        """
        syms = [r.symbol for r in candidates]
        score_map = {r.symbol: r.score for r in candidates}

        # Monta matriz de retornos diários para calcular correlação
        closes: dict[str, pd.Series] = {}
        for sym in syms:
            df = bars.get(sym)
            if df is not None and "close" in df.columns and len(df) >= 20:
                closes[sym] = df["close"].pct_change().dropna()

        if len(closes) < 2:
            return candidates, []

        close_df = pd.DataFrame(closes).dropna()
        if len(close_df) < 10:
            return candidates, []

        corr_matrix = close_df.corr()
        rejected: set[str] = set()

        for i, sym_a in enumerate(syms):
            if sym_a in rejected:
                continue
            for sym_b in syms[i + 1 :]:
                if sym_b in rejected:
                    continue
                if sym_a not in corr_matrix.columns or sym_b not in corr_matrix.columns:
                    continue
                corr = abs(corr_matrix.loc[sym_a, sym_b])
                if corr > self.max_correlation:
                    # Remove o de menor score
                    loser = sym_b if score_map.get(sym_a, 0) >= score_map.get(sym_b, 0) \
                            else sym_a
                    rejected.add(loser)
                    log.info(
                        f"PairScreener: {sym_a}/{sym_b} corr={corr:.2f} > "
                        f"{self.max_correlation} — removendo {loser}"
                    )

        kept = [r for r in candidates if r.symbol not in rejected]
        return kept, list(rejected)

    # ------------------------------------------------------------------
    # Helpers de métricas
    # ------------------------------------------------------------------
    def _spread_pips(self, symbol: str, pip: float) -> float:
        """Spread médio em pips — busca últimos N ticks do MT5 ou retorna 0."""
        if not _MT5_OK:
            return 0.0
        try:
            ticks = mt5.copy_ticks_from(
                symbol,
                mt5.symbol_info_tick(symbol).time - self.tick_history * 60,
                self.tick_history,
                mt5.COPY_TICKS_ALL,
            )
            if ticks is None or len(ticks) == 0:
                return 0.0
            spreads = [(t[2] - t[1]) / pip for t in ticks]   # (ask - bid) / pip
            return float(np.mean(spreads))
        except Exception:
            return 0.0

    def _atr_pips(
        self,
        symbol: str,
        bars: Optional[pd.DataFrame],
        pip: float,
        period: int = 14,
    ) -> float:
        """ATR(14) em pips calculado a partir das barras fornecidas."""
        df = bars
        if df is None or len(df) < period + 1:
            # Fallback: busca barras H1 do MT5
            df = self._fetch_bars_mt5(symbol, period + 5)
        if df is None or len(df) < period + 1:
            return self.min_atr_pips   # sem dados → passa no filtro (não penaliza)

        high  = df["high"].values
        low   = df["low"].values
        close = df["close"].values

        prev_close = close[:-1]
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(np.abs(high[1:] - prev_close), np.abs(low[1:] - prev_close)),
        )
        atr = float(np.mean(tr[-period:]))
        return atr / pip

    def _garch_vol(self, symbol: str, bars: Optional[pd.DataFrame]) -> float:
        """
        Volatilidade anualizada GARCH(1,1) em %.
        Se não tiver barras suficientes, usa vol histórica simples como proxy.
        """
        df = bars
        if df is None or len(df) < 100:
            df = self._fetch_bars_mt5(symbol, 200)
        if df is None or len(df) < 50:
            return 0.0   # sem dados → passa no filtro máximo

        closes = df["close"].astype(float)
        returns = np.log(closes / closes.shift(1)).dropna()

        try:
            from arch import arch_model
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model  = arch_model(returns * 10_000, vol="Garch", p=1, q=1,
                                    dist="t", mean="Constant")
                result = model.fit(disp="off", show_warning=False)
                fc     = result.forecast(horizon=1, reindex=False)
                var_1  = float(fc.variance.values[-1][0])
                # bps → % anualizado (H1: 24 barras/dia)
                bars_per_day = 24
                vol_pct_ann  = (np.sqrt(var_1 * bars_per_day) / 100) * np.sqrt(252)
                return float(vol_pct_ann)
        except Exception:
            # Fallback: std anualizada simples
            std_daily = float(returns.std()) * np.sqrt(24)
            return float(std_daily * np.sqrt(252) * 100)

    def _fetch_bars_mt5(self, symbol: str, n: int) -> Optional[pd.DataFrame]:
        """Busca n barras H1 do MT5 para cálculos internos."""
        if not _MT5_OK:
            return None
        try:
            rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, n)
            if rates is None or len(rates) == 0:
                return None
            df = pd.DataFrame(rates)
            df.columns = [c.lower() for c in df.dtype.names] \
                if hasattr(rates, "dtype") else df.columns
            # copy_rates retorna structured array
            df = pd.DataFrame({
                "open":  rates["open"],
                "high":  rates["high"],
                "low":   rates["low"],
                "close": rates["close"],
            })
            return df
        except Exception:
            return None


# ======================================================================
# TESTE LOCAL (sem MT5)
# ======================================================================
if __name__ == "__main__":
    import sys
    from signals.geo_score import GeoScorer

    np.random.seed(42)

    def _make_bars(n: int = 300, base: float = 1.08, vol: float = 0.0003) -> pd.DataFrame:
        prices = base + np.cumsum(np.random.normal(0, vol, n))
        prices = np.maximum(prices, base * 0.5)
        high   = prices + np.abs(np.random.normal(0, vol * 0.5, n))
        low    = prices - np.abs(np.random.normal(0, vol * 0.5, n))
        return pd.DataFrame({"open": prices, "high": high, "low": low, "close": prices})

    bars: dict[str, pd.DataFrame] = {
        "EURUSD": _make_bars(300, 1.0850, 0.0003),
        "GBPUSD": _make_bars(300, 1.2700, 0.0004),
        "USDJPY": _make_bars(300, 145.0,  0.05),
        "AUDUSD": _make_bars(300, 0.6450, 0.0003),
        "NZDUSD": _make_bars(300, 0.5950, 0.0003),
        "USDCAD": _make_bars(300, 1.3600, 0.0003),
        "USDCHF": _make_bars(300, 0.8950, 0.0003),
        "EURGBP": _make_bars(300, 0.8550, 0.0002),
        "XAUUSD": _make_bars(300, 2350.0, 5.00),
    }

    screener = PairScreener()
    geo      = GeoScorer()

    print("\n" + "=" * 60)
    print("  PAIR SCREENER — Teste sem MT5 (dados sintéticos)")
    print("=" * 60)

    headlines_normal = ["Fed holds rates", "Markets calm"]
    report = screener.screen(geo, bars, headlines_normal)

    print(f"\n  Pares selecionados ({len(report.selected)}): {report.selected}")
    if report.rejected_correlation:
        print(f"  Rejeitados por correlação: {report.rejected_correlation}")

    print("\n  Detalhes por símbolo:")
    for r in report.all_results:
        status = "OK " if r.passed else "REJ"
        reason = f" | {r.fail_reason}" if r.fail_reason else ""
        print(
            f"    [{status}] {r.symbol:<8} "
            f"spread={r.spread_pips:.2f}p  ATR={r.atr_pips:.1f}p  "
            f"GARCH={r.garch_vol_pct:.1f}%  score={r.score:.3f}{reason}"
        )

    print("\n  --- Cenário CRITICAL GeoScore ---")
    headlines_crisis = [
        "Taiwan invasion imminent", "PLA military exercises near Taiwan strait",
        "Emergency Fed meeting", "bank run fears", "US chip ban China",
    ]
    report_crisis = screener.screen(geo, bars, headlines_crisis)
    print(f"  Pares selecionados: {report_crisis.selected}")
    for r in report_crisis.all_results:
        if not r.passed:
            print(f"    REJEITADO {r.symbol}: {r.fail_reason}")

    print("\n  [OK] PairScreener OK\n")
