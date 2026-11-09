# Lince

Leitor único de documentos para bases de conhecimento e IA.

O Lince usa o texto nativo do arquivo sempre que ele é bom e chama o modelo de visão da sua plataforma
**só onde precisa**: imagem, gráfico, diagrama, tabela duvidosa, fórmula e página escaneada. A saída é um
Markdown pronto para busca e para LLM, mais metadados e um relatório do que foi lido de onde.

```python
import lince

r = lince.ler("relatorio.pdf", ia=lince.OpenAICompativel("https://openrouter.ai/api/v1", "seu/modelo", chave))
r.markdown          # corpo do documento
r.metadados         # versão, rodapé, marca d'água, propriedades, comentários, alertas de injeção
r.cobertura         # rotas por página, leituras pela IA, leituras do cache, pendências
r.uso               # custo de cada leitura (para o seu ledger)
r.estrutura         # páginas, slides, abas (e quais são ocultas), seções com a posição no Markdown (pontos de código)
                    # e, nas planilhas e no CSV, o perfil de cada tabela: colunas com o tipo, amostra e linhas no texto
```

## O que ele faz

| Situação | Como o Lince lê |
| --- | --- |
| Texto, listas e tabelas nativas | Direto do arquivo, sem IA |
| Equação do Word | LaTeX exato, sem IA |
| Gráfico nativo de PPTX/XLSX | Tipo, título e séries lidos do arquivo |
| Fluxograma de formas no PPTX | Mermaid com as setas e os rótulos (Sim/Não), sem IA |
| Imagem, gráfico vetorial, diagrama | Recorte lido pela IA, com o texto nativo do trecho como âncora |
| Títulos e legendas que são imagem | Agrupados numa folha numerada: uma chamada lê vários |
| Tabela nativa com estrutura duvidosa | Recorte da tabela lido pela IA, com os valores nativos como âncora |
| Fórmula escrita como texto no PDF | Recorte do bloco de fórmulas → LaTeX |
| Página escaneada ou com texto corrompido | Página lida pela IA (dividida em duas quando densa) |
| Imagem avulsa alta | Fatiada para não ser reduzida até ficar ilegível |

**Esforço máximo** (`Leitor(esforco="max")`): o que a IA marcou como `[ilegível]`, `≈` ou deixou vazio é
renderizado de novo em 2–3x a resolução e relido com a primeira leitura como contexto.

**Metadados corporativos**: cabeçalho e rodapé corridos saem do corpo e vão para os metadados (versão,
revisão, código, classificação). No PDF, sai a linha curta da borda da página em que só o número da página muda,
rotulado como página ("Página 3 de 12", "fls. 3"), a linha que é só o número ("3", "3 de 12", "- 3 -", "(3)",
registrada com o número trocado por `#`) e a linha idêntica na borda de ao menos 80% das páginas ("Contrato
123/2026 - Confidencial"). O número sem rótulo no meio de outras palavras fica no texto: no carnê com uma parcela por
página, "Parcela 3 de 12" é conteúdo. Também vão para os metadados a marca d'água, as propriedades do arquivo, os
comentários e as alterações rastreadas do Word e as abas ocultas do Excel.

**Segurança**: texto que a IA leria e uma pessoa não vê (branco no branco, fonte minúscula, modo invisível,
oculto do Word) sai do corpo e gera alerta; padrões de prompt injection em português e inglês geram alerta.
O conteúdo das imagens é tratado como dado, nunca como instrução.

## Peças plugáveis

```python
leitor = lince.Leitor(
    ia=lince.OpenAICompativel(url, modelo, chave),   # ou FuncaoLeitor(sua_funcao), ou o seu objeto com .ler()
    cache=lince.CacheRedis(redis_da_plataforma),     # ou CacheDisco(pasta), ou o seu: obter(k) / guardar(k, texto)
    ao_usar=registrar_custo,                         # chamado a cada leitura com tokens e modelo
    max_leituras=200,                                # teto por documento; o resto vira pendência registrada
)
```

- Sem `ia`, o Lince devolve o texto nativo completo e marca o que ficou pendente.
- `leitor.ler(arquivo, prazo=20)`: 20 s, a partir da chamada, para o documento (o chat lê com prazo; a base,
  sem). A extração nativa conta no prazo: o PDF é lido em faixas de 50 páginas, e as faixas que não começam a tempo
  não são lidas (`páginas N–M: não lidas (prazo esgotado)` em `cobertura.pendentes`); a primeira sempre entra, para o
  documento nunca sair vazio. Na faixa lida, a página que começa depois do prazo entra só com o texto (sem figuras,
  escaneado nem conferência de texto oculto, que vira alerta), registrada em `cobertura.pendentes`. Nenhuma leitura pela IA começa depois dele, e a
  que está em curso no `OpenAICompativel` é cortada, sem novas tentativas: o que não coube vira a pendência
  `prazo esgotado`, e o texto nativo sai completo. Nada fica rodando depois da entrega. Toda leitura concluída chega em
  `ao_usar` e em `Resultado.uso`; a chamada cortada pode ser cobrada pelo provedor sem aparecer ali (ela já tinha sido
  enviada). O corte vale também quando o provedor manda a resposta aos poucos ou para no meio dela; só a conexão, o
  envio de uma imagem grande ou um DNS lento podem passar alguns segundos do prazo. Provedor próprio lê o tempo que sobra em `lince.prazo_restante()` e pode levantar `lince.PrazoEsgotado`.
- 429 e 5xx do provedor são repetidos com espera exponencial e `Retry-After`.
- Falha da IA nunca derruba o documento: a leitura nativa fica e a pendência é registrada.
- `Uso.custo_usd` traz o custo da chamada quando o provedor informa (`usage.cost`, como no OpenRouter).

### Em duas fases: a IA é de quem chama

Para quem já tem controle de custo, modelos e cache próprios, o Lince não precisa chamar IA nenhuma:

```python
leitor = lince.Leitor()
plano = leitor.preparar("relatorio.pdf")      # texto extraído + o que a IA precisa ler
respostas = {t.id: meu_modelo(t.imagem, t.instrucao) for t in plano.tarefas}
r = leitor.concluir(plano, respostas)         # o mesmo Resultado que sairia com a IA do Lince
```

Cada `TarefaVisual` traz a imagem (PNG), a instrução, o tipo (imagem, página escaneada, desenho, tabela, fórmula ou
lote de recortes), onde ela está (página e caixa no PDF, `imagem 3` no Office), `chave` (hash da imagem, da instrução
e da versão do prompt, para o seu cache) e `tokens_estimados`. A tarefa sem resposta fica em `cobertura.pendentes`
com o texto nativo no lugar. `plano.para_json()` e `lince.Plano.de_json()` levam o plano entre processos.

## API HTTP

Serviço para qualquer plataforma chamar, com contrato **OpenAPI 3.1** versionado, servido em
`GET /v1/openapi.json` (gere o cliente a partir dele). Uma PR que quebre o contrato não passa no CI.

Cada envio, de um documento ou de um lote, cria uma **sessão**. Quem chama marca a sessão com uma `referencia`
própria, por exemplo `usuario:42`. Assim, um sistema com N usuários sabe de quem é cada resultado.

**O documento não fica guardado.** O arquivo é apagado assim que lido (a exceção é o cache opcional de transcrições,
abaixo). O resultado fica em memória só
até ser entregue: a primeira resposta com a sessão `concluida` (o próprio envio com `Prefer: wait`, ou um `GET`) é a
entrega. Depois dela, a sessão fica 60 s para repetir o pedido caso a resposta tenha se perdido, e some; um
`DELETE` confirma o recebimento e apaga na hora. Resultado que ninguém busca em 15 min é descartado.

```bash
docker run -d -p 8000:8000 -e LINCE_TOKENS="$LINCE_TOKEN" ghcr.io/yefclub/lince
```

```bash
curl -H "Authorization: Bearer $LINCE_TOKEN" \
     -H "X-IA-URL: https://openrouter.ai/api/v1" -H "X-IA-Modelo: seu/modelo" -H "X-IA-Chave: $CHAVE" \
     -H "Idempotency-Key: envio-8f2c" -H "Prefer: wait=120" \
     -F arquivo=@contrato.pdf -F arquivo=@anexo.xlsx -F referencia=usuario:42 \
     http://localhost:8000/v1/sessoes
```

| Rota | O que faz |
| --- | --- |
| `POST /v1/sessoes` | Envia 1 a 50 arquivos (campo `arquivo` repetido), com `referencia`, `esforco` (`padrao` ou `max`), `prazo` (1 a 3600 s por documento), `modo` (`leitura` ou `tarefas`) e `max_tarefas` (só no modo `tarefas`) opcionais. Responde `201` com a sessão e `Location`. Com `Prefer: wait=N` (até 300 s), espera terminar. |
| `GET /v1/sessoes/{id}` | Estado da sessão e de cada documento; o que já terminou vem com `markdown`, `metadados`, `cobertura`, `alertas_injecao`, `uso` (tokens por leitura) e `estrutura`. Aceita `Prefer: wait=N`. |
| `GET /v1/sessoes?referencia=…` | Sessões do token ainda não entregues, da mais nova para a mais antiga (resumo, sem o conteúdo lido). |
| `DELETE /v1/sessoes/{id}` | Confirma o recebimento (ou cancela o que ainda não foi lido) e apaga a sessão na hora. |
| `POST /v1/montagens` | Segunda fase do modo `tarefas`: recebe `{"plano", "respostas": {id: texto}}` e devolve o resultado montado. |
| `GET /v1/openapi.json`, `GET /saude` | Contrato; serviço no ar, versões e a memória residente do processo (`memoria.rss_bytes`, no Linux). |

- **Idempotência**: repetir o envio com a mesma `Idempotency-Key` antes da entrega devolve a mesma sessão (`200`),
  sem ler nem cobrar de novo. Reusar a chave com outro conteúdo dá `422`.
- **Erros**: todo erro sai em `application/problem+json` (RFC 9457), com `type` estável
  (`urn:lince:erro:<codigo>`) e `instance` igual ao `X-Request-Id`. O `X-Request-Id` enviado volta em toda
  resposta, o que facilita o rastreio entre os dois sistemas.
- **Sem `X-IA-URL`**: só a extração nativa; o que precisava de IA aparece em `cobertura.pendentes`.
- **Modo `tarefas`** (`-F modo=tarefas`): o Lince não chama IA. Cada documento volta com `tarefas` (imagem em base64,
  instrução, onde está, `chave` de cache e `tokens_estimados`) e um `plano` opaco, assinado pelo servidor para o token
  e válido por 24 h. Quem chama lê as imagens com o próprio modelo e manda `POST /v1/montagens`; nada fica guardado
  no servidor. Com várias réplicas, todas precisam do mesmo `LINCE_SEGREDO` (32+ caracteres aleatórios); sem ele, o
  plano só monta na instância que o entregou. O plano leva o identificador da chave que o selou: depois de um
  reinício sem `LINCE_SEGREDO` (ou numa réplica com outro segredo), a montagem responde `422 plano-de-outra-chave`, e
  a saída é refazer a fase 1; `422 plano-invalido` é só o plano alterado ou de outro token. Cada documento devolve
  até 200 tarefas e cada sessão, 300 somando o lote (cada tarefa leva a imagem em base64 no resultado, que fica em
  memória até a entrega); as que passam ficam nas pendências do preparo com o motivo, e `-F max_tarefas=N` baixa o
  teto do documento (nunca sobe).
- **Prazo**: com `prazo=20`, cada documento tem 20 s a partir do início da leitura dele (a espera na fila não
  conta; a extração nativa conta: no PDF, a faixa de páginas que não começa a tempo não é lida, e a página que começa
  depois do prazo entra só com o texto); o que a IA não leu a tempo aparece em
  `cobertura.pendentes` como `prazo esgotado`.
- **Fila justa**: os documentos são atendidos em rodízio entre tokens, então quem manda 500 não trava quem
  mandou 1. Até 16 documentos em leitura ao mesmo tempo por instância. As montagens (`POST /v1/montagens`) têm fila
  justa própria, com até 2 ao mesmo tempo: não esperam atrás de leituras presas na IA nem tomam a vaga delas.
- **Isolamento**: um token nunca vê sessão nem referência de outro. `LINCE_TOKENS` aceita vários tokens
  separados por vírgula, um por plataforma. Para uso local sem token: `lince-servidor --sem-autenticacao`.
- **Memória**: no Linux com glibc, o servidor (`lince-servidor`) fixa o limite de `mmap` do alocador em 16 MB, o que
  desliga o ajuste dinâmico que deixava as arenas das threads fragmentadas, e, a cada 60 s, devolve ao sistema a
  memória livre que o alocador guardou (`malloc_trim`). O processo volta perto do tamanho de repouso depois de uma carga, em vez de
  ficar no teto do contêiner. Em outra libc (Alpine/musl) nada muda.
- **Limites**: 100 MB por envio (com `Content-Length`), 50 arquivos por sessão, 64 documentos pendentes e 256 MB
  de resultado esperando entrega por token. Acima disso, `429` com `Retry-After`. A montagem aceita até 20 mil
  respostas e 64 M caracteres delas (acima, `413 envio-grande`, com o teto que passou no detalhe) e põe leituras no resultado até ele chegar a 16 M caracteres,
  contando o texto nativo: a leitura da mesma imagem repetida entra uma vez, e as outras posições levam só a
  referência. As que passam desse teto ficam em `cobertura.pendentes` (`resultado grande demais`), com o texto nativo
  no lugar, como na leitura com a IA do Lince. O teto vale para as leituras: o texto nativo sai sempre inteiro, mesmo
  que sozinho passe dele. No `.xlsb` que só o leitor tolerante abre, a tabela de strings compartilhadas tem teto de 1
  milhão de strings e 64 MB de texto; acima disso, o arquivo é recusado.
- **Escala**: as sessões ficam na memória da instância. Para várias réplicas, use `Prefer: wait` (resposta na
  mesma conexão) ou afinidade por id da sessão no balanceador.

## Documento como código (edição por IA)

Para uma IA editar um documento sem estragar layout, mestres, animações e gráficos, ela precisa da estrutura com
endereços estáveis. Com `codigo=sim` no envio, cada PPTX e DOCX traz `resultado.codigo`, com um id tirado do
próprio arquivo para cada peça:

- **PPTX:** slides, formas, parágrafos, células de tabela e séries de gráfico (`sld:258/sp:2/p:0`).
- **DOCX:** parágrafos e tabelas (cada célula traz os ids dos parágrafos dela), estilos e comentários. O id do
  parágrafo é o `w14:paraId` que o Word grava (`p:1A2B3C4D`); em arquivo sem ele, a posição no documento (`p:n12`),
  e aí vale mandar `se_texto`.
- **XLSX:** abas, células com valor, fórmula e data (`Fretes!B3`), mesclagens, tabelas e nomes definidos.

`POST /v1/edicoes` recebe o arquivo e as operações, aplica tudo ou nada e devolve o relatório: o que mudou, o diff
por parte, avisos (texto que pode transbordar) e o código novo. O arquivo editado sai em `GET /v1/edicoes/{id}/arquivo`.

```bash
curl -H "Authorization: Bearer $LINCE_TOKEN" -F arquivo=@deck.pptx \
     -F 'operacoes={"base_sha256":"<codigo.sha256>","operacoes":[
           {"op":"texto","alvo":"sld:258/sp:2/p:0","se_texto":"Título antigo","texto":"Título novo"},
           {"op":"valor_grafico","alvo":"sld:260/sp:5/ser:0","ponto":2,"valor":12.5},
           {"op":"ordem","slides":["sld:256","sld:259","sld:258","sld:260"]}]}' \
     http://localhost:8000/v1/edicoes
```

Operações do PPTX:

| Operação | O que faz |
| --- | --- |
| `texto` | Troca o texto de uma forma (uma linha por parágrafo) ou de um parágrafo, mantendo a formatação |
| `substituir` | Busca e troca num slide ou numa forma, mesmo com o texto partido em vários trechos de formatação |
| `notas` | Troca as notas do apresentador |
| `celula_tabela` | Troca o texto de uma célula |
| `valor_grafico` | Muda um ponto da série no gráfico e na planilha embutida dele |
| `ordem`, `remover_slide` | Reordena ou tira slides |

Operações do XLSX, escritas direto no XML da aba (o openpyxl perderia imagens e gráficos ao salvar):

| Operação | O que faz |
| --- | --- |
| `celula` | Grava um valor (número, texto, booleano ou vazio) ou uma fórmula (`=SUM(B2:B5)`, em inglês) numa célula, mantendo o estilo |
| `intervalo` | Grava uma matriz de valores a partir de uma célula; texto que começa com `=` é fórmula |
| `renomear_aba` | Renomeia a aba e atualiza as referências a ela em fórmulas, nomes, validações e gráficos |

Valor ou fórmula alterada faz o Excel recalcular tudo ao abrir, e por isso os resultados de fórmula no código novo
ainda são os antigos. A fórmula é conferida antes de entrar: sintaxe, funções conhecidas (em inglês) e o prefixo que
o Excel exige nas funções novas (`XLOOKUP` vira `_xlfn.XLOOKUP` no arquivo); matriz dinâmica (`FILTER`, `UNIQUE`…)
é recusada. Também são recusadas com o motivo: célula dentro de mesclagem, de fórmula matricial, na origem de uma
fórmula compartilhada, no cabeçalho ou na linha de totais de uma tabela, em tabela dinâmica, e aba protegida.
A fórmula também precisa citar abas e nomes que existem; referência estruturada de tabela (`Tabela1[Coluna]`) ainda
não é escrita: use o intervalo de células. Renomear recusa nome já usado por qualquer aba (inclusive de gráfico)
e pasta com a estrutura protegida, e falha fechado: se a aba antiga continuar citada em algum lugar que a edição não
atualiza (controle de formulário, VML), nada é aplicado e o erro diz onde. O pedido inteiro tem prazo de 60 s.

Operações do DOCX, que por padrão entram como **alterações controladas**: quem abre no Word vê o que a IA propôs e
aceita ou rejeita cada uma. `"revisao": false` aplica direto, e `"revisao": {"autor": "Ana"}` troca o autor (o
padrão é `Lince`).

| Operação | O que faz |
| --- | --- |
| `texto` | Troca o texto de um parágrafo, com a formatação do começo dele |
| `substituir` | Busca e troca num parágrafo ou no documento todo (sem `alvo`); corta no caractere exato, então o resto do parágrafo mantém negrito, itálico e links |
| `inserir_paragrafo_apos` | Parágrafo novo depois do alvo, com o formato dele ou com `estilo` |
| `remover_paragrafo` | Tira o parágrafo |
| `estilo_paragrafo` | Aplica um estilo do documento, pelo id ou pelo nome |
| `comentar` | Comentário no parágrafo, com `autor` |

No DOCX, um parágrafo recebe uma operação de texto por pedido, e o que já tem alteração controlada pendente no
arquivo (inclusive de formatação) é recusado até alguém aceitar ou rejeitar no Word. Imagem, campo (inclusive o
sumário), nota, equação, quebra de página, caixa de texto e controle de conteúdo no meio do parágrafo nunca são
apagados: `texto` recusa, e `substituir` só troca o trecho que não passa por eles (dentro de um controle de
conteúdo, troca). Para nenhum pedido prender o serviço, a edição de DOCX tem teto: parágrafo com mais de 200 mil caracteres ou 50 mil
elementos XML é recusado, e o pedido inteiro que passa de 60 s não aplica nada.

- **Nada se perde:** só as partes tocadas são reescritas; o resto do arquivo (mídia, mestres, partes que o Lince
  não conhece) sai idêntico em bytes. No deck real de uma empresa, com 16 slides, gráficos com planilha embutida e
  tabela, 5 edições alteraram 6 das 97 partes, e a renderização antes e depois mostrou o layout intacto.
- **Concorrência:** `se_texto` e `base_sha256` recusam a edição (409) se o documento mudou desde a leitura.
- **Formatos:** PPTX e PPTM; DOCX e DOCM; XLSX e XLSM.

## Deploy

A imagem roda sem root, tem healthcheck em `/saude` e não grava nada (não precisa de volume). Tags publicadas:
`X.Y.Z` e `X.Y`, para `linux/amd64` e `linux/arm64`, com atestado de procedência do GitHub.

```yaml
services:
  lince:
    image: ghcr.io/yefclub/lince:0.1
    environment:
      LINCE_TOKENS: ${LINCE_TOKENS}
    ports:
      - "8000:8000"
    read_only: true
    tmpfs:
      - /tmp # o documento só existe aqui enquanto é lido
```

Coloque atrás de um proxy reverso com TLS e timeout de leitura acima de 300 s.

### Antivírus (opcional)

Com `LINCE_CLAMAV=host:porta`, todo arquivo recebido passa pelo [ClamAV](https://www.clamav.net/) antes de ser
lido ou editado. Arquivo infectado recusa o envio inteiro (`422 arquivo-infectado`, com o nome da assinatura); antivírus fora
do ar recusa com `503` em vez de ler sem varrer. Sem a variável, nada muda. O ClamAV roda como serviço à parte: o
Lince só fala o protocolo do `clamd` pela rede. As variáveis estão em [`.env.example`](.env.example).

```yaml
services:
  lince:
    image: ghcr.io/yefclub/lince:0.1
    env_file: .env            # LINCE_TOKENS e LINCE_CLAMAV=clamav:3310
    depends_on: [clamav]
    ports: ["8000:8000"]
    read_only: true
    tmpfs: [/tmp]
  clamav:
    image: clamav/clamav:stable  # baixa e atualiza as assinaturas sozinho
    volumes: [clamav-db:/var/lib/clamav]
volumes:
  clamav-db:
```

No `clamd.conf`, `StreamMaxLength` precisa ser pelo menos `100M`, que é o teto de envio do Lince.

### Cache das leituras da IA (opcional)

Com `LINCE_REDIS=redis://host:6379/0`, a transcrição que a IA fez de cada recorte ou página fica no Redis por
`LINCE_CACHE_DIAS` dias (padrão 30). A mesma imagem, com a mesma instrução e o mesmo modelo, não é paga de novo, nem
em outro envio nem em outra versão do Lince. Fica a transcrição em texto puro, sob o hash da imagem; numa página
escaneada, ela é o texto da página. O arquivo nunca é guardado. Resposta vazia ou lote que a IA devolveu quebrado não
entram no cache: a próxima tentativa lê de novo.

O cache é **separado por token**: um cliente não descobre o que outro leu nem recebe resposta gravada por outro.
Sem a variável, não há cache. Com o Redis fora do ar, o Lince lê normalmente pela IA e só volta a tentar o Redis 30 s
depois de uma falha.

**Redis:** use uma instância (ou um banco) com teto de memória e descarte das chaves antigas, por exemplo
`maxmemory 2gb` e `maxmemory-policy allkeys-lru`. Não divida a memória com um Redis de filas que exige `noeviction`
(BullMQ, Sidekiq e afins): as transcrições acumulam, e quando a memória acaba quem cai são as filas. As chaves levam o
prefixo `lince:leitura:`.

Na biblioteca, passe o cliente síncrono que a plataforma já tem, com `socket_timeout` definido (sem ele, um Redis
travado prende a leitura): `lince.Leitor(ia=..., cache=lince.CacheRedis(cliente))` (instale com
`pip install "lince[redis]"`), ou qualquer objeto com `obter(chave)` e `guardar(chave, texto)`.

## Formatos

O formato vem da extensão do nome do arquivo. Qualquer outra extensão é recusada pela API com `415
formato-nao-suportado`, antes do upload entrar na fila. Na biblioteca e na linha de comando, um arquivo com
extensão fora da lista (ou sem extensão) é lido pelo conteúdo quando traz o marcador do formato; sem marcador, a
leitura falha com `lince.FormatoNaoSuportado`.

| Tipo | Extensões | Leitura |
| --- | --- | --- |
| PDF | `.pdf` | Nativo, escaneado ou misto, roteado por página |
| Word e texto rico | `.docx` `.docm` `.doc` `.odt` `.rtf` `.epub` | Texto, tabelas e imagens; texto oculto do `.docx` vai para os alertas |
| Apresentação | `.pptx` `.pptm` `.ppsx` `.ppsm` `.ppt` `.pps` `.pot` `.odp` | Slides, notas, tabelas e gráficos com os dados |
| Planilha | `.xlsx` `.xlsm` `.xlsb` `.xls` `.ods` `.csv` `.tsv` | Abas como tabelas; CSV e TSV com separador e codificação detectados |
| Imagem | `.png` `.jpg` `.jpeg` `.webp` `.tif` `.tiff` `.bmp` `.gif` | Pelo modelo de visão |
| Página da web | `.html` `.htm` | Títulos, listas, tabelas e links; imagem embutida (`data:`) e desenho SVG lidos pela IA, imagem externa nunca baixada; texto escondido pela própria tag vai para os alertas |
| Desenho vetorial | `.svg` | Desenhado (resvg) e lido pela IA; o texto do desenho fica no corpo e título e descrição vão para as propriedades. Referência a arquivo local ou endereço externo nunca entra no desenho |
| Texto | `.txt` `.md` `.json` `.yaml` `.yml` `.xml` `.tex` | Como está |

Documento como código e edição: `.pptx` e `.pptm` (`.ppsx` só na leitura do código).

Cabeçalho e rodapé de `.docx`, `.docm`, `.doc`, `.rtf`, `.xlsx`, `.xlsm`, `.xls`, `.ppt`, `.pps`, `.pot`, `.odt`, `.ods` e `.odp`
saem do corpo e ficam nos metadados, em `cabeçalho/rodapé`, com número de página e data legíveis
(`Página [página] de [páginas]`). Abas ocultas do `.xlsx` e do `.xls` ficam em `abas_ocultas`. Arquivo com macro
ganha `macros: true`: VBA no Office (OOXML, inclusive `.xlsb`, e `.doc`, `.xls`, `.ppt`, `.pps` e `.pot`), macro do Excel 4.0
(`.xlsx`, `.xlsm`, `.xlsb` e `.xls`) e macro do LibreOffice. A macro nunca é executada.

Cada extensão da tabela é testada no CI com um arquivo real em `tests/fixtures/reais/`: os de Word, Excel,
PowerPoint, OpenDocument, RTF, EPUB, PDF, HTML, SVG, CSV e imagem vêm dos conjuntos de teste públicos do Apache POI e do
Apache Tika, gravados pelos próprios programas; `.pps`, `.pot`, `.htm`, `.jpeg` e `.tiff` são cópias com outra extensão, e os de
texto foram escritos à mão. O `.xlsb` gravado fora da especificação (como o do Excel 2007 Beta 2), que o leitor do
corpo recusa, é lido registro a registro: valores, fórmulas com o resultado guardado e strings compartilhadas, e o
caminho fica registrado em `partes_ilegiveis`.

E-mail (`.eml`, `.msg`) e arquivos compactados (`.zip`) ainda não são aceitos.

## Instalação

```bash
pip install "lince @ git+https://github.com/Yefclub/lince"              # biblioteca
pip install "lince[servidor] @ git+https://github.com/Yefclub/lince"    # + API HTTP
pip install "lince[emf] @ git+https://github.com/Yefclub/lince"         # + desenho de EMF e WMF
```

O desenho de EMF e WMF (`lince[emf]`, metafile-render) é opcional: ele só tem wheel para Linux glibc, macOS e
Windows, e ainda não instala no Python 3.15. Sem ele, a imagem EMF ou WMF fica marcada no texto e entra em
`cobertura.pendentes` com o motivo; o resto do documento é lido igual.

**Alpine (musl).** Quem roda em Alpine deve usar o Lince como serviço: a imagem de referência
`ghcr.io/yefclub/lince` (Debian slim, Python 3.14, com o `lince[emf]`), chamada pela API HTTP, ao lado da aplicação.
Instalar a biblioteca no próprio Alpine é possível, mas caro: o `pdf-inspector` e o `html-to-markdown` não publicam
wheel musllinux, e o `pip` compila os dois a partir do código-fonte. O Rust do `apk` (`apk add rust cargo`) é antigo
demais para o `html-to-markdown`; use o Rust estável do `rustup` com o `build-base` (compilador C e cabeçalhos). Na
medição da issue #40, a compilação levou cerca de 16 min. O `lince[emf]` não instala em Alpine. A instalação direto do
GitHub precisa do `git`, que a imagem `python:*-alpine` não traz.

```sh
apk add build-base curl git
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable
. "$HOME/.cargo/env"
pip install "lince @ git+https://github.com/Yefclub/lince"
```

Linha de comando:

```bash
lince relatorio.pdf --url https://openrouter.ai/api/v1 --modelo seu/modelo --chave-arquivo ~/.chave --saida saida/
```

## Dependências

Todas com licença permissiva: [anydoc](https://github.com/firecrawl/anydoc) (MIT),
[pdf-inspector](https://github.com/firecrawl/pdf-inspector) (MIT), [pypdfium2](https://github.com/pypdfium2-team/pypdfium2)
(Apache-2.0/BSD-3), openpyxl (MIT), python-pptx (MIT), Pillow (MIT-CMU), lxml (BSD-3), defusedxml (PSF),
[olefile](https://github.com/decalage2/olefile) (BSD-2), [html-to-markdown](https://github.com/xberg-io/html-to-markdown) (MIT), tinycss2 (BSD-3); na API, Starlette e Uvicorn (BSD-3) e
python-multipart (Apache-2.0). Nada de GPL/AGPL. O CI e o build da imagem reprovam qualquer dependência fora
dessa lista, e a imagem leva `THIRD_PARTY_NOTICES.md` gerado do que foi instalado.

Atualizações: o Dependabot abre PR semanal (dependências, actions e imagem base), com espera de 3 dias após
cada publicação; patch e minor entram sozinhos quando o CI passa.

## Estado

Em validação. A bancada `bench/medir.py` gera os próprios documentos (nada de arquivo de terceiro), lê com uma IA
simulada e confere o que o Lince faz: texto nativo, roteamento, montagem, texto oculto e concorrência. Ela refaz a
tabela abaixo em qualquer máquina, e a versão curta roda no CI (`tests/test_bench.py`):

```bash
uv run --extra emf python bench/medir.py
```

| Conferência | Resultado | |
| --- | --- | --- |
| Lote de faturas do mesmo modelo (PDF) | 12/12 faturas no texto, rodapé com a folha nos metadados (0.0 s) | ok |
| PDF longo | 15000/15000 linhas de 300 páginas (1.6 s) | ok |
| Currículos com injeção escondida (5 técnicas) | 5/5 removidas, 5/5 com alerta (0.1 s) | ok |
| Apresentação com gráficos e logo repetido | 5/5 slides numerados, 5/5 gráficos no lugar, 6 leituras para 10 imagens (0.1 s) | ok |
| Leitura em duas fases (IA de quem chama) | 6 tarefas, resultado igual ao da IA do Lince (0.1 s) | ok |
| Planilha grande | 100000 linhas, perfil data/texto/texto/numero/numero, corte avisado no texto (1.8 s) | ok |
| Concorrência | 42/42 documentos ao mesmo tempo, 0 erros (1.3 s) | ok |
| Pico de memória do processo | 245 MB | ok |

Medido num contêiner de 4 CPUs, Python 3.13; o tempo não inclui gerar os documentos.

A qualidade da leitura pela IA depende do modelo e não entra nesta bancada. Na versão 0.1 ela foi medida uma vez,
com o mesmo modelo de visão para os dois motores, em testes no estilo olmOCR-Bench e em documentos que não podem
ser publicados, e a carga foi medida uma vez com a API. Por isso a medição abaixo não é refeita pela bancada:

| Teste (medição única, v0.1) | Docling + IA | Lince |
| --- | --- | --- |
| Bateria pública (Falcon, ANP, arXiv, Office) | 84% | 92% |
| Conjunto cego de documentos corporativos | 86% | 89% |
| Escaneados com gabarito exato (média e pesada) | 78% | 85–86% |
| Gráficos do ParseBench (pontos de dados) | 14% | 50–53% |
| Carga com 16 usuários simultâneos | 10 GB RAM, 10/160 perdidos | 1,4 GB, 0/160 perdidos |
| Carga com 200 usuários × 10 documentos | — | 2.000/2.000, 3,7 GB, 0 quedas |
| Carga com 1.000 usuários simultâneos × 3 documentos | — | 3.000/3.000 processados, 0 perdidos, 3,4 GB, 0 quedas |

## Contribuição e segurança

Issues são bem-vindas; pull requests ficam com o mantenedor ([CONTRIBUTING.md](CONTRIBUTING.md)).
Vulnerabilidade: relato privado, veja [SECURITY.md](SECURITY.md).

## Licença

MIT © Yef
