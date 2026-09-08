---
name: project-vantage-quant
description: Estado do desenvolvimento do Vantage Quant System — robô de trading Forex/XAU para Vantage Markets
metadata: 
  node_type: memory
  type: project
  originSessionId: 5d56b989-31e8-4684-8189-1154b9881dd0
  modified: 2026-09-08T15:29:28.685Z
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

**Sprint 1 concluído.** Próxima subfase: **2.1** — PairScreener algorítmico (`signals/pair_screener.py`).

## Descobertas críticas

### Símbolos na conta demo Vantage (verificado via check_symbols.py)
- **Sem sufixo:** EURUSD, GBPUSD, USDJPY, AUDUSD, NZDUSD, USDCAD, XAGUSD
- **Com sufixo +:** USDCHF+, EURGBP+, XAUUSD+
- **trade_mode=DISABLED (0)** em todos os símbolos testados em 2026-09-04 — retcode 10017
- Filling mode aceito: IOC (filling_mode=2) para todos os símbolos
- Causa provável: restrição de horário ou conta demo expirada — não é bug de código

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
