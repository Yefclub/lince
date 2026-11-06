---
tipo: feat
titulo: Documento como código para Word, com alterações controladas
---

Documentos do Word (`.docx` e `.docm`) agora também podem ser lidos como código e editados pela IA sem perder
formatação, imagens, notas ou partes que o Lince não conhece.

#### Leitura

Com `codigo=sim`, o resultado traz os parágrafos (com estilo, lista e texto), as tabelas (cada célula com os
parágrafos dela), os estilos disponíveis e os comentários. Cada parágrafo tem um id: o próprio id que o Word grava,
ou a posição no documento quando o arquivo não tem.

#### Edição com alterações controladas

`POST /v1/edicoes` aceita `texto`, `substituir`, `inserir_paragrafo_apos`, `remover_paragrafo`, `estilo_paragrafo` e
`comentar`. Por padrão, toda mudança entra como **alteração controlada**, com autor e data: quem abre o arquivo no
Word vê exatamente o que a IA propôs e aceita ou rejeita cada trecho. Para aplicar direto, a operação leva
`"revisao": false`.

#### Cuidados

- **Formatação:** a troca de texto corta no caractere exato, então negrito, itálico e links em volta ficam como estavam.
- **Objetos:** imagens, campos, notas, caixas de texto e controles de conteúdo no meio do parágrafo nunca são
  apagados.
- **Arquivo:** só as partes tocadas são reescritas, e o resto sai idêntico em bytes.
- **Testes:** passou em contratos, currículos e relatórios gerados pelo LibreOffice e pelo python-docx, e todos reabriram sem erro.
