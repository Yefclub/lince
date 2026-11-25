"""Montagem: junta nativo + leituras da IA, tira cabeçalho/rodapé corrido para os metadados."""

import hashlib  # noqa: F401
import io  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import zipfile  # noqa: F401
from collections import Counter
from xml.etree import ElementTree as ET  # noqa: F401

from .agrupamento import por_tarefa
from .cobertura_scan import parece_parcial
from .seguranca import alertas_injecao
from .texto import NUM_PAGINA, grupos_de_frases, sem_repeticao

# Caracteres que a montagem escreve, o texto nativo junto. A leitura de uma imagem repetida entra uma vez, e as outras
# posições levam só a referência: em 16 Mi cabem as 2.000 páginas que o PDF analisa (PAGINAS_ANALISE_MAX) com 8 mil
# caracteres cada. O teto vale para as leituras: as que não couberem com o nativo ficam pendentes, e o texto nativo sai
# inteiro mesmo que, sozinho, passe dele. O teto segura o resultado em JSON (até 4 bytes por caractere) em 64 MB, um
# quarto do que um token pode reter no servidor.
SAIDA_MAX = 16 * 1024 * 1024
REPETIDA = "_(mesma imagem lida acima: {})_"  # nas outras posições da mesma imagem: a leitura está na âncora citada
_TABELA_VISUAL = "**Leitura visual complementar**\n\n"
_TABELA_NATIVA = "\n\n**Texto nativo de referência**\n\n"


def _metadados(pasta, plano, corpo, moveis):
    """Tira do corpo as marcas [[tipo: …]] da IA e grava metadados.json (com o nativo do arquivo)."""
    marcas = re.findall(r"\[\[(cabeçalho|rodapé|marca d'água|controle):\s*(.*?)\]\]", corpo)
    corpo = re.sub(r"[ \t]*\[\[(cabeçalho|rodapé|marca d'água|controle):.*?\]\][ \t]*\n?", "", corpo)
    meta = dict(plano.get("meta_arquivo") or {})
    por_tipo = {}
    for tipo, txt in marcas:
        por_tipo.setdefault(tipo, set()).add(txt.strip())
    if moveis:
        por_tipo.setdefault("cabeçalho/rodapé", set()).update(moveis)
    for p in plano.get("paginas", []):  # a marca d'água que a página do PDF traz como texto em diagonal
        if p.get("marca_dagua"):
            por_tipo.setdefault("marca d'água", set()).update(p["marca_dagua"])
    for k, v in por_tipo.items():  # junta com o que o arquivo já declara (cabeçalho/rodapé nativo)
        meta[k] = sorted(set(meta.get(k, [])) | v)
    if plano.get("texto_oculto"):
        meta["texto_oculto_removido"] = plano["texto_oculto"]
    if anotacoes := plano.get("anotacoes_planilha"):
        # Endereços, URLs e textos de nota são literais. Não sofrem tipografia nem viram marcas da IA.
        corpo = corpo.rstrip() + "\n\n" + anotacoes
    alertas = alertas_injecao(corpo) + [a for o in plano.get("texto_oculto", []) for a in alertas_injecao(o["texto"])]
    alertas += [o["alerta"] for o in plano.get("texto_oculto", []) if o.get("alerta")]
    if alertas:
        meta["alertas_injecao"] = alertas
    with open(f"{pasta}/metadados.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    return corpo.strip() + "\n"


_CERCA_ABRE = re.compile(r"(?:```|~~~)")
_CERCA_FECHA = re.compile(r"(?:```|~~~)[ \t]*")


def acabamento(txt: str) -> str:
    """Limpeza final do corpo: travessão, parágrafo vazio solto e linhas em branco demais; blocos de código
    (``` ou ~~~ até a próxima linha que só fecha cerca) ficam como estão, e cerca que não fecha é texto comum.
    Linear no tamanho do texto, por mais cercas abertas que ele tenha."""
    linhas = txt.split("\n")
    inicio, pos = [], 0  # posição de cada linha no texto
    for ln in linhas:
        inicio.append(pos)
        pos += len(ln) + 1
    fechos = [k for k, ln in enumerate(linhas) if _CERCA_FECHA.fullmatch(ln)]
    saida, de, k, f = [], 0, 0, 0
    while k < len(linhas):
        if _CERCA_ABRE.match(linhas[k]):
            while f < len(fechos) and fechos[f] <= k:
                f += 1
            if f < len(fechos):  # bloco de código: da abertura ao fim da linha do fecho, intacto
                fim = inicio[fechos[f]] + len(linhas[fechos[f]])
                saida += [_limpar(txt[de : inicio[k]]), txt[inicio[k] : fim]]
                de, k = fim, fechos[f] + 1
                continue
        k += 1
    saida.append(_limpar(txt[de:]))
    return "".join(saida)


_HIFENS = re.compile(r"(?<=\w)--(?=\w)")
_ANCORA_OU_HIFENS = re.compile(r'<a id="[^"\n]*"></a>|\]\(#|(?<=\w)--(?=\w)')
_DELIMITADOR_DESTINO = re.compile(r"\\[^\n]|[()\s]")


def _travessoes(t):
    if "--" not in t:
        return t
    if "](#" not in t and '<a id="' not in t:
        return _HIFENS.sub("—", t)
    partes, inicio, protegido = [], 0, 0
    for m in _ANCORA_OU_HIFENS.finditer(t):
        if m.start() < protegido:
            continue
        if m[0] == "](#":
            # O conversor conserva parênteses internos balanceados e escapa espaços no fragmento.
            nivel, protegido = 1, len(t)
            for d in _DELIMITADOR_DESTINO.finditer(t, m.end()):
                if d[0].isspace():
                    protegido = d.start()
                    break
                nivel += (d[0] == "(") - (d[0] == ")")
                if not nivel:
                    protegido = d.end()
                    break
        elif m[0] == "--":
            partes.extend([t[inicio : m.start()], "—"])
            inicio = m.end()
    return "".join([*partes, t[inicio:]])


def _limpar(t: str) -> str:
    t = _travessoes(t)
    t = re.sub(r"(?m)^[ \t]*\\[ \t]*$", "", t)  # parágrafo vazio vira "\" no Markdown: some
    return re.sub(r"\n{3,}", "\n\n", t)


def _lidas(plano, respostas, ler) -> dict:
    """{tarefa: o que `ler(chave)` dá para ela}, na ordem de distribuir_lotes: a resposta avulsa primeiro e o lote por
    cima, item a item."""
    lotes = plano.get("lotes") or {}
    R = {}
    for chave in [k for k in respostas if k not in lotes] + [lid for lid in lotes if lid in respostas]:
        R.update(ler(chave))
    return R


def _textos(plano, chave, resposta) -> dict[str, str]:
    """O texto que a montagem escreve para cada tarefa da resposta `chave`: o item do lote tirado do JSON e a trava de
    repetição. A mesma função para montar e para medir antes (tamanho_da_saida): a estimativa mede o texto real."""
    return {tid: sem_repeticao(t).strip() for tid, t in por_tarefa(plano, chave, resposta).items()}


def _pendente(rotulo, alt):
    marca = f"> [{rotulo}: pendente]"
    return f"{marca} [texto alternativo: {alt}]" if alt else marca


def _ancora_leitura(tid):
    return f"<!-- lido pela IA: {tid} -->\n"


def _digital_do_scan(digital, visuais):
    if not digital:
        return {}

    def normalizar(linha):
        linha = re.sub(r"^(?:#{1,6}|>)\s+|^\[\[(?:cabeçalho|rodapé):\s*|\]\]$", "", linha.strip())
        return " ".join(linha.strip("*_`").split())

    lidas = {tid: Counter(normalizar(linha) for linha in visual.splitlines()) for tid, visual in visuais.items()}
    restantes = {}
    for pos, textos in digital.items():
        for texto, candidatas in textos:
            linha = normalizar(texto)
            for tid in candidatas:
                if lidas[tid][linha]:
                    lidas[tid][linha] -= 1
                    break
            else:
                restantes.setdefault(pos, []).append(texto)
    return restantes


def _cobre_ocr(nativo, visual):
    # A mesma tabela pode trocar a ordem das células. Conta ocorrências, sem exigir a mesma
    # ordem nem grafia perfeita do OCR. Uma legenda ou resposta curta conserva a reserva.
    # A comparação é de uma única folha, com teto para as listas e os contadores de palavras.
    if max(len(nativo), len(visual)) > 128_000:
        return False

    def palavras(texto):
        return re.findall(r"\w+", re.sub(r"<[^<>]{1,256}>", " ", texto).casefold())

    original, lida = palavras(nativo), Counter(palavras(visual))
    frequencias = Counter(original)
    if lida < frequencias:  # transcreveu só um subconjunto literal, mesmo reordenado
        return False
    if not original or (frequencias & lida).total() < 0.8 * len(original):
        return False

    # Numa lista de registros reconhecidos literalmente, basta faltar uma linha para perder
    # uma medição. Também vale se a visão acrescentou um título ou mudou a ordem dos registros.
    def registros(texto):
        return Counter(tuple(ws) for ln in texto.splitlines() if len(ws := palavras(ln)) >= 3)

    nativas = registros(nativo)
    iguais = (nativas & registros(visual)).total()
    if 2 <= iguais < nativas.total() and iguais >= nativas.total() / 2:
        return False
    # A maioria global também ocorreria se a transcrição acabasse antes de um parágrafo inteiro.
    # Cada trecho precisa ter correspondência local, mesmo quando a tabela muda a ordem das linhas.
    for i in range(0, len(original), 40):
        trecho = original[i : i + 40]
        if (Counter(trecho) & lida).total() < 0.7 * len(trecho):
            return False
    return True


def _texto_do_bloco(bloco):
    # A geometria dá o título sem o prefixo Markdown. Dois leitores podem atribuir
    # níveis diferentes ao mesmo título. O conteúdo e a pontuação precisam coincidir.
    bloco = bloco.strip()
    if "\n" not in bloco:
        bloco = re.sub(r"^#{1,6}[ \t]+", "", bloco)
    return " ".join(bloco.split())


def _cobre_tabela(nativo, visual):
    """Confere as linhas por célula e as notas por parágrafo antes de trocar a âncora nativa."""
    if max(len(nativo), len(visual)) > 128_000:
        return False

    def partes(md):
        linhas, texto = Counter(), []
        for bloco in md.split("\n\n"):
            if not bloco.lstrip().startswith("|"):
                texto.append(_texto_do_bloco(bloco).strip("*_ "))
                continue
            for linha in bloco.splitlines():
                celulas = tuple(" ".join(c.split()).strip("*_ ") for c in linha.strip().strip("|").split("|"))
                if any(not re.fullmatch(r":?-{2,}:?|", c) for c in celulas):
                    linhas[celulas] += 1
        return linhas, " ".join(texto)

    linhas_esperadas, texto_esperado = partes(nativo)
    linhas_lidas, texto_lido = partes(visual)
    return linhas_esperadas <= linhas_lidas and texto_esperado in texto_lido


def _paragrafos_reproduzidos(regioes, blocos, lido):
    """Um boxe vetorial pode conter parágrafos inteiros. A transcrição deve trazer o
    mesmo parágrafo com a mesma pontuação, e o recorte deve conter todas as linhas."""
    desenhos = [
        r
        for r in regioes
        if "substitui_tabela" not in r
        and "substitui_formula" not in r
        and not r.get("preserva_texto")
        and lido(r["tarefa"])
    ]
    # Conferência opcional: limita buscas de trecho em páginas e respostas muito grandes.
    if not desenhos or len(desenhos) > 16 or sum(map(len, blocos)) > 128_000:
        return set()
    unicos = Counter(_texto_do_bloco(b) for b in blocos)
    copiados = set()
    buscas = 0
    for r in desenhos:
        visual, ancora = lido(r["tarefa"]), r["texto_dentro"]
        if max(len(visual), len(ancora)) > 128_000:
            continue
        dentro = " " + " ".join(ancora.split()) + " "
        for b in visual.split("\n\n"):
            texto = _texto_do_bloco(b)
            if not texto or unicos[texto] != 1:
                continue
            buscas += 1
            if buscas > 128:
                return copiados
            if f" {texto} " in dentro:
                copiados.add(texto)
    return copiados


_PALAVRA = re.compile(r"\d[\d.,]*\d|\w+")
ROTULO_MAX = 300  # caracteres: acima disso o bloco é um parágrafo, conferido por `_paragrafos_reproduzidos`


def _rotulos_lidos(regioes, blocos, lido):
    """Os blocos curtos do texto nativo que são rótulos de um desenho já lido: todas as palavras e números do bloco
    estão dentro do recorte e também na leitura, na mesma quantidade. O extrator junta rótulos de caixas vizinhas
    numa linha sem sentido ("Custo por OTIF NPS Avarias entrega 93,9% 71 0,42%"); com a leitura completa, repetir
    essa linha só suja o texto. Se faltar uma palavra na leitura, o bloco fica."""
    lidos = set()
    for r in regioes:
        if not r.get("desenho") or r.get("preserva_texto") or not (visual := lido(r["tarefa"])):
            continue
        if max(len(visual), len(r["texto_dentro"])) > 128_000:
            continue
        # o que a leitura mandou para os metadados ([[cabeçalho: …]]) sai do corpo: não cobre o texto nativo
        corpo_lido = re.sub(r"\[\[(cabeçalho|rodapé|marca d'água|controle):.*?\]\]", " ", visual)
        na_leitura = Counter(_PALAVRA.findall(corpo_lido.lower()))
        no_recorte = Counter(_PALAVRA.findall(r["texto_dentro"].lower()))
        for b in blocos:
            texto = _texto_do_bloco(b)
            palavras = Counter(_PALAVRA.findall(texto.lower()))
            if palavras and len(texto) <= ROTULO_MAX and palavras <= no_recorte and palavras <= na_leitura:
                no_recorte -= palavras  # cada rótulo do recorte responde por um bloco só
                lidos.add(texto)
    return lidos


def montar(pasta, respostas, pendentes=None):
    plano = json.load(open(f"{pasta}/plano.json", encoding="utf-8"))
    R = _lidas(plano, respostas, lambda chave: _textos(plano, chave, respostas[chave]))
    iguais = plano.get("iguais", {})  # a mesma imagem em outro lugar: a mesma leitura
    marca = os.urandom(8).hex()  # a âncora provisória que o documento não tem como imitar

    def lido(tid):
        leitora = iguais.get(tid, tid)
        return R.get(leitora if leitora in R else tid, "")

    def bloco_ia(tid, rotulo):
        # provisório até o corpo estar pronto: na ordem do texto, a primeira posição recebe a leitura e as outras a
        # referência; o texto da IA também não passa pelo corte de cabeçalho nem pelo filtro de rótulos da figura
        if lido(tid):
            return f"<!-- lido pela IA: {tid} {marca} -->"
        return _pendente(rotulo, plano.get("alt_das_tarefas", {}).get(tid))

    def leituras(corpo):
        primeira = {}

        def troca(m):
            tid = m.group(1)
            leitora = iguais.get(tid, tid)
            if leitora in primeira:
                return _ancora_leitura(tid) + REPETIDA.format(primeira[leitora])
            primeira[leitora] = tid
            return _ancora_leitura(tid) + lido(tid)

        return re.sub(rf"<!-- lido pela IA: (\S+) {marca} -->", troca, corpo)  # numa passada: a leitura não é varrida

    partes = []
    if plano["tipo"] == "office":
        # só a marca que o Lince pôs (id de tarefa do plano) vira leitura; o texto do documento que imita uma fica
        ids = {t["id"] for t in plano.get("tarefas_originais", [])} | set(plano.get("iguais", {}))
        md = re.sub(
            r"\{\{IA:(\w+)\}\}",
            lambda m: "\n\n" + bloco_ia(m.group(1), "imagem") + "\n\n" if m.group(1) in ids else m[0],
            plano["md"],
        )
        partes.append(md)
        partes.extend(plano["extras"])
        corpo = leituras("\n\n".join(partes))
        # sem páginas não há cabeçalho corrido no corpo: o do Word vem das partes header/footer (metadados) e o leitor
        # do corpo não entrega os placeholders de rodapé, número e data do slide; a repetição no corpo é conteúdo
        return _metadados(pasta, plano, acabamento(corpo), set())
    paginas = []  # por página: o texto já pronto (escaneada) ou (cabeçalho, blocos nativos, inserções da IA)
    for p in plano["paginas"]:
        cab = f"<!-- página {p['i'] + 1} · {p['rota']} -->"
        if tid := p.get("ocr_da_imagem"):
            visual = lido(tid)
            partes_ocr = [cab, bloco_ia(tid, "figura")]
            if not visual or not _cobre_ocr(p["md"], visual):
                partes_ocr.append(p["md"])
                if visual and pendentes is not None:
                    pendentes.append(f"página {p['i'] + 1}: transcrição visual parcial, mantida a camada de OCR")
            paginas.append("\n\n".join(partes_ocr))
            continue
        if p["rota"] == "pagina":
            ids = p.get("tarefas") or [p["tarefa"]]
            partes_pagina = []
            sinais_scan = p.get("sinais_scan", {})
            for tid in ids:
                bloco = bloco_ia(tid, "página")
                visual = lido(tid)
                if not visual and (nativo := p.get("nativo_pendente", {}).get(tid)):
                    bloco = nativo + "\n\n" + bloco
                if pendentes is not None and visual and tid in sinais_scan:
                    parte, limiar = sinais_scan[tid]
                    local = f"página {p['i'] + 1}" + (f", faixa {parte}" if parte != "1/1" else "")
                    if parece_parcial(visual, limiar):
                        pendentes.append(f"{local} ({tid}): possível transcrição visual parcial pelo volume de texto")
                    elif limiar:
                        pendentes.append(f"{local} ({tid}): integralidade da transcrição visual não verificada")
                partes_pagina.append(bloco)
            digitais = _digital_do_scan(p.get("digital_scan"), {tid: lido(tid) for tid in ids})
            blocos = [x for pos, parte in enumerate(partes_pagina) for x in [*digitais.get(str(pos), []), parte]]
            blocos.extend(digitais.get(str(len(partes_pagina)), []))
            paginas.append(cab + "\n" + "\n\n".join(blocos))
            continue
        tabelas = p.get("tabelas", [])
        trocadas, complementadas = {}, {}
        for r in p["regioes"]:
            k, tid = r.get("substitui_tabela"), r["tarefa"]
            if k is None or not (visual := lido(tid)):
                continue
            estrutura = r.get("estrutura_tabela_confirmada", True)
            if not r.get("tabela_confirmada") or (estrutura and _cobre_tabela(tabelas[k], visual)):
                trocadas[k] = tid
            else:
                complementadas[k] = tid
                if pendentes is not None:
                    pendentes.append(f"{tid}: integralidade da transcrição da tabela não verificada")
        formulas = p.get("formulas", [])
        f_lidas = {
            r["substitui_formula"]: r["tarefa"] for r in p["regioes"] if "substitui_formula" in r and lido(r["tarefa"])
        }

        def troca(m):  # numa passada só: o texto que entra (resposta, tabela) não é varrido de novo
            k = int(m.group(2))
            if m.group(1) == "TABELA":
                if k in complementadas:
                    return _TABELA_VISUAL + bloco_ia(complementadas[k], "tabela") + _TABELA_NATIVA + tabelas[k]
                return bloco_ia(trocadas[k], "tabela") if k in trocadas else tabelas[k] if k < len(tabelas) else m[0]
            return bloco_ia(f_lidas[k], "fórmula") if k in f_lidas else formulas[k] if k < len(formulas) else m[0]

        md = re.sub(r"\{\{(TABELA|FORMULA):(\d+)\}\}", troca, p["md"])
        blocos = [b for b in md.split("\n\n") if b.strip()]
        # Vocabulário coincidente não prova que a IA leu o trecho. Isso vale tanto para uma
        # foto com legenda digital quanto para o texto sobre um desenho.
        copiados = _paragrafos_reproduzidos(p["regioes"], blocos, lido) | _rotulos_lidos(p["regioes"], blocos, lido)
        limpos = [b for b in blocos if not copiados or _texto_do_bloco(b) not in copiados]
        inserir = {}
        for r in p["regioes"]:
            if r.get("repetida") or "substitui_tabela" in r or "substitui_formula" in r:
                continue
            alvo = -1
            for k, b in enumerate(limpos):
                if r["depois_de"] and r["depois_de"] in b:
                    alvo = k
            inserir.setdefault(alvo, []).append(bloco_ia(r["tarefa"], "figura"))
        paginas.append((cab, limpos, inserir))
    moveis = _sem_cabecalho_corrido([(k, pg[1]) for k, pg in enumerate(paginas) if not isinstance(pg, str)])
    partes = []
    for pg, pagina in zip(paginas, plano["paginas"], strict=True):
        if isinstance(pg, str):
            partes.append(pg)
            if campos := pagina.get("campos"):
                partes.append(campos)
            continue
        cab, limpos, inserir = pg
        saida = [cab] + inserir.get(-1, [])
        # a frase partida é juntada depois de sair o cabeçalho corrido (senão ele entraria na frase); a figura de um
        # bloco juntado entra depois do grupo
        for a, b, texto in grupos_de_frases(limpos):
            if texto.strip():
                saida.append(texto)
            for k in range(a, b):
                saida.extend(inserir.get(k, []))
        if campos := pagina.get("campos"):
            saida.append(campos)
        partes.append("\n\n".join(saida))
    return _metadados(pasta, plano, acabamento(leituras("\n\n".join(partes))), moveis)


CABECALHO_MAX = 80  # linha de cabeçalho/rodapé corrido é curta; parágrafo que o leitor juntou numa linha, não
FRACAO_MAX = 0.25  # se a remoção levaria mais que isso do texto nativo do documento, nada sai
# títulos distintos que dizem que o documento tem seções de verdade: TITULOS_MIN_FRACAO das páginas, no mínimo
# TITULOS_MIN (o Ipea: 17 em 43 páginas). Abaixo disso, dois ou três documentos de um lote com um título próprio não
# fazem do título comum a todos ("Fatura") um cabeçalho corrido
TITULOS_MIN_FRACAO = 0.2
TITULOS_MIN = 3
FIXA_MIN = 0.8  # linha idêntica na borda de ao menos esta fração das páginas é cabeçalho fixo ("Confidencial")


DESLOCAMENTO_MAX = 50  # número impresso = índice da página + d (capa e páginas iniciais sem número): |d| até isto
# o rótulo logo antes do número que diz que ele é o da página, e a linha que é só o número ("3 de 12", "3/12")
_TITULO_FIXO = re.compile(r"#{1,6}[ \t]+")  # a marca de título que o extrator pôs no cabeçalho corrido
_ROTULO_PAGINA = re.compile(r"(?<![^\W\d_])(p[aá]gina|p[aá]g|page|pg|p|folha|fls|fl)\.?:?\s*$", flags=re.I)
_SO_O_NUMERO = re.compile(r"\W*#(\s*(de|of|/)\s*\d{1,4})?\W*", flags=re.I)
_ROMANOS = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
            (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]  # fmt: skip


def _numero(token) -> int | None:
    """Valor de um número de página escrito em algarismos ou em romanos (minúsculos ou maiúsculos)."""
    if token.isdigit():
        return int(token)
    t, valor = token.lower(), 0
    for v, r in _ROMANOS:
        while t.startswith(r):
            valor, t = valor + v, t[len(r) :]
    return valor if valor and not t and len(token) <= 8 else None


def _sem_cabecalho_corrido(nativas) -> set[str]:
    """Tira das páginas nativas (índice, blocos) o cabeçalho/rodapé corrido e devolve o que saiu, para os metadados.
    Só sai a linha curta, na borda da página, igual em quase todas as páginas, em que só o número da própria página
    varia, rotulado como página ("Página 3 de 12", "Relatório anual — fls. 3") ou sozinho ("3 de 12"), com um
    deslocamento constante entre o número impresso e o índice da página (a capa sem número desloca todos); o número sem
    rótulo no meio de outras palavras ("Parcela 3 de 12" num carnê) é conteúdo. A linha só com o número da página, na
    primeira ou na última posição, também sai, com o mesmo deslocamento em quase todas as páginas, e vai para os
    metadados como a linha com o número trocado por #. A linha curta idêntica na borda de FIXA_MIN das páginas, sem
    número de página, é cabeçalho fixo e sai também, se couber em FRACAO_MAX junto com o resto (senão só ela fica).
    Valor, data e número de documento que mudam entre páginas não casam, a página escaneada e o texto lido pela IA não
    passam por aqui, título (linha com #) só sai como cabeçalho fixo (idêntico na borda de FIXA_MIN das páginas, num
    documento com seções de verdade: veja `com_secoes`) e, se o que sairia passa de FRACAO_MAX do texto nativo do
    documento, nada sai."""

    def linhas(blocos):
        return [  # o bloco que traz leitura da IA (tabela ou fórmula trocada) fica de fora
            (k, j, ln)
            for k, b in enumerate(blocos)
            if "<!-- lido pela IA" not in b
            for j, ln in enumerate(b.split("\n"))
            if ln.strip()
        ]

    def chaves(ln, pagina):
        """(forma da linha com um número trocado por #, deslocamento desse número em relação à página)."""
        t = ln.strip()
        if len(t) > CABECALHO_MAX or t.startswith(("#", "|", "<!--", "> [")):  # título ("Capítulo 3") é conteúdo
            return set()
        out = set()
        for m in re.finditer(r"(?<![\w.,/])(\d{1,4}|[ivxlcdm]{1,8})(?![\w.,/])", t, flags=re.I):
            v = _numero(m.group())
            forma = t[: m.start()] + "#" + t[m.end() :]
            # o número só é o da página se a linha diz que é ("Página 3", "fls. 3") ou se a linha é só ele ("3 de 12");
            # "Parcela 3 de 12" num carnê de uma parcela por página é conteúdo
            pagina_dita = _ROTULO_PAGINA.search(t[: m.start()]) or _SO_O_NUMERO.fullmatch(forma)
            if v is not None and abs(v - pagina) <= DESLOCAMENTO_MAX and pagina_dita and re.search(r"[^\W\d]", forma):
                out.add((forma, v - pagina))  # só número (sem palavra) é o caso de so_numero
        return out

    def so_numero(ln, pagina):
        """(deslocamento, forma com o número trocado por #) do número de página que é a linha inteira ("3", "- 3 -",
        "(3)", "Página 3 de 12", "Página 3/12", "iv"), ou None."""
        t = ln.strip()
        if not (m := re.fullmatch(NUM_PAGINA, t, flags=re.I)) or t.count("(") != t.count(")"):
            return None  # "3)" é o item de uma lista, não o número da página: o parêntese abre e fecha, ou não há
        g = "n" if m.group("n") else "r"  # o número depois do rótulo, nunca uma letra dele
        v = _numero(m.group(g))
        if v is None or abs(v - pagina) > DESLOCAMENTO_MAX:
            return None
        return v - pagina, t[: m.start(g)] + "#" + t[m.end(g) :]

    def fixa(ln):
        """A linha, sem a marca de título, se ela pode ser cabeçalho fixo: curta, com letra, fora de tabela e marca do
        Lince. O título entra aqui (e só aqui): a linha idêntica na borda de quase toda página é o cabeçalho corrido que
        o extrator promoveu a título pela fonte grande ("# Carta de Conjuntura | 69 | Nota 22"), não uma seção."""
        t = _TITULO_FIXO.sub("", ln.strip(), count=1)
        if len(t) > CABECALHO_MAX or t.startswith(("#", "|", "<!--", "> [")) or not re.search(r"[^\W\d]", t):
            return None
        return t

    bordas = {}
    for i, blocos in nativas:
        cheias = linhas(blocos)
        bordas[i] = cheias[:2] + cheias[-2:]
    n = len(nativas)
    minimo = max(3, 0.6 * n)
    freq = Counter(c for i, _ in nativas for c in {c for _, _, ln in bordas[i] for c in chaves(ln, i + 1)})
    # em duas páginas, só a linha que diz o total ("Página 1 de 2" e "Página 2 de 2") prova a paginação
    moveis = {
        c
        for c, v in freq.items()
        if (n >= 3 and v >= minimo) or (n == 2 and v == 2 and re.search(r"#\s*(de|of|/)\s*2(?!\d)", c[0], flags=re.I))
    }
    # cabeçalho fixo: em cada posição da borda, a linha que se repete idêntica; se a parte idêntica entra no corpo
    # além das 2 linhas da borda (3ª linha do começo ou do fim também igual), são páginas iguais, e isso é conteúdo
    por_posicao = {}
    for _, blocos in nativas:
        cheias = linhas(blocos)
        for pos in (0, 1, 2, -1, -2, -3):
            if -len(cheias) <= pos < len(cheias):
                por_posicao.setdefault(pos, Counter())[cheias[pos][2].strip()] += 1
    repetidas = {
        pos: t
        for pos, c in por_posicao.items()
        for t, v in c.most_common(1)
        # com duas páginas, a linha fixa só sai se a paginação já ficou provada pela linha do número
        if (n >= 3 and v >= max(3, FIXA_MIN * n)) or (n == 2 and v == 2 and moveis)
    }
    terceira = {0: 2, 1: 2, -1: -3, -2: -3}  # de cada posição da borda, a primeira linha do corpo
    # o título que se repete só sai num documento com seções de verdade: outros títulos, distintos, em número que
    # acompanha o tamanho do documento (TITULOS_MIN_FRACAO das páginas, no mínimo TITULOS_MIN) e que não se repetem
    # na maioria das páginas. Num lote de faturas de uma página, "Fatura" no topo de cada uma é o título de cada
    # documento: um "Observações" numa delas ou um "Itens" em todas não fazem dele cabeçalho corrido
    paginas_do_titulo = {}
    for i, blocos in nativas:
        for b in blocos:
            for ln in b.split("\n"):
                if _TITULO_FIXO.match(ln.lstrip()):
                    paginas_do_titulo.setdefault(ln.strip(), set()).add(i)
    secoes_de_verdade = {t for t, pags in paginas_do_titulo.items() if len(pags) <= n / 2}
    com_secoes = len(secoes_de_verdade) >= max(TITULOS_MIN, TITULOS_MIN_FRACAO * n)
    fixas = {
        (pos, t)
        for pos, t in repetidas.items()
        if pos in terceira and terceira[pos] not in repetidas and fixa(t) and (com_secoes or not _TITULO_FIXO.match(t))
    }
    extremos = {}
    for i, blocos in nativas:
        cheias = linhas(blocos)
        extremos[i] = [
            (k, j, ln, *x) for k, j, ln in (cheias[:1] + cheias[-1:]) if (x := so_numero(ln, i + 1)) is not None
        ]
    desloc = Counter(d for e in extremos.values() for d in {x[3] for x in e})
    numeros = {d for d, v in desloc.items() if n >= 2 and v >= max(2, 0.6 * n)}
    tirar, tirar_fixas, total = {}, {}, 0
    for i, blocos in nativas:
        cheias = linhas(blocos)
        total += sum(len(ln.strip()) for _, _, ln in cheias)
        for k, j, ln in bordas[i]:
            if achadas := [c for c in chaves(ln, i + 1) if c in moveis]:
                tirar[(i, k, j)] = (ln, achadas[0][0])
        for pos, t in fixas:
            if -len(cheias) <= pos < len(cheias) and cheias[pos][2].strip() == t:
                tirar_fixas[(i, *cheias[pos][:2])] = (cheias[pos][2], fixa(t))
        for k, j, ln, d, forma in extremos[i]:
            if d in numeros:
                tirar.setdefault((i, k, j), (ln, forma))

    def peso(x):
        return sum(len(ln.strip()) for ln, _ in x.values())

    if peso(tirar) > FRACAO_MAX * total:
        return set()  # cabeçalho que pesaria tanto assim no texto não é cabeçalho: o documento fica como está
    fixas_fora = {c: v for c, v in tirar_fixas.items() if c not in tirar}
    if peso(tirar) + peso(fixas_fora) <= FRACAO_MAX * total:  # o fixo só sai se não levar o resto além do teto
        tirar |= fixas_fora
    for i, blocos in nativas:
        for k in sorted({k for (pi, k, _) in tirar if pi == i}):
            partes = blocos[k].split("\n")
            blocos[k] = "\n".join(ln for j, ln in enumerate(partes) if (i, k, j) not in tirar)
    return {c for _, c in tirar.values()}


def partes_da_imagem(plano, respostas):
    """As mesmas partes na montagem e na medida, inclusive a origem de cada página TIFF."""
    anterior = None
    for tarefa in plano["tarefas"]:
        pagina = tarefa.get("pagina")
        if pagina != anterior:
            formato = tarefa.get("formato_quadro", "TIFF")
            unidade = "página" if formato == "TIFF" else "quadro"
            yield f"<!-- {unidade} {pagina + 1} · {formato} -->"
            anterior = pagina
        yield respostas.get(tarefa["id"], "")


def medir_alternativas(e, respostas, ordem, limite):
    """Tamanho estimado da montagem do EPUB e quantidade de respostas no maior prefixo que cabe.

    Cada imagem pode substituir várias alternativas por uma leitura e referências.
    O custo pode cair. Percorre os lugares uma vez, e cada resposta ou item de lote uma vez.
    """
    md, alternativas = e["md"], e["alt_das_tarefas"]
    iguais = e.get("iguais", {})
    ids = {t["id"] for t in e.get("tarefas_originais", [])} | iguais.keys()
    total = len(md) + sum(len(x) + 2 for x in e.get("extras", [])) + 1
    sem_alt = len(_pendente("imagem", None))
    reservas = {tid: len(_pendente("imagem", alt)) for tid, alt in alternativas.items()}
    diferencas = Counter()
    primeiras = {}
    for m in re.finditer(r"\{\{IA:(\w+)\}\}", md):
        tid = m[1]
        if tid not in ids:
            continue
        leitora = iguais.get(tid, tid)
        reserva = reservas.get(tid, sem_alt)
        total += 4 + reserva - len(m[0])  # a montagem cerca a marca com duas quebras de cada lado
        fixo = len(_ancora_leitura(tid))
        if leitora in primeiras:
            fixo += len(REPETIDA.format(primeiras[leitora]))
        else:
            primeiras[leitora] = tid
        diferencas[leitora] += fixo - reserva
    cabem = 0
    for k, chave in enumerate(ordem, 1):
        for tid, texto in _textos(e, chave, respostas[chave]).items():
            # O plano oferece uma única tarefa por leitora, individual ou em lote.
            if texto and tid in diferencas:
                total += diferencas[tid] + len(texto)
        if total <= limite:
            cabem = k
    return total, cabem


def tamanho_da_saida(e, respostas, medidas=None) -> int:
    """Limite de texto da montagem: cada marca leva a leitura e, quando preciso, a reserva nativa. A leitura da mesma
    imagem repetida entra uma vez, e as outras posições levam a referência. Cada página leva o comentário e as
    junções. Conta antes de montar, para a saída ter teto como a entrada tem, com o texto de cada leitura medido como a
    montagem o escreve (_textos). `medidas` guarda esse tamanho por chave de resposta entre chamadas com as mesmas
    respostas (a busca do que cabe), para cada uma ser lida uma vez só."""
    if e.get("tipo") == "imagem":  # a imagem solta sai como as leituras das faixas, uma depois da outra
        return max(0, sum(len(t) + 2 for t in partes_da_imagem(e, respostas)) - 2)
    if e.get("tipo") == "office" and e.get("alt_das_tarefas"):
        return medir_alternativas(e, respostas, respostas, SAIDA_MAX)[0]

    medidas = {} if medidas is None else medidas

    def medir(chave):
        if chave not in medidas:
            medidas[chave] = {tid: len(t) for tid, t in _textos(e, chave, respostas[chave]).items()}
        return medidas[chave]

    R = _lidas(e, respostas, medir)
    iguais, contadas = e.get("iguais", {}), set()

    def cheia(tid):  # a leitura, a âncora "<!-- lido pela IA: id -->" e as junções em volta dela
        leitora = iguais.get(tid, tid)
        return R.get(leitora if leitora in R else tid, 0) + len(tid) + 64

    def leitura(tid):  # a primeira posição de cada leitura a traz inteira; as outras, a âncora e a referência
        leitora = iguais.get(tid, tid)
        if leitora in contadas:
            return len(REPETIDA) + 2 * len(tid) + 2 * len(leitora) + 64
        contadas.add(leitora)
        return cheia(tid)

    total = len(e.get("md", "")) + sum(len(x) + 2 for x in e.get("extras", [])) + 1  # junções e o fim de linha
    if anotacoes := e.get("anotacoes_planilha"):
        total += len(anotacoes) + 2
    for tid, n in Counter(re.findall(r"\{\{IA:(\w+)\}\}", e.get("md", ""))).items():
        total += leitura(tid) + (n - 1) * leitura(tid)
    for p in e.get("paginas", []):
        total += len(p.get("campos", "")) + (2 if p.get("campos") else 0)
        # o comentário "<!-- página N · rota -->" e as junções da página com a anterior e do comentário com o texto
        total += len(f"<!-- página {p.get('i', 0) + 1} · {p.get('rota', '')} -->") + 4
        # a região que substitui uma tabela ou fórmula entra na posição da marca, abaixo; as outras, onde a figura está
        total += max(len(p.get("md", "")), sum(len(t) + 2 for t in p.get("nativo_pendente", {}).values())) + sum(
            leitura(r.get("tarefa", ""))
            for r in p.get("regioes", [])
            if "substitui_tabela" not in r and "substitui_formula" not in r
        )
        if p.get("rota") == "pagina":  # página escaneada: cada metade lida entra inteira
            total += sum(leitura(t) for t in (p.get("tarefas") or [p.get("tarefa", "")]))
            total += sum(len(t) + 2 for linhas in p.get("digital_scan", {}).values() for t, _ in linhas)
        nativos = {"TABELA": p.get("tabelas", []), "FORMULA": p.get("formulas", [])}
        trocam = {  # a região lida que entra no lugar de cada marca; sem ela, a marca leva o nativo
            ("TABELA", int(r["substitui_tabela"])): (r.get("tarefa", ""), r.get("tabela_confirmada"))
            for r in p.get("regioes", [])
            if "substitui_tabela" in r
        } | {
            ("FORMULA", int(r["substitui_formula"])): (r.get("tarefa", ""), False)
            for r in p.get("regioes", [])
            if "substitui_formula" in r
        }
        for tipo, k in re.findall(r"\{\{(TABELA|FORMULA):(\d+)\}\}", p.get("md", "")):
            k = int(k)
            nativo = nativos[tipo]
            tamanho_nativo = len(nativo[k]) if k < len(nativo) else 0
            tid, pode_complementar = trocam.get((tipo, k), ("", False))
            lida = leitura(tid) if (tipo, k) in trocam else 0
            leitora = iguais.get(tid, tid)
            if pode_complementar and R.get(leitora if leitora in R else tid):
                total += tamanho_nativo + lida + len(_TABELA_VISUAL) + len(_TABELA_NATIVA)
            else:
                total += max(tamanho_nativo, lida)
    return total
