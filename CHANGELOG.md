# Changelog

## 0.5.0 — 2026-11-09

### Novidades

**Arquivos protegidos por senha são reconhecidos e lidos com a senha, sem que ela fique guardada**

**Erro próprio para arquivo com senha.** Antes, um PDF com senha de abertura falhava como "arquivo não pôde ser
aberto", igual a um arquivo corrompido, e quem enviava não tinha como saber que bastava a senha. Agora PDF com senha de
abertura, Word, Excel e PowerPoint protegidos (`.docx`, `.xlsx`, `.pptx`, `.doc`, `.xls`, `.ppt`) e documentos do
LibreOffice com senha (`.odt`, `.ods`, `.odp`) são reconhecidos antes de qualquer leitura: a API recusa o envio na hora
com `422 arquivo-protegido`, dizendo o formato e o nome do arquivo, sem ler nada nem chamar a IA. Na biblioteca, a
leitura levanta `lince.DocumentoProtegido`. O PDF que só tem senha de permissões (abre sem senha, mas não deixa
imprimir ou editar) continua sendo lido como sempre.

**Senha no envio.** O `POST /v1/sessoes` aceita o campo opcional `senha`, nos modos `leitura` e `tarefas`. Com a senha
certa, o arquivo é decifrado numa cópia temporária, só para a leitura, e apagado junto com a sessão; a montagem do
modo tarefas, a releitura ampliada e o código editável (`codigo=sim`) usam essa cópia, sem pedir a senha de novo. A
senha errada responde `422 senha-incorreta`, para o sistema pedir de novo à pessoa. Na biblioteca, é o parâmetro
`senha` de `Leitor.ler` e `Leitor.preparar`, e a senha errada levanta `lince.SenhaIncorreta`.

**A senha não fica em lugar nenhum.** Ela só abre o arquivo e é descartada: não vai para a sessão, o plano assinado,
os logs, os metadados, a resposta nem as mensagens de erro. As leituras do documento protegido também não entram no
cache de transcrições, para ninguém receber o conteúdo sem ter a senha. A cópia decifrada passa pelo antivírus, como o
arquivo enviado.

**Contra tentativa e erro.** Depois de 5 senhas erradas para o mesmo arquivo, ou 50 no mesmo token, em 15 minutos, as
tentativas seguintes são recusadas com `429 tentativas-de-senha` e `Retry-After`, até a senha errada mais antiga sair
da janela.

**O que fica só reconhecido.** Documentos do OpenOffice antigo (cifra Blowfish), do formato ODF 1.4 (AES-GCM) e Word
com ofuscação XOR são reconhecidos como protegidos, mas não decifrados; voltam como `arquivo-protegido`, com o motivo. O
arquivo que a senha certa abre, mas que está corrompido, também volta como `arquivo-protegido`, com o motivo,
sem contar como senha errada. O arquivo que, decifrado, passa dos limites de leitura (um "zip bomb" escondido dentro
do documento cifrado) é recusado com `422 documento-recusado`.

Contrato 1.13.0: o campo `senha` e os códigos `arquivo-protegido`, `senha-incorreta`, `tentativas-de-senha` e
`senha-invalida` (senha acima de 1024 caracteres) são novos e opcionais; nada muda para quem já usa a `/v1`.

### Correções

**Resultados esperando entrega vão para o disco, com teto de 1 GiB por token configurável**

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

## 0.4.3 — 2026-11-09

### Correções

**Servidor devolve ao sistema a memória que as leituras liberaram**

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

## 0.4.2 — 2026-11-09

### Correções

**Figuras de PDF girado (plantas, desenhos em paisagem) chegam inteiras e de pé para a IA**

**Página girada.** Num PDF com a página girada (`/Rotate` 90, 180 ou 270, comum em plantas, diagramas elétricos e
desenhos de CAD em paisagem), a figura virava tarefa, mas o recorte enviado à IA saía em branco: as posições vinham
de um jeito e a imagem da página, de outro. Agora a análise da página usa o mesmo referencial para tudo, e o recorte
de cada figura, a página inteira escaneada e a releitura ampliada do esforço máximo vão para a IA de pé, como a página
aparece na tela. A conferência de texto oculto dessas páginas passa a comparar a cor do texto com o fundo certo.

**Página com a área visível herdada.** Quando a página não declara o próprio tamanho e herda a área do documento, a
parte visível era medida pela folha errada: num desenho com a área visível na metade direita da folha, valia a
metade esquerda, e a página que ficava sem área visível entrava só com o texto. Agora a área visível vem da mesma
conta que a renderização faz.

Os demais PDFs sem rotação saem iguais.

## 0.4.1 — 2026-11-09

### Correções

**PDF com imagem fora da folha e planilha exportada com "\" nos nomes voltam a ser lidos**

**PDF de desenho técnico.** Um PDF com uma imagem fora da área visível da página (comum em diagramas elétricos e
desenhos exportados de CAD, que têm objetos fora da folha ou a origem da página deslocada) falhava inteiro na versão
0.4.0, sem texto nenhum. Agora a posição de cada imagem e desenho é medida a partir da área visível da página, a
região que cai toda fora dela é ignorada e a que passa da borda é recortada só na parte que aparece. Se ainda assim
uma página der erro, ela entra só com o texto, com o motivo em `cobertura.pendentes`, e as outras páginas seguem
normalmente.

**Planilha, documento ou apresentação com "\" nos nomes internos.** Alguns sistemas (cadastros exportados de ERP)
gravam o pacote do Office com os nomes internos escritos como `xl\workbook.xml`. O Excel e o LibreOffice abrem esses
arquivos, mas o Lince os recusava como formato não reconhecido. Agora os nomes são normalizados numa cópia temporária
antes da leitura, para `.xlsx`, `.xlsm`, `.docx`, `.pptx` e os outros pacotes, e o resultado é igual ao do arquivo
gravado do jeito comum. A conferência de segurança continua: caminho fora do pacote e nome que fica repetido depois
da troca são recusados.

Os documentos que já eram lidos saem iguais.

## 0.4.0 — 2026-11-09

### Correções

**Imagem repetida lida uma vez no texto, montagem que nunca volta vazia e plano que diz quando refazer a leitura**

**Imagem repetida.** O mesmo logo em centenas de páginas ou slides agora entra no texto uma vez só, inteiro, na
primeira posição. Nas outras fica a âncora `<!-- lido pela IA: id -->` com a referência curta
`_(mesma imagem lida acima: id)_`, coerente com `estrutura.imagens`. Antes, a leitura era copiada em cada lugar: um
DOCX com o mesmo logo 2.000 vezes gerava 240 milhões de caracteres e levava minutos para montar.

**Montagem sem 413 vazio.** As leituras entram no resultado até ele chegar a 16 milhões de caracteres, teto
documentado no contrato. Acima dele, `POST /v1/montagens` responde 200 com o texto nativo e as leituras que couberem;
as outras aparecem em `cobertura.pendentes` como `resultado grande demais`, como já acontecia na leitura com a IA do
Lince. O teto vale só para as leituras: o texto nativo do documento sai sempre inteiro, mesmo que sozinho passe dele.
As montagens ganharam fila justa própria, com até duas ao mesmo tempo: não esperam atrás de leituras presas na IA nem
tiram a vaga delas.

**Plano depois de reiniciar.** O plano do modo `tarefas` leva o identificador da chave que o selou. Depois de um
reinício sem `LINCE_SEGREDO`, a montagem responde `422 plano-de-outra-chave`, com a orientação de refazer a fase 1;
`422 plano-invalido` fica só para o plano alterado ou de outro token. O contrato foi para a versão 1.10.0.

**Teto de tarefas na API.** No modo `tarefas`, cada documento devolve até 200 tarefas e cada sessão, 300. As que
passam ficam nas pendências do preparo, com o motivo, e o novo campo opcional `max_tarefas` baixa o teto do
documento. Um documento que falha antes de entregar as tarefas não gasta a cota da sessão.

**Cabeçalho fixo no PDF.** Uma linha curta idêntica na borda de ao menos 80% das páginas, como "Contrato 123/2026 -
Confidencial", sai do corpo e vai para `metadados["cabeçalho/rodapé"]`. O número de página tirado do corpo também passa
a ser registrado ali. Um número só conta como o da página quando a linha diz isso ("Página 3 de 12", "Página 3/12",
"fls. 3") ou é só ele ("3 de 12", "- 3 -", "(3)"; "3)" sem a abertura é item de lista): no carnê com uma parcela por
página, "Parcela 3 de 12" fica no texto, e "Cliente: Transportadora 3 Ltda" na terceira fatura também. Faturas do mesmo
modelo, linhas numeradas e páginas inteiras iguais continuam no texto; das faturas, só a linha de marca idêntica em
todas pode ir para os metadados, como cabeçalho fixo.

**Planilha `.xlsb` hostil.** No `.xlsb` que só o leitor tolerante abre, a tabela de strings compartilhadas tem teto
de 1 milhão de strings e 64 MB de texto. Acima disso, o arquivo é recusado antes de ocupar memória.

**Alpine.** O README traz a receita que funciona (Rust estável pelo `rustup` com `build-base`, e o `git` para
instalar do GitHub; cerca de 16 min de compilação) e recomenda usar a imagem Debian do Lince como serviço.

**PDF grande não segura os outros, respeita o prazo desde o início e o texto oculto não apaga o visível**

**Um PDF grande não faz os outros esperarem.** O PDF agora é lido em faixas de páginas, e a trava da PDFium é
tomada por pouco tempo e passada, por ordem de chegada, a quem está esperando. Um PDF de uma página que chega
durante a leitura de um de 2.500 sai em menos de meio segundo; antes, esperava dezenas de segundos. Com a trava só
rodam as chamadas à PDFium, que custam o que a própria biblioteca limita: abrir o documento e a página, coletar os
objetos, extrair o texto, copiar páginas e renderizar. Todo o resto roda fora dela: o pdf_inspector (que não usa a
PDFium, inclusive na cópia sem o texto oculto), a análise da página (agrupamento de figuras e desenhos, texto oculto,
tabelas, fórmulas, hífens), os recortes e os arquivos das figuras, também na releitura ampliada do esforço máximo. O
documento é aberto uma vez só, e não a cada vez que pega a trava (nem a cada figura relida no esforço máximo): um PDF
com a tabela de referências danificada não a reconstrói de novo a cada vez, e um PDF de uma página pega a trava duas
vezes. Uma leitura interrompida (por exemplo, com Ctrl+C) em qualquer ponto do pedido da trava, na fila, na espera ou
ao receber a vez, não deixa a trava presa.

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

**Página hostil não prende os outros PDFs.** Uma página montada para pesar na análise (dezenas de milhares de desenhos
espaçados, milhares de trechos invisíveis sobre milhares de imagens, um bloco de fórmula de centenas de milhares de
caracteres) é analisada fora da trava, com teto de tempo (abaixo): um PDF de uma página que chega no meio sai em menos
de um segundo, em vez de esperar a análise inteira. A página com objetos demais para a PDFium coletar (mais de 100 mil,
contando os de dentro dos blocos reutilizáveis a cada vez que são desenhados) entra só com o texto, com a pendência
"página N: só o texto, sem figuras nem páginas escaneadas lidas (conteúdo demais para a análise visual)" e o alerta de
texto oculto não conferido; o resto do documento é lido normalmente.

**Hífen de fim de linha em tempo linear.** Juntar as palavras partidas no fim da linha procurava cada forma com hífen
no texto inteiro: uma página com 20 mil quebras levava 4 s parada, e uma palavra de 20 mil letras sem hífen, 7 s.
Agora as duas levam centésimos. A decisão continua pelo vocabulário do documento: o hífen fica quando a 2ª metade
aparece sozinha no texto ou quando o par aparece em outro lugar como duas partes de uma palavra hifenizada
("propellant- based" com "propellant-based"). O par precisa aparecer inteiro: "co- op" vira "coop" mesmo com
"co-operation" no texto, onde antes bastava "co-op" estar dentro de outra palavra.

**Página que levaria tempo demais para analisar.** A análise da página, que agora roda fora da vez da PDFium, tem 3 s de
processamento por página (o tempo da própria leitura, sem contar a espera pelos outros documentos, então a mesma página
sai igual com o servidor cheio ou vazio): uma página montada para travar a leitura (dezenas de milhares de desenhos
espaçados, milhares de textos invisíveis sobre milhares de imagens) entra só com o texto, com a pendência e o alerta de
texto oculto não conferido, e a leitura segue na próxima página.

### Segurança

**Texto oculto do Word e do PowerPoint sai sem levar junto o texto visível igual a ele**

O texto que a IA leria e quem abre o documento não vê (branco no branco, `vanish`, fonte minúscula, letra sem
preenchimento) agora sai do próprio arquivo, antes da leitura, e não mais por busca no texto já extraído.

#### O texto visível fica intacto

Antes, um trecho oculto que repetia um texto visível apagava as duas cópias: um "não se responsabiliza" escondido
tirava a frase "O fornecedor não se responsabiliza por atrasos na entrega." do contrato, e um "Cliente" em branco
virava "Clientes atendidos: 40" em "s atendidos: 40". Agora só o trecho escondido sai, em qualquer quantidade de
cópias do visível, no Word (`.docx`, `.docm`) e no PowerPoint. O que saiu continua listado em
`texto_oculto_removido`, com o alerta.

#### Menos rotas para esconder texto

- **Tudo o que o leitor lê é conferido**: as partes do Word e os slides são achados pelas relações do pacote com as
  mesmas regras do leitor do corpo (a relação repetida vale pela última, o tipo vale pelo endereço inteiro, o
  atributo vale com ou sem prefixo e o caminho com `%2E%2E` ou `%2F` não resolve), e o texto sai de todos os ramos de
  conteúdo alternativo (`mc:AlternateContent`). O que estava oculto só no ramo que o leitor usa também entra em
  `texto_oculto_removido`.
- **Formato pelo conteúdo**: a conferência segue o formato que o leitor detecta no arquivo, não a extensão. Um Word
  gravado como `.doc` e uma apresentação gravada como `.docx` são conferidos, e o Word gravado como `.pptx` ou `.xlsx`
  também tem o cabeçalho, o rodapé e os comentários conferidos nos metadados.
- **OOXML estrito**: o Word e o PowerPoint gravados no formato estrito (`http://purl.oclc.org/ooxml/...`) são
  conferidos como os de transição.
- **Herança completa no Word**: o estilo da tabela (inclusive a formatação da primeira linha, da última e das faixas)
  entra na cadeia de cor, tamanho e `vanish`; o `vanish` segue a regra do Word, em que ligado no estilo de parágrafo
  e de novo no de caractere se anula; e o texto em árabe, hebraico ou marcado como escrita complexa vale pelo tamanho
  de escrita complexa (`szCs`).
- **Imagem atrás do texto**: uma imagem pequena "atrás do texto" deixava de conferir a cor do documento inteiro.
  Agora ela só deixa incerto o texto que pode cobrir, perto do parágrafo em que está presa. O papel timbrado de
  página inteira continua valendo para a página toda, e a faixa do cabeçalho ou do rodapé que entra na área do texto
  deixa incerto só o texto embaixo dela (o título branco sobre a faixa azul do cabeçalho fica). A forma antiga (VML)
  atrás do texto conta do mesmo jeito. A âncora que não desenha nada (sem figura, de tamanho zero ou com uma imagem
  toda transparente) não deixa mais nada incerto: um PNG transparente de 1 EMU no cabeçalho bastava para desligar a
  conferência do documento inteiro. A imagem de uma cor só igual à página conta como a própria página.
- **Caixa de texto sobre foto**: a caixa de texto flutuante sem preenchimento mostra o que está embaixo dela. Sobre a
  foto ou o retângulo escuro da capa, o título branco fica; longe de qualquer outro desenho (pela posição na página,
  quando o arquivo diz), o branco no branco sai.
- **Letra sem preenchimento**: o texto sem preenchimento (`noFill`) ou quase transparente (até 8% de opacidade), no
  PowerPoint e no Word (`w14:textFill`), conta como oculto, sobre qualquer fundo. Com contorno visível, ou com meia
  transparência, continua no texto.
- **Tabela com estilo**: a tabela com o estilo mais comum (Grade da Tabela) e os estilos só de borda passam a ter o
  fundo da página; só o estilo que pinta a célula deixa o fundo incerto.
- **Mais partes conferidas**: caixa de texto (pela cor declarada da forma), notas de rodapé e de fim, comentários,
  cabeçalho e rodapé. O texto oculto do cabeçalho, do rodapé e dos comentários também não aparece mais nos
  metadados.
- **Contraste quase nulo**: cinza quase branco sobre branco (como E8E8E8) conta como oculto; cinza claro legível e
  letra branca numa faixa escura, não.
- **Letra espremida**: escala horizontal de até 10% com fonte de até 4 pt conta como oculto.
- **PowerPoint**: a conferência percorre o slide inteiro, como o leitor: conteúdo alternativo, grupos, campos (como o
  número do slide), células de tabela (com o preenchimento da célula como fundo) e as anotações do orador. O tamanho e
  a cor vêm da herança toda (estilo da caixa, placeholder do layout e do mestre, estilos de texto do mestre) e do
  ajuste automático do texto; o texto da forma oculta pelo painel de seleção sai. Letra branca sobre uma imagem toda
  branca (como fundo da caixa ou como figura embaixo) conta como oculta; sobre foto, continua no texto. Apresentações
  de exibição (`.ppsx`, `.ppsm`), que antes ficavam sem conferir, agora são conferidas. A cor de imagem é conferida até
  50 milhões de pixels por documento; depois disso, a imagem conta como foto, para a leitura não travar. A forma que só tem uma fórmula,
  o placeholder de número, data ou rodapé (antes pulado, mesmo quando o leitor o lia) e a lista de slides guardada
  dentro de outro elemento também são conferidos.

#### Rede de segurança: o que o leitor leu e a conferência não viu aparece no resultado

Depois da limpeza, o texto que o leitor extrai do arquivo é comparado com o texto que a conferência examinou. Se uma
palavra lida (de 3 letras ou mais, sem contar números e pontuação) não estiver em nada conferido, o alerta "texto
oculto não conferido: texto lido fora da conferência" sai com a contagem e uma amostra das palavras, e a mesma linha
entra em `cobertura.pendentes`. Assim, uma rota para esconder texto que a conferência ainda não conhece não passa
calada. A numeração dos títulos, os gráficos, os SmartArt e o texto alternativo das imagens contam como conferidos:
não têm como ser escondidos por cor ou tamanho, e continuam fora da remoção. Só conta o que o leitor de fato escreve:
o texto da instrução de um campo, o texto movido de lugar (`w:moveFrom`), a célula coberta por mesclagem, o rótulo de
um nível de numeração que nenhum parágrafo usa, a parte do gráfico que o leitor não escreve e o segundo texto de um
mesmo trecho do slide não servem mais para mascarar uma palavra oculta. Documentos normais não geram o alerta,
inclusive com o símbolo ≝ numa fórmula.

#### Parte que não dá para conferir aparece no resultado

Quando uma parte do arquivo não pode ser conferida (por exemplo, XML com entidade sem definição), ela fica como está,
o alerta diz qual parte falhou e por quê, e a mesma linha entra em `cobertura.pendentes`
("texto oculto não conferido: word/document.xml (motivo)"), para quem chama poder decidir. O mesmo vale para o slide
forjado com formas demais (mais de 500), para a apresentação cuja conferência passaria do teto de custo e para o
Word com caixas de texto demais sobre desenhos: esses slides e partes ficam como estão, com o motivo, em vez de travar
a leitura.

O que sai do arquivo é só o texto oculto: declaração, `DOCTYPE`, namespaces e o resto da parte ficam como estavam.

## 0.3.1 — 2026-11-08

### Segurança

**Resposta enorme do provedor de IA não derruba mais a leitura**

Na leitura com a IA configurada pelo Lince (cabeçalhos `X-IA-*` na API ou `OpenAICompativel` na biblioteca), o corpo
da resposta do provedor agora é lido até 8 MB. Um provedor com defeito, ou hostil, que devolve uma resposta maior
não faz mais a memória do processo crescer sem limite: aquela leitura vira pendência (`RespostaGrande`), sem novas
tentativas, e o texto nativo do documento sai completo.

O resultado da leitura também passa a ter o mesmo teto de tamanho do resultado que `POST /v1/montagens` já
tinha. Quando a mesma leitura entraria em tantos lugares que o texto final passaria do teto (uma imagem repetida
centenas de vezes com uma resposta enorme), as leituras da IA ficam de fora com a pendência "resultado grande
demais" e o documento sai com o texto nativo, em vez de levar o processo junto.

## 0.3.0 — 2026-11-08

### Correções

**Nada de perder texto, texto oculto conferido de verdade e IA chamada por quem usa o Lince**

Correções e melhorias para usar o Lince como leitor de documentos de uma aplicação em produção.

#### Texto que sumia volta

- **PDF de páginas do mesmo modelo** (lote de faturas, notas, boletos, holerites) e **PDF longo** perdiam o corpo
  das páginas, tomado por cabeçalho repetido. Agora só sai do corpo a linha curta da borda da página, igual em quase
  todas as páginas, em que só o número da página muda ("Página 3 de 12"). Valor, data e número de documento que mudam
  entre páginas ficam, o título fica, o texto lido pela IA nunca sai e, se o que sairia pesaria mais de um quarto do
  texto, nada sai.
- **Word e PowerPoint**: a linha repetida no corpo (checklist, ata, formulário) não é mais apagada. O cabeçalho e o
  rodapé do Word vêm das partes próprias deles, como antes.

#### Texto oculto

- O `DOCTYPE` no XML do Word não desliga mais a conferência; parte que não pode ser lida vira alerta, nunca "nada
  oculto".
- Passa a detectar o texto escondido pelo estilo (inclusive herdado), pela cor do tema, pelo destaque e pelo
  sombreamento da mesma cor do texto, e no PowerPoint a caixa fora do slide, a fonte minúscula e a cor do fundo.
- O alerta de injeção passa a pegar "ignore todas as instruções anteriores".

#### A IA pode ser de quem chama

`Leitor.preparar` devolve o texto extraído e cada imagem que a IA precisa ler (com a instrução, onde ela está, a
chave de cache e os tokens estimados); `Leitor.concluir` monta o mesmo resultado com as respostas. Na API, `modo=tarefas`
no envio e `POST /v1/montagens`, com o plano assinado por um segredo que só o servidor conhece; com várias
réplicas, todas recebem o mesmo `LINCE_SEGREDO`. Quando o Lince chama a IA, `uso` traz o custo em dólar que o
provedor informa.

#### Posição e repetição

- Cada slide começa com `<!-- slide N -->`, também em `estrutura.inicio_dos_slides`; a nota do gráfico e o
  diagrama entram no slide deles.
- `estrutura.imagens` diz onde está cada imagem (página, slide, aba e célula) e qual leitura a leu.
- A mesma imagem repetida (o logo em todo slide) é lida uma vez, e a leitura vai para todos os lugares.

#### Instalação, concorrência e limites

- O desenho de EMF e WMF vira o extra `lince[emf]`, o que permite instalar em Alpine; sem ele, a imagem entra em
  `cobertura.pendentes` com o motivo. O README explica o que o Alpine precisa.
- A trava da PDFium vale só para o PDF: Office, HTML e texto de outros documentos seguem em paralelo. No PDF, a
  análise visual tem teto de 2.000 páginas e respeita o prazo; acima disso a página entra com o texto completo,
  registrada nas pendências.
- Sem fonte nenhuma no sistema, o texto dos desenhos SVG sai com a fonte embutida do Pillow.
- O `.xlsb` gravado fora da especificação, que o leitor do corpo recusa, é lido registro a registro.
- A bancada `bench/medir.py` refaz a tabela de estado do README com documentos gerados e IA simulada, e roda no CI.

O contrato da API passa para a versão 1.9.0, só com campos e rotas novos.

### Documentação

**Notas da 0.2.0 corrigidas sobre as imagens EMF e WMF**

As notas da versão 0.2.0 no `CHANGELOG.md` diziam, em três entradas escritas antes da leitura de EMF e WMF (imagens
dos arquivos antigos do Office, imagem que não abre e planilhas do Excel com imagens), que esses desenhos ficavam
marcados como não lidos. Na própria 0.2.0 eles passaram a ser desenhados e lidos pela IA. O texto agora diz isso.
Continuam marcados como não lidos, por exemplo, o PICT do Mac, a imagem corrompida e o EMF ou WMF sem o desenhista
instalado (Python 3.15). Nada muda na leitura.

### Manutenção

**Testes da imagem repetida e das faixas sem depender da ordem das leituras**

Os testes da imagem repetida e das faixas de uma imagem alta dependiam da ordem em que as leituras pela IA terminam,
que muda porque elas correm em paralelo, e falhavam às vezes no CI. No teste da imagem repetida, a IA simulada agora
responde pelo conteúdo da imagem; no das faixas, responde um texto fixo, e o teste conta as dez faixas lidas. Os dois
valem em qualquer ordem. Nada muda na leitura.

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

Um documento do Word, do PowerPoint ou do LibreOffice com uma imagem que não abre (corrompida, ou EMF e WMF quando o
desenhista não está instalado, como no Python 3.15) falhava inteiro, sem texto nenhum. Agora o documento é lido
normalmente: a imagem fica marcada no lugar em que aparecia, com o formato (por exemplo
`[imagem 1: imagem EMF não lida]`), e entra em `partes_ilegiveis`, para a plataforma saber o que ficou de fora.

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
- A imagem EMF ou WMF colada numa planilha, que antes sumia sem aviso, agora entra na leitura (desenhada e lida pela
  IA, ou marcada como não lida quando o desenhista não está instalado).
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
