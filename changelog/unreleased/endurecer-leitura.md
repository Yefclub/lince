---
tipo: security
titulo: Documento hostil é recusado antes de ser aberto
---

Arquivos Office, ODF e EPUB (que são pacotes zip) passam por uma conferência antes de qualquer descompactação:
pacote que descompactaria demais (zip bomb), com partes demais ou com caminho fora do pacote é recusado com um erro
claro. XML com expansão de entidades ("billion laughs") é barrado pelo parser; se isso acontecer numa parte de
metadados ou num gráfico, só aquela parte fica de fora e o documento continua sendo lido, com a parte registrada em
`metadados.partes_ilegiveis`.
