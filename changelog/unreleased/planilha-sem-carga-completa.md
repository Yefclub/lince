---
tipo: perf
titulo: Planilhas do Excel com imagens e gráficos lidas bem mais rápido
---

A leitura das imagens e dos gráficos de uma planilha `.xlsx` deixou de carregar a planilha inteira duas vezes. As
imagens agora saem direto das partes de desenho do arquivo, e um gráfico sem valores guardados lê só o intervalo que
desenha. Numa planilha de 100 mil linhas, a leitura caiu de 17 para 7 segundos.

Junto vieram três correções:

- A imagem numa coluna depois da Z (AA, AB...) passou a vir com a célula certa.
- A imagem EMF ou WMF colada numa planilha, que antes sumia sem aviso, agora fica marcada como não lida.
- Planilhas com macro (`.xlsm`) passaram a ter imagens e gráficos lidos como as `.xlsx`.
