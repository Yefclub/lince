---
tipo: feat
titulo: Cache das leituras da IA no Redis, compartilhado entre envios e versões
---

#### A mesma imagem não é paga duas vezes

O Lince pode guardar no Redis o que a IA leu de cada recorte ou página. Quando a mesma imagem volta, com a mesma
instrução e o mesmo modelo (um logotipo repetido, um documento lido no chat e depois na base de conhecimento), a
leitura vem do cache e não custa nada. O relatório de uso marca `cache: true` nessas leituras.

#### Use o Redis que a sua plataforma já tem

Na API, `LINCE_REDIS=redis://host:6379/0` liga o cache e `LINCE_CACHE_DIAS` diz por quantos dias cada leitura fica
(padrão 30). Na biblioteca, passe o cliente Redis da sua plataforma: `lince.CacheRedis(cliente)`. As chaves levam o
prefixo `lince:leitura:`, sem colidir com o resto. Fica a transcrição em texto puro, sob o hash da imagem (numa
página escaneada, é o texto da página); o arquivo nunca. Na API, o cache é separado por token: um cliente não
descobre o que outro leu nem recebe resposta gravada por outro. Use um Redis com teto de memória e descarte das
chaves antigas, separado de filas que não podem perder dados.

#### Versão nova não lê tudo de novo

A chave do cache deixou de depender da versão do Lince: atualizar não faz pagar de novo o que já foi lido. Ela muda
só quando muda o que interfere na resposta (a imagem, a instrução, o modelo ou a versão do prompt).

#### Redis fora do ar não atrapalha

Se o Redis cair, o documento é lido normalmente pela IA, como sem cache, e a leitura nunca falha por causa dele.
Resposta vazia ou lote que a IA devolveu quebrado não entram no cache, então uma falha passageira do provedor não fica
valendo por dias.
