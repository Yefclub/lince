---
tipo: security
titulo: Resposta enorme do provedor de IA não derruba mais a leitura
---

Na leitura com a IA configurada pelo Lince (cabeçalhos `X-IA-*` na API ou `OpenAICompativel` na biblioteca), o corpo
da resposta do provedor agora é lido até 8 MB. Um provedor com defeito, ou hostil, que devolve uma resposta maior
não faz mais a memória do processo crescer sem limite: aquela leitura vira pendência (`RespostaGrande`), sem novas
tentativas, e o texto nativo do documento sai completo.

O resultado da leitura também passa a ter o mesmo teto de tamanho do resultado que `POST /v1/montagens` já
tinha. Quando a mesma leitura entraria em tantos lugares que o texto final passaria do teto (uma imagem repetida
centenas de vezes com uma resposta enorme), as leituras da IA ficam de fora com a pendência "resultado grande
demais" e o documento sai com o texto nativo, em vez de levar o processo junto.
