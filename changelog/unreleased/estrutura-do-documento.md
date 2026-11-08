---
tipo: feat
titulo: Estrutura do documento: páginas, slides, abas e seções com posição
---

Toda leitura agora traz `estrutura`, para quem navega pelo documento ou divide o texto para a busca:

- **Páginas** do PDF, e onde cada página começa no Markdown, para cada trecho indexado saber de que página veio.
- **Slides** das apresentações (PowerPoint, inclusive o antigo `.ppt`, e LibreOffice), contados pela lista de slides
  do próprio arquivo: slide sem título também conta, e o slide-mestre não conta.
- **Abas** das planilhas (Excel, inclusive `.xlsb` e o antigo `.xls`, e LibreOffice), na ordem, com as ocultas
  marcadas.
- **Seções**: cada título do texto, com o nível e a posição em que começa, sem confundir com um `#` dentro de bloco de
  código. A posição conta caracteres como o Python; em JavaScript, conte por `Array.from(texto)`, para emoji e outros
  caracteres especiais não deslocarem as posições.

Na API, a estrutura vem em cada documento, junto com o Markdown e os metadados.
