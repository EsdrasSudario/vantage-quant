"""
signals/garch_model.py
======================
GARCH(1,1) Volatility Forecasting para Vantage Quant System.

Função principal:
  - Estima volatilidade condicional de retornos tick/OHLCV
  - Gera forecasts de vol para N períodos à frente
  - Classifica regime (baixa/média/alta vol) para ajuste de sizing
  - Integra com Kelly fracionário via vol_scaling()

Uso:
    from signals.garch_model import GARCHModel
    g = GARCHModel("EURUSD")
    g.fit(returns_series)
    vol_forecast = g.forecast(horizon=5)
    regime = g.regime()
"""

import numpy as np
import pandas as pd
from arch import arch_model
from dataclasses import dataclass
from typing import Optional
import warnings
warnings.filterwarnings("ignore")


@dataclass
class GARCHResult:
    symbol: str
    current_vol_annualized: float   # % anualizado
    forecast_vol: np.ndarray        # próximos N períodos (diário)
    regime: str                     # "low" | "medium" | "high"
    alpha: float                    # coef ARCH
    beta: float                     # coef GARCH
    persistence: float              # alpha + beta (< 1 = estacionário)
    half_life: int                  # dias para reverter à média
    size_scalar: float              # multiplicador para Kelly (0.5 a 1.0)


class GARCHModel:
    """
    GARCH(1,1) com distribuição t-Student (fat tails — essencial para FOREX).
    
    Parâmetros de regime (baseados em vol histórica do EURUSD):
      - low:    annualized vol < 6%
      - medium: 6% <= vol < 10%
      - high:   vol >= 10%
    """

    REGIME_THRESHOLDS = {"low": 6.0, "medium": 10.0}

    def __init__(self, symbol: str, freq: str = "1min"):
        self.symbol = symbol
        self.freq   = freq
        self.model  = None
        self.result = None
        self._fitted = False

    # ------------------------------------------------------------------
    # FIT
    # ------------------------------------------------------------------
    def fit(self, prices: pd.Series) -> "GARCHModel":
        """
        Ajusta GARCH(1,1) a uma série de preços (close ou mid).
        
        Args:
            prices: pd.Series com preços (index = datetime)
        """
        # Retornos logarítmicos em bps (escala arch)
        returns = np.log(prices / prices.shift(1)).dropna() * 10_000

        self.model  = arch_model(returns, vol="Garch", p=1, q=1,
                                 dist="t", mean="Constant")
        self.result = self.model.fit(disp="off", show_warning=False)
        self._fitted = True
        return self

    # ------------------------------------------------------------------
    # FORECAST
    # ------------------------------------------------------------------
    def forecast(self, horizon: int = 5) -> GARCHResult:
        """
        Gera forecast de volatilidade para os próximos `horizon` períodos.

        Returns:
            GARCHResult com vol, regime, coeficientes e scalar de sizing.
        """
        if not self._fitted:
            raise RuntimeError("Chame .fit(prices) antes de .forecast()")

        fc = self.result.forecast(horizon=horizon, reindex=False)
        # Variância condicional → desvio padrão → anualizado
        # bps → % : dividir por 100; anualizar: * sqrt(252 * períodos/dia)
        periods_per_day = {"1min": 1440, "5min": 288, "1h": 24, "1d": 1}
        ppd = periods_per_day.get(self.freq, 288)

        var_forecast  = fc.variance.values[-1]                     # shape (horizon,)
        vol_bps_daily = np.sqrt(var_forecast * ppd)                # diário em bps
        vol_pct_ann   = vol_bps_daily / 100 * np.sqrt(252)        # anualizado em %

        current_vol = float(vol_pct_ann[0])

        # Parâmetros do modelo
        params      = self.result.params
        alpha       = float(params.get("alpha[1]", 0))
        beta        = float(params.get("beta[1]", 0))
        persistence = alpha + beta
        half_life   = int(np.log(0.5) / np.log(persistence)) if persistence < 1 else 999

        # Regime
        if current_vol < self.REGIME_THRESHOLDS["low"]:
            regime = "low"
            scalar = 1.0          # vol baixa → sizing normal
        elif current_vol < self.REGIME_THRESHOLDS["medium"]:
            regime = "medium"
            scalar = 0.75         # vol média → reduz 25%
        else:
            regime = "high"
            scalar = 0.50         # vol alta → reduz 50%

        return GARCHResult(
            symbol=self.symbol,
            current_vol_annualized=round(current_vol, 4),
            forecast_vol=vol_pct_ann,
            regime=regime,
            alpha=round(alpha, 6),
            beta=round(beta, 6),
            persistence=round(persistence, 6),
            half_life=half_life,
            size_scalar=scalar,
        )

    # ------------------------------------------------------------------
    # UTILITÁRIOS
    # ------------------------------------------------------------------
    def summary(self) -> str:
        if not self._fitted:
            return "Modelo não ajustado."
        return str(self.result.summary())

    @staticmethod
    def vol_scaling(base_size: float, garch_result: GARCHResult) -> float:
        """Aplica scalar de vol ao tamanho de posição base."""
        return round(base_size * garch_result.size_scalar, 4)


# ======================================================================
# TESTE LOCAL
# ======================================================================
if __name__ == "__main__":
    import sys

    print("\n" + "="*55)
    print("  GARCH(1,1) — Teste com dados sintéticos EURUSD")
    print("="*55)

    # Simula série de preços EURUSD (GBM com vol realista)
    np.random.seed(42)
    n     = 2000
    dt    = 1/1440                    # 1 minuto
    sigma = 0.065                     # ~6.5% vol anualizada
    S0    = 1.0850
    returns_sim = np.random.normal(0, sigma * np.sqrt(dt), n)
    prices_sim  = S0 * np.exp(np.cumsum(returns_sim))
    idx  = pd.date_range("2026-08-01", periods=n, freq="1min")
    prices = pd.Series(prices_sim, index=idx, name="EURUSD_close")

    # Fit e forecast
    g = GARCHModel("EURUSD", freq="1min")
    g.fit(prices)
    res = g.forecast(horizon=5)

    print(f"\n  Símbolo          : {res.symbol}")
    print(f"  Vol atual (ann.) : {res.current_vol_annualized:.2f}%")
    print(f"  Regime           : {res.regime.upper()}")
    print(f"  Alpha (ARCH)     : {res.alpha}")
    print(f"  Beta  (GARCH)    : {res.beta}")
    print(f"  Persistência     : {res.persistence}")
    print(f"  Half-life        : {res.half_life} períodos")
    print(f"  Size scalar      : {res.size_scalar}")
    print(f"\n  Forecast vol (próx. 5 períodos):")
    for i, v in enumerate(res.forecast_vol, 1):
        print(f"    t+{i}: {v:.2f}%")

    # Sizing
    base_lot = 0.10
    adj_lot  = GARCHModel.vol_scaling(base_lot, res)
    print(f"\n  Kelly base lot   : {base_lot}")
    print(f"  GARCH-adj lot    : {adj_lot}")
    print("\n  ✅ GARCH module OK\n")
