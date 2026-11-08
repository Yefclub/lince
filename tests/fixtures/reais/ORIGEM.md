# Origem dos arquivos de teste reais

Arquivos usados em `tests/test_formatos_reais.py` para provar a leitura e a edição com documentos gravados por
programas de verdade (Microsoft Word, Excel e PowerPoint, LibreOffice, NeoOffice), não só por bibliotecas.

## Apache POI e Apache Tika

Licença: **Apache License 2.0** (texto em `LICENSE-Apache-2.0.txt`; avisos dos projetos em `NOTICE-Apache-POI.txt`
e `NOTICE-Apache-Tika.txt`). Arquivos tirados dos conjuntos de teste públicos dos projetos.

| Arquivo aqui | Origem |
| --- | --- |
| `poi_SampleDoc.docx`, `poi_SimpleMacro.docm`, `poi_SimpleMacro.doc`, `poi_DiffFirstPageHeadFoot.doc`, `poi_HeaderFooterUnicode.doc` | apache/poi `test-data/document/` (mesmo nome, sem o prefixo `poi_`) |
| `poi_SampleSS.xlsx`, `poi_SimpleMacro.xlsm`, `poi_SimpleMacro.xls`, `poi_sample.xlsb`, `poi_testVarious.xlsb`, `poi_EmbeddedChartHeaderTest.xls`, `poi_47251_1.xls`, `poi_TwoSheetsOneHidden.xls`, `poi_45761.xls`, `poi_60405.xls` | apache/poi `test-data/spreadsheet/` (mesmo nome, sem o prefixo `poi_`) |
| `poi_vector_image.doc` | apache/poi `test-data/document/vector_image.doc` |
| `poi_SimpleWithImages.xls` | apache/poi `test-data/spreadsheet/SimpleWithImages.xls` |
| `poi_pictures.ppt` | apache/poi `test-data/slideshow/pictures.ppt` |
| `poi_SampleShow.pptx`, `poi_SampleShow.ppt`, `poi_SimpleMacro.pptm`, `poi_SimpleMacro.ppt`, `poi_headers_footers.ppt`, `poi_slide_master.ppt` | apache/poi `test-data/slideshow/` (mesmo nome, sem o prefixo `poi_`) |
| `test*.*` (PDF, Word, Excel, PowerPoint, ODF, RTF, EPUB, HTML, SVG, CSV, imagens) | apache/tika, pastas `test-documents/` dos módulos de parser, mesmo nome |

**Modificados pelo Lince:** nomes de pessoas e de empresas foram trocados por "Autor de teste" (ou texto neutro do
mesmo tamanho), para que nenhum fixture traga dado real de pessoa ou empresa. Nos pacotes zip (OOXML, ODF, EPUB)
foram reescritas só as partes que continham nomes (as XML como texto; as binárias, como a do `.xlsb`, com troca do
mesmo tamanho); nos binários (DOC, XLS, PPT, PDF) a troca tem o mesmo número
de bytes, para não deslocar nenhum ponteiro interno, e o RTF foi editado como texto. Fora esses nomes (inclusive um
que aparecia no texto de um slide do `testPPT`), o conteúdo, a estrutura e o programa que gravou cada arquivo
continuam os originais; os arquivos sem nome nenhum não foram tocados. Nos OLE, o nome curto de empresa (três a seis
letras) foi trocado só no stream de propriedades (`DocumentSummaryInformation`), para a troca não acertar outro texto.

`testEXCEL.xlsb` fica como caso de falha conhecida: o leitor recusa o arquivo com erro claro do documento. Ele foi
gravado fora da especificação (o registro BrtBundleSh traz quatro bytes a mais antes do `rId` da aba), e o calamine,
o outro leitor livre de `.xlsb` testado, também não o abre.

## Derivados (mesmo conteúdo, outra extensão)

Servem para provar o roteamento pela extensão; o formato binário é o mesmo.

- `testPPT.pps` e `testPPT.pot`: cópias de `testPPT.ppt`.
- `testJPEG.jpeg`: cópia de `testJPEG.jpg`.
- `testHTML.htm`: cópia de `testHTML.html`.
- `testTIFF.tiff`: cópia de `testTIFF.tif`.

## Escritos à mão para o Lince

`texto.tsv`, `texto.txt`, `texto.md`, `texto.json`, `texto.yaml`, `texto.yml`, `texto.xml`, `texto.tex` (MIT, como o
resto do repositório).

## Gerados pelo Lince

`lince_imagem_em_linha.doc`: Word 97-2003 com um parágrafo, uma imagem PNG sintética (caixa e círculo coloridos) e
outro parágrafo, montado com python-docx e convertido para `.doc` pelo LibreOffice. Sem dado de pessoa ou empresa;
serve para provar que a imagem que o leitor do corpo já entrega não é lida duas vezes.
