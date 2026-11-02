---
tipo: infra
titulo: Bot de versão com GitHub App próprio
---

O carimbo de versão na `dev` e a release na `main` passam a ser feitos por um GitHub App do Lince, que tem exceção
nas regras de `dev`, `main` e tags. O token padrão do GitHub Actions era recusado pelas regras das branches. Quando
as regras recusam o bot, o erro agora diz isso claramente, em vez de tentar de novo.
