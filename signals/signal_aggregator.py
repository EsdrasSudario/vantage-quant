"""
signals/signal_aggregator.py
=============================
Agrega GARCH + Kalman + GeoScore em um sinal unificado de trading.

Output: SignalPacket com direção, sizing scalar, confiança e motivo.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Optional
from signals.garch_model   import GARCHModel, GARCHResult
from signals.kalman_filter import KalmanTrend, KalmanSignal
from signals.geo_score     import GeoScorer, GeoResult


@dataclass
class SignalPacket:
    symbol:        str
    direction:     str      # "BUY" | "SELL" | "FLAT"
    confidence:    float    # 0.0 a 1.0
    size_scalar:   float    # multiplicador final para Kelly
    garch_regime:  str
    kalman_signal: str
    geo_level:     str
    geo_score:     float
    reason:        str      # texto explicativo


class SignalAggregator:
    """
    Lógica de agregação:
      1. GeoScore CRITICAL → FLAT (não operar)
      2. Kalman + GARCH concordam → sinal confirmado
      3. Size scalar = Kelly_scalar * GARCH_scalar * Geo_scalar
    """

    GEO_SCALARS = {"LOW": 1.0, "MEDIUM": 0.75, "HIGH": 0.50, "CRITICAL": 0.0}

    def __init__(self, symbol: str):
        self.symbol  = symbol
        self.garch   = GARCHModel(symbol)
        self.kalman  = KalmanTrend(process_noise=1e-5, obs_noise=1e-3, signal_threshold=0.3)
        self.geo     = GeoScorer()
        self._garch_fitted = False

    def fit_garch(self, prices: pd.Series) -> None:
        self.garch.fit(prices)
        self._garch_fitted = True

    def evaluate(
        self,
        current_price: float,
        price_history: pd.Series,
        headlines: list,
    ) -> SignalPacket:

        # Warm-up Kalman com histórico se ainda não inicializado
        if not self.kalman._initialized and len(price_history) > 50:
            for p in price_history.iloc[-200:].values:
                self.kalman.update(float(p))

        # 1. GeoScore
        geo: GeoResult = self.geo.score(headlines)
        geo_scalar = self.GEO_SCALARS[geo.level]

        if geo.level == "CRITICAL":
            return SignalPacket(
                symbol=self.symbol, direction="FLAT",
                confidence=0.0, size_scalar=0.0,
                garch_regime="N/A", kalman_signal="N/A",
                geo_level=geo.level, geo_score=geo.score,
                reason=f"GeoScore CRITICAL ({geo.score:.3f}) — trading suspenso"
            )

        # 2. GARCH
        if not self._garch_fitted:
            self.fit_garch(price_history)
        garch_res: GARCHResult = self.garch.forecast(horizon=3)

        # 3. Kalman
        kal: KalmanSignal = self.kalman.update(current_price)

        # 4. Agregação direcional
        direction = "FLAT"
        confidence = 0.0
        MIN_CONFIDENCE = 0.15   # filtro: descarta sinais de baixa qualidade

        if kal.signal in ["BUY", "SELL"]:
            raw_conf   = kal.confidence * (1 - geo.score)
            # Filtro de confianca minima: evita trades marginais que so pagam comissao
            if raw_conf < MIN_CONFIDENCE:
                return SignalPacket(
                    symbol=self.symbol, direction="FLAT",
                    confidence=0.0, size_scalar=0.0,
                    garch_regime=garch_res.regime, kalman_signal=kal.signal,
                    geo_level=geo.level, geo_score=geo.score,
                    reason=f"Confianca {raw_conf:.3f} < {MIN_CONFIDENCE} (filtro qualidade)"
                )
            direction  = kal.signal
            confidence = raw_conf
        else:
            return SignalPacket(
                symbol=self.symbol, direction="FLAT",
                confidence=0.0, size_scalar=0.0,
                garch_regime=garch_res.regime, kalman_signal=kal.signal,
                geo_level=geo.level, geo_score=geo.score,
                reason=f"Kalman FLAT (inovacao insuficiente)"
            )

        # 5. Size scalar combinado
        size_scalar = round(garch_res.size_scalar * geo_scalar, 4)

        reason = (
            f"Kalman={kal.signal} conf={kal.confidence:.2f} | "
            f"GARCH regime={garch_res.regime} vol={garch_res.current_vol_annualized:.1f}% | "
            f"Geo={geo.level} ({geo.score:.3f})"
        )

        return SignalPacket(
            symbol=self.symbol, direction=direction,
            confidence=round(confidence, 4), size_scalar=size_scalar,
            garch_regime=garch_res.regime, kalman_signal=kal.signal,
            geo_level=geo.level, geo_score=geo.score, reason=reason
        )


# ======================================================================
# TESTE LOCAL
# ======================================================================
if __name__ == "__main__":
    import numpy as np

    np.random.seed(7)
    n   = 1000
    idx = pd.date_range("2026-08-01", periods=n, freq="5min")
    prices = pd.Series(
        1.0850 + np.cumsum(np.random.normal(0.00002, 0.0003, n)),
        index=idx
    )

    agg = SignalAggregator("EURUSD")
    agg.fit_garch(prices)

    scenarios = [
        ("Normal", ["Fed holds", "EUR steady"]),
        ("Tensão", ["Iran threatens Hormuz", "Oil surges"]),
        ("Crise",  ["Taiwan invasion imminent", "Emergency Fed meeting", "bank run"]),
    ]

    print("\n" + "="*60)
    print("  SIGNAL AGGREGATOR — EURUSD")
    print("="*60)

    for name, headlines in scenarios:
        pkt = agg.evaluate(float(prices.iloc[-1]), prices, headlines)
        print(f"\n  [{name}]")
        print(f"  Direction  : {pkt.direction}")
        print(f"  Confidence : {pkt.confidence:.4f}")
        print(f"  SizeScalar : {pkt.size_scalar}")
        print(f"  Reason     : {pkt.reason}")
    print("\n  ✅ SignalAggregator OK\n")
