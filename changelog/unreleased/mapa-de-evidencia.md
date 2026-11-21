---
tipo: feat
titulo: Mapa de evidência do PDF: índice com as páginas, páginas visuais e a imagem da página original
---

**Índice com as páginas de cada seção.** O resultado de um PDF traz `estrutura.indice`: cada seção com a página onde
começa e a última que ela pode ocupar (`pagina_fim`). A fonte são os marcadores do próprio PDF (`fonte: sumario`) ou,
sem eles, os títulos do texto (`fonte: titulos`), sem o que o extrator promove a título e não é seção: o cabeçalho
corrido que se repete em toda página, a frase em negrito, o item da bibliografia e a entrada do sumário impresso. Quem
busca pode navegar pelo índice antes de ler, como num sumário, ou pôr o caminho da seção em cada trecho que indexa.

**Onde vale olhar o original.** `estrutura.paginas_visuais` lista as páginas em que o Markdown traz a transcrição de
algo que se vê (imagem, desenho, tabela, fórmula, página escaneada) e o que a leitura não garante: valor estimado
(`≈`), trecho ilegível ou leitura que não veio. Com `lince.imagens_das_paginas(arquivo, [12, 13])`, quem responde
pega essas páginas em PNG, como são exibidas, e manda ao modelo junto com o Markdown na hora da pergunta.

**Nada muda para quem já usa.** O Markdown, os metadados e o resto da estrutura saem idênticos (conferido byte a
byte). Contrato da API 1.15.0: `indice` e `paginas_visuais` são campos novos e opcionais da estrutura.
