---
tipo: feat
titulo: Páginas HTML lidas como documento, com as mesmas proteções do Office
---

Arquivos `.html` e `.htm` (página salva, relatório exportado, e-mail salvo em HTML) agora são lidos. Títulos,
listas, tabelas e links viram Markdown, e o título, o autor, a descrição e as palavras-chave da página vão para as
propriedades.

#### As mesmas proteções do resto

Texto que a pessoa não vê sai do corpo e gera alerta, como no Word e no PDF: marcado como oculto ou escondido pelo
estilo da própria tag (`display:none`, `opacity:0`, `visibility:hidden` ou fonte zero). O estilo é lido com as regras
do CSS, como o navegador: vale a última declaração válida, `!important` vence, e comentário ou escape no meio não
enganam a leitura.
Assim, uma instrução escondida numa página para enganar a IA é detectada. O que só a renderização da página
decide fica de fora, para não apagar conteúdo visível: a cor do texto contra o fundo (branco sobre azul é visível) e
o estilo definido por classe numa folha de estilos. O estilo de esconder vale para tudo o que vem dentro dele: só o trecho que volta a ter
tamanho ou a ficar visível (como as colunas dos e-mails montados com fonte zero) continua no texto. Scripts, estilos e conteúdo de `<noscript>` não viram
texto, e a imagem dentro de um trecho oculto também não é lida.

Página com aninhamento acima de 2048 níveis é lida até onde dá, e o corte fica registrado em `partes_ilegiveis`.
Links e mídias com o arquivo embutido no endereço (`data:`) não despejam o conteúdo em base64 no texto.

#### Imagens

A imagem embutida na própria página é lida pela IA e entra no lugar em que aparecia. A imagem que aponta para outro
endereço nunca é baixada: o Lince não acessa a rede por causa de um documento. Ela fica marcada no texto com a
descrição que a página dá a ela. Desenhos SVG dentro da página mantêm o texto deles, como os rótulos de um gráfico.
