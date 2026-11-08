---
tipo: chore
titulo: Testes da imagem repetida e das faixas sem depender da ordem das leituras
---

Os testes da imagem repetida e das faixas de uma imagem alta dependiam da ordem em que as leituras pela IA terminam,
que muda porque elas correm em paralelo, e falhavam às vezes no CI. No teste da imagem repetida, a IA simulada agora
responde pelo conteúdo da imagem; no das faixas, responde um texto fixo, e o teste conta as dez faixas lidas. Os dois
valem em qualquer ordem. Nada muda na leitura.
