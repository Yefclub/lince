---
tipo: feat
titulo: Cabeçalho, rodapé, abas ocultas e macros também nos arquivos antigos do Office e no RTF
---

#### Cabeçalho e rodapé em todos os formatos de Office

O cabeçalho e o rodapé agora chegam nos metadados (`cabeçalho/rodapé`) também nos formatos antigos do Office:
documentos do Word 97-2003 (`.doc`), planilhas do Excel 97-2003 (`.xls`), apresentações do PowerPoint 97-2003
(`.ppt`, inclusive o rodapé das anotações) e documentos RTF. Antes, só os formatos modernos e os do LibreOffice
traziam essa informação, que costuma guardar a classificação ("Confidencial"), a versão e a data do documento.

Número de página e data aparecem legíveis, como `Página [página] de [páginas]`, também no Word antigo. No RTF, o
texto do cabeçalho é decodificado pela fonte de cada trecho, então cabeçalhos em japonês, chinês ou outro alfabeto
chegam corretos.

#### Abas ocultas no Excel antigo

Planilhas `.xls` com abas ocultas (inclusive as "muito ocultas", que só uma macro reexibe) trazem o nome delas em
`abas_ocultas`, como já acontecia com o `.xlsx`.

#### Mais macros sinalizadas

Duas situações que passavam sem aviso agora trazem `macros: true`: macro VBA em apresentações do PowerPoint antigo
(`.ppt`), que fica guardada de um jeito diferente do Word e do Excel, e macro do Excel 4.0 em planilhas `.xls`, um
formato antigo de macro ainda usado em ataques. A macro nunca é executada.

#### Testado com arquivos reais

Cada novidade é testada no CI com arquivos reais dos conjuntos públicos do Apache POI e do Apache Tika, com os nomes
de pessoas e empresas trocados. Arquivo corrompido ou malformado não derruba a leitura: o corpo continua sendo lido,
e os metadados que não puderam ser lidos ficam registrados em `partes_ilegiveis`.
