---
tipo: fix
titulo: Nada de perder texto, texto oculto conferido de verdade e IA chamada por quem usa o Lince
---

Correções e melhorias para usar o Lince como leitor de documentos de uma aplicação em produção.

#### Texto que sumia volta

- **PDF de páginas do mesmo modelo** (lote de faturas, notas, boletos, holerites) e **PDF longo** perdiam o corpo
  das páginas, tomado por cabeçalho repetido. Agora só sai do corpo a linha curta da borda da página, igual em quase
  todas as páginas, em que só o número da página muda ("Página 3 de 12"). Valor, data e número de documento que mudam
  entre páginas ficam, o título fica, o texto lido pela IA nunca sai e, se o que sairia pesaria mais de um quarto do
  texto, nada sai.
- **Word e PowerPoint**: a linha repetida no corpo (checklist, ata, formulário) não é mais apagada. O cabeçalho e o
  rodapé do Word vêm das partes próprias deles, como antes.

#### Texto oculto

- O `DOCTYPE` no XML do Word não desliga mais a conferência; parte que não pode ser lida vira alerta, nunca "nada
  oculto".
- Passa a detectar o texto escondido pelo estilo (inclusive herdado), pela cor do tema, pelo destaque e pelo
  sombreamento da mesma cor do texto, e no PowerPoint a caixa fora do slide, a fonte minúscula e a cor do fundo.
- O alerta de injeção passa a pegar "ignore todas as instruções anteriores".

#### A IA pode ser de quem chama

`Leitor.preparar` devolve o texto extraído e cada imagem que a IA precisa ler (com a instrução, onde ela está, a
chave de cache e os tokens estimados); `Leitor.concluir` monta o mesmo resultado com as respostas. Na API, `modo=tarefas`
no envio e `POST /v1/montagens`. Quando o Lince chama a IA, `uso` traz o custo em dólar que o provedor informa.

#### Posição e repetição

- Cada slide começa com `<!-- slide N -->`, também em `estrutura.inicio_dos_slides`; a nota do gráfico e o
  diagrama entram no slide deles.
- `estrutura.imagens` diz onde está cada imagem (página, slide, aba e célula) e qual leitura a leu.
- A mesma imagem repetida (o logo em todo slide) é lida uma vez, e a leitura vai para todos os lugares.

#### Instalação, concorrência e limites

- O desenho de EMF e WMF vira o extra `lince[emf]`, o que permite instalar em Alpine; sem ele, a imagem entra em
  `cobertura.pendentes` com o motivo. O README explica o que o Alpine precisa.
- A trava da PDFium vale só para o PDF: Office, HTML e texto de outros documentos seguem em paralelo. No PDF, a
  análise visual tem teto de 2.000 páginas e respeita o prazo; acima disso a página entra com o texto completo,
  registrada nas pendências.
- Sem fonte nenhuma no sistema, o texto dos desenhos SVG sai com a fonte embutida do Pillow.
- O `.xlsb` gravado fora da especificação, que o leitor do corpo recusa, é lido registro a registro.
- A bancada `bench/medir.py` refaz a tabela de estado do README com documentos gerados e IA simulada, e roda no CI.

O contrato da API passa para a versão 1.9.0, só com campos e rotas novos.
