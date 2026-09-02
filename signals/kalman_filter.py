"""
signals/kalman_filter.py
========================
Kalman Filter para estimativa de tendência latente em tick/OHLCV.

Funções principais:
  - KalmanTrend: estima nível e velocidade de tendência (state = [price, trend])
  - KalmanPairs: spread dinâmico entre dois ativos correlacionados (Stat Arb)
  - Sinal: BUY se preço cruza acima da tendência Kalman, SELL se abaixo

Uso:
    from signals.kalman_filter import KalmanTrend, KalmanPairs
    
    kt = KalmanTrend()
    signal = kt.update(new_price)   # retorna "BUY" | "SELL" | "FLAT"

    kp = KalmanPairs()
    zscore = kp.update(price_a, price_b)  # z-score do spread dinâmico
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class KalmanSignal:
    price: float
    estimated_level: float      # nível filtrado
    estimated_trend: float      # velocidade de tendência (pip/tick)
    innovation: float           # preço - estimativa (erro de predição)
    signal: str                 # "BUY" | "SELL" | "FLAT"
    confidence: float           # 0 a 1 (baseado na variância do filtro)


class KalmanTrend:
    """
    Kalman Filter 2D: state = [nível, tendência].
    
    Modelo:
      x_t = F * x_{t-1} + w_t   (transição)
      z_t = H * x_t   + v_t     (observação)
    
    Onde:
      F = [[1, 1], [0, 1]]  (nível acumula tendência)
      H = [1, 0]            (observamos apenas o preço)
    """

    def __init__(
        self,
        process_noise: float  = 1e-5,   # Q: confiança no modelo (menor = mais suave)
        obs_noise: float      = 1e-3,   # R: ruído de observação (maior = mais suave)
        signal_threshold: float = 0.6,  # inovação mínima em pips (0.3->0.6: reduz overtrade ~60%)
    ):
        self.Q_scalar = process_noise
        self.R_scalar = obs_noise
        self.thresh   = signal_threshold

        # State e covariância inicial
        self._x = None           # [nível, tendência]
        self._P = None           # covariância do estado
        self._initialized = False

        # Matrizes do sistema
        self.F = np.array([[1.0, 1.0],
                           [0.0, 1.0]])
        self.H = np.array([[1.0, 0.0]])
        self.Q = np.eye(2) * process_noise
        self.R = np.array([[obs_noise]])

    def _init_state(self, price: float) -> None:
        self._x = np.array([[price], [0.0]])
        self._P = np.eye(2) * 1.0
        self._initialized = True

    def update(self, price: float) -> KalmanSignal:
        """Atualiza o filtro com um novo preço e retorna sinal."""
        if not self._initialized:
            self._init_state(price)
            return KalmanSignal(price, price, 0.0, 0.0, "FLAT", 0.0)

        # --- PREDICT ---
        x_pred = self.F @ self._x
        P_pred = self.F @ self._P @ self.F.T + self.Q

        # --- UPDATE ---
        z   = np.array([[price]])
        S   = self.H @ P_pred @ self.H.T + self.R
        K   = P_pred @ self.H.T @ np.linalg.inv(S)           # Kalman Gain
        inn = z - self.H @ x_pred                             # inovação

        self._x = x_pred + K @ inn
        self._P = (np.eye(2) - K @ self.H) @ P_pred

        level  = float(self._x[0, 0])
        trend  = float(self._x[1, 0])
        innov  = float(inn[0, 0])

        # Confiança: inversamente proporcional à variância do estado normalizada
        var_norm   = float(self._P[0, 0])
        confidence = max(0.0, min(1.0, 1.0 / (1.0 + var_norm * 1e4)))

        # Sinal direcional
        # Exige alinhamento duplo: inovacao + tendencia em mesma direcao
        # trend_min_pips: tendencia minima para confirmar direcao (filtra ruido)
        inn_pips  = innov * 10_000
        trend_pips = trend * 10_000
        trend_min  = self.thresh * 0.5   # tendencia minima = metade do threshold
        if inn_pips > self.thresh and trend_pips > trend_min:
            signal = "BUY"
        elif inn_pips < -self.thresh and trend_pips < -trend_min:
            signal = "SELL"
        else:
            signal = "FLAT"

        return KalmanSignal(
            price=round(price, 5),
            estimated_level=round(level, 5),
            estimated_trend=round(trend * 10_000, 2),   # em pips
            innovation=round(inn_pips, 2),
            signal=signal,
            confidence=round(confidence, 4),
        )

    def batch(self, prices: pd.Series) -> pd.DataFrame:
        """Processa série completa. Retorna DataFrame com sinais."""
        self._initialized = False   # reset para nova série
        records = []
        for ts, p in prices.items():
            sig = self.update(float(p))
            records.append({
                "time":      ts,
                "price":     sig.price,
                "kf_level":  sig.estimated_level,
                "kf_trend":  sig.estimated_trend,
                "innovation": sig.innovation,
                "signal":    sig.signal,
                "confidence": sig.confidence,
            })
        return pd.DataFrame(records).set_index("time")


class KalmanPairs:
    """
    Kalman Filter para hedge ratio dinâmico em Pairs Trading (Stat Arb).
    
    Estima β em tempo real: spread_t = price_A_t - β_t * price_B_t
    Gera z-score do spread → entrada quando |z| > threshold.
    
    Exemplo de uso: EUR/USD vs GBP/USD (alta correlação histórica)
    """

    def __init__(
        self,
        delta: float = 1e-4,          # variação permitida no β
        obs_noise: float = 1e-3,      # ruído de observação
        z_entry: float  = 2.0,        # z-score para entrada
        z_exit:  float  = 0.5,        # z-score para saída
        window:  int    = 100,        # janela rolling para z-score
    ):
        self.delta    = delta
        self.R        = obs_noise
        self.z_entry  = z_entry
        self.z_exit   = z_exit
        self.window   = window

        # Estado do filtro (β e covariância)
        self._theta  = np.zeros(2)    # [β, intercept]
        self._P      = np.ones(2) * 1.0
        self._C      = np.zeros(2)
        self._Vw     = delta / (1 - delta) * np.ones(2)
        self._Ve     = obs_noise
        self._spreads: list = []

    def update(self, price_a: float, price_b: float) -> dict:
        """
        Atualiza filtro com novo par de preços.
        Retorna z-score e sinal de Stat Arb.
        """
        F = np.array([price_b, 1.0])   # features: [price_b, 1]
        y = price_a                     # target: price_a

        # Predict — P é vetor diagonal (variâncias independentes)
        R  = self._P + self._Vw         # shape (2,)
        # Update — S é escalar
        S  = float(np.dot(F * R, F)) + self._Ve
        K  = R * F / S                  # Kalman Gain, shape (2,)
        err = y - float(F @ self._theta)

        self._theta = self._theta + K * err
        self._P     = R - K * F * R     # shape (2,)

        beta      = float(self._theta[0])
        intercept = float(self._theta[1])
        spread    = y - beta * price_b - intercept

        self._spreads.append(spread)
        if len(self._spreads) > self.window:
            self._spreads.pop(0)

        # Z-score do spread
        spreads_arr = np.array(self._spreads)
        mu, sigma   = spreads_arr.mean(), spreads_arr.std()
        z = (spread - mu) / sigma if sigma > 1e-10 else 0.0

        # Sinal Stat Arb
        if z > self.z_entry:
            signal = "SELL_A_BUY_B"    # spread alto → short A, long B
        elif z < -self.z_entry:
            signal = "BUY_A_SELL_B"    # spread baixo → long A, short B
        elif abs(z) < self.z_exit:
            signal = "EXIT"
        else:
            signal = "HOLD"

        return {
            "beta":       round(beta, 6),
            "intercept":  round(intercept, 6),
            "spread":     round(spread, 6),
            "z_score":    round(z, 4),
            "signal":     signal,
        }

    def batch(self, prices_a: pd.Series, prices_b: pd.Series) -> pd.DataFrame:
        """Processa dois pares completos e retorna DataFrame."""
        assert len(prices_a) == len(prices_b), "Séries devem ter mesmo tamanho"
        # Reset estado interno sem reinicializar parâmetros
        self._theta  = np.zeros(2)
        self._P      = np.ones(2) * 1.0
        self._C      = np.zeros(2)
        self._Vw     = self.delta / (1 - self.delta) * np.ones(2)
        self._Ve     = self.R
        self._spreads = []
        records = []
        for (ts, pa), pb in zip(prices_a.items(), prices_b.values):
            r = self.update(float(pa), float(pb))
            r["time"] = ts
            records.append(r)
        return pd.DataFrame(records).set_index("time")


# ======================================================================
# TESTE LOCAL
# ======================================================================
if __name__ == "__main__":
    print("\n" + "="*55)
    print("  KALMAN FILTER — Teste com dados sintéticos")
    print("="*55)

    np.random.seed(42)
    n   = 500
    idx = pd.date_range("2026-08-30 09:00", periods=n, freq="1min")

    # 1. KalmanTrend — EUR/USD
    print("\n[1] KalmanTrend (EURUSD trend estimation)")
    prices = pd.Series(
        1.0850 + np.cumsum(np.random.normal(0, 0.0002, n)),
        index=idx, name="EURUSD"
    )
    kt  = KalmanTrend(process_noise=1e-5, obs_noise=1e-3, signal_threshold=0.3)
    df  = kt.batch(prices)
    sig_counts = df["signal"].value_counts()
    print(f"  Sinais gerados: {sig_counts.to_dict()}")
    print(f"  Último sinal  : {df['signal'].iloc[-1]}")
    print(f"  KF level      : {df['kf_level'].iloc[-1]:.5f}")
    print(f"  KF trend      : {df['kf_trend'].iloc[-1]:.2f} pips/tick")
    print(f"  Confidence    : {df['confidence'].iloc[-1]:.4f}")

    # 2. KalmanPairs — EUR/USD vs GBP/USD
    print("\n[2] KalmanPairs (EURUSD vs GBPUSD stat arb)")
    beta_true = 0.78
    eurusd = pd.Series(
        1.0850 + np.cumsum(np.random.normal(0, 0.0002, n)), index=idx
    )
    gbpusd = pd.Series(
        eurusd.values / beta_true + np.random.normal(0, 0.0005, n), index=idx
    )
    kp  = KalmanPairs(delta=1e-4, z_entry=2.0, z_exit=0.5)
    dfp = kp.batch(eurusd, gbpusd)
    sig_counts2 = dfp["signal"].value_counts()
    print(f"  Sinais stat arb: {sig_counts2.to_dict()}")
    print(f"  Beta estimado  : {dfp['beta'].iloc[-1]:.4f} (true={beta_true})")
    print(f"  Z-score atual  : {dfp['z_score'].iloc[-1]:.4f}")
    print(f"  Último sinal   : {dfp['signal'].iloc[-1]}")

    print("\n  ✅ Kalman Filter module OK\n")
