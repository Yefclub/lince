---
tipo: perf
titulo: PDF grande lido em paralelo, com o mesmo resultado e o tempo de cada etapa no log
---

**A fase 1 do PDF usa todas as CPUs.** Antes, a leitura de um PDF (o texto, a análise das páginas, a renderização e
os recortes das figuras) andava uma página por vez no serviço inteiro, e vários PDFs grandes chegando juntos faziam
fila. Agora ela roda num pool de processos, um por CPU do contêiner (até 8): as páginas de um mesmo documento e os
documentos diferentes andam ao mesmo tempo, com fila justa entre eles, para um PDF de 2.000 páginas não segurar os
outros. Vale para o modo `tarefas` e para a leitura com a IA do Lince.

**Mesmo resultado.** O texto, as tarefas e os recortes saem iguais aos da leitura em série, inclusive a mesma imagem
repetida em várias páginas (o logo do cabeçalho), lida uma vez só. Conferido em 108 PDFs reais e gerados.

**Quanto muda.** Num contêiner de 4 CPUs, a fase 1 de um relatório de 32 páginas com 4 fotos e 3 gráficos por página
caiu para menos de um terço do tempo, e a de um manual de 128 páginas, para menos da metade. A bancada
(`bench/medir.py`) mede o documento sozinho e 4 ao mesmo tempo, em série e em processos, e confere que o plano sai
igual.

**Onde vai o tempo.** Cada fase 1 deixa uma linha no log do serviço com a sessão, o documento e o tempo de cada etapa
(abrir, inspecionar, coletar, regiões, renderizar, recortar), para quem opera ver o que pesa.

**Mais robusto.** Se um processo cai no meio de um PDF hostil, ele é trocado por um novo e o documento é lido de novo,
uma vez, em vez de derrubar o serviço. Sem processos disponíveis, a leitura segue em série, como antes.

Para o deploy: reserve 4 CPUs para uma base de conhecimento que recebe PDFs grandes em lote, e cerca de 300 MB de
memória por CPU além do que o serviço já usa. Na biblioteca, o paralelo é opcional: `lince.paralelo.ativar()` no
início do programa.
