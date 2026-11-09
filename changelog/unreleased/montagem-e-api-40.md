---
tipo: fix
titulo: Imagem repetida lida uma vez no texto, montagem que nunca volta vazia e plano que diz quando refazer a leitura
---

**Imagem repetida.** O mesmo logo em centenas de páginas ou slides agora entra no texto uma vez só, inteiro, na
primeira posição. Nas outras fica a âncora `<!-- lido pela IA: id -->` com a referência curta
`_(mesma imagem lida acima: id)_`, coerente com `estrutura.imagens`. Antes, a leitura era copiada em cada lugar: um
DOCX com o mesmo logo 2.000 vezes gerava 240 milhões de caracteres e levava minutos para montar.

**Montagem sem 413 vazio.** As leituras entram no resultado até ele chegar a 16 milhões de caracteres, teto
documentado no contrato. Acima dele, `POST /v1/montagens` responde 200 com o texto nativo e as leituras que couberem;
as outras aparecem em `cobertura.pendentes` como `resultado grande demais`, como já acontecia na leitura com a IA do
Lince. O teto vale só para as leituras: o texto nativo do documento sai sempre inteiro, mesmo que sozinho passe dele.
As montagens ganharam fila justa própria, com até duas ao mesmo tempo: não esperam atrás de leituras presas na IA nem
tiram a vaga delas.

**Plano depois de reiniciar.** O plano do modo `tarefas` leva o identificador da chave que o selou. Depois de um
reinício sem `LINCE_SEGREDO`, a montagem responde `422 plano-de-outra-chave`, com a orientação de refazer a fase 1;
`422 plano-invalido` fica só para o plano alterado ou de outro token. O contrato foi para a versão 1.10.0.

**Teto de tarefas na API.** No modo `tarefas`, cada documento devolve até 200 tarefas e cada sessão, 300. As que
passam ficam nas pendências do preparo, com o motivo, e o novo campo opcional `max_tarefas` baixa o teto do
documento. Um documento que falha antes de entregar as tarefas não gasta a cota da sessão.

**Cabeçalho fixo no PDF.** Uma linha curta idêntica na borda de ao menos 80% das páginas, como
"Contrato 123/2026 - Confidencial", sai do corpo e vai para `metadados["cabeçalho/rodapé"]`. O número de página
tirado do corpo também passa a ser registrado ali. Um número só conta como o da página quando a linha diz isso
("Página 3 de 12", "Página 3/12", "fls. 3") ou é só ele ("3 de 12", "- 3 -", "(3)"; "3)" sem a abertura é item de
lista): no carnê com uma parcela por página, "Parcela 3 de 12" fica no texto, e "Cliente: Transportadora 3 Ltda" na
terceira fatura também. Faturas do mesmo modelo, linhas numeradas e páginas inteiras iguais continuam no texto; das faturas, só a linha de marca idêntica em todas pode ir para os
metadados, como cabeçalho fixo.

**Planilha `.xlsb` hostil.** No `.xlsb` que só o leitor tolerante abre, a tabela de strings compartilhadas tem teto
de 1 milhão de strings e 64 MB de texto. Acima disso, o arquivo é recusado antes de ocupar memória.

**Alpine.** O README traz a receita que funciona (Rust estável pelo `rustup` com `build-base`, e o `git` para
instalar do GitHub; cerca de 16 min de compilação) e recomenda usar a imagem Debian do Lince como serviço.
