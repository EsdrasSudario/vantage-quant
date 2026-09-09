---
name: project-vantage-quant
description: Estado do desenvolvimento do Vantage Quant System — robô de trading Forex/XAU para Vantage Markets
metadata: 
  node_type: memory
  type: project
  originSessionId: 5d56b989-31e8-4684-8189-1154b9881dd0
  modified: 2026-09-09T13:37:11.866Z
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

**Sprint 2.3 concluído.** Próxima subfase: **2.5** — Migrar testes para Cent Account real ($50) — ação manual do usuário. Após isso: **Sprint 3.1** TickCollector asyncio.

### Detalhes do PairScreener (Sprint 2.1)

- 5 filtros em cascata: liquidez (spread < 2 pips), vol mínima (ATR14 > 5 pips), vol máxima (GARCH < 30%), correlação (> 0.80 remove menor score), GeoScore CRITICAL por exposição temática do par
- Score composto: 40% spread + 40% ATR + 20% GARCH → ranking dos aprovados
- Integrado ao `main.py`: executa no startup e a cada 4h (`_SCREENER_INTERVAL_SEC = 14400`)
- Loop principal filtrado por `active_symbols` (subconjunto aprovado pelo screener)
- Fallback para barras H1 via MT5 quando `bars` dict não fornecido

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

### Problema identificado no Kalman confidence
- O modelo só produz 4 valores discretos: 0.1558, 0.1862, 0.2134, 0.2326
- MAX absoluto = 0.2326 — threshold 0.30 elimina 100% dos sinais
- **Conclusão:** confidence do Kalman não discrimina qualidade — recalibração no Sprint 6.2
- **0.8 bloqueado** — manter MIN_CONFIDENCE=0.15 por enquanto

## Regra de trabalho acordada

Implementar uma subfase por vez. Aguardar autorização do usuário para commit/push e para iniciar a próxima subfase.

## Plano completo de referência

O documento completo está em `C:\Users\esdras.costa\Downloads\vantage_implementation_plan.md`
