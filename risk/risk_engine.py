"""
risk/risk_engine.py
===================
Risk Engine em tempo real para Vantage Quant System.

Controles implementados:
  1. Circuit Breaker Global     — PnL diário < -2% NAV -> halt total
  2. Stop por posição           — perda individual > -1.5% -> fecha posição
  3. VaR histórico 95% rolling  — limite de exposure total
  4. Geo Circuit Breaker        — GeoScore > 0.85 -> leverage 25%
  5. Correlation Filter         — bloqueia se intra-portfolio corr > 0.70
  6. Slippage Monitor           — alerta se avg slippage > 2 pips
  7. Max simultaneous positions — limite de posições abertas

Uso:
    from risk.risk_engine import RiskEngine
    engine = RiskEngine(nav=100_000)
    approved, size = engine.pre_trade_check(signal_packet)
"""

import json
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime, date
import logging

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [RISK] %(levelname)s %(message)s")
log = logging.getLogger("RiskEngine")


@dataclass
class RiskLimits:
    max_daily_loss_pct:   float = 0.020   # 2% do NAV
    max_position_loss_pct: float = 0.015  # 1.5% por posição
    var_95_limit_pct:     float = 0.015   # VaR 95% máximo
    geo_score_threshold:  float = 0.850   # acima -> reduz leverage
    geo_leverage_scalar:  float = 0.250   # 25% do tamanho normal
    max_corr:             float = 0.700   # correlação máxima intra-portfolio
    max_slippage_pips:    float = 2.000   # alerta de slippage
    max_open_positions:   int   = 6       # máximo de posições simultâneas
    var_window_days:      int   = 20      # janela para VaR histórico


@dataclass
class Position:
    symbol:     str
    direction:  str         # "BUY" | "SELL"
    volume:     float       # lotes
    entry_price: float
    open_time:  datetime = field(default_factory=datetime.utcnow)
    current_pnl: float  = 0.0


@dataclass
class RiskDecision:
    approved:       bool
    adjusted_size:  float       # lotes após ajuste de risco
    scalar_applied: float       # multiplicador aplicado
    reason:         str
    alerts:         List[str] = field(default_factory=list)


class RiskEngine:

    _STATE_PATH = "logs/risk_state.json"

    def __init__(self, nav: float, limits: Optional[RiskLimits] = None,
                 state_path: Optional[str] = None):
        self.nav              = nav
        self.limits           = limits or RiskLimits()
        self.positions:  Dict[str, Position]  = {}
        self.daily_pnl:  float = 0.0
        self.daily_date: date  = date.today()
        self._pnl_history: List[float] = []   # retornos diários para VaR
        self._fill_slippages: List[float] = []
        self._halted: bool = False
        self._state_path = state_path or self._STATE_PATH
        self.load_state()

    # ------------------------------------------------------------------
    # PRE-TRADE CHECK — ponto central de decisão
    # ------------------------------------------------------------------
    def pre_trade_check(
        self,
        symbol:      str,
        direction:   str,
        base_size:   float,      # lotes do Kelly
        geo_score:   float,
        signal_conf: float,
        prices_df:   Optional[pd.DataFrame] = None,  # para VaR e correlação
    ) -> RiskDecision:

        self._reset_daily_if_needed()
        alerts: List[str] = []
        scalar = 1.0

        # 0. Sistema em halt?
        if self._halted:
            return RiskDecision(False, 0.0, 0.0, "Sistema em HALT — circuit breaker ativo", alerts)

        # 1. Circuit Breaker Global
        if self.daily_pnl / self.nav < -self.limits.max_daily_loss_pct:
            self._halted = True
            log.critical(f"CIRCUIT BREAKER: PnL diário {self.daily_pnl/self.nav*100:.2f}%")
            return RiskDecision(False, 0.0, 0.0,
                f"Circuit Breaker: PnL diário {self.daily_pnl:.2f} < -{self.limits.max_daily_loss_pct*100:.0f}% NAV",
                alerts)

        # 2. Máximo de posições abertas
        if len(self.positions) >= self.limits.max_open_positions:
            return RiskDecision(False, 0.0, 0.0,
                f"Max posições ({self.limits.max_open_positions}) atingido", alerts)

        # 3. Geo Circuit Breaker
        if geo_score > self.limits.geo_score_threshold:
            scalar *= self.limits.geo_leverage_scalar
            alerts.append(f"GeoScore={geo_score:.3f} > {self.limits.geo_score_threshold} -> leverage 25%")
            log.warning(f"Geo risk: scalar reduzido para {scalar:.2f}")

        # 4. VaR histórico
        if prices_df is not None and len(self._pnl_history) >= self.limits.var_window_days:
            var_breach, var_val = self._check_var(prices_df)
            if var_breach:
                scalar *= 0.5
                alerts.append(f"VaR 95% = {var_val*100:.2f}% > limite -> size reduzido 50%")

        # 5. Correlation Filter
        if prices_df is not None and len(self.positions) > 0:
            high_corr = self._check_correlation(symbol, prices_df)
            if high_corr:
                scalar *= 0.5
                alerts.append(f"Alta correlação com posição existente -> size reduzido 50%")

        # 6. Confidence scaling
        scalar *= max(signal_conf, 0.3)   # mínimo 30% do size se baixa confiança

        # 7. Slippage alert (sem bloquear, apenas alertar)
        if self._avg_slippage() > self.limits.max_slippage_pips:
            alerts.append(f"Avg slippage {self._avg_slippage():.1f} pips > {self.limits.max_slippage_pips} pips")

        adjusted = round(max(base_size * scalar, 0.01), 2)
        reason   = f"Aprovado | scalar={scalar:.3f} | size={base_size:.2f}->{adjusted:.2f} lotes"
        if alerts:
            reason += " | ALERTAS: " + "; ".join(alerts)

        log.info(f"PRE-TRADE {symbol} {direction}: {reason}")
        return RiskDecision(True, adjusted, round(scalar, 4), reason, alerts)

    # ------------------------------------------------------------------
    # POSITION MANAGEMENT
    # ------------------------------------------------------------------
    def save_state(self) -> None:
        """Persiste estado crítico em logs/risk_state.json."""
        state = {
            "date":        str(self.daily_date),
            "halted":      self._halted,
            "daily_pnl":   self.daily_pnl,
            "pnl_history": self._pnl_history,
            "positions": {
                sym: {
                    "symbol":      pos.symbol,
                    "direction":   pos.direction,
                    "volume":      pos.volume,
                    "entry_price": pos.entry_price,
                    "open_time":   pos.open_time.isoformat(),
                    "current_pnl": pos.current_pnl,
                }
                for sym, pos in self.positions.items()
            },
        }
        try:
            Path(self._state_path).parent.mkdir(parents=True, exist_ok=True)
            Path(self._state_path).write_text(json.dumps(state, indent=2))
        except Exception as exc:
            log.error(f"save_state falhou: {exc}")

    def load_state(self) -> None:
        """Restaura estado do arquivo JSON, se existir e for do dia atual."""
        path = Path(self._state_path)
        if not path.exists():
            return
        try:
            state = json.loads(path.read_text())
        except Exception as exc:
            log.warning(f"load_state: arquivo inválido — ignorado ({exc})")
            return

        # Histórico VaR sempre restaurado (multi-dia)
        self._pnl_history = state.get("pnl_history", [])

        # daily_pnl e halt só valem se for o mesmo dia
        if state.get("date") == str(date.today()):
            self.daily_pnl = state.get("daily_pnl", 0.0)
            self._halted   = state.get("halted", False)

            for sym, p in state.get("positions", {}).items():
                self.positions[sym] = Position(
                    symbol=p["symbol"],
                    direction=p["direction"],
                    volume=p["volume"],
                    entry_price=p["entry_price"],
                    open_time=datetime.fromisoformat(p["open_time"]),
                    current_pnl=p.get("current_pnl", 0.0),
                )
            log.info(
                f"load_state: PnL={self.daily_pnl:.2f} | halted={self._halted} "
                f"| posições={len(self.positions)} | VaR pts={len(self._pnl_history)}"
            )
        else:
            log.info(f"load_state: data diferente ({state.get('date')}) — daily PnL/halt zerados; VaR restaurado ({len(self._pnl_history)} pts)")

    def open_position(self, pos: Position) -> None:
        self.positions[pos.symbol] = pos
        log.info(f"OPEN {pos.symbol} {pos.direction} {pos.volume}L @ {pos.entry_price}")
        self.save_state()

    # Pip sizes por símbolo (mantido em sync com order_manager._PIP_SIZE)
    _PIP = {"USDJPY": 0.01, "USDJPY+": 0.01, "EURJPY": 0.01, "EURJPY+": 0.01,
            "GBPJPY": 0.01, "GBPJPY+": 0.01, "AUDJPY": 0.01, "AUDJPY+": 0.01,
            "NZDJPY": 0.01, "NZDJPY+": 0.01, "CADJPY": 0.01, "CADJPY+": 0.01,
            "XAUUSD": 1.00, "XAUUSD+": 1.00}

    def update_position_pnl(self, symbol: str, current_price: float,
                            pip_value: float = 10.0) -> Optional[str]:
        """Atualiza PnL e verifica stop por posição. Retorna 'CLOSE' se stop atingido."""
        pos = self.positions.get(symbol)
        if not pos:
            return None

        pip_size = self._PIP.get(symbol, 0.0001)
        pip_diff = (current_price - pos.entry_price) / pip_size
        if pos.direction == "SELL":
            pip_diff = -pip_diff

        pos.current_pnl = pip_diff * pos.volume * pip_value

        loss_pct = pos.current_pnl / self.nav
        if loss_pct < -self.limits.max_position_loss_pct:
            log.warning(f"STOP POSIÇÃO {symbol}: {loss_pct*100:.2f}% < -{self.limits.max_position_loss_pct*100:.0f}%")
            return "CLOSE"
        return None

    def close_position(self, symbol: str, exit_price: float,
                       pip_value: float = 10.0) -> float:
        pos = self.positions.pop(symbol, None)
        if not pos:
            return 0.0
        pip_size = self._PIP.get(pos.symbol, 0.0001)
        pip_diff = (exit_price - pos.entry_price) / pip_size
        if pos.direction == "SELL":
            pip_diff = -pip_diff
        pnl = pip_diff * pos.volume * pip_value
        self.daily_pnl += pnl
        log.info(f"CLOSE {symbol} PnL={pnl:.2f} | Daily PnL={self.daily_pnl:.2f}")
        self.save_state()
        return pnl

    def record_fill_slippage(self, requested_price: float,
                             filled_price: float) -> None:
        slip_pips = abs(filled_price - requested_price) * 10_000
        self._fill_slippages.append(slip_pips)
        if len(self._fill_slippages) > 50:
            self._fill_slippages.pop(0)

    def record_daily_return(self) -> None:
        ret = self.daily_pnl / self.nav
        self._pnl_history.append(ret)
        if len(self._pnl_history) > 252:
            self._pnl_history.pop(0)

    def reset_halt(self) -> None:
        self._halted = False
        log.info("Halt resetado manualmente.")

    # ------------------------------------------------------------------
    # INTERNOS
    # ------------------------------------------------------------------
    def _reset_daily_if_needed(self) -> None:
        today = date.today()
        if today != self.daily_date:
            self.record_daily_return()
            self.daily_pnl  = 0.0
            self.daily_date = today
            self._halted    = False
            self.save_state()

    def _check_var(self, prices_df: pd.DataFrame) -> Tuple[bool, float]:
        rets   = np.array(self._pnl_history[-self.limits.var_window_days:])
        var_95 = float(abs(np.percentile(rets, 5)))
        return var_95 > self.limits.var_95_limit_pct, var_95

    def _check_correlation(self, symbol: str,
                           prices_df: pd.DataFrame) -> bool:
        if symbol not in prices_df.columns:
            return False
        for existing in self.positions:
            if existing in prices_df.columns:
                corr = prices_df[symbol].corr(prices_df[existing])
                if abs(corr) > self.limits.max_corr:
                    log.warning(f"Alta corr {symbol}/{existing}: {corr:.3f}")
                    return True
        return False

    def _avg_slippage(self) -> float:
        return np.mean(self._fill_slippages) if self._fill_slippages else 0.0

    def status(self) -> dict:
        return {
            "NAV":             self.nav,
            "Daily PnL":       round(self.daily_pnl, 2),
            "Daily PnL %":     round(self.daily_pnl / self.nav * 100, 3),
            "Open Positions":  len(self.positions),
            "Halted":          self._halted,
            "Avg Slippage":    round(self._avg_slippage(), 2),
            "VaR history pts": len(self._pnl_history),
        }


# ======================================================================
# TESTE LOCAL
# ======================================================================
if __name__ == "__main__":
    print("\n" + "="*55)
    print("  RISK ENGINE — Teste completo")
    print("="*55)

    engine = RiskEngine(nav=100_000)

    tests = [
        ("EURUSD", "BUY",  0.10, 0.30, 0.75),
        ("GBPUSD", "BUY",  0.10, 0.55, 0.80),
        ("USDJPY", "SELL", 0.10, 0.88, 0.70),  # geo alto
    ]

    for sym, direc, size, geo, conf in tests:
        dec = engine.pre_trade_check(sym, direc, size, geo, conf)
        print(f"\n  {sym} {direc} | Geo={geo} | Conf={conf}")
        print(f"  Approved : {dec.approved}")
        print(f"  Adj size : {dec.adjusted_size} lotes")
        print(f"  Scalar   : {dec.scalar_applied}")
        if dec.alerts:
            print(f"  Alerts   : {dec.alerts}")

    print(f"\n  Status: {engine.status()}")
    print("\n  ✅ RiskEngine OK\n")
