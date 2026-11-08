---
tipo: perf
titulo: Planilhas grandes lidas em segundos e com pouca memória
---

Uma planilha `.xlsx` grande (acima de 200 mil células) agora é lida em fluxo, sem montar o documento inteiro na
memória. Numa planilha de 100 mil linhas, a leitura caiu de 17 segundos e 1,4 GB de memória para 2 segundos e
pouco mais de 100 MB.

#### O que entra no texto

Cada aba entra como tabela, igual às planilhas menores. Os limites são os mesmos que o Orbital já usava:

- até 5.000 linhas de dados por aba;
- até 200 mil células por aba e 500 mil no documento;
- até 500 caracteres por célula.

Quando a aba passa desses limites, o texto avisa no ponto do corte que o restante não está na leitura, e o corte fica
registrado em `partes_ilegiveis`. Quem lê, pessoa ou IA, nunca toma o recorte pelo todo. Abas ocultas e vazias
ficam de fora, como antes.
