# ============================================================
# Vantage Quant System — MT5 Connection Manager
# Compatível com Windows (MetaTrader5) e Linux (mt5linux via Wine)
# ============================================================

import os
import sys
import platform
from datetime import datetime
from dotenv import load_dotenv

# Carrega credenciais do .env
load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'config', '.env'))

MT5_LOGIN  = int(os.getenv("MT5_LOGIN"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD")
MT5_SERVER   = os.getenv("MT5_SERVER")

# Import condicional: Windows usa MetaTrader5, Linux usa mt5linux
IS_WINDOWS = platform.system() == "Windows"

if IS_WINDOWS:
    import MetaTrader5 as mt5
else:
    # Linux: requer MT5 rodando via Wine com servidor rpyc ativo
    # Instruções: ver README.md
    from mt5linux import MetaTrader5
    mt5 = MetaTrader5(host='localhost', port=18812)


def connect() -> bool:
    """Inicializa e autentica conexão com MT5."""
    print(f"\n{'='*55}")
    print(f"  VANTAGE QUANT SYSTEM — Conectando...")
    print(f"  Servidor : {MT5_SERVER}")
    print(f"  Login    : {MT5_LOGIN}")
    print(f"  OS       : {platform.system()}")
    print(f"{'='*55}")

    if IS_WINDOWS:
        ok = mt5.initialize(
            server=MT5_SERVER,
            login=MT5_LOGIN,
            password=MT5_PASSWORD
        )
    else:
        ok = mt5.initialize()

    if not ok:
        err = mt5.last_error()
        print(f"\n❌ Falha na conexão: {err}")
        return False

    print(f"\n✅ Conexão estabelecida!")
    return True


def account_info() -> dict:
    """Retorna informações detalhadas da conta."""
    info = mt5.account_info()
    if info is None:
        print("❌ Não foi possível obter informações da conta.")
        return {}

    data = {
        "login":       info.login,
        "server":      info.server,
        "name":        info.name,
        "currency":    info.currency,
        "balance":     info.balance,
        "equity":      info.equity,
        "margin":      info.margin,
        "free_margin": info.margin_free,
        "leverage":    info.leverage,
        "profit":      info.profit,
        "tipo":        "DEMO" if info.trade_mode == 0 else "LIVE",
    }

    print(f"\n{'='*55}")
    print(f"  INFORMAÇÕES DA CONTA")
    print(f"{'='*55}")
    for k, v in data.items():
        label = k.upper().ljust(15)
        if isinstance(v, float):
            print(f"  {label}: {v:,.2f}")
        else:
            print(f"  {label}: {v}")
    print(f"{'='*55}\n")
    return data


def market_snapshot(symbols: list) -> None:
    """Exibe snapshot de preços em tempo real."""
    print(f"\n{'='*55}")
    print(f"  SNAPSHOT DE MERCADO — {datetime.now().strftime('%H:%M:%S')}")
    print(f"  {'PAR':<12} {'BID':>10} {'ASK':>10} {'SPREAD':>8}")
    print(f"  {'-'*42}")

    for sym in symbols:
        tick = mt5.symbol_info_tick(sym)
        if tick:
            spread_pip = round((tick.ask - tick.bid) * 10000, 1)
            print(f"  {sym:<12} {tick.bid:>10.5f} {tick.ask:>10.5f} {spread_pip:>7.1f}p")
        else:
            print(f"  {sym:<12} {'N/A':>10} {'N/A':>10} {'---':>8}")
    print(f"{'='*55}\n")


def disconnect() -> None:
    mt5.shutdown()
    print("🔌 Desconectado do MT5.")


# ============================================================
# EXECUÇÃO DIRETA — Teste de conexão
# ============================================================
if __name__ == "__main__":
    if connect():
        account_info()
        market_snapshot([
            "EURUSD", "GBPUSD", "USDJPY",
            "AUDUSD", "USDCAD", "XAUUSD"
        ])
        disconnect()
    else:
        sys.exit(1)
