# Vantage Quant System — Plano de Implementação

> **Contexto:** Este documento resume o estado atual do sistema e detalha as implementações pendentes por camada, em ordem de prioridade. Use-o como briefing em qualquer nova sessão para continuar o desenvolvimento sem perda de contexto.
>
> **⚠️ Atualizado em 2026-09-08:** Sprint 0, Sprint 1 e Sprint 2.1 concluídos. Sistema pronto para testes na Cent Account (Sprint 2.5 — ação manual). Próxima implementação: Sprint 3.1 TickCollector asyncio. Descoberta crítica do Kalman registrada — MIN_CONFIDENCE bloqueado em 0.15 até Sprint 6.2.

---

## Repositório

```
https://github.com/EsdrasSudario/vantage-quant.git
```

**Conta demo atual:**
- Server: `VantageMarkets-Demo`
- Login: `26018171`
- Tipo: RAW ECN

---

## ⚠️ Conta e Custos — Análise Crítica (atualizado 2026-09-02)

### Tipos de conta disponíveis na Vantage Markets

| Conta | Spread | Comissão round turn | Depósito mín. | EA/Scalping | Indicado para o robô |
|-------|--------|---------------------|---------------|-------------|----------------------|
| Standard STP | 1.0–1.4 pip | $0 | $50 | ✅ | ❌ Spread alto destrói edge |
| **RAW ECN** | ~0.12 pip | **$6.00/lot** ($3/lado) | $50 | ✅ | ⚠️ Viável com recalibração |
| **PRO ECN** | ~0.0 pip | **$3.00/lot** ($1.50/lado) | $10.000 real / demo livre | ✅ Equinix prioritário | ✅ **Ideal — projetada para algo** |
| Cent | ~0.0 pip | $0–$3.00 | $50 | ✅ | ✅ **Ideal para testes com capital real mínimo** |
| Swap-Free | igual base | + taxa adm. noturna | $50 | ✅ | ❌ Taxa extra sem benefício (robô intraday) |

### ✅ Bug de custo corrigido (Sprint 0.5 — commit 07c03e6)

`core/costs.py` implementado. Custo RAW ECN `$6.00/lot` round turn ativo em todo o sistema. Backtest v4 rerun com custo real concluído.

**Resultado com custo correto:**
- AUDUSD: +$15.54 ✅ | NZDUSD: +$19.73 ✅
- EURUSD, GBPUSD, USDJPY, USDCAD: negativos ❌

---

## Estado Atual das 6 Camadas (atualizado 2026-09-08)

| # | Camada | Status | Observação |
|---|--------|--------|------------|
| 1 | Data Ingestion | ❌ Ausente | Sem tick_collector, Kafka, TimescaleDB — Sprint 3 |
| 2 | Signal Engine | ✅ Funcional | GARCH + Kalman + GeoScore + Aggregator + KalmanPairs integrado |
| 3 | Portfolio & Sizing | ✅ Funcional | Kelly + PairScreener com filtro de correlação (Sprint 2.1) |
| 4 | Execution Engine | ✅ Funcional | MT5 direto + paper fallback + symbol_resolver dinâmico |
| 5 | Risk Monitor | ✅ Funcional | 7 checks, circuit breaker, VaR in-memory + persistência JSON |
| 6 | Logging/Analytics | ❌ Ausente | Só `logging` padrão Python — Sprint 5 |

**Lacunas ainda abertas:**
- Data layer sem TimescaleDB (Sprint 3)
- RiskEngine sem Redis — persistência atual é JSON simples (Sprint 4)
- Logging sem structlog/Grafana/Prometheus (Sprint 5)
- Backtest sem walk-forward validation (Sprint 6.1)
- Kalman confidence não discrimina qualidade de sinal — recalibração Sprint 6.2

---

## Estrutura de Pastas Atual (atualizado 2026-09-08)

```
vantage_quant/
├── main.py                        ✅ loop principal + PairScreener + symbol_resolver
├── config/.env                    ✅ credenciais (não commitar)
├── core/
│   ├── connection.py              ✅ conexão MT5
│   ├── costs.py                   ✅ custo RAW ECN $6/lot (fonte única)
│   └── symbol_resolver.py         ✅ resolução dinâmica de símbolos habilitados
├── signals/
│   ├── garch_model.py             ✅ GARCH(1,1) t-Student
│   ├── kalman_filter.py           ✅ KalmanTrend + KalmanPairs (integrado Sprint 1.1)
│   ├── geo_score.py               ✅ NLP geopolítico com RSS feeds reais
│   ├── signal_aggregator.py       ✅ combina sinais → SignalPacket
│   └── pair_screener.py           ✅ 5 filtros + score composto (Sprint 2.1)
├── risk/
│   └── risk_engine.py             ✅ 7 checks + circuit breaker + persistência JSON
├── execution/
│   └── order_manager.py           ✅ Kelly + MT5 + paper fallback + pip size correto
├── backtest/
│   ├── engine.py                  ✅ bar-by-bar + custo real
│   └── bt_runner_100_v4.py        ✅ backtest com $6/lot RAW ECN
├── tools/
│   └── check_symbols.py           ✅ diagnóstico de símbolos da conta
├── data/                          ❌ pasta vazia — Sprint 3
└── logs/
    ├── risk_state.json            ✅ persistência do RiskEngine
    └── *.log                      ⚠️ só logging texto padrão Python
```

---

## Ordem de Prioridade de Implementação (atualizado 2026-09-08)

```
SPRINT 0 ✅ CONCLUÍDO (commit 71f218c, 030ecd1, 07c03e6, 6bd887d)
├── 0.1  ✅ close_position() corrigido
├── 0.2  ✅ RiskEngine rastreia posições reais
├── 0.3  ✅ pip size JPY/XAU corrigido
├── 0.4  ✅ GeoScore com RSS feeds reais
├── 0.5  ✅ core/costs.py — $6/lot RAW ECN
├── 0.6  ✅ --max-iter default=0 (infinito)
├── 0.7  ✅ Backtests rerunnados com custo real (v4/v4b)
├── 0.8  ⏸ MIN_CONFIDENCE bloqueado — Kalman max=0.2326, threshold 0.30
│         elimina 100% dos sinais. Mantido em 0.15. Recalibração no Sprint 6.2.
└── 0.9  ❌ Conta demo PRO ECN — ação manual pendente

SPRINT 1 ✅ CONCLUÍDO (commits 74cf7cd, 6cc58a3, 48d92d7, 7e62a0e)
├── 1.1  ✅ KalmanPairs integrado ao main.py
├── 1.2  ✅ Defaults → AUDUSD, NZDUSD, USDJPY
├── 1.2b ✅ symbol_resolver.py — resolução dinâmica de símbolos
└── 1.3  ✅ Persistência RiskEngine (logs/risk_state.json)

SPRINT 2 ✅ CONCLUÍDO (commit 4d433dd)
└── 2.1  ✅ PairScreener algorítmico (5 filtros + score composto + integrado main.py)

SPRINT 2.5 ⏸ AGUARDA AÇÃO MANUAL — Validação Cent Account
└── Migrar testes para Cent Account real ($50)                  [1 dia setup]
    Depositar $50, abrir conta Cent na Vantage, alterar .env ACCOUNT_TYPE=CENT
    Rodar 30–60 dias para validar antes de escalar

SPRINT 3 🔜 PRÓXIMA IMPLEMENTAÇÃO
├── 3.1 TickCollector asyncio                                   [2 dias]
├── 3.2 Docker + TimescaleDB                                    [1 dia]
└── 3.3 db.py wrapper                                           [1 dia]

SPRINT 4
├── 4.1 Docker + Redis                                          [4h]
└── 4.2 RiskEngine com Redis                                    [1 dia]

SPRINT 5
├── 5.1 structlog                                               [4h]
├── 5.2 Docker + Prometheus + Grafana                           [1 dia]
└── 5.3 metrics.py + dashboards                                 [1 dia]

SPRINT 6
├── 6.1 Walk-Forward Validator                                  [2 dias]
└── 6.2 Kalman threshold dinâmico por ATR                       [1 dia]
    ⚠️ Recalibração obrigatória: confidence atual produz só 4 valores
    discretos (0.1558, 0.1862, 0.2134, 0.2326). Não discrimina qualidade de
    sinal. Após recalibração, revisar MIN_CONFIDENCE e Sprint 0.8.

SPRINT 7 (Quando Cent Account validada)
└── Migrar para PRO ECN real ($10.000 depósito)                 [1 dia config]
```

---

## Descoberta Crítica — Kalman Confidence (Sprint 6.2)

O modelo Kalman atual produz apenas **4 valores discretos** de confidence: `0.1558, 0.1862, 0.2134, 0.2326`. O máximo absoluto é **0.2326**, portanto qualquer `MIN_CONFIDENCE ≥ 0.30` elimina 100% dos sinais. Além disso, a confiança mais alta (0.21+) performa **pior** que a mais baixa — o campo `confidence` não discrimina qualidade de sinal no modelo atual. **Sprint 0.8 permanece bloqueado** até esta recalibração.

---

## Dependências Python a Instalar

```bash
# Fase 3
pip install psycopg2-binary asyncpg

# Fase 4
pip install redis

# Fase 5
pip install structlog prometheus-client

# Já instaladas (confirmar)
pip install MetaTrader5 arch pykalman pandas numpy scipy scikit-learn
```

---

*Gerado em: 2026-09-02 | Atualizado: 2026-09-08 | Sistema: Vantage Quant v4 (Sprint 2.1 concluído)*
