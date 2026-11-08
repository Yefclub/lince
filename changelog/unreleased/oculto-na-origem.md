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

- **Imagem atrás do texto**: uma imagem pequena "atrás do texto" deixava de conferir a cor do documento inteiro.
  Agora ela só deixa incerto o texto que pode cobrir, perto do parágrafo em que está presa. O papel timbrado de
  página inteira continua valendo para a página toda.
- **Tabela com estilo**: a tabela com o estilo mais comum (Grade da Tabela) e os estilos só de borda passam a ter o
  fundo da página; só o estilo que pinta a célula deixa o fundo incerto.
- **Mais partes conferidas**: caixa de texto (pela cor declarada da forma), notas de rodapé e de fim, comentários,
  cabeçalho e rodapé. O texto oculto do cabeçalho, do rodapé e dos comentários também não aparece mais nos
  metadados.
- **Contraste quase nulo**: cinza quase branco sobre branco (como E8E8E8) conta como oculto; cinza claro legível e
  letra branca numa faixa escura, não.
- **Letra espremida**: escala horizontal de até 10% com fonte de até 4 pt conta como oculto.
- **PowerPoint**: letra branca sobre uma imagem toda branca (como fundo da caixa ou como figura embaixo) conta como
  oculta; sobre foto, continua no texto.

#### Parte que não dá para conferir aparece no resultado

Quando uma parte do arquivo não pode ser conferida (por exemplo, XML com entidade sem definição), ela fica como está,
o alerta diz qual parte falhou e por quê, e a mesma linha entra em `cobertura.pendentes`
("texto oculto não conferido: word/document.xml (motivo)"), para quem chama poder decidir.
