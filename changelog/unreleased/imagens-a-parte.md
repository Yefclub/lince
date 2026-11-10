---
tipo: feat
titulo: PDF grande inteiro no modo tarefas, com as imagens buscadas uma a uma
---

**O fim do documento não fica mais de fora.** No modo `tarefas`, um PDF com mais de 200 leituras parava na tarefa 200,
e o resto ia para as pendências como "teto de leituras" (num PDF técnico de 207 páginas, as páginas 188 a 207 ficaram
sem leitura). Com o novo campo `imagens=separadas`, o teto passa a 1.000 tarefas por documento e 5.000 por sessão: um
PDF de umas 500 páginas escaneadas entra inteiro no plano.

**Imagens fora da resposta.** Com `imagens=separadas`, a sessão volta com as tarefas sem a imagem embutida, só com o
id, a página, a região e o tamanho (`imagem_bytes`), e cada imagem é buscada em
`GET /v1/sessoes/{id}/itens/{item}/tarefas/{tarefa}/imagem`. A resposta deixa de ter centenas de MB num documento escaneado
grande, e quem chama já pode começar a IA de um documento assim que ele fica pronto, sem esperar o resto do lote.
Cada imagem conta no limite de retenção do token só até ser buscada, e a sessão espera, até 15 minutos depois de
concluída, pelas imagens que ainda não foram buscadas. A montagem (`POST /v1/montagens`) de um documento não espera pelas imagens dos outros: ela não
guarda nada e não é barrada pelo limite de retenção.

**Pendência que diz o que ficou de fora.** Quando o teto é atingido, a pendência agora diz as páginas, por exemplo
"páginas 188–207: teto de 200 tarefas por documento", em vez de uma linha por tarefa. Quem chama pode mostrar ao usuário
exatamente o que não foi lido.

**Para quem não muda nada.** Sem o campo novo, tudo segue como antes: as imagens embutidas em base64, o teto de 200
tarefas por documento e 300 por sessão, e o mesmo texto, as mesmas tarefas e as mesmas imagens. Só muda o texto da
pendência quando o teto é atingido.

Na biblioteca, `Leitor.preparar(..., imagens_em=pasta)` grava as imagens das tarefas na pasta em vez de trazê-las na
memória. Contrato da API 1.14.0: o campo `imagens`, a rota da imagem, `imagem_bytes` e os códigos `imagens-invalido` e
`imagem-nao-encontrada` são novos e opcionais.
