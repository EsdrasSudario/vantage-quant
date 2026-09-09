---
name: project-vantage-quant
description: Estado do desenvolvimento do Vantage Quant System — robô de trading Forex/XAU para Vantage Markets
metadata: 
  node_type: memory
  type: project
  originSessionId: 5d56b989-31e8-4684-8189-1154b9881dd0
  modified: 2026-09-09T18:00:50.529Z
---

# Vantage Quant System

Robô de trading algorítmico Forex/XAU conectado à corretora Vantage Markets via MT5.

**Repositório:** https://github.com/EsdrasSudario/vantage-quant.git  
**Diretório local:** `C:\Users\esdras.costa\Desktop\T\vantage_quant`  
**Conta demo atual:** RAW ECN | Server: `VantageMarkets-Demo` | Login: `26018171`

## Estratégia definida

Swing Intraday — 3–8 trades/dia, TP=40 pips, SL=20 pips, máx. 3 posições simultâneas.

## Status das subfases (SPRINT 0)

| Subfase | Descrição | Status | Commit |
|---------|-----------|--------|--------|
| 0.1 B1 | close_position() corrigido | ✅ feito | 71f218c |
| 0.2 B2 | RiskEngine rastreia posições reais | ✅ feito | 71f218c |
| 0.3 B3 | pip size JPY/XAU corrigido | ✅ feito | 71f218c |
| 0.6    | --max-iter default=0 (infinito) | ✅ feito | 71f218c |
| 0.4 B4 | GeoScore com RSS feeds reais (feedparser) | ✅ feito | 030ecd1 |
| 0.5    | core/costs.py + backtest/engine.py custo real | ✅ feito | 07c03e6 |
| 0.7    | Backtest com custo real (bt_runner_100_v4/v4b) | ✅ feito | 6bd887d |
| 0.8    | MIN_CONFIDENCE 0.15 → 0.30 | ⏸ bloqueado | — |
| 0.9    | Abrir conta demo PRO ECN | ❌ ação manual | — |

## Status das subfases (SPRINT 1)

| Subfase | Descrição | Status | Commit |
|---------|-----------|--------|--------|
| 1.1 | KalmanPairs integrado ao loop principal | ✅ feito | 74cf7cd |
| 1.2 | Defaults de pares → AUDUSD, NZDUSD, USDJPY | ✅ feito | 6cc58a3 |
| 1.2b | Resolução dinâmica de símbolos (symbol_resolver.py) | ✅ feito | 48d92d7 |
| 1.3 | Persistência do RiskEngine (logs/risk_state.json) | ✅ feito | 7e62a0e |

## Status das subfases (SPRINT 2)

| Subfase | Descrição | Status | Commit |
|---------|-----------|--------|--------|
| 2.1 | PairScreener algorítmico (`signals/pair_screener.py`) | ✅ feito | 4d433dd |
| 2.2 | sync_closed_positions() — fix bug posição zumbi SL/TP | ✅ feito | 3fbb1c6 |
| 2.3 | sync_startup_positions() — restaura posições no startup | ✅ feito | f76706c |
| 2.4 | PairScreener: resolve símbolo MT5 antes de chamar API | ✅ feito | 23bef48 |

**Sprint 2.4 concluído.**

### Sessão XAUUSD+ Live — Bugs identificados e status (2026-09-09)

Identificados 4 bugs após execução live com 5 iterações no XAUUSD+:

| # | Bug | Severidade | Arquivo | Status |
|---|-----|-----------|---------|--------|
| 1 | `status()` e circuit breaker ignoravam PnL não-realizado | Crítico | `risk/risk_engine.py` | ✅ **CORRIGIDO** |
| 2 | `record_fill_slippage` usa `10_000` hardcoded (errado para XAU, pip=1.00) | Médio | `risk/risk_engine.py:264` | ✅ **CORRIGIDO** commit 38d35cf |
| 3 | `_simulate_order` não tem `XAUUSD+` → fallback price=1.0 | Baixo | `execution/order_manager.py:242` | ✅ **CORRIGIDO** commit ed867d8 |
| 4 | Screener recebe OHLC com open=high=low=close (ATR zero em main.py) | Baixo | `main.py:394` | ✅ **CORRIGIDO** commit ba222b8 |

#### Bug #1 — CORRIGIDO e commitado (commit f1ca143)

- **`pre_trade_check()` linha 106:** circuit breaker usa `daily_pnl + sum(p.current_pnl for p in self.positions.values())` — evita posições abertas em grande perda escaparem do halt
- **`status()` linha 314:** retorna `Daily PnL` (total = realizado + não-realizado), `Realized PnL` e `Unrealized PnL` separados
- Validado por simulação: 11/11 testes PASS

#### Bug #3 — CORRIGIDO e commitado (commit ed867d8)

- **`_simulate_order()` linha 239:** `base_prices` expandido com todos os símbolos da conta demo: `NZDUSD`, `USDCHF+`, `EURGBP+`, `XAGUSD`, `XAUUSD+`
- Hierarquia de fallback: quando `mt5_connected=True`, tenta `symbol_info_tick()` para preço real (mid de bid/ask); fallback para tabela estática se tick None ou ask=0; fallback final para 1.0 se símbolo desconhecido
- Validado: 20/20 testes mock (8 cenários, 10 símbolos demo)

#### Bug #2 — CORRIGIDO e commitado (commit 38d35cf)

- **`record_fill_slippage()` linha 263:** assinatura estendida com `symbol: str = ""`
- Usa `self._PIP.get(symbol, 0.0001)` para pip_size correto por símbolo (XAUUSD+=1.00, JPY=0.01, default=0.0001)
- `slip_pips = abs(filled_price - requested_price) / pip_size` — divisão correta (antes: `* 10_000` hardcoded)
- `main.py:486` atualizado para passar `symbol=sym`
- Validado: EURUSD 0.3p ✅ | XAUUSD+ 0.11p ✅ (antes reportava 1100p) | USDJPY 0.5p ✅

#### Bug #4 — CORRIGIDO e commitado (commit ba222b8)

- **Causa:** `bars_for_screen` em `main.py` replicava a `pd.Series` de closes em `open`, `high`, `low` e `close` → `high == low` → `ATR = 0` → screener rejeitava todos os pares por vol mínima insuficiente
- **Fix:** nova função `get_ohlc(symbol, n_bars=200)` em `main.py`:
  - Com MT5: `copy_rates_from_pos(TIMEFRAME_H1)` → DataFrame OHLC real com high≠low
  - Fallback paper trading: OHLC sintético com ruído diferenciado por coluna, cobre todos os símbolos demo (incl. XAUUSD+)
- **`bars_for_screen` refatorado:** resolve símbolo MT5 via `_screen_sym_map` antes de chamar `get_ohlc` (ex: XAUUSD → XAUUSD+)
- Validado: 38/38 testes mock (5 cenários, 4 símbolos)

Todos os 4 bugs da sessão live XAUUSD+ corrigidos. Próxima subfase: **Sprint 2.5** Cent Account → **Sprint 3.1** TickCollector asyncio.

### Hotfix — order_manager.py (commit d50713f — 2026-09-09)

- Magic number atualizado: `20260831` → `09092026` (data atual)
- Comment de abertura: `vantage_quant_v1` → `vantage_quant_v2`
- Comment de fechamento: `close_vantage_quant` → `close_vantage_quant_v2`
- Aplicado em ambos os blocos de request (abertura linha ~203 e fechamento linha ~293)

### Hotfix — order_manager.py (commit 1482a0d — 2026-09-09)

- Correção de `SyntaxError`: `09092026` → `9092026` nos dois blocos de request
- Literal com zero à esquerda é inválido em Python 3 (interpretado como octal)

### Detalhes do PairScreener (Sprint 2.1 + 2.4)

- 5 filtros em cascata: liquidez (spread < 2 pips), vol mínima (ATR14 > 5 pips), vol máxima (GARCH < 30%), correlação (> 0.80 remove menor score), GeoScore CRITICAL por exposição temática do par
- Score composto: 40% spread + 40% ATR + 20% GARCH → ranking dos aprovados
- Integrado ao `main.py`: executa no startup e a cada 4h (`_SCREENER_INTERVAL_SEC = 14400`)
- Loop principal filtrado por `active_symbols` (subconjunto aprovado pelo screener)
- Fallback para barras H1 via MT5 quando `bars` dict não fornecido
- **Sprint 2.4:** `symbol_resolve_fn` injetado no `__init__` — `_fetch_bars_mt5` e `_spread_pips` resolvem nome canônico → nome MT5 (ex: XAUUSD → XAUUSD+) antes de cada chamada; nome canônico mantido em toda a lógica interna
- **Sprint 2.4:** Bugfix `df.dtype.names → rates.dtype.names` em `_fetch_bars_mt5` (causava retorno silencioso de None com dados válidos)
- **Sprint 2.4:** `main.py` constrói closure `_resolve_symbol` via `resolve_mapping(symbols)` e passa como `symbol_resolve_fn` ao `PairScreener`
- **Sprint 2.4:** `tests/test_pair_screener.py` criado com 12 testes unitários (7 originais + 5 do padrão main.py)

## Descobertas críticas

### Símbolos na conta demo Vantage (verificado via check_symbols.py)
- **Sem sufixo:** EURUSD, GBPUSD, USDJPY, AUDUSD, NZDUSD, USDCAD, XAGUSD
- **Com sufixo +:** USDCHF+, EURGBP+, XAUUSD+
- **trade_mode=DISABLED (0)** em todos os símbolos testados em 2026-09-04 — retcode 10017
- Filling mode aceito: IOC (filling_mode=2) para todos os símbolos
- Causa provável: restrição de horário ou conta demo expirada — não é bug de código

### Sync de startup — posições MT5 restauradas no boot (Sprint 2.3)
- **Problema:** ao reiniciar o processo com posições vivas no terminal, `open_positions` e `risk.positions` começavam vazios — RiskEngine subestimava exposição real, podendo abrir além do `max_open_positions`
- **Fix:** `sync_startup_positions()` em `main.py` (linha 131) — chama `mt5.positions_get()` uma vez no startup, popula `open_positions` e `risk.positions` com dados reais (volume, entry_price, direction, ticket, current_pnl, open_time)
- Resolve sufixo `+` via `rev_map` (ex: EURUSD+ → EURUSD), filtra símbolos fora do universo, suporta filtro por `magic` (padrão 0 = todos)
- Chamada em `run()` logo após warm-up GARCH/Kalman, antes do `while`
- Validado: 27/27 testes unitários (mock MT5), incluindo circuit breaker de `max_open_positions` funcionando imediatamente após sync — commit f76706c

### Bug crítico corrigido — posição zumbi após SL/TP (Sprint 2.2)
- **Problema:** quando o MT5 fechava uma posição por SL/TP, `open_positions[sym]` permanecia no estado interno, travando novas ordens no símbolo para sempre
- **Fix:** `sync_closed_positions()` em `main.py` — chama `mt5.positions_get()` a cada iteração; se posição ausente no MT5, chama `risk.close_position()` e remove do dict
- Cobre posições diretas e legs de stat arb (KalmanPairs) — validado com 4 testes unitários (mock MT5), commit 3fbb1c6

### Resultado do backtest v4 (custo real RAW ECN $6/lot)
- **Pares aprovados (Net>0, PF>1.3):** AUDUSD (+$15.54), NZDUSD (+$19.73)
- **Reprovados:** EURUSD, GBPUSD, USDJPY, USDCAD
- **XAUUSD+ incompatível** com SL/TP fixos em pips no backtest H1
- ⚠️ pip size XAUUSD corrigido de 0.10 → **1.00** (hotfix cfca9db) em `order_manager.py`, `pair_screener.py`, `risk_engine.py` — SL=20p=$20, TP=40p=$40 agora realistas

### Problema identificado no Kalman confidence
- O modelo só produz 4 valores discretos: 0.1558, 0.1862, 0.2134, 0.2326
- MAX absoluto = 0.2326 — threshold 0.30 elimina 100% dos sinais
- **Conclusão:** confidence do Kalman não discrimina qualidade — recalibração no Sprint 6.2
- **0.8 bloqueado** — manter MIN_CONFIDENCE=0.15 por enquanto

## Regra de trabalho acordada

Implementar uma subfase por vez. Aguardar autorização do usuário para commit/push e para iniciar a próxima subfase.

## Plano completo de referência

O documento completo está em `C:\Users\esdras.costa\Downloads\vantage_implementation_plan.md`
