---
tipo: feat
titulo: Prazo por documento: o chat recebe o texto na hora, e a IA completa o resto depois
---

#### Leitura com hora para acabar

Agora dá para pedir que cada documento fique pronto em um prazo: `leitor.ler(arquivo, prazo=20)` na biblioteca, ou
o campo `prazo` (1 a 3600 segundos) no envio pela API. É o que um chat precisa: a pessoa não fica esperando a IA
descrever todas as imagens de um relatório grande. O texto nativo sai sempre completo. O que a IA não conseguiu ler
a tempo aparece em `cobertura.pendentes` como `prazo esgotado`, nunca como lacuna silenciosa. Sem prazo, como numa
base de conhecimento, o documento é lido até o fim, como antes.

#### Nada se perde depois do prazo

A leitura que já estava em andamento quando o prazo acabou continua em segundo plano. A resposta vai para o cache,
e a próxima leitura do mesmo documento já a encontra pronta, sem pagar de novo. O custo dessa leitura também é
registrado em `ao_usar`, então o controle de gastos da plataforma não perde nenhuma chamada.
