---
tipo: fix
titulo: Abas ocultas, notas de célula, tabela do PPT, fluxograma do ODP e títulos do RTF
---

Correções que vieram do benchmark contra o Docling (issue 150), nos formatos fora do PDF.

- **Aba oculta** do `.xlsx`, `.xls` e `.ods`: o conteúdo sai do corpo, como o texto oculto, mas não some mais. O começo
  dele (com as notas de célula da aba) vai para `texto_oculto_removido`, com o nome da aba, e passa pela conferência de
  prompt injection. No `.ods` a aba oculta entrava no corpo; agora sai do próprio arquivo, pela posição, antes da
  leitura, como no Excel, e o nome vai para `abas_ocultas`. Gráfico e imagem de aba oculta também ficam fora do corpo (e a
  imagem não vai para a IA). No `.xls`, cuja imagem não diz de que aba é, as imagens de um arquivo com aba oculta
  ficam de fora, com o motivo em `partes_ilegiveis`. Planilha com XML que o leitor seguro recusa e que pode esconder
  aba (DTD com entidade, prefixo sem namespace) é recusada, em vez de lida com a aba oculta dentro.
- **Nota de célula** do `.xls` e do `.ods` sai como a do `.xlsx`, com o endereço da célula.
- **Tabela do `.ppt`** sai como tabela, pela posição das células no slide, no lugar dos parágrafos soltos.
- **Fluxograma do `.odp`** sai com as ligações, a direção das setas e os rótulos Sim/Não, em Mermaid e em lista, como
  o do `.pptx`.
- **Títulos do `.rtf`** saem com o nível da folha de estilos do arquivo, quando o texto do título não se repete num
  parágrafo comum. Título acima do nível 6 sai como nível 6, o último do Markdown. Acima de 512 títulos, os seguintes
  ficam como texto e o corte vai para `partes_ilegiveis`.

O `.ods` é lido em fluxo, direto do `content.xml`. Dependência nova: `xlrd` (BSD-3), para as abas ocultas e as notas de
célula do `.xls`, uma aba por vez.
