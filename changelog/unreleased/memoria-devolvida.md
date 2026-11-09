---
tipo: fix
titulo: Servidor devolve ao sistema a memória que as leituras liberaram
---

**Memória que não voltava.** Depois de um dia de uso, o servidor ficava encostado no teto de memória do contêiner
mesmo sem nenhuma sessão guardada. Não era vazamento: era memória que o processo já tinha liberado e que o alocador do
sistema (glibc) guardava para si, fragmentada pelas leituras em paralelo de PDFs com imagens grandes. Um documento
grande no momento errado podia derrubar o serviço.

Agora, no Linux com glibc, o servidor (`lince-servidor`):

- fixa no início o limite em que o alocador passa a pedir memória direto ao sistema, o que desliga o ajuste automático
  que deixava as áreas de cada thread fragmentadas;
- devolve ao sistema, a cada 60 s, a memória livre que ainda tenha ficado guardada, fora de qualquer trava.

Medido com 8 leituras em paralelo de um PDF com imagens grandes: o processo, que ia de 100 MB para cerca de 234 MB e
ficava lá, agora volta a cerca de 80 MB, com o mesmo tempo de leitura. Em outra libc (Alpine/musl) nada muda.

**Memória visível.** `GET /saude` passa a trazer `memoria.rss_bytes`, a memória residente do processo, para quem
opera acompanhar (contrato 1.11.0, campo novo e opcional).
