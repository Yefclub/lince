---
tipo: fix
titulo: Cabeçalho corrido em fonte grande sai do corpo do PDF, e a frase em negrito partida volta inteira
---

**Cabeçalho que parecia título.** O cabeçalho corrido escrito em fonte grande ("Carta de Conjuntura | 69 | Nota 22",
na Carta de Conjuntura do Ipea) chegava à montagem marcado como título e ficava no corpo, uma vez por página (40 vezes
num documento de 43 páginas). Agora a linha idêntica na borda de ao menos 80% das páginas sai do corpo e vai para os
metadados (`cabeçalho/rodapé`) também quando está marcada como título. Título de seção que não se repete na borda fica.

**Frase em negrito partida.** Relatórios que abrem o parágrafo com uma frase em negrito (o Relatório de Política
Monetária do Banco Central, por exemplo) chegavam com o começo da frase como título e o resto no parágrafo:
`## A atividade econômica… mas o mercado de trabalho` seguido de `**seguiu aquecido.** O PIB…`. A frase volta a ser
uma só, em negrito, no começo do parágrafo, e deixa de aparecer como seção em `estrutura.secoes`. Só junta quando o
bloco seguinte continua a frase; título de verdade, rótulo terminado em dois-pontos e item de lista ficam como estão.
Nenhuma palavra sai do texto (conferido no relatório de 90 páginas: as mesmas 38.186 palavras, 30 falsos títulos a
menos).
