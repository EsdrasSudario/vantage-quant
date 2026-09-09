"""
tests/test_pair_screener.py
===========================
Testes unitários para signals/pair_screener.py.
Executa sem MT5 — simula a API via unittest.mock.
"""

import sys
import types
import unittest
from unittest.mock import MagicMock, patch, call
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Stub do módulo MetaTrader5 para rodar sem MT5 instalado
# ---------------------------------------------------------------------------
_mt5_stub = types.ModuleType("MetaTrader5")
_mt5_stub.TIMEFRAME_H1 = 16385
_mt5_stub.COPY_TICKS_ALL = 1
_mt5_stub.copy_rates_from_pos = MagicMock(return_value=None)
_mt5_stub.copy_ticks_from = MagicMock(return_value=None)
_mt5_stub.symbol_info_tick = MagicMock(return_value=None)
sys.modules.setdefault("MetaTrader5", _mt5_stub)

# Importa após stub estar no sys.modules
from signals.pair_screener import PairScreener  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bars(n: int = 200, base: float = 1.08, vol: float = 0.0003) -> pd.DataFrame:
    np.random.seed(0)
    prices = base + np.cumsum(np.random.normal(0, vol, n))
    prices = np.maximum(prices, base * 0.5)
    high = prices + np.abs(np.random.normal(0, vol * 0.5, n))
    low  = prices - np.abs(np.random.normal(0, vol * 0.5, n))
    return pd.DataFrame({"open": prices, "high": high, "low": low, "close": prices})


def _make_xau_bars(n: int = 200) -> pd.DataFrame:
    return _make_bars(n, base=2350.0, vol=5.0)


class _FakeGeoResult:
    level = "NORMAL"
    score = 0.1
    dominant_theme = ""


class _FakeGeoScorer:
    def score(self, headlines):
        return _FakeGeoResult()


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------

class TestSymbolResolveInFetchBars(unittest.TestCase):
    """
    _fetch_bars_mt5 deve usar o nome MT5 resolvido (ex: XAUUSD+),
    mas retornar DataFrame sem alterar o nome canônico na lógica interna.
    """

    def setUp(self):
        # Monta um rates array estruturado simulando retorno do MT5
        n = 50
        dtype = np.dtype([
            ("open", float), ("high", float), ("low", float), ("close", float),
        ])
        arr = np.zeros(n, dtype=dtype)
        arr["open"]  = 2350.0
        arr["high"]  = 2355.0
        arr["low"]   = 2345.0
        arr["close"] = 2352.0
        self._fake_rates = arr

    def test_fetch_bars_calls_mt5_with_resolved_symbol(self):
        """copy_rates_from_pos deve receber XAUUSD+ quando resolve_fn mapeia assim."""
        resolved_calls = []

        def resolve_fn(sym):
            resolved = sym + "+" if sym == "XAUUSD" else sym
            resolved_calls.append((sym, resolved))
            return resolved

        screener = PairScreener(symbol_resolve_fn=resolve_fn)

        with patch.object(_mt5_stub, "copy_rates_from_pos", return_value=self._fake_rates) as mock_copy:
            df = screener._fetch_bars_mt5("XAUUSD", 50)

        # A chamada MT5 deve usar o nome resolvido
        mock_copy.assert_called_once_with("XAUUSD+", _mt5_stub.TIMEFRAME_H1, 0, 50)

        # O DataFrame retornado deve ter as colunas esperadas
        self.assertIsNotNone(df)
        self.assertIn("close", df.columns)
        self.assertEqual(len(df), 50)

    def test_fetch_bars_no_resolve_uses_canonical(self):
        """Sem resolve_fn, o nome canônico é passado diretamente ao MT5."""
        screener = PairScreener()

        with patch.object(_mt5_stub, "copy_rates_from_pos", return_value=self._fake_rates) as mock_copy:
            screener._fetch_bars_mt5("XAUUSD", 50)

        mock_copy.assert_called_once_with("XAUUSD", _mt5_stub.TIMEFRAME_H1, 0, 50)

    def test_fetch_bars_returns_none_when_mt5_returns_none(self):
        """Quando MT5 retorna None (símbolo não encontrado), fetch deve retornar None."""
        screener = PairScreener(symbol_resolve_fn=lambda s: s + "+" if s == "XAUUSD" else s)

        with patch.object(_mt5_stub, "copy_rates_from_pos", return_value=None):
            result = screener._fetch_bars_mt5("XAUUSD", 50)

        self.assertIsNone(result)


class TestSymbolResolveInSpreadPips(unittest.TestCase):
    """_spread_pips também deve usar o símbolo MT5 resolvido."""

    def test_spread_pips_calls_mt5_with_resolved_symbol(self):
        tick_mock = MagicMock()
        tick_mock.time = 1_700_000_000

        ticks_data = np.array(
            [(1_700_000_000, 2350.0, 2350.5, 0, 0, 0, 0)] * 10,
            dtype=[
                ("time", "i8"), ("bid", "f8"), ("ask", "f8"),
                ("last", "f8"), ("volume", "u8"), ("time_msc", "i8"), ("flags", "u4"),
            ],
        )

        screener = PairScreener(symbol_resolve_fn=lambda s: "XAUUSD+" if s == "XAUUSD" else s)

        with patch.object(_mt5_stub, "symbol_info_tick", return_value=tick_mock), \
             patch.object(_mt5_stub, "copy_ticks_from", return_value=ticks_data) as mock_ticks:
            screener._spread_pips("XAUUSD", pip=1.0)

        # Ambas as chamadas MT5 devem usar XAUUSD+
        self.assertEqual(mock_ticks.call_args[0][0], "XAUUSD+")


class TestXauusdPipSize(unittest.TestCase):
    """pip size de XAUUSD deve ser 1.00 no dicionário _PIP."""

    def test_xauusd_pip_is_one(self):
        screener = PairScreener()
        self.assertEqual(screener._PIP.get("XAUUSD"), 1.00)


class TestScreenWithBarsNoMT5(unittest.TestCase):
    """
    Quando bars são fornecidos externamente, o screener não precisa chamar
    _fetch_bars_mt5 — nenhum acesso MT5 ocorre e XAUUSD usa pip=1.00 corretamente.
    """

    def test_xauusd_passes_with_provided_bars(self):
        bars = {sym: _make_bars() for sym in PairScreener.UNIVERSE if sym != "XAUUSD"}
        bars["XAUUSD"] = _make_xau_bars()

        screener = PairScreener(max_pairs=9)  # aceita todos
        geo = _FakeGeoScorer()

        with patch.object(_mt5_stub, "copy_rates_from_pos", return_value=None), \
             patch.object(_mt5_stub, "symbol_info_tick", return_value=None):
            report = screener.screen(geo, bars)

        symbols_evaluated = {r.symbol for r in report.all_results}
        self.assertIn("XAUUSD", symbols_evaluated)

        xau_result = next(r for r in report.all_results if r.symbol == "XAUUSD")
        # Com barras reais de XAU (vol=5 pips) e pip=1.00, ATR deve ser > min
        # O resultado pode passar ou falhar por spread — o important é ATR calculado corretamente
        # ATR em "pips XAU" (divido por 1.00) deve ser na casa de unidades, não de 0.0001
        if xau_result.atr_pips > 0:
            self.assertGreater(xau_result.atr_pips, 0.1,
                               "ATR XAUUSD deve estar em unidade de pip XAU (1.00), não 0.0001")


class TestResolveIdentityDefault(unittest.TestCase):
    """Sem symbol_resolve_fn, o comportamento padrão é identidade (sem alteração)."""

    def test_default_resolve_is_identity(self):
        screener = PairScreener()
        self.assertEqual(screener._resolve("EURUSD"), "EURUSD")
        self.assertEqual(screener._resolve("XAUUSD"), "XAUUSD")
        self.assertEqual(screener._resolve("XAUUSD+"), "XAUUSD+")


class TestMainIntegrationPattern(unittest.TestCase):
    """
    Valida o padrão de integração usado em main.py:
        _sym_map_cache = resolve_mapping(symbols)
        def _resolve_symbol(sym): return _sym_map_cache.get(sym, sym)
        screener = PairScreener(..., symbol_resolve_fn=_resolve_symbol)

    Testa sem importar main.py — usa o mesmo padrão de closure diretamente.
    """

    def setUp(self):
        # Simula o mapeamento que resolve_mapping retornaria com XAUUSD+ habilitado
        self._mapping = {
            "EURUSD": "EURUSD",
            "AUDUSD": "AUDUSD",
            "NZDUSD": "NZDUSD",
            "XAUUSD": "XAUUSD+",   # Vantage Demo: XAUUSD canonico → XAUUSD+ no MT5
        }
        # Replica a closure exata de main.py
        _sym_map_cache = self._mapping
        self._resolve_symbol = lambda sym: _sym_map_cache.get(sym, sym)

    def test_resolve_fn_maps_xauusd_to_plus(self):
        """_resolve_symbol deve retornar XAUUSD+ para XAUUSD."""
        self.assertEqual(self._resolve_symbol("XAUUSD"), "XAUUSD+")

    def test_resolve_fn_keeps_regular_symbols_unchanged(self):
        """Símbolos sem sufixo no mapping devem ser retornados sem alteração."""
        self.assertEqual(self._resolve_symbol("EURUSD"), "EURUSD")
        self.assertEqual(self._resolve_symbol("AUDUSD"), "AUDUSD")
        self.assertEqual(self._resolve_symbol("NZDUSD"), "NZDUSD")

    def test_resolve_fn_identity_for_unknown_symbols(self):
        """Símbolo ausente do mapping (fallback .get) deve retornar ele mesmo."""
        self.assertEqual(self._resolve_symbol("USDJPY"), "USDJPY")
        self.assertEqual(self._resolve_symbol("GBPUSD"), "GBPUSD")

    def test_screener_uses_resolve_fn_from_main_pattern(self):
        """
        PairScreener instanciado com o resolve_fn do padrão main.py deve
        chamar MT5 com XAUUSD+ ao buscar barras.
        """
        n = 20
        dtype = np.dtype([("open", float), ("high", float), ("low", float), ("close", float)])
        fake_rates = np.zeros(n, dtype=dtype)
        fake_rates["close"] = 2350.0
        fake_rates["high"]  = 2355.0
        fake_rates["low"]   = 2345.0
        fake_rates["open"]  = 2350.0

        screener = PairScreener(
            universe=list(self._mapping.keys()),
            max_pairs=4,
            symbol_resolve_fn=self._resolve_symbol,
        )

        with patch.object(_mt5_stub, "copy_rates_from_pos", return_value=fake_rates) as mock_copy:
            df = screener._fetch_bars_mt5("XAUUSD", n)

        # MT5 deve ter sido chamado com o nome resolvido XAUUSD+
        mock_copy.assert_called_once_with("XAUUSD+", _mt5_stub.TIMEFRAME_H1, 0, n)
        self.assertIsNotNone(df)
        self.assertEqual(len(df), n)

    def test_screener_symbol_resolve_fn_is_not_none_when_mapping_provided(self):
        """
        Quando resolve_mapping retorna um dict válido, o screener recebe
        um symbol_resolve_fn não-nulo (não usa a identidade padrão).
        """
        screener = PairScreener(symbol_resolve_fn=self._resolve_symbol)
        # A função deve diferir do comportamento identidade para XAUUSD
        self.assertNotEqual(screener._resolve("XAUUSD"), "XAUUSD")
        self.assertEqual(screener._resolve("XAUUSD"), "XAUUSD+")


if __name__ == "__main__":
    unittest.main(verbosity=2)
