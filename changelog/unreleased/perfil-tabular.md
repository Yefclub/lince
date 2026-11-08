---
tipo: feat
titulo: Perfil de cada planilha para a IA consultar os dados sem ler tudo
---

A leitura de planilhas (`.xlsx`, `.xlsm`, `.xlsb`, `.xls`, `.ods`) e de CSV/TSV agora traz, em
`estrutura.perfil_tabular`, um resumo de cada tabela. Com ele, a IA decide como consultar uma planilha grande sem
precisar ler o texto inteiro.

#### O que vem em cada tabela

- o nome da aba (vazio no CSV);
- o cabeçalho e cada coluna com o tipo que predomina nela: número, data, booleano, texto ou vazio;
- até 5 linhas de amostra;
- quantas linhas com dado estão no texto.

#### Sem prometer o que o texto não tem

O perfil é montado das mesmas linhas que viram a tabela do Markdown, então nunca diz mais do que o texto traz. A
aba cortada por um limite de leitura vem marcada com `extracao_cortada`, e a planilha com mais de 64 colunas vem
com `colunas_cortadas`. O resultado é o mesmo nos dois caminhos de leitura, o normal e o de planilha grande.

Nos arquivos `.xlsx` e `.xlsm`, `estrutura.nomes_definidos` lista os intervalos com nome criados pelo usuário (até 32,
sem os nomes internos do Excel, como a área de impressão). O contrato da
API passa para a versão 1.8.0, só com campos novos.
