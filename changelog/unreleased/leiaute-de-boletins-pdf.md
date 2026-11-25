---
tipo: fix
titulo: PDF de boletim e folheto - fundos de seção, colunas lado a lado e figura no lugar certo
---

Correções que vieram da auditoria em PDFs reais (issue 151).

- **Fundo de seção não é figura.** A caixa de canto arredondado atrás de um bloco de texto, a moldura que envolve as
  seções de uma página e as faixas de cabeçalho, cada uma atrás de uma linha de texto, não viram mais uma figura de
  página inteira, que mandava para a leitura visual os gráficos de novo, junto com a prosa. Quadro com "Figura N." e
  moldura com a grade de uma tabela continuam figura.
- **Uma leitura por gráfico.** A área de traçado dentro do recorte de um gráfico (a miniatura que ia no lote, sem
  título e eixos) e o sombreado das colunas de previsão dentro de uma tabela não geram mais uma segunda tarefa.
- **Coluna lateral e colunas lado a lado.** A coluna lateral estreita (um quarto da página) ao lado do corpo e as
  faixas com três ou quatro avisos lado a lado saem uma coluna de cada vez, quando o extrator emendou as linhas delas
  na mesma altura. A remontagem por cortes recursivos (o corte XY) só entra com essa prova, não reordena tabela
  (de valores, de texto ou com grade) e devolve a saída do extrator se alguma letra faltar.
- **A figura fica na seção dela.** O lugar da figura no texto vem das duas linhas acima dela, na coluna dela: a linha
  mais próxima da página inteira podia ser da coluna ao lado, e a frase que fecha um parágrafo pode se repetir.
