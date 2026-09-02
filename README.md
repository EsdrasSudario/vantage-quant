# Vantage Quant System

Sistema de trading algoritmico integrado a Vantage Markets via MT5 Python API.

**Stack:** GARCH(1,1) + Kalman Filter + GeoScore NLP + Kelly Sizing + Risk Engine

---

## Setup rapido (Windows)

### Pre-requisitos
1. **MetaTrader 5** instalado — [metatrader5.com](https://www.metatrader5.com/pt/download)
2. **Python 3.10+** — [python.org](https://www.python.org/downloads/)
3. Conta Vantage Markets (demo ou real)

### Instalacao

```bash
# Clonar o repositorio
git clone https://github.com/SEU_USUARIO/vantage_quant.git
cd vantage_quant

# Instalar dependencias
pip install -r requirements.txt

# Configurar credenciais
copy config\.env.example config\.env
# Editar config\.env com seu login, senha e servidor MT5

# Testar conexao
python SETUP_WINDOWS.py
```

### Configurar credenciais

Edite o arquivo `config/.env`:

```
MT5_LOGIN=SEU_LOGIN
MT5_PASSWORD=SUA_SENHA
MT5_SERVER=VantageMarkets-Demo
MAX_DAILY_LOSS_PCT=0.02
MAX_POSITION_SIZE_PCT=0.02
MAX_LEVERAGE=100
```

---

## Estrutura do projeto

```
vantage_quant/
├── main.py                    Loop principal de trading
├── SETUP_WINDOWS.py           Teste de conexao e setup
├── requirements.txt           Dependencias Python
├── config/
│   ├── .env                   Credenciais (nao commitar)
│   └── .env.example           Template de credenciais
├── core/
│   └── connection.py          Gerenciador de conexao MT5
├── signals/
│   ├── garch_model.py         GARCH(1,1) — volatility forecasting
│   ├── kalman_filter.py       Kalman Filter — trend estimation
│   ├── geo_score.py           GeoScore NLP — risco geopolitico
│   └── signal_aggregator.py   Agrega sinais em decisao final
├── risk/
│   └── risk_engine.py         VaR + circuit breaker + position limits
├── execution/
│   └── order_manager.py       OMS — Kelly sizing + envio MT5
├── backtest/
│   ├── engine.py              Motor de backtest barra a barra
│   ├── bt_runner_100.py       Backtest calibrado para $100
│   ├── bt_runner_100_v2.py    Backtest v2 — pip-aware por ativo
│   └── bt_runner_100_v3.py    Backtest v3 — filtros de qualidade
└── logs/                      Trades CSV e equity curves
```

---

## Executar

```bash
# Rodar sistema ao vivo (paper trading se MT5 nao conectar)
python main.py

# Rodar com simbolos especificos
python main.py --symbols EURUSD GBPUSD AUDUSD NZDUSD --interval 60

# Rodar backtest calibrado $100
python backtest/bt_runner_100_v3.py
```

---

## Arquitetura de sinais

```
Tick MT5 → GARCH(1,1) → Regime (low/medium/high vol)
         → Kalman Filter → Direcao (BUY/SELL/FLAT)
         → GeoScore NLP  → Risco geopolitico (0-1)
         → SignalAggregator → Sinal final + size_scalar
         → RiskEngine → Pre-trade check (VaR, DD, corr)
         → OrderManager → Kelly sizing → MT5 order_send
```

---

## Configuracao da conta

| Parametro | Valor |
|-----------|-------|
| Broker | Vantage Markets |
| Tipo | RAW ECN |
| Comissao | $1.50/lote (half-turn) |
| Spread | 0.0 pip |
| Min lot | 0.01 |
| Alavancagem | 1:100 |

---

## Status do backtest (v3 — Set 2026)

Periodo: Nov 2025 → Set 2026 | Capital: $100 | 7 simbolos

| Simbolo | Trades | Win% | Net PnL |
|---------|--------|------|---------|
| AUDUSD  | 112    | 44.6% | +$9.23 |
| NZDUSD  | 86     | 37.2% | +$7.23 |
| GBPUSD  | 126    | 40.5% | +$0.82 |
| XAUUSD+ | 94     | 38.3% | +$1.79 |
| USDJPY  | 139    | 38.8% | -$18.01 |
| EURUSD  | 132    | 35.6% | -$36.94 |
| USDCAD  | 106    | 29.2% | -$56.10 |

**v4 pendente:** remover USDCAD/EURUSD, normalizar threshold Kalman por ativo.

---

## Seguranca

- **Nunca** commitar `config/.env` — esta no `.gitignore`
- Trocar senha apos clonar em nova maquina
- Usar conta DEMO para testes
- Circuit breaker ativo: halt se PnL diario < -2% NAV
