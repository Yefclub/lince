---
tipo: fix
titulo: Resultados esperando entrega vão para o disco, com teto de 1 GiB por token configurável
---

**Teto maior e fora da memória.** Cada token podia ter até 256 MB de resultado esperando entrega, e dois documentos
pesados ao mesmo tempo (um PDF escaneado com as imagens da 1ª fase do modo tarefas) já lotavam o token, que vale para
todos os usuários de um ambiente. Agora o teto padrão é 1 GiB, cerca de 6 documentos pesados de uma vez, e o
resultado de cada documento, assim como o arquivo de cada edição, fica num arquivo temporário em `/tmp` até ser
entregue, e não na memória do serviço. Monte um volume de disco em `/tmp` (não `tmpfs`, que é memória) com a soma
dos tetos dos tokens e folga: o teto é conferido na entrada de cada envio, e um documento já aceito guarda o
resultado inteiro. Se o disco encher, o documento falha com o motivo, e a sessão termina normalmente.

**Teto por token.** Cada token pode ter o próprio teto na `LINCE_TOKENS`, como `nome:token:teto` (`qa:abc123:2G`),
para um ambiente com mais usuários receber mais. Token sem teto segue no padrão, e a configuração de antes continua
valendo.

**Libera na entrega.** O resultado entregue (a primeira resposta com a sessão concluída) deixa de contar no teto na
hora, e não só no `DELETE` ou quando a sessão vence.

**429 que diz quanto esperar.** O `429 limite-retencao` traz `Retry-After` com o tempo até o próximo resultado que o
token não buscou vencer, e `em_uso_bytes` e `teto_bytes` no corpo (contrato 1.12.0, campos novos e opcionais).
