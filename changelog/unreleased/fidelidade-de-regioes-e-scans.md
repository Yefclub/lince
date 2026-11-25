---
tipo: fix
titulo: Leitura de figuras, tabelas e scans com verificação de cobertura
---

No PDF, o leitor reconhece figuras compactas com rótulos, separa painéis independentes e conserva legendas, títulos e notas associados aos recortes. Os limites visíveis das imagens e os itens posicionados da fórmula evitam incluir a coluna vizinha. Cabeçalhos e tabelas fragmentados são reconciliados quando a geometria e o texto do arquivo confirmam o resultado.

Em páginas escaneadas, a cobertura distingue falta de resposta, sinais de leitura parcial e leitura sem confirmação independente. A função `imagens_das_paginas` aceita `regiao` para renderizar um detalhe do PDF na orientação exibida.
