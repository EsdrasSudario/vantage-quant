"""
execution/order_manager.py
===========================
Order Management System (OMS) para Vantage Quant System.

Integra:
  - Kelly fracionário para position sizing
  - Risk Engine pre-trade check
  - Envio de ordens via MT5 Python API
  - Tracking de fills, slippage e PnL

Uso:
    from execution.order_manager import OrderManager
    oms = OrderManager(nav=100_000, mt5_connected=True)
    result = oms.execute(signal_packet, geo_score=0.3, capital=100_000)
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime

log = logging.getLogger("OMS")

# ── Pip size por símbolo ──────────────────────────────────────────────────────
# Pares JPY: 1 pip = 0.01 | XAU: 1 pip = 1.00 | demais: 1 pip = 0.0001
_PIP_SIZE = {
    "USDJPY": 0.01,  "USDJPY+": 0.01,
    "EURJPY": 0.01,  "EURJPY+": 0.01,
    "GBPJPY": 0.01,  "GBPJPY+": 0.01,
    "AUDJPY": 0.01,  "AUDJPY+": 0.01,
    "NZDJPY": 0.01,  "NZDJPY+": 0.01,
    "CADJPY": 0.01,  "CADJPY+": 0.01,
    "CHFJPY": 0.01,  "CHFJPY+": 0.01,
    "XAUUSD": 1.00,  "XAUUSD+": 1.00,
}
_DEFAULT_PIP = 0.0001

def _pip(symbol: str) -> float:
    """Retorna o tamanho de 1 pip para o símbolo."""
    return _PIP_SIZE.get(symbol.upper(), _DEFAULT_PIP)

# ── Comissão por tipo de conta (round turn) ──────────────────────────────────
from core.costs import get_commission_per_lot
_COMMISSION_RATE = get_commission_per_lot()
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [OMS] %(levelname)s %(message)s")

# Import condicional MT5 (só Windows)
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    log.warning("MetaTrader5 não disponível — modo simulação ativo.")


@dataclass
class OrderResult:
    success:       bool
    symbol:        str
    direction:     str
    volume:        float
    requested_price: float
    filled_price:  float
    slippage_pips: float
    ticket:        int
    commission:    float       # RAW ECN: $6.00/lote round turn
    timestamp:     datetime = field(default_factory=datetime.utcnow)
    error_msg:     str = ""


class KellySizer:
    """Kelly fracionário com cap máximo por trade."""

    def __init__(self, max_fraction: float = 0.02,
                 min_lot: float = 0.01, max_lot: float = 10.0):
        self.max_fraction = max_fraction
        self.min_lot      = min_lot
        self.max_lot      = max_lot

    def size(self, win_prob: float, win_loss_ratio: float,
             capital: float, size_scalar: float = 1.0) -> float:
        """
        Kelly fracionário:
          f* = p - (1-p)/R   capped a max_fraction
          volume = f* * capital / 100_000  (1 lote = $100k notional)

        Args:
            win_prob:       probabilidade estimada pelo modelo
            win_loss_ratio: E[ganho] / E[perda] esperados
            capital:        NAV em USD
            size_scalar:    multiplicador do Risk Engine (0.0 a 1.0)
        """
        kelly_f  = win_prob - (1 - win_prob) / max(win_loss_ratio, 0.01)
        kelly_f  = max(kelly_f, 0.0)
        fraction = min(kelly_f, self.max_fraction) * size_scalar

        notional = capital * fraction
        lots     = notional / 100_000                  # 1 standard lot
        lots     = round(max(self.min_lot,
                         min(self.max_lot, lots)), 2)
        return lots

    @staticmethod
    def commission(volume: float, rate_per_lot: float = None) -> float:
        """
        Comissão round turn por lote:
          RAW ECN:      $6.00/lot  (padrão)
          PRO ECN:      $3.00/lot
          STANDARD STP: $0.00/lot
          CENT:         $0.06/lot
        Lê ACCOUNT_TYPE do ambiente via _COMMISSION_RATE global.
        """
        rate = rate_per_lot if rate_per_lot is not None else _COMMISSION_RATE
        return round(volume * rate, 4)


class OrderManager:

    def __init__(self, nav: float, mt5_connected: bool = False,
                 risk_engine=None):
        self.nav           = nav
        self.mt5_connected = mt5_connected and MT5_AVAILABLE
        self.risk_engine   = risk_engine
        self.kelly         = KellySizer()
        self._order_log: List[OrderResult] = []

    # ------------------------------------------------------------------
    # EXECUÇÃO PRINCIPAL
    # ------------------------------------------------------------------
    def execute(
        self,
        symbol:       str,
        direction:    str,          # "BUY" | "SELL"
        win_prob:     float,        # saída do modelo (ex: 0.57)
        win_loss_ratio: float,      # R:R esperado (ex: 1.5)
        geo_score:    float,        # 0.0 a 1.0
        size_scalar:  float = 1.0, # do Risk Engine
        sl_pips:      int   = 20,
        tp_pips:      int   = 40,
    ) -> OrderResult:

        if direction == "FLAT":
            return OrderResult(False, symbol, direction, 0, 0, 0, 0, 0, 0,
                               error_msg="Sinal FLAT — sem execução")

        # 1. Kelly sizing
        volume = self.kelly.size(win_prob, win_loss_ratio, self.nav, size_scalar)
        comm   = self.kelly.commission(volume)

        log.info(f"Kelly size: prob={win_prob:.2f} R={win_loss_ratio:.2f} "
                 f"scalar={size_scalar:.3f} -> {volume}L | comm=${comm}")

        # 2. Risk Engine pre-trade
        if self.risk_engine:
            dec = self.risk_engine.pre_trade_check(
                symbol, direction, volume, geo_score, win_prob
            )
            if not dec.approved:
                return OrderResult(False, symbol, direction, 0, 0, 0, 0, 0,
                                   comm, error_msg=dec.reason)
            volume = dec.adjusted_size

        # 3. Enviar ordem
        if self.mt5_connected:
            return self._send_mt5_order(symbol, direction, volume, sl_pips, tp_pips, comm)
        else:
            return self._simulate_order(symbol, direction, volume, sl_pips, tp_pips, comm)

    # ------------------------------------------------------------------
    # MT5 — ENVIO REAL (Windows)
    # ------------------------------------------------------------------
    def _send_mt5_order(self, symbol: str, direction: str,
                        volume: float, sl_pips: int,
                        tp_pips: int, comm: float) -> OrderResult:

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return OrderResult(False, symbol, direction, volume, 0, 0, 0, 0,
                               comm, error_msg=f"Tick não disponível para {symbol}")

        pip_size = _pip(symbol)

        if direction == "BUY":
            price    = tick.ask
            sl       = round(price - sl_pips * pip_size, 5)
            tp       = round(price + tp_pips * pip_size, 5)
            order_tp = mt5.ORDER_TYPE_BUY
        else:
            price    = tick.bid
            sl       = round(price + sl_pips * pip_size, 5)
            tp       = round(price - tp_pips * pip_size, 5)
            order_tp = mt5.ORDER_TYPE_SELL

        request = {
            "action":    mt5.TRADE_ACTION_DEAL,
            "symbol":    symbol,
            "volume":    volume,
            "type":      order_tp,
            "price":     price,
            "sl":        sl,
            "tp":        tp,
            "deviation": 10,
            "magic":     09092026,
            "comment":   "vantage_quant_v2",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)

        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            err = result.comment if result else "Sem resposta"
            log.error(f"Ordem falhou: {err}")
            return OrderResult(False, symbol, direction, volume, price, 0, 0,
                               0, comm, error_msg=err)

        filled   = result.price
        slip     = round(abs(filled - price) / _pip(symbol), 2)
        log.info(f"FILL {symbol} {direction} {volume}L @ {filled} | slip={slip}p | ticket={result.order}")

        res = OrderResult(
            success=True, symbol=symbol, direction=direction,
            volume=volume, requested_price=price, filled_price=filled,
            slippage_pips=slip, ticket=result.order, commission=comm
        )
        self._order_log.append(res)
        return res

    # ------------------------------------------------------------------
    # SIMULAÇÃO (Linux / sem MT5)
    # ------------------------------------------------------------------
    def _simulate_order(self, symbol: str, direction: str,
                        volume: float, sl_pips: int,
                        tp_pips: int, comm: float) -> OrderResult:

        import random
        base_prices = {
            "EURUSD": 1.0850, "GBPUSD": 1.2720, "USDJPY": 148.50,
            "AUDUSD": 0.6480, "USDCAD": 1.3540, "XAUUSD": 2450.0,
        }
        price    = base_prices.get(symbol, 1.0000)
        slip     = round(random.uniform(0.1, 1.5), 2)    # 0.1–1.5 pips (RAW ECN)
        filled   = round(price + slip * _pip(symbol) * (1 if direction=="BUY" else -1), 5)
        ticket   = int(time.time() * 1000) % 999999

        log.info(f"[SIM] FILL {symbol} {direction} {volume}L @ {filled} "
                 f"| slip={slip}p | comm=${comm} | ticket={ticket}")

        res = OrderResult(
            success=True, symbol=symbol, direction=direction,
            volume=volume, requested_price=price, filled_price=filled,
            slippage_pips=slip, ticket=ticket, commission=comm
        )
        self._order_log.append(res)
        return res

    # ------------------------------------------------------------------
    # CLOSE POSITION
    # ------------------------------------------------------------------
    def close_position(self, symbol: str, direction: str,
                       volume: float) -> OrderResult:
        """Fecha posição aberta via ticket real (MT5) ou simulação."""
        if self.mt5_connected:
            return self._close_mt5_position(symbol, direction, volume)
        # Modo simulação
        close_dir = "SELL" if direction == "BUY" else "BUY"
        comm = self.kelly.commission(volume)
        return self._simulate_order(symbol, close_dir, volume, 0, 0, comm)

    def _close_mt5_position(self, symbol: str, direction: str,
                             volume: float) -> OrderResult:
        """Fecha posição pelo ticket exato — evita fechar a posição errada."""
        positions = mt5.positions_get(symbol=symbol)
        if not positions:
            return OrderResult(False, symbol, direction, volume, 0, 0, 0, 0, 0,
                               error_msg=f"Nenhuma posição aberta em {symbol}")

        pos = positions[0]
        close_type = (mt5.ORDER_TYPE_SELL
                      if pos.type == mt5.POSITION_TYPE_BUY
                      else mt5.ORDER_TYPE_BUY)
        tick = mt5.symbol_info_tick(symbol)
        price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask

        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       symbol,
            "volume":       pos.volume,
            "type":         close_type,
            "position":     pos.ticket,
            "price":        price,
            "deviation":    10,
            "magic":        09092026,
            "comment":      "close_vantage_quant_v2",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            err = result.comment if result else "Sem resposta"
            log.error(f"Close falhou: {err}")
            return OrderResult(False, symbol, direction, pos.volume, price,
                               0, 0, 0, self.kelly.commission(pos.volume),
                               error_msg=err)

        comm = self.kelly.commission(pos.volume)
        log.info(f"CLOSE {symbol} ticket={pos.ticket} @ {result.price} | comm=${comm}")
        return OrderResult(
            success=True, symbol=symbol, direction="CLOSE",
            volume=pos.volume, requested_price=price,
            filled_price=result.price,
            slippage_pips=round(abs(result.price - price) * 10_000 / _pip(symbol) * _DEFAULT_PIP, 2),
            ticket=result.order, commission=comm
        )

    # ------------------------------------------------------------------
    # REPORTS
    # ------------------------------------------------------------------
    def trade_log(self):
        print(f"\n{'='*60}")
        print(f"  TRADE LOG — {len(self._order_log)} ordens")
        print(f"{'='*60}")
        total_comm = 0.0
        for r in self._order_log:
            status = "✅" if r.success else "❌"
            print(f"  {status} {r.symbol:<8} {r.direction:<5} "
                  f"{r.volume:.2f}L @ {r.filled_price:.5f} "
                  f"slip={r.slippage_pips:.1f}p  comm=${r.commission:.2f}")
            total_comm += r.commission
        print(f"\n  Total comissões: ${total_comm:.2f}")
        print(f"{'='*60}\n")


# ======================================================================
# TESTE LOCAL
# ======================================================================
if __name__ == "__main__":
    from risk.risk_engine import RiskEngine

    print("\n" + "="*55)
    print("  ORDER MANAGER — Teste de execução simulada")
    print("="*55)

    engine = RiskEngine(nav=100_000)
    oms    = OrderManager(nav=100_000, mt5_connected=False, risk_engine=engine)

    orders = [
        ("EURUSD", "BUY",  0.57, 1.8, 0.25),
        ("USDJPY", "SELL", 0.62, 2.0, 0.30),
        ("XAUUSD", "BUY",  0.51, 1.5, 0.88),   # geo alto → bloqueado parcial
    ]

    for sym, direc, prob, rr, geo in orders:
        res = oms.execute(sym, direc, prob, rr, geo)
        print(f"\n  {sym} {direc}")
        print(f"  Success  : {res.success}")
        print(f"  Volume   : {res.volume:.2f}L")
        print(f"  Filled @ : {res.filled_price:.5f}")
        print(f"  Slippage : {res.slippage_pips:.1f} pips")
        print(f"  Comm     : ${res.commission:.2f}")

    oms.trade_log()
    print("  ✅ OrderManager OK\n")
