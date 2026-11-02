---
tipo: feat
titulo: Documento como código: IA lê a estrutura do PPTX e edita sem estragar o arquivo
---

Com `codigo=sim` no envio, cada apresentação traz a estrutura editável: slides, formas, parágrafos, células de
tabela e séries de gráfico, cada um com um id estável tirado do próprio arquivo.

O novo `POST /v1/edicoes` aplica operações (trocar texto, buscar e trocar, notas, célula de tabela, valor de
gráfico, reordenar e remover slides) e devolve o arquivo novo. Só as partes tocadas são reescritas: mídia, mestres,
animações e partes desconhecidas saem idênticas. É tudo ou nada, com verificação de que o documento não mudou desde
a leitura e aviso quando um texto cresce a ponto de poder transbordar a caixa.
