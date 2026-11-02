---
tipo: security
titulo: Antivírus opcional com ClamAV
---

Com `LINCE_CLAMAV=host:porta`, todo arquivo enviado, para leitura ou edição, passa pelo ClamAV antes de ser aberto. Arquivo infectado, mesmo
escondido dentro de um `.docx`, recusa o envio inteiro com o nome da assinatura (`422 arquivo-infectado`). Se o
antivírus estiver fora do ar, o envio é recusado (`503`) em vez de ser lido sem varredura. Sem a variável, nada
muda. O novo `.env.example` lista as variáveis aceitas.
