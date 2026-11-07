# Origem dos arquivos de teste reais

Arquivos usados em `tests/test_formatos_reais.py` para provar a leitura e a edição com documentos gravados por
programas de verdade (Microsoft Word, Excel e PowerPoint, LibreOffice, NeoOffice), não só por bibliotecas.

## Apache POI e Apache Tika

Licença: **Apache License 2.0** (texto em `LICENSE-Apache-2.0.txt`; avisos dos projetos em `NOTICE-Apache-POI.txt`
e `NOTICE-Apache-Tika.txt`). Arquivos tirados dos conjuntos de teste públicos dos projetos.

| Arquivo aqui | Origem |
| --- | --- |
| `poi_SampleDoc.docx`, `poi_SimpleMacro.docm`, `poi_SimpleMacro.doc` | apache/poi `test-data/document/` (`SampleDoc.docx`, `SimpleMacro.docm`, `SimpleMacro.doc`) |
| `poi_SampleSS.xlsx`, `poi_SimpleMacro.xlsm`, `poi_SimpleMacro.xls`, `poi_sample.xlsb`, `poi_testVarious.xlsb` | apache/poi `test-data/spreadsheet/` (`SampleSS.xlsx`, `SimpleMacro.xlsm`, `SimpleMacro.xls`, `sample.xlsb`, `testVarious.xlsb`) |
| `poi_SampleShow.pptx`, `poi_SampleShow.ppt`, `poi_SimpleMacro.pptm` | apache/poi `test-data/slideshow/` |
| `test*.*` (PDF, Word, Excel, PowerPoint, ODF, RTF, EPUB, CSV, imagens) | apache/tika, pastas `test-documents/` dos módulos de parser, mesmo nome |

**Modificados pelo Lince:** nomes de pessoas e de empresas foram trocados por "Autor de teste" (ou texto neutro do
mesmo tamanho), para que nenhum fixture traga dado real de pessoa ou empresa. Nos pacotes zip (OOXML, ODF, EPUB)
foram reescritas só as partes que continham nomes (as XML como texto; as binárias, como a do `.xlsb`, com troca do
mesmo tamanho); nos binários (DOC, XLS, PPT, PDF) a troca tem o mesmo número
de bytes, para não deslocar nenhum ponteiro interno, e o RTF foi editado como texto. Fora esses nomes (inclusive um
que aparecia no texto de um slide do `testPPT`), o conteúdo, a estrutura e o programa que gravou cada arquivo
continuam os originais; os arquivos sem nome nenhum não foram tocados.

`testEXCEL.xlsb` fica como caso de falha conhecida: o leitor atual recusa o arquivo com erro claro do documento.

## Derivados (mesmo conteúdo, outra extensão)

Servem para provar o roteamento pela extensão; o formato binário é o mesmo.

- `testPPT.pps` e `testPPT.pot`: cópias de `testPPT.ppt`.
- `testJPEG.jpeg`: cópia de `testJPEG.jpg`.
- `testTIFF.tiff`: cópia de `testTIFF.tif`.

## Escritos à mão para o Lince

`texto.tsv`, `texto.txt`, `texto.md`, `texto.json`, `texto.yaml`, `texto.yml`, `texto.xml`, `texto.tex` (MIT, como o
resto do repositório).
