"""
core/symbol_resolver.py
=======================
Resolução dinâmica de símbolos habilitados na corretora Vantage Markets.

A Vantage pode desabilitar símbolos (trade_mode=DISABLED / retcode 10017)
em determinados horários ou tipos de conta. Este módulo descobre quais
símbolos estão habilitados e tenta o correspondente (sem sufixo ↔ com '+')
antes de cada ciclo de trading.
"""

import logging
import MetaTrader5 as mt5

log = logging.getLogger("SymbolResolver")

# Mapeamento bidirecional sem sufixo ↔ com sufixo '+'
_COUNTERPARTS: dict[str, str] = {
    "AUDUSD":  "AUDUSD+",  "AUDUSD+":  "AUDUSD",
    "NZDUSD":  "NZDUSD+",  "NZDUSD+":  "NZDUSD",
    "EURUSD":  "EURUSD+",  "EURUSD+":  "EURUSD",
    "GBPUSD":  "GBPUSD+",  "GBPUSD+":  "GBPUSD",
    "USDJPY":  "USDJPY+",  "USDJPY+":  "USDJPY",
    "USDCAD":  "USDCAD+",  "USDCAD+":  "USDCAD",
    "USDCHF":  "USDCHF+",  "USDCHF+":  "USDCHF",
    "EURGBP":  "EURGBP+",  "EURGBP+":  "EURGBP",
    "XAUUSD":  "XAUUSD+",  "XAUUSD+":  "XAUUSD",
    "XAGUSD":  "XAGUSD+",  "XAGUSD+":  "XAGUSD",
}


def _is_enabled(symbol: str) -> bool:
    """Retorna True se o símbolo existe e está habilitado para negociação."""
    info = mt5.symbol_info(symbol)
    return info is not None and info.trade_mode != 0  # 0 = SYMBOL_TRADE_MODE_DISABLED


def resolve_mapping(requested: list[str]) -> dict[str, str]:
    """
    Retorna dicionário {símbolo_original: símbolo_resolvido} para os símbolos
    habilitados. Símbolos DISABLED sem correspondente são omitidos do resultado.

    Use este mapeamento para separar:
      - chaves internas (aggregators, histories, open_positions) → original
      - chamadas MT5 (ticks, ordens)                            → resolvido
    """
    mapping: dict[str, str] = {}
    for sym in requested:
        if _is_enabled(sym):
            mapping[sym] = sym
            continue

        alt = _COUNTERPARTS.get(sym)
        if alt and _is_enabled(alt):
            log.warning(f"{sym} DISABLED — usando correspondente {alt} (backtest foi em {sym})")
            mapping[sym] = alt
        else:
            log.warning(f"{sym} DISABLED e sem correspondente habilitado — ignorado neste ciclo")

    return mapping


def resolve_symbols(requested: list[str]) -> list[str]:
    """
    Versão simplificada — retorna apenas a lista de símbolos resolvidos.
    Use resolve_mapping() quando precisar rastrear original → resolvido.
    """
    return list(resolve_mapping(requested).values())
