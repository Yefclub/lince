---
tipo: fix
titulo: Abas ocultas, notas de célula, tabela do PPT, fluxograma do ODP e títulos do RTF
---

Correções que vieram do benchmark contra o Docling (issue 150), nos formatos fora do PDF.

- **Aba oculta** do `.xlsx`, `.xls` e `.ods`: o conteúdo sai do corpo, como o texto oculto, mas não some mais. O começo
  dele vai para `texto_oculto_removido`, com o nome da aba, e passa pela conferência de prompt injection. No `.ods` a aba
  oculta entrava no corpo; agora fica de fora, como no Excel, e o nome vai para `abas_ocultas`.
- **Nota de célula** do `.xls` e do `.ods` sai como a do `.xlsx`, com o endereço da célula.
- **Tabela do `.ppt`** sai como tabela, pela posição das células no slide, no lugar dos parágrafos soltos.
- **Fluxograma do `.odp`** sai com as ligações, a direção das setas e os rótulos Sim/Não, em Mermaid e em lista, como
  o do `.pptx`.
- **Títulos do `.rtf`** saem com o nível da folha de estilos do arquivo.

Dependências novas: `python-calamine` (MIT), para ler as abas ocultas do `.xls`, `.xlsb` e `.ods`, e `xlrd` (BSD-3),
para as notas de célula do `.xls`.
