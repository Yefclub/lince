---
tipo: feat
titulo: Imagens dos arquivos antigos do Office (.doc, .xls, .ppt) lidas pela IA
---

As imagens dentro de apresentações `.ppt`, planilhas `.xls` e documentos `.doc` (Office 97-2003) agora são lidas.
Antes, as do PowerPoint e do Excel sumiam sem aviso, e no Word se perdia o que não era imagem em linha (como o
logotipo do cabeçalho).

#### Como aparecem

Fotos, capturas e figuras (JPEG, PNG, bitmap e TIFF) vão para a IA e a leitura entra numa seção **Imagens do
documento**, depois do texto: esses formatos não guardam a posição da imagem junto do texto. A imagem que o Lince já
lia no lugar certo do `.doc` não é lida de novo. Desenhos vetoriais (EMF, WMF e PICT, comuns em diagramas e gráficos
colados) ficam marcados como não lidos e registrados em `partes_ilegiveis`, nunca somem em silêncio.

#### Seguro com arquivo hostil

A busca no armazém de imagens do arquivo tem teto de trabalho proporcional ao tamanho, de 512 imagens distintas e de
descompressão dos desenhos vetoriais; um arquivo montado para estourar memória é cortado sem derrubar a leitura.
