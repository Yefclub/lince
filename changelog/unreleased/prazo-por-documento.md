---
tipo: feat
titulo: Prazo por documento: o chat recebe o texto na hora, sem esperar a IA
---

#### Leitura com hora para acabar

Agora dá para dar um prazo às leituras pela IA de cada documento: `leitor.ler(arquivo, prazo=20)` na biblioteca,
ou o campo `prazo` (1 a 3600 segundos) no envio pela API. É o que um chat precisa: a pessoa não fica esperando a IA
descrever todas as imagens de um relatório grande. O texto nativo sai sempre completo. O que a IA não conseguiu ler
a tempo aparece em `cobertura.pendentes` como `prazo esgotado`, nunca como lacuna silenciosa. Sem prazo, como numa
base de conhecimento, o documento é lido até o fim, como antes.

#### O prazo vale de verdade

Nenhuma leitura pela IA começa depois do prazo, e a que está em andamento é interrompida quando ele vence, sem novas
tentativas, inclusive quando o provedor manda a resposta aos poucos. Nada fica rodando depois da entrega, então o
servidor não acumula chamadas penduradas, e toda leitura concluída aparece no relatório de uso da própria resposta.
A chamada interrompida já tinha sido enviada e pode ser cobrada pelo provedor sem aparecer nesse relatório. O corte
acompanha a rede: uma conexão lenta pode passar alguns segundos do prazo. Quem usa um provedor próprio vê o tempo que
sobra em `lince.prazo_restante()`.
