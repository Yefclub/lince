---
tipo: security
titulo: Chave do bot só em dev e main, e análise estática com CodeQL
---

A chave privada do GitHub App do bot (`LINCE_BOT_CHAVE`) passa a ficar no environment `lince-bot`, que só libera
jobs de `dev` e `main`: um workflow rodando em outra branch não consegue mais pedir o token do bot. O novo
`codeql.yml` roda o CodeQL no código Python e nos próprios workflows a cada push em `dev` e `main`, a cada PR e
toda segunda, com os alertas na aba Security; alerta novo de segurança alta ou crítica bloqueia o merge na `dev`.
