---
tipo: feat
titulo: Estrutura do documento: páginas, slides, abas e seções com posição
---

Toda leitura agora traz `estrutura`, para quem navega pelo documento ou divide o texto para a busca:

- **Páginas** do PDF, e onde cada página começa no Markdown, para cada trecho indexado saber de que página veio.
- **Slides** das apresentações (PowerPoint, inclusive o antigo `.ppt`, e LibreOffice), contados pelo próprio arquivo:
  slide sem título também conta.
- **Abas** das planilhas (Excel, inclusive `.xlsb` e o antigo `.xls`, e LibreOffice), na ordem, com as ocultas
  marcadas.
- **Seções**: cada título do texto, com o nível e a posição em que começa, sem confundir com um `#` dentro de bloco de
  código.

Na API, a estrutura vem em cada documento, junto com o Markdown e os metadados.
