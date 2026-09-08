"""
check_symbols.py — lista simbolos disponíveis na conta demo Vantage
e verifica quais dos pares candidatos existem (com/sem sufixo).
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv("config/.env")

import MetaTrader5 as mt5

CANDIDATES_BASE = [
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
    "NZDUSD", "USDCAD", "USDCHF", "EURGBP",
    "XAUUSD", "XAGUSD",
]

ok = mt5.initialize(
    server="VantageMarkets-Demo",
    login=26018171,
    password=os.getenv("MT5_PASSWORD", ""),
)
if not ok:
    print(f"MT5 falhou: {mt5.last_error()}")
    sys.exit(1)

acc = mt5.account_info()
print(f"\nServidor : {acc.server}")
print(f"Login    : {acc.login}")
print(f"Tipo     : {acc.account_type if hasattr(acc, 'account_type') else 'N/A'}")
print(f"Moeda    : {acc.currency}")
print(f"Balance  : ${acc.balance:.2f}\n")

print(f"{'PAR BASE':<12} {'SEM SUFIXO':^14} {'COM +':^14} {'USAR':<14}")
print("-" * 56)

for base in CANDIDATES_BASE:
    variants = [base, base + "+"]
    found = {}
    for v in variants:
        mt5.symbol_select(v, True)
        info = mt5.symbol_info(v)
        tick = mt5.symbol_info_tick(v) if info else None
        found[v] = (info is not None) and (tick is not None) and (tick.bid > 0)

    no_suffix = "✓" if found[base] else "✗"
    with_plus  = "✓" if found[base + "+"] else "✗"

    if found[base] and not found[base + "+"]:
        usar = base
    elif found[base + "+"] and not found[base]:
        usar = base + "+"
    elif found[base] and found[base + "+"]:
        usar = base + "  (ambos)"
    else:
        usar = "INDISPONIVEL"

    print(f"{base:<12} {no_suffix:^14} {with_plus:^14} {usar:<14}")

mt5.shutdown()
print("\nDone.")
