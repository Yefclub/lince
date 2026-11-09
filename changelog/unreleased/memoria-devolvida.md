---
tipo: fix
titulo: Servidor devolve ao sistema a memória que as leituras liberaram
---

**Memória que não voltava.** Depois de um dia de uso, o servidor ficava encostado no teto de memória do contêiner
mesmo sem nenhuma sessão guardada. Não era vazamento: era memória que o processo já tinha liberado e que o alocador do
sistema (glibc) guardava para si, fragmentada pelas leituras em paralelo de PDFs com imagens grandes. Um documento
grande no momento errado podia derrubar o serviço.

Agora, no Linux com glibc, o servidor:

- aloca os buffers grandes (imagem decodificada, página renderizada) fora das áreas compartilhadas do alocador, e eles
  voltam inteiros ao sistema quando são liberados;
- devolve ao sistema, a cada 60 s, a memória livre que ainda tenha ficado guardada, fora de qualquer trava.

Medido com 8 leituras em paralelo de um PDF com imagens grandes: o processo, que ia de 100 MB para cerca de 240 MB e
ficava lá, agora volta a cerca de 85 MB. Essa carga ficou cerca de 7% mais lenta; documentos sem imagens grandes não
mudam. Em outra libc (Alpine/musl) nada muda.

**Memória visível.** `GET /saude` passa a trazer `memoria.rss_bytes`, a memória residente do processo, para quem
opera acompanhar (contrato 1.11.0, campo novo e opcional).
