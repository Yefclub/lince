---
tipo: fix
titulo: PDF grande não segura os outros, respeita o prazo desde o início e o texto oculto não apaga o visível
---

**Um PDF grande não faz os outros esperarem.** O PDF agora é lido em faixas de páginas, e a trava da PDFium é
tomada por pouco tempo e passada, por ordem de chegada, a quem está esperando. Um PDF de uma página que chega
durante a leitura de um de 2.500 sai em menos de meio segundo; antes, esperava dezenas de segundos. O pdf_inspector,
que não usa a PDFium, roda fora da trava. O documento é aberto uma vez só, e não a cada vez que pega a trava (nem a
cada figura relida no esforço máximo): um PDF com a tabela de referências danificada não a reconstrói de novo a cada
vez, e um PDF de uma página pega a trava duas vezes. Uma leitura interrompida (por exemplo, com Ctrl+C) em qualquer
ponto do pedido da trava, na fila, na espera ou ao receber a vez, não deixa a trava presa.

**O prazo vale desde a extração.** Com `prazo`, a leitura para entre uma faixa e outra quando o tempo que resta não
cobre a montagem do que já foi lido: o documento sai com o texto extraído, e as páginas restantes aparecem em
`cobertura.pendentes` como "páginas N–M: não lidas (prazo esgotado)". Num PDF de 2.500 páginas, `prazo=3` termina em
cerca de 3 s; antes, levava mais de 10 s. A primeira faixa sempre é lida, para o documento nunca sair sem texto, e
`cobertura.paginas` continua contando o documento inteiro.

**Teto de páginas de texto.** Além do teto de 2.000 páginas da análise visual, um PDF tem no máximo 5.000 páginas de
texto lidas. As seguintes não são lidas e entram nas pendências ("não lidas (teto de 5000 páginas de texto)").

**Contagem de páginas que não bate.** Um PDF que declara mais páginas do que tem (por exemplo, um milhão) não derruba
mais a leitura: as páginas que existem são lidas, e o resto entra numa pendência só, como "páginas 4–1000000: não
lidas (a página 4 não abre)". Um PDF que declara menos páginas do que tem é lido como um visualizador de PDF o mostra:
só as páginas da contagem são lidas e contadas, curto ou longo, e as de fora não entram no resultado nem nas
pendências. O texto vem sempre de uma cópia das páginas que o leitor abre, e não mais do arquivo inteiro: um PDF com
cem mil páginas e a contagem 1 sai em menos de um segundo, em vez de prender a leitura por minutos.

**Texto oculto sai pela posição.** O texto branco, minúsculo, invisível ou fora da página é tirado de uma cópia da
página, objeto por objeto, antes de o texto dela ser gerado; o documento original não muda. Antes, o trecho oculto
era procurado no texto pronto e apagava também as cópias visíveis iguais a ele: um "não se responsabiliza" branco
cortava a frase visível "O fornecedor não se responsabiliza por atrasos na entrega.". Agora a frase visível fica
inteira e o oculto continua registrado em `texto_oculto_removido`. Uma página com milhares de trechos ocultos é
conferida em menos de um segundo, sem segurar os outros PDFs.

**Oculto que não sai vira alerta e pendência.** Quando um trecho oculto não pode ser tirado da cópia, ele fica no
corpo e a página ganha o alerta "texto oculto que pode ter ficado no corpo: página N", em `alertas_injecao` e em
`cobertura.pendentes`. Isso vale para o texto dentro de um bloco reutilizável do PDF (Form XObject), que pode estar
desenhado também num lugar visível e não é regravado, e para a página com trechos ocultos sobrepostos demais para
tirar um a um.

**Página com recorte fora da folha.** A página cuja área de recorte (CropBox) não cai sobre a folha (MediaBox)
derrubava a leitura do documento inteiro, e também a releitura ampliada do esforço máximo (`esforco="max"`). Agora ela
vale pela folha inteira, como em outros leitores de PDF, nas duas leituras, e o texto oculto dela sai do mesmo jeito.

**Página com conteúdo demais para a análise visual.** Cada página tem um orçamento para a análise visual, que roda com
a vez da PDFium: o agrupamento de imagens e de desenhos, o texto oculto, as tabelas, as fórmulas e a renderização
gastam dele pelo trabalho que fazem, e os recortes das figuras são gravados depois, fora da vez. A página que passa do
orçamento (um mapa com dezenas de milhares de desenhos espaçados, ou um arquivo montado para travar a leitura) entra
só com o texto, com a pendência "página N: só o texto, sem figuras nem páginas escaneadas lidas (conteúdo demais para
a análise visual)" e o alerta de texto oculto não conferido; o resto do documento é lido normalmente. Dentro do
orçamento, nenhuma página segura a vez por mais de cerca de um segundo: 49.999 desenhos espaçados numa página levavam
75 s e agora entram só com o texto em menos de um segundo. Um gráfico com milhares de traços que se juntam continua
indo para a IA.

**Hífen de fim de linha em tempo linear.** Juntar as palavras partidas no fim da linha procurava cada forma com hífen
no texto inteiro: uma página com 20 mil quebras levava 4 s parada. Agora leva centésimos, com a mesma decisão.
