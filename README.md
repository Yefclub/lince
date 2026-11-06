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
revisão, código, classificação), junto com marca d'água, propriedades do arquivo, comentários e alterações
rastreadas do Word e abas ocultas do Excel.

**Segurança**: texto que a IA leria e uma pessoa não vê (branco no branco, fonte minúscula, modo invisível,
oculto do Word) sai do corpo e gera alerta; padrões de prompt injection em português e inglês geram alerta.
O conteúdo das imagens é tratado como dado, nunca como instrução.

## Peças plugáveis

```python
leitor = lince.Leitor(
    ia=lince.OpenAICompativel(url, modelo, chave),   # ou FuncaoLeitor(sua_funcao), ou o seu objeto com .ler()
    cache=lince.CacheDisco("/var/cache/lince"),      # ou o seu: obter(k) / guardar(k, texto)
    ao_usar=registrar_custo,                         # chamado a cada leitura com tokens e modelo
    max_leituras=200,                                # teto por documento; o resto vira pendência registrada
)
```

- Sem `ia`, o Lince devolve o texto nativo completo e marca o que ficou pendente.
- 429 e 5xx do provedor são repetidos com espera exponencial e `Retry-After`.
- Falha da IA nunca derruba o documento: a leitura nativa fica e a pendência é registrada.

## API HTTP

Serviço para qualquer plataforma chamar, com contrato **OpenAPI 3.1** versionado, servido em
`GET /v1/openapi.json` (gere o cliente a partir dele). Uma PR que quebre o contrato não passa no CI.

Cada envio, de um documento ou de um lote, cria uma **sessão**. Quem chama marca a sessão com uma `referencia`
própria, por exemplo `usuario:42`. Assim, um sistema com N usuários sabe de quem é cada resultado.

**Nada fica guardado.** O arquivo é apagado assim que lido, e não há cache em disco. O resultado fica em memória só
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
| `POST /v1/sessoes` | Envia 1 a 50 arquivos (campo `arquivo` repetido), com `referencia` e `esforco` (`padrao` ou `max`) opcionais. Responde `201` com a sessão e `Location`. Com `Prefer: wait=N` (até 300 s), espera terminar. |
| `GET /v1/sessoes/{id}` | Estado da sessão e de cada documento; o que já terminou vem com `markdown`, `metadados`, `cobertura`, `alertas_injecao` e `uso` (tokens por leitura). Aceita `Prefer: wait=N`. |
| `GET /v1/sessoes?referencia=…` | Sessões do token ainda não entregues, da mais nova para a mais antiga (resumo, sem o conteúdo lido). |
| `DELETE /v1/sessoes/{id}` | Confirma o recebimento (ou cancela o que ainda não foi lido) e apaga a sessão na hora. |
| `GET /v1/openapi.json`, `GET /saude` | Contrato; serviço no ar e versões. |

- **Idempotência**: repetir o envio com a mesma `Idempotency-Key` antes da entrega devolve a mesma sessão (`200`),
  sem ler nem cobrar de novo. Reusar a chave com outro conteúdo dá `422`.
- **Erros**: todo erro sai em `application/problem+json` (RFC 9457), com `type` estável
  (`urn:lince:erro:<codigo>`) e `instance` igual ao `X-Request-Id`. O `X-Request-Id` enviado volta em toda
  resposta, o que facilita o rastreio entre os dois sistemas.
- **Sem `X-IA-URL`**: só a extração nativa; o que precisava de IA aparece em `cobertura.pendentes`.
- **Fila justa**: os documentos são atendidos em rodízio entre tokens, então quem manda 500 não trava quem
  mandou 1. Até 16 documentos em leitura ao mesmo tempo por instância.
- **Isolamento**: um token nunca vê sessão nem referência de outro. `LINCE_TOKENS` aceita vários tokens
  separados por vírgula, um por plataforma. Para uso local sem token: `lince-servidor --sem-autenticacao`.
- **Limites**: 100 MB por envio (com `Content-Length`), 50 arquivos por sessão, 64 documentos pendentes e 256 MB
  de resultado esperando entrega por token. Acima disso, `429` com `Retry-After`.
- **Escala**: as sessões ficam na memória da instância. Para várias réplicas, use `Prefer: wait` (resposta na
  mesma conexão) ou afinidade por id da sessão no balanceador.

## Documento como código (edição por IA)

Para uma IA editar um documento sem estragar layout, mestres, animações e gráficos, ela precisa da estrutura com
endereços estáveis. Com `codigo=sim` no envio, cada PPTX traz `resultado.codigo`: slides, formas, parágrafos,
células de tabela e séries de gráfico, cada um com um id tirado do próprio arquivo (`sld:258/sp:2/p:0`).

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

| Operação | O que faz |
| --- | --- |
| `texto` | Troca o texto de uma forma (uma linha por parágrafo) ou de um parágrafo, mantendo a formatação |
| `substituir` | Busca e troca num slide ou numa forma, mesmo com o texto partido em vários trechos de formatação |
| `notas` | Troca as notas do apresentador |
| `celula_tabela` | Troca o texto de uma célula |
| `valor_grafico` | Muda um ponto da série no gráfico e na planilha embutida dele |
| `ordem`, `remover_slide` | Reordena ou tira slides |

- **Nada se perde:** só as partes tocadas são reescritas; o resto do arquivo (mídia, mestres, partes que o Lince
  não conhece) sai idêntico em bytes. No deck real de uma empresa, com 16 slides, gráficos com planilha embutida e
  tabela, 5 edições alteraram 6 das 97 partes, e a renderização antes e depois mostrou o layout intacto.
- **Concorrência:** `se_texto` e `base_sha256` recusam a edição (409) se o documento mudou desde a leitura.
- **Formatos:** PPTX hoje.

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
| Texto | `.txt` `.md` `.json` `.yaml` `.yml` `.xml` `.tex` | Como está |

Documento como código e edição: `.pptx` e `.pptm` (`.ppsx` só na leitura do código).

HTML, e-mail (`.eml`, `.msg`) e arquivos compactados (`.zip`) ainda não são aceitos.

## Instalação

```bash
pip install "lince @ git+https://github.com/Yefclub/lince"              # biblioteca
pip install "lince[servidor] @ git+https://github.com/Yefclub/lince"    # + API HTTP
```

Linha de comando:

```bash
lince relatorio.pdf --url https://openrouter.ai/api/v1 --modelo seu/modelo --chave-arquivo ~/.chave --saida saida/
```

## Dependências

Todas com licença permissiva: [anydoc](https://github.com/firecrawl/anydoc) (MIT),
[pdf-inspector](https://github.com/firecrawl/pdf-inspector) (MIT), [pypdfium2](https://github.com/pypdfium2-team/pypdfium2)
(Apache-2.0/BSD-3), openpyxl (MIT), python-pptx (MIT), Pillow (MIT-CMU); na API, Starlette e Uvicorn (BSD-3) e
python-multipart (Apache-2.0). Nada de GPL/AGPL. O CI e o build da imagem reprovam qualquer dependência fora
dessa lista, e a imagem leva `THIRD_PARTY_NOTICES.md` gerado do que foi instalado.

Atualizações: o Dependabot abre PR semanal (dependências, actions e imagem base), com espera de 3 dias após
cada publicação; patch e minor entram sozinhos quando o CI passa.

## Estado

Versão 0.1, em validação. Resultados medidos com o mesmo modelo de visão para os dois motores, testes no estilo
olmOCR-Bench e documentos inéditos criados por um agente sem contexto:

| Teste | Docling + IA | Lince |
| --- | --- | --- |
| Bateria pública (Falcon, ANP, arXiv, Office) | 84% | 92% |
| Conjunto cego de documentos corporativos | 86% | 89% |
| Escaneados com gabarito exato (média e pesada) | 78% | 85–86% |
| Gráficos do ParseBench (pontos de dados) | 14% | 50–53% |
| Currículos com injeção oculta | 4/5 removidas, sem alerta | 5/5 removidas, 6/6 alertas |
| Carga com 16 usuários simultâneos | 10 GB RAM, 10/160 perdidos | 1,4 GB, 0/160 perdidos |
| Carga com 200 usuários × 10 documentos | — | 2.000/2.000, 3,7 GB, 0 quedas |
| Carga com 1.000 usuários simultâneos × 3 documentos | — | 3.000/3.000 processados, 0 perdidos, 3,4 GB, 0 quedas |

## Contribuição e segurança

Issues são bem-vindas; pull requests ficam com o mantenedor ([CONTRIBUTING.md](CONTRIBUTING.md)).
Vulnerabilidade: relato privado, veja [SECURITY.md](SECURITY.md).

## Licença

MIT © Yef
