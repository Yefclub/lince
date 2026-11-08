---
tipo: feat
titulo: Imagens EMF e WMF do Office passam a ser lidas
---

As imagens vetoriais do Windows (EMF e WMF), comuns em logotipos, diagramas e gráficos colados em documentos do
Word, do PowerPoint e do Excel, antes ficavam marcadas como não lidas. Agora elas são desenhadas e lidas pela IA
como qualquer outra imagem.

Nos formatos atuais (`.docx`, `.pptx`, `.xlsx`), a leitura entra no lugar em que a imagem aparece no texto. Nos
antigos (`.doc`, `.ppt`, `.xls`), que não guardam essa posição, ela entra na seção de imagens do documento, como as
outras imagens desses formatos. O desenho
roda à parte, com teto de tempo e de memória, no mesmo lote dos SVG do documento: um arquivo hostil não prende a
leitura.

A imagem Docker passa a usar Python 3.14, porque o desenhista de EMF e WMF ainda não instala no 3.15. Quem usa o
Lince como biblioteca no Python 3.15 continua com a leitura de antes: a imagem EMF ou WMF fica marcada como não
lida, sem quebrar nada.
