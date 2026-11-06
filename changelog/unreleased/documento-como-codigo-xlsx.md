---
tipo: feat
titulo: Documento como código para planilhas do Excel
---

Planilhas `.xlsx` e `.xlsm` agora também podem ser lidas como código e editadas pela IA sem perder gráficos,
imagens, tabelas, macros ou formatação.

#### Leitura

Com `codigo=sim`, o resultado traz cada aba com as células preenchidas. Cada célula tem um id no formato de sempre
(`Fretes!B3`), com o valor, a fórmula quando tem, e a data já convertida quando a célula é formatada como data.
Também vêm as mesclagens, as tabelas e os nomes definidos.

#### Edição

`POST /v1/edicoes` aceita três operações:

- **`celula`:** grava um valor ou uma fórmula numa célula, mantendo o estilo dela.
- **`intervalo`:** grava uma matriz de valores de uma vez.
- **`renomear_aba`:** renomeia a aba e corrige as referências a ela em fórmulas, nomes, validações e gráficos.

Quando um valor ou uma fórmula muda, o Excel recalcula a planilha inteira ao abrir. Células que não podem ser
escritas sozinhas sem quebrar o arquivo são recusadas com o motivo: dentro de mesclagem, de fórmula matricial ou no
cabeçalho de uma tabela.

Testado em planilhas geradas pelo LibreOffice e pelo openpyxl. Depois de editar e renomear, nenhuma fórmula ficou
com erro, e o LibreOffice recalculou os totais certos.
