# Origem dos arquivos de teste reais

Arquivos usados em `tests/test_formatos_reais.py` para provar a leitura e a edição com documentos gravados por
programas de verdade (Microsoft Word, Excel e PowerPoint, LibreOffice, NeoOffice), não só por bibliotecas.

## Apache POI e Apache Tika

Licença: **Apache License 2.0**. Arquivos copiados sem alteração dos conjuntos de teste públicos dos projetos.

| Arquivo aqui | Origem |
| --- | --- |
| `poi_SampleDoc.docx`, `poi_SimpleMacro.docm` | apache/poi `test-data/document/` (`SampleDoc.docx`, `SimpleMacro.docm`) |
| `poi_SampleSS.xlsx`, `poi_SimpleMacro.xlsm`, `poi_sample.xlsb`, `poi_testVarious.xlsb` | apache/poi `test-data/spreadsheet/` (`SampleSS.xlsx`, `SimpleMacro.xlsm`, `sample.xlsb`, `testVarious.xlsb`) |
| `poi_SampleShow.pptx`, `poi_SampleShow.ppt`, `poi_SimpleMacro.pptm` | apache/poi `test-data/slideshow/` |
| `test*.*` (PDF, Word, Excel, PowerPoint, ODF, RTF, EPUB, CSV, imagens) | apache/tika, pastas `test-documents/` dos módulos de parser, mesmo nome |

Os metadados de autoria dentro de alguns arquivos (por exemplo, `dc:creator`) trazem os nomes dos contribuidores
que os criaram, como publicados pelos projetos.

## Derivados (mesmo conteúdo, outra extensão)

Servem para provar o roteamento pela extensão; o formato binário é o mesmo.

- `testPPT.pps` e `testPPT.pot`: cópias de `testPPT.ppt`.
- `testJPEG.jpeg`: cópia de `testJPEG.jpg`.
- `testTIFF.tiff`: cópia de `testTIFF.tif`.

## Escritos à mão para o Lince

`texto.tsv`, `texto.txt`, `texto.md`, `texto.json`, `texto.yaml`, `texto.yml`, `texto.xml`, `texto.tex` (MIT, como o
resto do repositório).
