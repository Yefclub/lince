---
tipo: security
titulo: Texto oculto do Word e do PowerPoint sai sem levar junto o texto visível igual a ele
---

O texto que a IA leria e quem abre o documento não vê (branco no branco, `vanish`, fonte minúscula) agora sai do
próprio arquivo, antes da leitura, e não mais por busca no texto já extraído.

#### O texto visível fica intacto

Antes, um trecho oculto que repetia um texto visível apagava as duas cópias: um "não se responsabiliza" escondido
tirava a frase "O fornecedor não se responsabiliza por atrasos na entrega." do contrato, e um "Cliente" em branco
virava "Clientes atendidos: 40" em "s atendidos: 40". Agora só o trecho escondido sai, em qualquer quantidade de
cópias do visível, no Word (`.docx`, `.docm`) e no PowerPoint. O que saiu continua listado em
`texto_oculto_removido`, com o alerta.

#### Menos rotas para esconder texto

- **Tudo o que o leitor lê é conferido**: as partes do Word são achadas pelas relações do pacote, como o leitor do
  corpo as acha (um corpo gravado com outro nome não escapa mais), e o texto sai de todos os ramos de conteúdo
  alternativo (`mc:AlternateContent`). O que estava oculto só no ramo que o leitor usa também entra em
  `texto_oculto_removido`.
- **Herança completa no Word**: o estilo da tabela (inclusive a formatação da primeira linha, da última e das faixas)
  entra na cadeia de cor, tamanho e `vanish`; o `vanish` segue a regra do Word, em que ligado no estilo de parágrafo
  e de novo no de caractere se anula; e o texto em árabe, hebraico ou marcado como escrita complexa vale pelo tamanho
  de escrita complexa (`szCs`).
- **Imagem atrás do texto**: uma imagem pequena "atrás do texto" deixava de conferir a cor do documento inteiro.
  Agora ela só deixa incerto o texto que pode cobrir, perto do parágrafo em que está presa. O papel timbrado de
  página inteira continua valendo para a página toda, e a faixa do cabeçalho ou do rodapé que entra na área do texto
  deixa incerto só o texto embaixo dela (o título branco sobre a faixa azul do cabeçalho fica). A forma antiga (VML)
  atrás do texto conta do mesmo jeito.
- **Tabela com estilo**: a tabela com o estilo mais comum (Grade da Tabela) e os estilos só de borda passam a ter o
  fundo da página; só o estilo que pinta a célula deixa o fundo incerto.
- **Mais partes conferidas**: caixa de texto (pela cor declarada da forma), notas de rodapé e de fim, comentários,
  cabeçalho e rodapé. O texto oculto do cabeçalho, do rodapé e dos comentários também não aparece mais nos
  metadados.
- **Contraste quase nulo**: cinza quase branco sobre branco (como E8E8E8) conta como oculto; cinza claro legível e
  letra branca numa faixa escura, não.
- **Letra espremida**: escala horizontal de até 10% com fonte de até 4 pt conta como oculto.
- **PowerPoint**: a conferência percorre o slide inteiro, como o leitor: conteúdo alternativo, grupos, campos (como o
  número do slide), células de tabela (com o preenchimento da célula como fundo) e as anotações do orador. O tamanho e
  a cor vêm da herança toda (estilo da caixa, placeholder do layout e do mestre, estilos de texto do mestre) e do
  ajuste automático do texto; o texto da forma oculta pelo painel de seleção sai. Letra branca sobre uma imagem toda
  branca (como fundo da caixa ou como figura embaixo) conta como oculta; sobre foto, continua no texto. Apresentações
  de exibição (`.ppsx`, `.ppsm`), que antes ficavam sem conferir, agora são conferidas.

#### Parte que não dá para conferir aparece no resultado

Quando uma parte do arquivo não pode ser conferida (por exemplo, XML com entidade sem definição), ela fica como está,
o alerta diz qual parte falhou e por quê, e a mesma linha entra em `cobertura.pendentes`
("texto oculto não conferido: word/document.xml (motivo)"), para quem chama poder decidir. O mesmo vale para o slide
forjado com formas demais (mais de 500) ou para a apresentação cuja conferência passaria do teto de custo: esses
slides ficam como estão, com o motivo, em vez de travar a leitura.

O que sai do arquivo é só o texto oculto: declaração, `DOCTYPE`, namespaces e o resto da parte ficam como estavam.
