# Changelog

## 0.2.0 — 2026-11-08

### Novidades

**Cache das leituras da IA no Redis, compartilhado entre envios e versões**

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

**Documento como código para Word, com alterações controladas**

Documentos do Word (`.docx` e `.docm`) agora também podem ser lidos como código e editados pela IA sem perder
formatação, imagens, notas ou partes que o Lince não conhece.

#### Leitura

Com `codigo=sim`, o resultado traz os parágrafos (com estilo, lista e texto), as tabelas (cada célula com os
parágrafos dela), os estilos disponíveis e os comentários. Cada parágrafo tem um id: o próprio id que o Word grava,
ou a posição no documento quando o arquivo não tem.

#### Edição com alterações controladas

`POST /v1/edicoes` aceita `texto`, `substituir`, `inserir_paragrafo_apos`, `remover_paragrafo`, `estilo_paragrafo` e
`comentar`. Por padrão, toda mudança entra como **alteração controlada**, com autor e data: quem abre o arquivo no
Word vê exatamente o que a IA propôs e aceita ou rejeita cada trecho. Para aplicar direto, a operação leva
`"revisao": false`.

#### Cuidados

- **Formatação:** a troca de texto corta no caractere exato, então negrito, itálico e links em volta ficam como estavam.
- **Objetos:** imagens, campos, notas, caixas de texto e controles de conteúdo no meio do parágrafo nunca são
  apagados.
- **Arquivo:** só as partes tocadas são reescritas, e o resto sai idêntico em bytes.
- **Testes:** passou em contratos, currículos e relatórios gerados pelo LibreOffice e pelo python-docx, e todos reabriram sem erro.

**Documento como código: IA lê a estrutura do PPTX e edita sem estragar o arquivo**

Com `codigo=sim` no envio, cada apresentação traz a estrutura editável: slides, formas, parágrafos, células de
tabela e séries de gráfico, cada um com um id estável tirado do próprio arquivo.

O novo `POST /v1/edicoes` aplica operações (trocar texto, buscar e trocar, notas, célula de tabela, valor de
gráfico, reordenar e remover slides) e devolve o arquivo novo. Só as partes tocadas são reescritas: mídia, mestres,
animações e partes desconhecidas saem idênticas. É tudo ou nada, com verificação de que o documento não mudou desde
a leitura e aviso quando um texto cresce a ponto de poder transbordar a caixa.

**Documento como código para planilhas do Excel**

Planilhas `.xlsx` e `.xlsm` agora também podem ser lidas como código e editadas pela IA sem perder gráficos,
imagens, tabelas, macros ou formatação.

#### Leitura

Com `codigo=sim`, o resultado traz cada aba com as células preenchidas. Cada célula tem um id no formato de sempre
(`Fretes!B3`), com o valor, a fórmula quando tem, e a data já convertida quando a célula é formatada como data.
Também vêm as mesclagens, as tabelas e os nomes definidos.

#### Edição

`POST /v1/edicoes` aceita três operações:

- **`celula`:** grava um valor ou uma fórmula numa célula, mantendo o estilo dela.
- **`intervalo`:** grava uma matriz de valores de uma vez.
- **`renomear_aba`:** renomeia a aba e corrige as referências a ela em fórmulas, nomes, validações e gráficos.

Quando um valor ou uma fórmula muda, o Excel recalcula a planilha inteira ao abrir. A fórmula é conferida antes
de entrar (sintaxe e funções em inglês), e as funções novas do Excel, como `XLOOKUP`, já saem no formato que ele
espera. O que quebraria o arquivo é recusado com o motivo: célula dentro de mesclagem ou de fórmula matricial,
cabeçalho ou totais de tabela, aba protegida.

Testado em planilhas geradas pelo LibreOffice e pelo openpyxl. Depois de editar e renomear, nenhuma fórmula ficou
com erro, e o LibreOffice recalculou os totais certos.

**Imagens EMF e WMF do Office passam a ser lidas**

As imagens vetoriais do Windows (EMF e WMF), comuns em logotipos, diagramas e gráficos colados em documentos do
Word, do PowerPoint e do Excel, antes ficavam marcadas como não lidas. Agora elas são desenhadas e lidas pela IA
como qualquer outra imagem.

Nos formatos atuais (`.docx`, `.pptx`, `.xlsx`), a leitura entra no lugar em que a imagem aparece no texto. Nos
antigos (`.doc`, `.ppt`, `.xls`), que não guardam essa posição, ela entra na seção de imagens do documento, como as
outras imagens desses formatos. O desenho
roda à parte, com teto de tempo e de memória, no mesmo lote dos SVG do documento: um arquivo hostil não prende a
leitura.

A imagem Docker passa a usar Python 3.14, porque o desenhista de EMF e WMF ainda não instala no 3.15. Quem usa o
Lince como biblioteca no Python 3.15 continua com a leitura de antes: a imagem EMF ou WMF fica marcada como não
lida, sem quebrar nada.

**Estrutura do documento: páginas, slides, abas e seções com posição**

Toda leitura agora traz `estrutura`, para quem navega pelo documento ou divide o texto para a busca:

- **Páginas** do PDF, e onde cada página começa no Markdown, para cada trecho indexado saber de que página veio.
- **Slides** das apresentações (PowerPoint, inclusive o antigo `.ppt`, e LibreOffice), contados pela lista de slides
  do próprio arquivo: slide sem título também conta, e o slide-mestre não conta.
- **Abas** das planilhas (Excel, inclusive `.xlsb` e o antigo `.xls`, e LibreOffice), na ordem, com as ocultas
  marcadas.
- **Seções**: cada título do texto, com o nível e a posição em que começa, sem confundir com um `#` dentro de bloco de
  código. A posição conta caracteres como o Python; em JavaScript, conte por `Array.from(texto)`, para emoji e outros
  caracteres especiais não deslocarem as posições.

Na API, a estrutura vem em cada documento, junto com o Markdown e os metadados.

**CSV e TSV lidos, e formato não suportado recusado na hora**

**CSV e TSV.** Arquivos `.csv` e `.tsv` agora são lidos como tabela. O separador (vírgula, ponto e vírgula ou
tabulação) e a codificação (UTF-8 ou Latin-1) são detectados sozinhos. Antes, esses arquivos falhavam na leitura.

**Formato não suportado.** Um envio com arquivo de formato que o Lince não lê, como `.eml` ou `.zip`, é recusado
na hora com `415 formato-nao-suportado`, dizendo qual arquivo e quais extensões são aceitas. O lote não entra na
fila. Na biblioteca, um arquivo sem extensão conhecida é lido pelo conteúdo quando ele traz o marcador do formato
(PDF, RTF, Office, OpenDocument); sem marcador, a leitura falha com `lince.FormatoNaoSuportado`.

**Lista exata.** O README traz a tabela dos formatos aceitos, por tipo. Cada extensão foi conferida com um arquivo real,
menos o `.xlsb`, que entra pela lista de formatos do leitor de Office e já era lido antes.

**Gráficos das planilhas antigas do Excel (.xls) identificados**

Os gráficos das planilhas `.xls` (Excel 97-2003) agora aparecem na leitura com o tipo (barras, linhas, pizza, área,
dispersão, radar), o título e o nome de cada série. Os valores que o gráfico desenha já vinham nas tabelas da
planilha; agora fica claro que existe um gráfico, o que ele mostra e com quais séries. Antes, o gráfico do `.xls`
sumia sem deixar rastro.

**Gráficos do LibreOffice lidos com tipo, título e dados**

Os gráficos de planilhas (`.ods`), apresentações (`.odp`) e documentos (`.odt`) do LibreOffice agora entram na
leitura, como já acontecia com os do Excel, do Word e do PowerPoint: o tipo (barras, linhas, pizza...), o título e uma
tabela com as categorias e os valores de cada série, exatamente os números que o gráfico desenha. Antes, o gráfico do
LibreOffice sumia; numa apresentação, os números só existiam dentro dele.

Tabelas de gráfico muito grandes são cortadas em 1.000 linhas e 64 séries, e o corte fica registrado em
`partes_ilegiveis`.

**Imagens das planilhas do LibreOffice lidas pela IA**

Imagens coladas numa planilha `.ods` (logotipo, foto, captura de tela, diagrama) agora são lidas pela IA e entram
no texto com a aba em que estão, como já acontecia nas planilhas do Excel (`.xlsx`). Antes, só o texto e as tabelas
da planilha do LibreOffice eram lidos e a imagem se perdia.

A prévia do gráfico, que o LibreOffice guarda como imagem, não é lida de novo (o gráfico já entra com tipo, título e
dados). A mesma imagem repetida é lida uma vez, ícones pequenos ficam de fora e há o mesmo teto de 512 imagens.

**Logotipo e imagens do cabeçalho e do rodapé lidos pela IA**

A imagem no cabeçalho ou no rodapé de um documento (logotipo do papel timbrado, carimbo, selo, assinatura
digitalizada) agora é lida pela IA no Word (`.docx`, `.docm`), no LibreOffice (`.odt`, `.ods`, `.odp`) e no RTF.
Antes, o texto do cabeçalho ia para os metadados, mas a imagem se perdia.

A leitura entra numa seção **Imagens do cabeçalho e do rodapé**, depois do texto. A mesma imagem repetida em várias
seções, ou que também aparece no corpo, é lida uma vez só, e ícones pequenos (abaixo de 48 px) ficam de fora, como
nas outras imagens.

**Imagens dos arquivos antigos do Office (.doc, .xls, .ppt) lidas pela IA**

As imagens dentro de apresentações `.ppt`, planilhas `.xls` e documentos `.doc` (Office 97-2003) agora são lidas.
Antes, as do PowerPoint e do Excel sumiam sem aviso, e no Word se perdia o que não era imagem em linha (como o
logotipo do cabeçalho).

#### Como aparecem

Fotos, capturas e figuras (JPEG, PNG, bitmap e TIFF) vão para a IA e a leitura entra numa seção **Imagens do
documento**, depois do texto: esses formatos não guardam a posição da imagem junto do texto. A imagem que o Lince já
lia no lugar certo do `.doc` não é lida de novo. Desenhos vetoriais EMF e WMF, comuns em diagramas e gráficos
colados, também são desenhados e lidos pela IA. O PICT do Mac fica marcado como não lido e registrado em
`partes_ilegiveis`, nunca some em silêncio.

#### Seguro com arquivo hostil

A busca no armazém de imagens do arquivo tem teto de trabalho proporcional ao tamanho, de 512 imagens distintas e de
descompressão dos desenhos vetoriais; um arquivo montado para estourar memória é cortado sem derrubar a leitura.

**Páginas HTML lidas como documento, com as mesmas proteções do Office**

Arquivos `.html` e `.htm` (página salva, relatório exportado, e-mail salvo em HTML) agora são lidos. Títulos,
listas, tabelas e links viram Markdown, e o título, o autor, a descrição e as palavras-chave da página vão para as
propriedades.

#### As mesmas proteções do resto

Texto que a pessoa não vê sai do corpo e gera alerta, como no Word e no PDF: marcado como oculto ou escondido pelo
estilo da própria tag (`display:none`, `opacity:0`, `visibility:hidden` ou fonte zero). O estilo é lido com as
regras do CSS, como o navegador: vale a última declaração válida, `!important` vence, e comentário ou escape no meio
não enganam a leitura. Assim, uma instrução escondida numa página para enganar a IA é detectada. O que só a
renderização da página decide fica de fora, para não apagar conteúdo visível: a cor do texto contra o fundo (branco
sobre azul é visível) e o estilo definido por classe numa folha de estilos. O estilo de esconder vale para tudo o
que vem dentro dele: só o trecho que volta a ter tamanho ou a ficar visível (como as colunas dos e-mails montados
com fonte zero) continua no texto. Scripts, estilos, conteúdo de `<noscript>`, `<dialog>` fechado, `<datalist>` e o
conteúdo alternativo de `<canvas>`, `<video>` e `<audio>` não viram texto, e a imagem dentro de um trecho oculto
também não é lida.

Página com aninhamento acima de 2048 níveis é lida até onde dá, e o corte fica registrado em `partes_ilegiveis`.
Links e mídias com o arquivo embutido no endereço (`data:`) não despejam o conteúdo em base64 no texto.

#### Imagens

A imagem embutida na própria página é lida pela IA e entra no lugar em que aparecia. A imagem que aponta para outro
endereço nunca é baixada: o Lince não acessa a rede por causa de um documento. Ela fica marcada no texto com a
descrição que a página dá a ela. Desenhos SVG dentro da página mantêm o texto deles, como os rótulos de um gráfico.

**Desenhos SVG lidos pela IA, como arquivo ou dentro de uma página**

Arquivos `.svg` (diagramas, fluxogramas, gráficos e ícones exportados de Visio, draw.io, Illustrator ou Inkscape)
agora são lidos. O desenho é renderizado e vai para a IA como qualquer outra imagem, então caixas, setas, cores e a
posição de cada rótulo entram na transcrição. O texto do desenho também fica no corpo, uma linha por rótulo, e vale
mesmo sem IA. O título e a descrição do arquivo vão para as propriedades.

#### Também dentro de páginas HTML

O desenho SVG embutido numa página HTML passa pelo mesmo caminho e é lido no lugar em que aparece, com gradientes,
setas e estilos. Ícones pequenos (abaixo de 48 px) não vão para a IA, como já acontece com as imagens, e cada página
tem um teto de 50 desenhos lidos; o que passar disso fica no texto e é registrado.

#### Seguro por padrão

Uma referência a arquivo local ou a endereço externo dentro do SVG nunca entra no desenho: só o que está no próprio
arquivo é desenhado. Texto escondido no SVG sai do corpo e gera alerta, como no HTML. Um SVG montado para travar a
leitura é cortado: a bomba de entidades na hora, e o desenho que prende o processador (filtro pesado) depois de no
máximo 30 segundos ou do prazo do documento, o que vier antes, com o texto preservado.

#### Licenças conferidas por versão

O desenho usa o resvg (MIT). Como o pacote não declara a licença no índice do Python, ele entra numa lista nova de
exceções declaradas da checagem de licenças, que vale só para a versão conferida: ao atualizar o pacote, o CI
reprova até a licença da versão nova ser conferida de novo.

**Cabeçalho, rodapé, abas ocultas e macros também nos arquivos antigos do Office e no RTF**

#### Cabeçalho e rodapé em todos os formatos de Office

O cabeçalho e o rodapé agora chegam nos metadados (`cabeçalho/rodapé`) também nos formatos antigos do Office:
documentos do Word 97-2003 (`.doc`), planilhas do Excel 97-2003 (`.xls`), apresentações do PowerPoint 97-2003
(`.ppt`, inclusive o rodapé das anotações) e documentos RTF. Antes, só os formatos modernos e os do LibreOffice
traziam essa informação, que costuma guardar a classificação ("Confidencial"), a versão e a data do documento.

Número de página e data aparecem legíveis, como `Página [página] de [páginas]`, também no Word antigo. No RTF, o
texto do cabeçalho é decodificado pela fonte de cada trecho, então cabeçalhos em japonês, chinês ou outro alfabeto
chegam corretos.

#### Abas ocultas no Excel antigo

Planilhas `.xls` com abas ocultas (inclusive as "muito ocultas", que só uma macro reexibe) trazem o nome delas em
`abas_ocultas`, como já acontecia com o `.xlsx`.

#### Mais macros sinalizadas

Duas situações que passavam sem aviso agora trazem `macros: true`: macro VBA em apresentações do PowerPoint antigo
(`.ppt`), que fica guardada de um jeito diferente do Word e do Excel, e macro do Excel 4.0 em planilhas `.xls`, um
formato antigo de macro ainda usado em ataques. A macro nunca é executada.

#### Testado com arquivos reais

Cada novidade é testada no CI com arquivos reais dos conjuntos públicos do Apache POI e do Apache Tika, com os nomes
de pessoas e empresas trocados. Arquivo corrompido ou malformado não derruba a leitura: o corpo continua sendo lido,
e os metadados que não puderam ser lidos ficam registrados em `partes_ilegiveis`.

**Perfil de cada planilha para a IA consultar os dados sem ler tudo**

A leitura de planilhas (`.xlsx`, `.xlsm`, `.xlsb`, `.xls`, `.ods`) e de CSV/TSV agora traz, em
`estrutura.perfil_tabular`, um resumo de cada tabela. Com ele, a IA decide como consultar uma planilha grande sem
precisar ler o texto inteiro.

#### O que vem em cada tabela

- o nome da aba (vazio no CSV);
- o cabeçalho e cada coluna com o tipo que predomina nela: número, data, booleano, texto ou vazio;
- até 5 linhas de amostra;
- quantas linhas com dado estão no texto.

#### Sem prometer o que o texto não tem

O perfil é montado das mesmas linhas que viram a tabela do Markdown, então nunca diz mais do que o texto traz. A
aba cortada por um limite de leitura vem marcada com `extracao_cortada`, e a planilha com mais de 64 colunas vem
com `colunas_cortadas`. O resultado é o mesmo nos dois caminhos de leitura, o normal e o de planilha grande.

Nos arquivos `.xlsx` e `.xlsm`, `estrutura.nomes_definidos` lista os intervalos com nome criados pelo usuário (até 32,
sem os nomes internos do Excel, como a área de impressão).

O contrato da API passa para a versão 1.8.0, só com campos novos. Na leitura de planilha grande, a célula agora
mantém os espaços de dentro do texto, como nas planilhas menores.

**Prazo por documento: o chat recebe o texto na hora, sem esperar a IA**

#### Leitura com hora para acabar

Agora dá para dar um prazo às leituras pela IA de cada documento: `leitor.ler(arquivo, prazo=20)` na biblioteca,
ou o campo `prazo` (1 a 3600 segundos) no envio pela API. É o que um chat precisa: a pessoa não fica esperando a IA
descrever todas as imagens de um relatório grande. O texto nativo sai sempre completo. O que a IA não conseguiu ler
a tempo aparece em `cobertura.pendentes` como `prazo esgotado`, nunca como lacuna silenciosa. Sem prazo, como numa
base de conhecimento, o documento é lido até o fim, como antes.

#### O prazo vale de verdade

Nenhuma leitura pela IA começa depois do prazo, e a que está em andamento é interrompida quando ele vence, sem novas
tentativas, inclusive quando o provedor manda a resposta aos poucos. Nada fica rodando depois da entrega, então o
servidor não acumula chamadas penduradas, e toda leitura concluída aparece no relatório de uso da própria resposta.
A chamada interrompida já tinha sido enviada e pode ser cobrada pelo provedor sem aparecer nesse relatório. O corte
acompanha a rede: uma conexão lenta pode passar alguns segundos do prazo. Quem usa um provedor próprio vê o tempo que
sobra em `lince.prazo_restante()`.

### Correções

**Cabeçalho e rodapé preservados, e todos os formatos testados com arquivos reais**

#### Cabeçalho e rodapé não se perdem mais

O cabeçalho e o rodapé de documentos do Word (`.docx`, `.docm`), de planilhas do Excel (`.xlsx`, `.xlsm`) e de
arquivos do LibreOffice (texto, planilha e apresentação) eram ignorados na leitura. Agora eles aparecem nos metadados, em `cabeçalho/rodapé`. Assim, a
classificação ("Confidencial") e a versão que costumam ficar ali chegam junto com o documento. No Excel, os códigos
de página e data aparecem legíveis, como `Página [página] de [páginas]`.

#### Arquivos com macro sinalizados

Documentos, planilhas e apresentações com macro trazem `macros: true` nos metadados: VBA do Office (inclusive
`.doc` e `.xls` antigos), macro do Excel 4.0 nas planilhas modernas e macro do LibreOffice. A macro nunca é
executada.

#### Texto mais limpo

Parágrafos vazios de arquivos do LibreOffice e de livros EPUB não aparecem mais como linhas com uma barra solta.

#### Testado com arquivos de verdade

Cada formato aceito agora é testado no CI com um arquivo real: os de Office, OpenDocument, RTF, EPUB, PDF e imagem
vêm dos conjuntos de teste públicos do Apache POI e do Apache Tika, com os nomes de pessoas trocados. Também são testadas a edição de arquivos reais
do Office e a preservação das macros. Alguns arquivos `.xlsb` ainda falham na leitura, com erro claro do documento.

**Imagem que não abre não derruba mais o documento do Office**

Um documento do Word, do PowerPoint ou do LibreOffice com uma imagem em formato que o Lince ainda não desenha (EMF e
WMF, comuns em diagramas colados do Visio e em gráficos copiados do Excel) ou com a imagem corrompida falhava inteiro,
sem texto nenhum. Agora o documento é lido normalmente: a imagem fica marcada no lugar em que aparecia, com
o formato (por exemplo `[imagem 1: imagem EMF não lida]`), e entra em `partes_ilegiveis`, para a plataforma saber o
que ficou de fora.

#### SVG dentro do Office

Ícones e desenhos SVG inseridos no Word e no PowerPoint (comuns no Office 365) agora são desenhados e lidos pela
IA, como as outras imagens, no lugar em que aparecem. Os desenhos de um documento são feitos juntos, com o mesmo teto
de tempo e de quantidade (50) das páginas HTML.

### Desempenho

**Planilhas grandes lidas em segundos e com pouca memória**

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

**Planilhas do Excel com imagens e gráficos lidas bem mais rápido**

A leitura das imagens e dos gráficos de uma planilha `.xlsx` deixou de carregar a planilha inteira duas vezes. As
imagens agora saem direto das partes de desenho do arquivo, e um gráfico sem valores guardados lê só o intervalo que
desenha. Numa planilha de 100 mil linhas, a leitura caiu de 17 para 7 segundos.

Junto vieram três correções:

- A imagem numa coluna depois da Z (AA, AB...) passou a vir com a célula certa.
- A imagem EMF ou WMF colada numa planilha, que antes sumia sem aviso, agora fica marcada como não lida.
- Planilhas com macro (`.xlsm`) passaram a ter imagens e gráficos lidos como as `.xlsx`.

### Segurança

**Antivírus opcional com ClamAV**

Com `LINCE_CLAMAV=host:porta`, todo arquivo enviado, para leitura ou edição, passa pelo ClamAV antes de ser aberto.
Arquivo infectado, mesmo escondido dentro de um `.docx`, recusa o envio inteiro com o nome da assinatura
(`422 arquivo-infectado`). Se o antivírus estiver fora do ar, o envio é recusado (`503`) em vez de ser lido sem
varredura. Sem a variável, nada muda. O novo `.env.example` lista as variáveis aceitas.

**Chave do bot só em dev e main, e análise estática com CodeQL**

A chave privada do GitHub App do bot (`LINCE_BOT_CHAVE`) passa a ficar no environment `lince-bot`, que só libera
jobs de `dev` e `main`: um workflow rodando em outra branch não consegue mais pedir o token do bot. O novo
`codeql.yml` roda o CodeQL no código Python e nos próprios workflows a cada push em `dev` e `main`, a cada PR e
toda segunda, com os alertas na aba Security; alerta novo de segurança alta ou crítica bloqueia o merge na `dev`.

**Documento hostil é recusado antes de ser aberto**

Arquivos Office, ODF e EPUB (que são pacotes zip) passam por uma conferência antes de qualquer descompactação:
pacote que descompactaria demais (zip bomb), com partes demais ou com caminho fora do pacote é recusado com um erro
claro. XML com expansão de entidades ("billion laughs") é barrado pelo parser; se isso acontecer numa parte de
metadados ou num gráfico, só aquela parte fica de fora e o documento continua sendo lido, com a parte registrada em
`metadados.partes_ilegiveis`.

### Infraestrutura

**Bot de versão com GitHub App próprio**

O carimbo de versão na `dev` e a release na `main` passam a ser feitos por um GitHub App do Lince, que tem exceção
nas regras de `dev`, `main` e tags. O token padrão do GitHub Actions era recusado pelas regras das branches. Quando
as regras recusam o bot, o erro agora diz isso claramente, em vez de tentar de novo.

**Versões e releases automáticas, com ambiente de desenvolvimento**

O Lince passa a ter uma branch `dev` que recebe todas as mudanças. A cada merge nela, o bot carimba uma versão de
desenvolvimento (por exemplo `0.1.1.dev20261102120000`) e publica a imagem `ghcr.io/yefclub/lince:dev`, para testar
antes da release.

A release é uma PR de `dev` para `main` com a label `release:patch`, `release:minor` ou `release:major`. No merge, o
bot calcula a versão, junta as notas de `changelog/unreleased/` no `CHANGELOG.md`, cria a tag, publica a imagem
(`X.Y.Z`, `X.Y` e `latest`) e a release no GitHub, e traz a `main` de volta para a `dev`.

### Documentação

**Regras de review e merge para agentes de código**

O `AGENTS.md` passa a dizer como agentes de IA entregam mudanças no Lince: review independente antes do push,
achado corrigido na mesma PR, merge em `dev` autorizado só com review aprovado e CI verde, e a release para
`main` sempre como decisão do mantenedor. Também cita os jobs `contrato` e `fluxo` e o Dependabot mirando a `dev`.

### Manutenção

**Bot de versão identificado pelo Client ID do GitHub App**

Os workflows de versão e de release passam a identificar o GitHub App do bot pelo **Client ID**
(`LINCE_BOT_CLIENT_ID`), no lugar do App ID, que a action oficial de token marcou como obsoleto.

**Arquivos de teste sem nome de pessoa residual**

Quatro apresentações de teste (`testPPT.pptx`, `.pptm`, `.ppsx` e `.ppsm`) ainda traziam parte do nome de uma
pessoa num slide, porque o nome estava dividido em trechos de texto diferentes e a troca anterior só pegou o
primeiro. Agora o slide diz "Autor de teste", como os demais arquivos. Nada muda na leitura de documentos.
