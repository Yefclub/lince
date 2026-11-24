---
tipo: fix
titulo: Planilhas Excel preservam links, notas, validações, recuos e regras de negrito
---

O XLSX e o XLSM incluem as anotações com a aba e o endereço de origem no Markdown. A leitura registra o
intervalo e a condição original do negrito. Para razões e razões logarítmicas entre células da mesma linha,
identifica as células que satisfazem a regra pelos valores numéricos salvos, inclusive antes do arredondamento
da exibição. Outras expressões de negrito ficam identificadas como não avaliadas.

O perfil tabular mantém o tipo numérico quando a máscara exibe um montante com espaços, moeda ou um traço.
Blocos lado a lado com títulos próprios e sequências de cabeçalhos iguais recebem perfis separados, com título,
coluna de origem e os rótulos distribuídos por várias linhas.
