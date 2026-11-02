---
tipo: infra
titulo: Versões e releases automáticas, com ambiente de desenvolvimento
---

O Lince passa a ter uma branch `dev` que recebe todas as mudanças. A cada merge nela, o bot carimba uma versão de
desenvolvimento (por exemplo `0.1.1.dev20261102120000`) e publica a imagem `ghcr.io/yefclub/lince:dev`, para testar
antes da release.

A release é uma PR de `dev` para `main` com a label `release:patch`, `release:minor` ou `release:major`. No merge, o
bot calcula a versão, junta as notas de `changelog/unreleased/` no `CHANGELOG.md`, cria a tag, publica a imagem
(`X.Y.Z`, `X.Y` e `latest`) e a release no GitHub, e traz a `main` de volta para a `dev`.
