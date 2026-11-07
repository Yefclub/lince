---
tipo: fix
titulo: Cabeçalho e rodapé preservados, e todos os formatos testados com arquivos reais
---

#### Cabeçalho e rodapé não se perdem mais

O cabeçalho e o rodapé de documentos do Word, de planilhas do Excel e de arquivos do LibreOffice (texto, planilha e
apresentação) eram ignorados na leitura. Agora eles aparecem nos metadados, em `cabeçalho/rodapé`. Assim, a
classificação ("Confidencial") e a versão que costumam ficar ali chegam junto com o documento. No Excel, os códigos
de página e data aparecem legíveis, como `Página [página] de [páginas]`.

#### Arquivos com macro sinalizados

Documentos, planilhas e apresentações com macro trazem `macros: true` nos metadados. A macro nunca é executada.

#### Texto mais limpo

Parágrafos vazios de arquivos do LibreOffice e de livros EPUB não aparecem mais como linhas com uma barra solta.

#### Testado com arquivos de verdade

Cada formato aceito agora é testado no CI com um arquivo gravado pelo Word, Excel, PowerPoint ou LibreOffice,
vindo dos conjuntos de teste públicos do Apache POI e do Apache Tika. Também são testadas a edição de arquivos reais
do Office e a preservação das macros. Alguns arquivos `.xlsb` ainda falham na leitura, com erro claro do documento.
