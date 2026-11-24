---
tipo: fix
titulo: Planilhas preservam máscaras numéricas, identificam o cache das fórmulas e recuperam desenhos do XLSB
---

No XLSX e XLSM, a leitura em fluxo aplica o formato numérico às células, incluindo identificadores com zeros à
esquerda, percentuais e moeda. As datas continuam em ISO. As fórmulas têm um aviso por aba com a quantidade e até
32 endereços de cada grupo. A expressão aparece quando consta da célula e a leitura confirma que a aba está
desprotegida. Os valores vêm do último resultado salvo no arquivo. As fórmulas sem resultado salvo entram nas
pendências.

O XLSB passa a trazer o texto das formas DrawingML e os gráficos vinculados às abas visíveis, com os dados em
cache. Formas e grupos ocultos ficam fora dessa leitura. Nos CSV e TSV, um campo vazio permanece vazio no Markdown.
O perfil tabular reconhece um cabeçalho com
rótulos distintos à direita de um título isolado por uma linha vazia. Título e notas na margem ficam no texto e
as contagens e amostras passam a considerar as linhas da tabela.
