"""
============================================================
 VANTAGE QUANT SYSTEM — Setup & Teste de Conexão
 Execute este arquivo no seu Windows com MT5 instalado
 
 Pré-requisitos:
   1. MetaTrader 5 instalado e logado na conta demo
   2. Python 3.10+ instalado
   3. pip install MetaTrader5 pandas numpy python-dotenv

 Execução:
   python SETUP_WINDOWS.py
============================================================
"""

import subprocess
import sys
import os

def install_deps():
    """Instala dependências automaticamente."""
    deps = ["MetaTrader5", "pandas", "numpy", "python-dotenv",
            "scipy", "scikit-learn", "requests"]
    print("📦 Instalando dependências...\n")
    for dep in deps:
        subprocess.check_call([sys.executable, "-m", "pip", "install", dep, "-q"])
        print(f"  ✅ {dep}")
    print("\n✅ Todas as dependências instaladas!\n")

def test_connection():
    """Testa conexão com a conta demo Vantage."""
    import MetaTrader5 as mt5
    from datetime import datetime
    import pandas as pd

    LOGIN    = 26018171
    PASSWORD = "!y2%U2dD"
    SERVER   = "VantageMarkets-Demo"

    print("="*60)
    print("  TESTE DE CONEXÃO — VANTAGE MARKETS DEMO")
    print("="*60)

    # 1. Inicializar
    if not mt5.initialize(server=SERVER, login=LOGIN, password=PASSWORD):
        print(f"❌ Falha: {mt5.last_error()}")
        print("\n⚠️  Certifique-se que o MT5 está instalado e com conta demo ativa.")
        return False

    print(f"\n✅ Conectado com sucesso!")

    # 2. Info da conta
    acc = mt5.account_info()
    print(f"\n{'─'*40}")
    print(f"  Login      : {acc.login}")
    print(f"  Nome       : {acc.name}")
    print(f"  Servidor   : {acc.server}")
    print(f"  Tipo       : {'DEMO' if acc.trade_mode == 0 else 'LIVE'}")
    print(f"  Moeda      : {acc.currency}")
    print(f"  Balance    : ${acc.balance:,.2f}")
    print(f"  Equity     : ${acc.equity:,.2f}")
    print(f"  Alavancagem: 1:{acc.leverage}")
    print(f"{'─'*40}")

    # 3. Snapshot de mercado
    symbols = ["EURUSD","GBPUSD","USDJPY","AUDUSD","USDCAD","XAUUSD","BTCUSD"]
    print(f"\n  PREÇOS EM TEMPO REAL — {datetime.now().strftime('%H:%M:%S')}")
    print(f"  {'PAR':<10} {'BID':>10} {'ASK':>10} {'SPREAD':>8}")
    print(f"  {'─'*40}")
    for sym in symbols:
        tick = mt5.symbol_info_tick(sym)
        if tick:
            spread = round((tick.ask - tick.bid) * 10000, 1)
            print(f"  {sym:<10} {tick.bid:>10.5f} {tick.ask:>10.5f} {spread:>7.1f}p")

    # 4. Histórico recente (últimos 100 ticks EURUSD)
    ticks = mt5.copy_ticks_from("EURUSD", datetime(2026, 8, 30), 100, mt5.COPY_TICKS_ALL)
    if ticks is not None:
        df = pd.DataFrame(ticks)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        print(f"\n  ÚLTIMOS 5 TICKS — EURUSD")
        print(f"  {'─'*40}")
        print(df[['time','bid','ask']].tail(5).to_string(index=False))

    # 5. Instrumentos disponíveis
    all_symbols = mt5.symbols_get()
    print(f"\n  Total de instrumentos disponíveis: {len(all_symbols)}")

    mt5.shutdown()
    print(f"\n{'='*60}")
    print("  ✅ SISTEMA PRONTO PARA TRADING ALGORÍTMICO")
    print(f"{'='*60}\n")
    return True

# ============================================================
if __name__ == "__main__":
    print("\n🚀 VANTAGE QUANT SYSTEM — Inicializando Setup\n")
    
    try:
        install_deps()
        test_connection()
    except ImportError:
        print("\n❌ MetaTrader5 não encontrado.")
        print("   Execute: pip install MetaTrader5")
        print("   E certifique-se que está rodando no Windows com MT5 instalado.\n")
    except Exception as e:
        print(f"\n❌ Erro: {e}")
        import traceback
        traceback.print_exc()
