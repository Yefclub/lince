# Segurança

Encontrou uma vulnerabilidade? Não abra issue pública. Use o relato privado do GitHub:
**Security → Report a vulnerability** neste repositório. A resposta inicial sai em até 7 dias.

Entra no escopo: a biblioteca, a API HTTP e a imagem Docker publicadas aqui, inclusive formas de fazer o
texto de um documento escapar da marcação de conteúdo não confiável ou chegar ao modelo sem ser visto por
uma pessoa (texto oculto, prompt injection).

**Modelo de confiança da API.** Quem tem token escolhe o provedor de IA (`X-IA-URL`) e, portanto, para onde o
servidor faz requisições. Por isso:

- Só `http`/`https`, sem query nem fragmento; redirect do provedor não é seguido (a chave não vai para outro host).
- O documento não é guardado: é apagado assim que lido, e o resultado sai da memória na entrega (ou em 15 min, se
  ninguém buscar). A chave do provedor sai da memória quando a sessão termina. Só com `LINCE_REDIS` ligado, a
  transcrição que a IA fez de cada recorte ou página fica no Redis por `LINCE_CACHE_DIAS`, separada por token (um
  cliente não vê nem envenena o cache de outro).
- Cada cliente tem até 64 leituras pendentes; upload exige `Content-Length` e tem teto de 100 MB.
- `Retry-After` do provedor tem teto de 60 s. Um provedor lento dentro do timeout (120 s por chamada) ainda ocupa
  uma das 16 vagas de leitura: a fila justa limita o efeito sobre os outros clientes, não o elimina.
- Dê tokens só a plataformas confiáveis. Em rede com serviços internos sensíveis, isole o contêiner
  (rede própria, saída só para os provedores usados).

**Antivírus.** Com `LINCE_CLAMAV`, todo arquivo passa pelo ClamAV antes de qualquer parser; sem veredito
(clamd fora do ar, limite de tamanho do clamd), o envio é recusado.

Só a última versão publicada recebe correções.
