---
tipo: chore
titulo: Teste da imagem repetida sem depender da ordem das leituras
---

Os testes da imagem repetida e das faixas de uma imagem alta dependiam da ordem em que as leituras pela IA terminam, que
muda porque elas correm em paralelo, e falhavam às vezes no CI. Agora a IA simulada responde pelo conteúdo da imagem
e os testes valem em qualquer ordem. Nada muda na leitura.
