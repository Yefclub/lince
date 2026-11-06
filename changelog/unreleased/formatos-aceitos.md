---
tipo: feat
titulo: CSV e TSV lidos, e formato não suportado recusado na hora
---

**CSV e TSV.** Arquivos `.csv` e `.tsv` agora são lidos como tabela. O separador (vírgula, ponto e vírgula ou
tabulação) e a codificação (UTF-8 ou Latin-1) são detectados sozinhos. Antes, esses arquivos falhavam na leitura.

**Formato não suportado.** Um envio com arquivo de formato que o Lince não lê, como `.html` ou `.eml`, é recusado
na hora com `415 formato-nao-suportado`, dizendo qual arquivo e quais extensões são aceitas. O lote não entra na
fila. Na biblioteca, um arquivo sem extensão conhecida é lido pelo conteúdo quando ele traz o marcador do formato
(PDF, RTF, Office, OpenDocument); sem marcador, a leitura falha com `lince.FormatoNaoSuportado`.

**Lista exata.** O README traz a tabela dos formatos aceitos, por tipo, conferida arquivo a arquivo.
