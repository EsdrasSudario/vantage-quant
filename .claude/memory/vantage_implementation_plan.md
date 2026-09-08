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

### Como o custo é configurável por conta

**Arquivo:** `backtest/engine.py`, `execution/order_manager.py`

```python
# config/.env — adicionar:
ACCOUNT_TYPE=RAW_ECN   # ou PRO_ECN ou CENT

# core/costs.py — criar este arquivo:
import os

COMMISSION_ROUND_TURN = {
    "STANDARD_STP": 0.00,    # custo embutido no spread
    "RAW_ECN":      6.00,    # $3.00/lado × 2
    "PRO_ECN":      3.00,    # $1.50/lado × 2
    "CENT":         0.06,    # RAW_ECN ÷ 100 (lotes em centavos)
}

def get_commission_per_lot() -> float:
    account = os.getenv("ACCOUNT_TYPE", "RAW_ECN")
    return COMMISSION_ROUND_TURN.get(account, 6.00)
```

```python
# backtest/engine.py — substituir hardcode por:
from core.costs import get_commission_per_lot
COMMISSION = get_commission_per_lot()   # usa .env automaticamente
```

### Estratégia de conta recomendada em 3 etapas

**Etapa 1 — Agora (sem custo):**
Abrir conta **demo PRO ECN** na Vantage. Corrigir custo no código para `$3.00/lot` round turn. Rerunnar todos os backtests — os resultados atuais não são confiáveis.

**Etapa 2 — Validação real com risco mínimo:**
Migrar para **Cent Account real** ($50 depósito). O robô opera com execução real e custos reais, mas exposição 100× menor. Validar por 30–60 dias.

**Etapa 3 — Escala:**
Com resultados consistentes na Cent, migrar para **PRO ECN real** ($10.000 depósito mínimo). O sistema já estará calibrado para esse custo exato.

### Recalibração necessária após correção de custo

| Parâmetro | Valor atual | Valor recomendado (RAW ECN) | Valor recomendado (PRO ECN) |
|-----------|-------------|-----------------------------|-----------------------------|
| Commission hardcode | $1.50/lot | $6.00/lot | $3.00/lot |
| MIN_CONFIDENCE | 0.15 | **≥ 0.35** | **≥ 0.25** |
| TP mínimo (pips) | não definido | **≥ 10 pips** (cobrir $6 + margem) | **≥ 5 pips** |
| Frequência máx. trades | ilimitada | **Reduzir** — custo alto penaliza overtrading | Maior frequência viável |

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

## Plano de Implementação por Fase

---

### FASE 0 — Definição de Estratégia, Correções Bloqueadoras e Recalibração ✅ CONCLUÍDA
> Todos os bloqueadores críticos corrigidos. Sistema seguro para rodar em MT5 Live (exceto 0.8 bloqueado e 0.9 aguardando ação manual).

---

#### 0.0 Diagnóstico MT5 Live — Situação atual do sistema (2026-09-03)

**O sistema consegue conectar e enviar ordens de entrada em Live.** Porém, há 4 bloqueadores identificados que tornam a operação Live arriscada no estado atual:

| # | Bloqueador | Severidade | Arquivo | Tempo fix |
|---|-----------|-----------|---------|-----------|
| B1 | Fechamento de posições com bug — fecha errado no MT5 | 🔴 Crítico | `execution/order_manager.py` | ~2h |
| B2 | RiskEngine cego às posições reais — `open_position_from_result` não existe | 🔴 Crítico | `main.py` + `risk/risk_engine.py` | ~2h |
| B3 | SL/TP calculado com pip fixo 0.0001 — quebra USDJPY e XAUUSD | 🟡 Importante | `execution/order_manager.py` | ~1h |
| B4 | Headlines geopolíticas hardcoded — GeoScore nunca muda | 🟡 Importante | `main.py` → `get_geo_headlines()` | ~3h |

**Resumo de prontidão para Live:**

| Componente | Status |
|------------|--------|
| Conexão MT5 / autenticação | ✅ Funciona |
| Envio de ordens de entrada | ✅ Funciona |
| SL/TP correto (EUR/GBP/AUD/NZD/CAD) | ✅ Funciona |
| SL/TP correto para JPY e XAU | 🔴 Bug — pip errado |
| Fechamento de posições | 🔴 Bug — abre ordem errada |
| RiskEngine rastreando posições reais | 🔴 Bug — cego ao Live |
| GeoScore com dados de mercado reais | 🔴 Headlines fixas |
| Custo de comissão correto | 🔴 4× subestimado |
| `--max-iter` rodando sessão completa | ⚠️ Para em 1h40 com default |

> ⚠️ **Os itens B1 e B2 devem ser corrigidos antes de qualquer teste em MT5 Live.** Sem eles, o sistema pode acumular posições abertas sem controle de risco funcional.

---

#### 0.0b Estratégia-alvo definida: SWING INTRADAY ✅

**Decisão registrada:** O robô opera na estratégia **Swing Intraday**.

| Parâmetro | Valor definido | Justificativa |
|-----------|---------------|---------------|
| Trades/dia alvo | **3–8 operações** | Compatível com custo RAW ECN ($6/lot) |
| TP | **30–40 pips** (manter atual) | Mínimo para cobrir custo + ter edge real |
| SL | **20 pips** (manter atual) | R:R ≥ 2:1 com TP 40 pips |
| Posições simultâneas | **máx. 3** (1/símbolo, 3 símbolos) | Já implementado no código |
| Conta adequada | **RAW ECN** (atual) ou **Cent** (testes) | Swing intraday viável com $6/lot se TP ≥ 30 pips |
| PRO ECN | Desejável no longo prazo | Reduz custo pela metade, melhora edge |
| `--max-iter` | **Deve ser `0` (infinito)** | Default atual de 100 = só ~1h40 de operação |
| `--interval` | **60s** (manter) | Adequado — avalia sinal a cada minuto |

**Por que Swing Intraday é viável na RAW ECN:**
Com TP de 40 pips e lote de 0.01 (micro), o ganho bruto por trade é ~$4. O custo round turn na RAW ECN é $6.00/lot padrão — mas em micro lotes (0.01 lot) o custo cai para **$0.06/trade**. O risco é o sizing errado por lote cheio. Ver item 0.1.

**O que NÃO fazer (descartado):**
Alta frequência (10–30 trades/dia) com TP < 15 pips foi descartada. Com RAW ECN $6/lot, qualquer TP abaixo de 12 pips em lote padrão é matematicamente negativo antes mesmo do sinal errar.

---

#### 0.1 Corrigir B1 — Fechamento de posições em MT5 Live

**Arquivo:** `execution/order_manager.py`

**Problema:** `close_position()` chama `self.execute()` com `win_prob=0.5` hardcoded, recalculando volume pelo Kelly em vez de usar o volume da posição original. No MT5 Live isso abre uma nova ordem oposta com sizing errado em vez de fechar a posição existente.

**Correção:**

```python
def close_position(self, symbol: str, direction: str, volume: float) -> OrderResult:
    """Fecha posição existente via ordem oposta com volume exato."""
    if self.mt5_connected:
        return self._close_mt5_position(symbol, direction, volume)
    # Paper: simula fechamento
    close_dir = "SELL" if direction == "BUY" else "BUY"
    tick = mt5.symbol_info_tick(symbol) if self.mt5_connected else None
    price = (tick.bid if close_dir == "SELL" else tick.ask) if tick else \
            self._base_prices.get(symbol, 1.0)
    res = OrderResult(True, symbol, close_dir, volume, price, price, 0.0, 0, 0.0)
    self._order_log.append(res)
    return res

def _close_mt5_position(self, symbol: str, direction: str, volume: float) -> OrderResult:
    """Fecha posição MT5 real usando positions_get + ordem oposta com volume exato."""
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return OrderResult(False, symbol, direction, volume, 0, 0, 0, 0, 0,
                           error_msg=f"Nenhuma posição aberta em {symbol}")
    pos = positions[0]
    close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY \
                 else mt5.ORDER_TYPE_BUY
    tick = mt5.symbol_info_tick(symbol)
    price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       symbol,
        "volume":       pos.volume,          # ← volume EXATO da posição
        "type":         close_type,
        "position":     pos.ticket,          # ← ticket da posição a fechar
        "price":        price,
        "deviation":    10,
        "magic":        20260831,
        "comment":      "vantage_quant_close",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        err = result.comment if result else "Sem resposta"
        return OrderResult(False, symbol, direction, volume, price, 0, 0, 0, 0,
                           error_msg=err)
    slip = round(abs(result.price - price) * 10_000, 2)
    res = OrderResult(True, symbol, direction, pos.volume, price, result.price,
                      slip, result.order, 0.0)
    self._order_log.append(res)
    return res
```

#### 0.2 Corrigir B2 — RiskEngine cego às posições reais

**Arquivo:** `main.py`

**Problema:** Linha 217 faz `risk.open_position_from_result = res` — atributo dinâmico Python que o `RiskEngine` nunca lê. O motor de risco não sabe quantas posições estão abertas no MT5, tornando os checks de VaR, circuit breaker e limite de 6 posições ineficazes.

**Correção:** Substituir a linha com a chamada correta ao método `open_position()`:

```python
# main.py — após res = oms.execute(...)
if res.success:
    open_positions[sym] = {
        "direction": pkt.direction,
        "volume":    res.volume,
        "entry":     res.filled_price,
    }
    # CORREÇÃO: usar o método correto do RiskEngine
    from risk.risk_engine import Position
    risk.open_position(Position(
        symbol=sym,
        direction=pkt.direction,
        volume=res.volume,
        entry_price=res.filled_price,
        nav_at_open=nav,
    ))
    # REMOVER: risk.open_position_from_result = res  ← deletar esta linha
```

Verificar também se a dataclass `Position` em `risk_engine.py` tem todos esses campos — ajustar se necessário.

#### 0.3 Corrigir B3 — Pip size por símbolo para SL/TP

**Arquivo:** `execution/order_manager.py`

**Problema:** `sl_pips * 0.0001` fixo quebra USDJPY (pip = 0.01) e XAUUSD (pip = 0.1). A Vantage rejeita ou executa ordens com SL/TP absurdos para esses símbolos.

**Correção:**

```python
# Adicionar no topo da classe OrderManager ou em core/costs.py
PIP_SIZE = {
    "USDJPY": 0.01,  "EURJPY": 0.01,  "GBPJPY": 0.01,
    "AUDJPY": 0.01,  "NZDJPY": 0.01,  "CADJPY": 0.01,
    "XAUUSD": 0.10,  "XAGUSD": 0.01,
}
DEFAULT_PIP = 0.0001

def _pip(self, symbol: str) -> float:
    return PIP_SIZE.get(symbol, DEFAULT_PIP)

# No _send_mt5_order — substituir 0.0001 por self._pip(symbol):
sl = round(price - sl_pips * self._pip(symbol), 5)
tp = round(price + tp_pips * self._pip(symbol), 5)
```

#### 0.4 Corrigir B4 — GeoScore com headlines reais

**Arquivo:** `main.py` → função `get_geo_headlines()`

**Problema:** Headlines fixas hardcoded → GeoScore retorna sempre o mesmo valor → circuit breaker geopolítico nunca dispara em eventos reais.

**Solução mínima (sem API paga):** Usar RSS feeds gratuitos de Reuters, BBC, e FT para buscar headlines reais a cada ciclo de 60s.

```python
import feedparser

GEO_RSS_FEEDS = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://rss.ft.com/rss/time/sections/96da3bc2-eeb4-4a87-a790-e5e7e6b84a51",
]

def get_geo_headlines(max_items: int = 20) -> list[str]:
    """Busca headlines reais via RSS. Fallback para lista fixa se offline."""
    headlines = []
    try:
        for url in GEO_RSS_FEEDS:
            feed = feedparser.parse(url)
            headlines += [e.title for e in feed.entries[:max_items // len(GEO_RSS_FEEDS)]]
        return headlines if headlines else _fallback_headlines()
    except Exception:
        return _fallback_headlines()

def _fallback_headlines() -> list[str]:
    return ["Market conditions normal — no geopolitical alerts"]
```

Instalar: `pip install feedparser`

#### 0.5 Criar `core/costs.py` e corrigir custo em todo o sistema

Arquivos a modificar:
- Criar `core/costs.py`
- `backtest/engine.py` — substituir `1.50` por `get_commission_per_lot()`
- `execution/order_manager.py` — mesmo ajuste no KellySizer
- `config/.env` — adicionar `ACCOUNT_TYPE=RAW_ECN`

Conforme código na seção "Conta e Custos" acima.

#### 0.2 Corrigir `--max-iter` default em `main.py`

**Arquivo:** `main.py`

```python
# Linha 264 — mudar default de 100 para 0 (infinito):
parser.add_argument("--max-iter", type=int, default=0,
                    help="Número máximo de iterações (0 = infinito)")
```

Com `default=100`, o robô para após ~1h40. Para Swing Intraday o sistema precisa rodar a sessão completa (Londres + NY = ~10h) ou 24h. Ajustar para 0 e adicionar horário de encerramento por sessão de mercado no futuro (Sprint 1).

#### 0.3 Rerunnar todos os backtests com custo real

Rodar `bt_runner` para todos os símbolos com `ACCOUNT_TYPE=RAW_ECN` ($6.00/lot) e estratégia Swing Intraday (TP=40, SL=20). Documentar quais pares permanecem lucrativos com custo correto.

**Critério de aprovação por símbolo:** Sharpe > 0.5, Profit Factor > 1.3, MaxDD < 15%.

#### 0.4 Recalibrar MIN_CONFIDENCE para Swing Intraday + RAW ECN

Após novos backtests, ajustar `MIN_CONFIDENCE` de 0.15 para valor que filtra trades de baixa qualidade sem reduzir demais o volume (alvo: 3–8 trades/dia). Ponto de partida: **0.30** (menos restritivo que 0.35 porque o TP longo já exige sinal mais forte naturalmente).

```python
# signals/signal_aggregator.py
MIN_CONFIDENCE = 0.30   # era 0.15
```

#### 0.6 Corrigir `--max-iter` default em `main.py`

**Arquivo:** `main.py`

```python
# Mudar default de 100 para 0 (infinito):
parser.add_argument("--max-iter", type=int, default=0,
                    help="Número máximo de iterações (0 = infinito)")
```

Com `default=100`, o robô para após ~1h40 independente do que acontece. Para Swing Intraday, o sistema precisa rodar a sessão completa de Londres + Nova York (~10h) sem interrupção.

#### 0.7 Rerunnar todos os backtests com custo real

Rodar `bt_runner` para todos os símbolos com `ACCOUNT_TYPE=RAW_ECN` ($6.00/lot) e estratégia Swing Intraday (TP=40, SL=20). Documentar quais pares permanecem lucrativos com custo correto.

**Critério de aprovação por símbolo:** Sharpe > 0.5, Profit Factor > 1.3, MaxDD < 15%.

#### 0.8 Recalibrar MIN_CONFIDENCE para Swing Intraday + RAW ECN

Após novos backtests, ajustar `MIN_CONFIDENCE` de 0.15 para 0.30. Ponto de partida conservador — o TP longo (40 pips) já funciona como filtro natural de sinais fracos.

```python
# signals/signal_aggregator.py
MIN_CONFIDENCE = 0.30   # era 0.15
```

#### 0.9 Abrir conta demo PRO ECN na Vantage

Acessar [vantagemarkets.com/trading/accounts/pro-ecn](https://www.vantagemarkets.com/trading/accounts/pro-ecn/) e abrir demo. Alterar `MT5_SERVER` no `.env` para o servidor PRO ECN demo e testar conectividade. Comparar resultados com RAW ECN demo para quantificar o impacto da redução de custo no Swing Intraday.

**Tempo estimado FASE 0 completa:** 2–3 dias
**Impacto:** Após concluir a FASE 0, o sistema estará seguro para rodar em MT5 Live — com controle de risco funcional, fechamento correto de posições, custo real e dados geopolíticos ao vivo.

---

### FASE 1 — Correções Rápidas ✅ CONCLUÍDA (commits 74cf7cd, 6cc58a3, 48d92d7, 7e62a0e)

#### 1.1 Integrar KalmanPairs ao `main.py`
**Arquivo:** `main.py` + `signals/kalman_filter.py`

**O que fazer:**
- Definir lista de pares correlacionados (ex: `[("EURUSD","GBPUSD"), ("AUDUSD","NZDUSD")]`)
- Instanciar `KalmanPairs` para cada par no startup
- A cada ciclo, chamar `kp.update(price_a, price_b)` e checar `kp.spread_zscore()`
- Se z-score > 2.0 → gerar sinal stat arb (long spread) com sizing independente do Kelly principal

```python
# Exemplo de integração em main.py
kalman_pairs = {
    ("EURUSD", "GBPUSD"): KalmanPairs(),
    ("AUDUSD", "NZDUSD"): KalmanPairs(),
}
for (sym_a, sym_b), kp in kalman_pairs.items():
    price_a = mt5.symbol_info_tick(sym_a).bid
    price_b = mt5.symbol_info_tick(sym_b).bid
    kp.update(price_a, price_b)
    if abs(kp.spread_zscore()) > 2.0:
        # gerar sinal de reversão ao mean
        pass
```

#### 1.2 Remover USDCAD e EURUSD dos defaults
**Arquivo:** `main.py`

**O que fazer:**
- Alterar default do argumento `--symbols` de `["EURUSD","USDJPY","GBPUSD"]` para `["AUDUSD","NZDUSD","USDJPY"]`
- Adicionar AUDUSD e NZDUSD à tabela `base_prices` no `order_manager.py` (já existem — confirmar valores)
- Documentar no README como v4

**Justificativa:** bt_runner_100_v3 mostrou EURUSD −$36,94 e USDCAD −$56,10. AUDUSD +$9,23 e NZDUSD +$7,23.

#### 1.2b Resolução dinâmica de símbolos habilitados na corretora
**Arquivo:** `main.py` + novo `core/symbol_resolver.py`

**Contexto:** A Vantage Markets pode desabilitar símbolos (`trade_mode=DISABLED`) em determinados horários ou tipos de conta. O sistema atual falha silenciosamente com `retcode=10017 Trade disabled`. A solução é descobrir quais símbolos estão habilitados no início de cada ciclo e resolver automaticamente o símbolo correspondente (ex: `AUDUSD` → `AUDUSD+`) antes de iniciar o fluxo de sinais.

**Fluxo:**
```
STARTUP
  └── Descoberta: symbol_info() em todos os símbolos disponíveis na conta
        → Monta dicionário: {"AUDUSD": DISABLED, "AUDUSD+": FULL, ...}

INÍCIO DE CADA CICLO
  └── Resolução: para cada símbolo em --symbols
        → Se trade_mode habilitado → usa diretamente
        → Se DISABLED → busca correspondente habilitado (tenta + sufixo ou sem sufixo)
        → Se nenhum habilitado → remove do ciclo com aviso
  └── Segue fluxo normal com lista de símbolos resolvidos
```

**O que fazer:**

1. Criar `core/symbol_resolver.py`:

```python
import MetaTrader5 as mt5
import logging

log = logging.getLogger("SymbolResolver")

# Mapeamento de correspondentes conhecidos (sem sufixo ↔ com sufixo)
_COUNTERPARTS = {
    "AUDUSD": "AUDUSD+", "AUDUSD+": "AUDUSD",
    "NZDUSD": "NZDUSD+", "NZDUSD+": "NZDUSD",
    "EURUSD": "EURUSD+", "EURUSD+": "EURUSD",
    "GBPUSD": "GBPUSD+", "GBPUSD+": "GBPUSD",
    "USDJPY": "USDJPY+", "USDJPY+": "USDJPY",
    "USDCAD": "USDCAD+", "USDCAD+": "USDCAD",
    "USDCHF": "USDCHF+", "USDCHF+": "USDCHF",
    "XAUUSD": "XAUUSD+", "XAUUSD+": "XAUUSD",
}

def _is_enabled(symbol: str) -> bool:
    """Retorna True se o símbolo está habilitado para negociação."""
    info = mt5.symbol_info(symbol)
    return info is not None and info.trade_mode != 0  # 0 = DISABLED

def discover_enabled(universe: list[str]) -> dict[str, str]:
    """
    Retorna dicionário {símbolo_solicitado: símbolo_resolvido} para cada
    símbolo do universo. Se o símbolo estiver DISABLED, tenta o correspondente.
    Se nenhum estiver habilitado, o símbolo é omitido do resultado.
    """
    resolved = {}
    for sym in universe:
        if _is_enabled(sym):
            resolved[sym] = sym
        else:
            alt = _COUNTERPARTS.get(sym)
            if alt and _is_enabled(alt):
                log.warning(f"{sym} DISABLED — usando correspondente {alt}")
                resolved[sym] = alt
            else:
                log.warning(f"{sym} DISABLED e sem correspondente habilitado — removido do ciclo")
    return resolved

def resolve_symbols(requested: list[str]) -> list[str]:
    """
    Resolve a lista de símbolos solicitados, substituindo DISABLED pelo
    correspondente habilitado. Retorna lista de símbolos prontos para uso.
    Loga aviso quando o símbolo backtestado difere do símbolo resolvido.
    """
    mapping = discover_enabled(requested)
    result = []
    for sym in requested:
        resolved = mapping.get(sym)
        if resolved is None:
            log.warning(f"{sym}: nenhum símbolo habilitado disponível — ignorado neste ciclo")
            continue
        if resolved != sym:
            log.warning(f"AVISO: usando {resolved} no lugar de {sym} — backtest foi feito em {sym}")
        result.append(resolved)
    return result
```

2. Integrar em `main.py` — chamar `resolve_symbols()` no início de cada ciclo:

```python
from core.symbol_resolver import resolve_symbols

# No início do loop principal, antes de gerar sinais:
active_symbols = resolve_symbols(symbols)
if not active_symbols:
    log.warning("Nenhum símbolo habilitado neste ciclo — aguardando próximo intervalo")
    time.sleep(interval_sec)
    continue
```

**Observações importantes:**
- Re-resolução a cada ciclo (não apenas no startup) — símbolos podem ser habilitados/desabilitados ao longo do dia
- KalmanPairs deve usar os símbolos resolvidos — se `AUDUSD` virar `AUDUSD+`, o par Kalman acompanha
- Quando o símbolo resolvido difere do backtestado, logar aviso claro (não bloquear execução)
- Não requer alteração no backtest — o resolver é exclusivo do loop live

**Tempo estimado:** ~2h

#### 1.3 Persistir estado do RiskEngine
**Arquivo:** `risk/risk_engine.py`

**O que fazer:**
- Adicionar método `save_state(path)` que serializa `self._halted`, `self._daily_pnl`, `self._positions` para JSON
- Adicionar método `load_state(path)` chamado no `__init__`
- Chamar `save_state()` após cada `post_trade_update()`
- Usar arquivo `logs/risk_state.json`

```python
import json
from pathlib import Path

def save_state(self, path="logs/risk_state.json"):
    state = {
        "halted": self._halted,
        "daily_pnl": self._daily_pnl,
        "positions": self._positions,
        "date": str(date.today())
    }
    Path(path).write_text(json.dumps(state))

def load_state(self, path="logs/risk_state.json"):
    if Path(path).exists():
        state = json.loads(Path(path).read_text())
        if state.get("date") == str(date.today()):
            self._halted = state["halted"]
            self._daily_pnl = state["daily_pnl"]
```

---

### FASE 2 — Screening de Pares ✅ CONCLUÍDA (commit 4d433dd)

#### 2.1 ✅ `signals/pair_screener.py` implementado
**Objetivo:** Selecionar automaticamente os N melhores pares a operar em cada sessão.

**Critérios de filtro (implementar em ordem):**
1. **Liquidez mínima:** spread médio < 2 pips nas últimas 100 ticks
2. **Volatilidade mínima:** ATR(14) > 0.0005 (5 pips)
3. **Volatilidade máxima:** GARCH annualizado < 30% (evitar pares em crise)
4. **Correlação:** excluir pares com correlação > 0.80 entre si (manter só o melhor Sharpe do grupo)
5. **GeoScore:** excluir pares cujo par de moedas está exposto a score CRITICAL

```python
class PairScreener:
    UNIVERSE = [
        "EURUSD","GBPUSD","USDJPY","AUDUSD","NZDUSD",
        "USDCAD","USDCHF","EURGBP","XAUUSD"
    ]
    MAX_PAIRS = 4  # máximo de pares simultâneos

    def screen(self, geo_scorer: GeoScore) -> list[str]:
        candidates = []
        for symbol in self.UNIVERSE:
            if not self._passes_liquidity(symbol): continue
            if not self._passes_volatility(symbol): continue
            if geo_scorer.get_level() == "CRITICAL": continue
            candidates.append(symbol)
        return self._filter_correlation(candidates)[:self.MAX_PAIRS]
```

**Integração em `main.py`:** Rodar screener uma vez no startup e a cada 4h.

---

### FASE 3 — Data Layer 🔜 PRÓXIMA (1–2 semanas)
> Adiciona persistência real de tick data. Requer Docker para TimescaleDB.

#### 3.1 Criar `data/tick_collector.py`

**O que fazer:**
- Loop assíncrono (`asyncio`) coletando ticks via `mt5.symbol_info_tick()`
- Buffer circular in-memory (deque maxlen=10000 por símbolo)
- Flush para TimescaleDB a cada 60s

```python
import asyncio
from collections import deque
import MetaTrader5 as mt5

class TickCollector:
    def __init__(self, symbols: list[str]):
        self.symbols = symbols
        self.buffers = {s: deque(maxlen=10_000) for s in symbols}

    async def collect_loop(self):
        while True:
            for symbol in self.symbols:
                tick = mt5.symbol_info_tick(symbol)
                if tick:
                    self.buffers[symbol].append({
                        "time": tick.time,
                        "bid": tick.bid,
                        "ask": tick.ask,
                        "volume": tick.volume
                    })
            await asyncio.sleep(0.1)  # 10 ticks/s

    def get_recent(self, symbol: str, n: int = 500) -> list:
        return list(self.buffers[symbol])[-n:]
```

#### 3.2 Setup TimescaleDB via Docker

**Arquivo:** `docker-compose.yml` (criar na raiz do projeto)

```yaml
version: "3.8"
services:
  timescaledb:
    image: timescale/timescaledb:latest-pg15
    environment:
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      POSTGRES_DB: vantage_quant
    ports:
      - "5432:5432"
    volumes:
      - tsdb_data:/var/lib/postgresql/data

volumes:
  tsdb_data:
```

**Schema SQL** (`data/schema.sql`):
```sql
CREATE TABLE ticks (
    time        TIMESTAMPTZ NOT NULL,
    symbol      TEXT NOT NULL,
    bid         DOUBLE PRECISION,
    ask         DOUBLE PRECISION,
    volume      BIGINT
);
SELECT create_hypertable('ticks', 'time');
CREATE INDEX ON ticks (symbol, time DESC);
```

#### 3.3 Criar `data/db.py` — wrapper de acesso ao TimescaleDB

```python
import psycopg2
import pandas as pd

class TickDB:
    def __init__(self, dsn: str):
        self.conn = psycopg2.connect(dsn)

    def insert_ticks(self, symbol: str, ticks: list[dict]):
        with self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO ticks VALUES (%(time)s,%(symbol)s,%(bid)s,%(ask)s,%(volume)s)",
                [{**t, "symbol": symbol} for t in ticks]
            )
        self.conn.commit()

    def get_ohlcv(self, symbol: str, tf: str = "1min", limit: int = 500) -> pd.DataFrame:
        sql = f"""
            SELECT time_bucket('1 minute', time) AS ts,
                   first(bid, time) AS open,
                   max(bid) AS high,
                   min(bid) AS low,
                   last(bid, time) AS close,
                   sum(volume) AS volume
            FROM ticks
            WHERE symbol = %s
            GROUP BY ts ORDER BY ts DESC LIMIT %s
        """
        return pd.read_sql(sql, self.conn, params=(symbol, limit))
```

---

### FASE 4 — Redis para Estado em Tempo Real (3–5 dias)
> Substitui in-memory do RiskEngine por Redis. Permite múltiplos workers no futuro.

#### 4.1 Setup Redis

**Adicionar ao `docker-compose.yml`:**
```yaml
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
```

#### 4.2 Refatorar `risk/risk_engine.py` para usar Redis

**O que mudar:**
- `self._daily_pnl` → `redis.get("daily_pnl")`
- `self._halted` → `redis.get("circuit_breaker_halted")`
- `self._positions` → `redis.hgetall("positions")`
- TTL automático no daily_pnl (expira à meia-noite UTC)

```python
import redis

class RiskEngine:
    def __init__(self, redis_url="redis://localhost:6379"):
        self.r = redis.from_url(redis_url)

    @property
    def daily_pnl(self) -> float:
        val = self.r.get("daily_pnl")
        return float(val) if val else 0.0

    def update_pnl(self, delta: float):
        self.r.incrbyfloat("daily_pnl", delta)
        # TTL até fim do dia UTC
        import datetime
        now = datetime.datetime.utcnow()
        midnight = (now + datetime.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        self.r.expireat("daily_pnl", midnight)
```

---

### FASE 5 — Logging & Observabilidade (3–5 dias)
> Substituir `logging` texto por stack estruturada.

#### 5.1 Logging estruturado com `structlog`

**Instalar:** `pip install structlog`

**Criar `core/logger.py`:**
```python
import structlog

def get_logger(name: str):
    return structlog.get_logger(name).bind(system="vantage_quant")
```

**Usar em todos os módulos:**
```python
from core.logger import get_logger
log = get_logger(__name__)
log.info("signal_generated", symbol="EURUSD", direction="BUY", confidence=0.72)
```

#### 5.2 Setup Grafana + Prometheus

**Adicionar ao `docker-compose.yml`:**
```yaml
  prometheus:
    image: prom/prometheus
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
    ports:
      - "9090:9090"

  grafana:
    image: grafana/grafana
    ports:
      - "3000:3000"
    environment:
      GF_SECURITY_ADMIN_PASSWORD: admin
```

#### 5.3 Criar `core/metrics.py` — exposição de métricas

```python
from prometheus_client import Counter, Gauge, start_http_server

ORDERS_TOTAL = Counter("vq_orders_total", "Total orders sent", ["symbol","side"])
DAILY_PNL    = Gauge("vq_daily_pnl_usd", "Daily PnL in USD")
GEO_SCORE    = Gauge("vq_geo_score", "Geopolitical risk score")
POSITIONS    = Gauge("vq_open_positions", "Number of open positions")

def start_metrics_server(port=8000):
    start_http_server(port)
```

**Dashboards Grafana a criar:**
- PnL acumulado (linha temporal)
- Distribuição de sinais por par
- GeoScore ao longo do tempo
- Circuit breaker on/off
- Latência do loop principal (histograma)

---

### FASE 6 — Backtesting Avançado (1 semana)

#### 6.1 Walk-Forward Validation

**Arquivo:** `backtest/walk_forward.py`

**Lógica:**
- Dividir histórico em N janelas (ex: 12 meses)
- Para cada janela: 70% treino (otimização de parâmetros), 30% teste (out-of-sample)
- Reportar Sharpe out-of-sample por janela

```python
class WalkForwardValidator:
    def __init__(self, data: pd.DataFrame, n_splits: int = 6, train_ratio: float = 0.7):
        self.data = data
        self.n_splits = n_splits
        self.train_ratio = train_ratio

    def run(self, strategy_fn) -> pd.DataFrame:
        results = []
        window = len(self.data) // self.n_splits
        for i in range(self.n_splits):
            start = i * window
            split = start + int(window * self.train_ratio)
            end = start + window
            train = self.data.iloc[start:split]
            test  = self.data.iloc[split:end]
            params = strategy_fn.optimize(train)
            oos_result = strategy_fn.run(test, params)
            results.append(oos_result)
        return pd.DataFrame(results)
```

#### 6.2 Recalibração do Kalman + threshold dinâmico por ATR

**Arquivo:** `signals/kalman_filter.py`

> ⚠️ **Descoberta crítica (2026-09-08):** O modelo Kalman atual produz apenas **4 valores discretos** de confidence: `0.1558, 0.1862, 0.2134, 0.2326`. O máximo absoluto é **0.2326**, portanto qualquer `MIN_CONFIDENCE ≥ 0.30` elimina 100% dos sinais. Além disso, a confiança mais alta (0.21+) performa **pior** que a mais baixa — o campo `confidence` não discrimina qualidade de sinal no modelo atual. **Sprint 0.8 permanece bloqueado** até esta recalibração.

**O que fazer:**
- Investigar por que o modelo colapsa em 4 valores — provável problema de inicialização das matrizes de covariância
- Calcular `signal_threshold` dinamicamente como `k * ATR(14)` em vez de 0.6 pips fixo
- `k = 0.5` como default calibrável por símbolo
- Adicionar parâmetro `atr_multiplier` ao `KalmanTrend.__init__()`
- Após recalibração: re-avaliar `MIN_CONFIDENCE` e desbloquear Sprint 0.8

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
    ⚠️ Recalibração obrigatória aqui: confidence atual produz só 4 valores
    discretos (0.1558, 0.1862, 0.2134, 0.2326). Não discrimina qualidade de
    sinal. Após recalibração, revisar MIN_CONFIDENCE e Sprint 0.8.

SPRINT 7 (Quando Cent Account validada)
└── Migrar para PRO ECN real ($10.000 depósito)                 [1 dia config]
```

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

## Variáveis de Ambiente a Adicionar ao `.env`

```dotenv
# Conta atual (RAW ECN demo)
MT5_SERVER=VantageMarkets-Demo
MT5_LOGIN=26018171
MT5_PASSWORD=sua_senha_aqui

# ← NOVO: tipo de conta (controla custo de comissão em todo o sistema)
# Opções: STANDARD_STP | RAW_ECN | PRO_ECN | CENT
ACCOUNT_TYPE=RAW_ECN

# Para conta demo PRO ECN (quando aberta):
# MT5_SERVER=VantageMarkets-Demo   ← confirmar servidor correto com a Vantage
# ACCOUNT_TYPE=PRO_ECN

# Para Cent Account:
# ACCOUNT_TYPE=CENT

# Fase 3 — TimescaleDB
DB_HOST=localhost
DB_PORT=5432
DB_NAME=vantage_quant
DB_USER=postgres
DB_PASSWORD=sua_senha_db

# Fase 4 — Redis
REDIS_URL=redis://localhost:6379

# Fase 5 — Métricas
METRICS_PORT=8000
```

---

## Contexto Macro/Geopolítico Ativo (Ago/Set 2026)

| Par | Tese | Direção | Prioridade |
|-----|------|---------|------------|
| USD/JPY | Divergência Fed-BoJ | Short USD | Alta |
| EUR/USD | Expansão fiscal alemã + USD debasement | Long EUR | Alta |
| XAU/USD | Oil shock Hormuz / safe haven | Volatilidade | Média |
| AUD/JPY | BoJ hike surpresa + carry unwind | Short AUD | Média |

---

## Como Usar Este Documento em Nova Sessão

1. Cole este arquivo no início da conversa ou faça upload
2. Diga: *"Continue o desenvolvimento do Vantage Quant conforme o plano. Quero implementar o SPRINT X."*
3. O assistente terá todo o contexto necessário para implementar sem perguntas adicionais.

---

---

## Referências Oficiais Vantage Markets

- [Tipos de conta](https://www.vantagemarkets.com/trading/accounts/)
- [RAW ECN](https://www.vantagemarkets.com/trading/accounts/raw-ecn/) — $6.00/lot round turn, $50 depósito
- [PRO ECN](https://www.vantagemarkets.com/trading/accounts/pro-ecn/) — $3.00/lot round turn, $10.000 depósito real
- [Tabela de comissões](https://www.vantagemarkets.com/trading/fees/commission/)
- [Standard STP](https://www.vantagemarkets.com/trading/accounts/stp/) — spread 1.0+ pip, sem comissão

---

*Gerado em: 2026-09-02 | Atualizado: 2026-09-08 | Sistema: Vantage Quant v4 (Sprint 2.1 concluído)*
