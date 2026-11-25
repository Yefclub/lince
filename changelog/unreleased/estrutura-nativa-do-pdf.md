---
tipo: fix
titulo: Tabelas com grade, colunas, gráficos, rodapés e formulários do PDF lidos pela estrutura do próprio arquivo
---

Correções que vieram do benchmark contra o Docling (issue 150) e da auditoria em PDFs reais (issue 151).

- **Tabela com grade desenhada** sai com as células que o PDF desenhou, sem IA, inclusive com célula mesclada (HTML com
  `rowspan`/`colspan`). O quadro vizinho, a nota e o eixo de um gráfico que o extrator juntava à tabela saem como texto,
  ao lado dela. Antes a tabela ia para a IA num recorte que podia cortar a última linha, e a linha sumia sem pendência.
- **Tabela sem grade**: o recorte passa a ter o tamanho do bloco de linhas da tabela, sem invadir o gráfico ao lado nem
  perder a primeira e a última linha.
- **Gráfico vetorial**: as barras e a linha do gráfico se juntam pelos eixos e viram um recorte só, com o gráfico inteiro.
- **Duas colunas** que o extrator emendava (títulos fundidos, frases de uma coluna dentro da outra) voltam à ordem de
  leitura pela posição dos itens.
- **Cabeçalho e rodapé corridos** saem do corpo também em documento de duas páginas, quando "Página 1 de 2" prova a
  paginação, e quando o extrator cola o rodapé no último parágrafo da página.
- **Marca d'água** em diagonal ("MINUTA") vai para os metadados em vez de virar título no corpo.
- **Formulário (AcroForm)**: o campo sai com o rótulo impresso ao lado dele ("Inscrição estadual"), não com o nome
  interno ("ie").
- **Fórmula**: sem uma caixa só dela, o texto nativo fica; some o recorte que cortava a coluna vizinha.
- **Rótulos de um desenho já lido** pela IA não se repetem embaralhados no texto quando a leitura traz todos eles.
- **Tabela lida pela IA** só substitui a tabela nativa duvidosa quando traz todos os números dela, com a mesma casa
  decimal; senão as duas ficam e a pendência é registrada.

Dependência nova: `pdfplumber` (MIT), pelo algoritmo de detecção de grade.
