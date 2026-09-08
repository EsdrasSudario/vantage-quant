---
name: feedback-workflow
description: Como o usuário quer que o desenvolvimento seja conduzido neste projeto
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 5d56b989-31e8-4684-8189-1154b9881dd0
  modified: 2026-09-04T14:10:34.516Z
---

Implementar uma subfase por vez, em ordem, esperando autorização explícita do usuário antes de:
1. Fazer commit
2. Fazer push
3. Iniciar a próxima subfase

**Why:** Usuário quer controle granular sobre cada entrega — confirmado na sessão de 2026-09-04.

**How to apply:** Após implementar e testar uma subfase, apresentar o resultado e aguardar "pode commitar" ou "pode começar o próximo" antes de agir.
