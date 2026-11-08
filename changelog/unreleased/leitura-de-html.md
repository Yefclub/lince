---
tipo: feat
titulo: Páginas HTML lidas como documento, com as mesmas proteções do Office
---

Arquivos `.html` e `.htm` (página salva, relatório exportado, e-mail salvo em HTML) agora são lidos. Títulos,
listas, tabelas e links viram Markdown, e o título, o autor, a descrição e as palavras-chave da página vão para as
propriedades.

#### As mesmas proteções do resto

Texto que a pessoa não vê (escondido por estilo, com fonte zero ou transparente, ou marcado como oculto) sai do corpo
e gera alerta, como no Word e no PDF. Uma instrução escondida numa página para enganar a IA é detectada. Scripts,
estilos e conteúdo de `<noscript>` não viram texto.

#### Imagens

A imagem embutida na própria página é lida pela IA e entra no lugar em que aparecia. A imagem que aponta para outro
endereço nunca é baixada: o Lince não acessa a rede por causa de um documento. Ela fica marcada no texto com a
descrição que a página dá a ela.
