---
tipo: fix
titulo: Figuras de PDF girado (plantas, desenhos em paisagem) chegam inteiras e de pé para a IA
---

**Página girada.** Num PDF com a página girada (`/Rotate` 90, 180 ou 270, comum em plantas, diagramas elétricos e
desenhos de CAD em paisagem), a figura virava tarefa, mas o recorte enviado à IA saía em branco: as posições vinham
de um jeito e a imagem da página, de outro. Agora a análise da página usa o mesmo referencial para tudo, e o recorte
de cada figura, a página inteira escaneada e a releitura ampliada do esforço máximo vão para a IA de pé, como a página
aparece na tela. A conferência de texto oculto dessas páginas passa a comparar a cor do texto com o fundo certo.

**Página com a área visível herdada.** Quando a página não declara o próprio tamanho e herda a área do documento, a
parte visível era medida pela folha errada: num desenho com a área visível na metade direita da folha, valia a
metade esquerda, e a página que ficava sem área visível entrava só com o texto. Agora a área visível vem da mesma
conta que a renderização faz.

Os demais PDFs sem rotação saem iguais.
