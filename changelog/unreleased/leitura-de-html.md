---
tipo: feat
titulo: Páginas HTML lidas como documento, com as mesmas proteções do Office
---

Arquivos `.html` e `.htm` (página salva, relatório exportado, e-mail salvo em HTML) agora são lidos. Títulos,
listas, tabelas e links viram Markdown, e o título, o autor, a descrição e as palavras-chave da página vão para as
propriedades.

#### As mesmas proteções do resto

Texto que a pessoa não vê sai do corpo e gera alerta, como no Word e no PDF: marcado como oculto, escondido pelo
estilo da própria tag (`display:none`, transparente, fonte minúscula ou cor branca, inclusive com `!important`).
Assim, uma instrução escondida numa página para enganar a IA é detectada. Estilo definido por classe numa folha de
estilos não é avaliado, porque a página não é renderizada. Scripts, estilos e conteúdo de `<noscript>` não viram
texto, e a imagem dentro de um trecho oculto também não é lida.

Página com aninhamento acima de 2048 níveis é lida até onde dá, e o corte fica registrado em `partes_ilegiveis`.
Links e mídias com o arquivo embutido no endereço (`data:`) não despejam o conteúdo em base64 no texto.

#### Imagens

A imagem embutida na própria página é lida pela IA e entra no lugar em que aparecia. A imagem que aponta para outro
endereço nunca é baixada: o Lince não acessa a rede por causa de um documento. Ela fica marcada no texto com a
descrição que a página dá a ela.
