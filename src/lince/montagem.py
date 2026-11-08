"""Montagem: junta nativo + leituras da IA, tira cabeçalho/rodapé corrido para os metadados."""

import hashlib  # noqa: F401
import io  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import zipfile  # noqa: F401
from xml.etree import ElementTree as ET  # noqa: F401

from .agrupamento import distribuir_lotes
from .seguranca import alertas_injecao
from .texto import NUM_PAGINA, sem_repeticao

# Caracteres que a montagem escreve, o texto nativo junto. A leitura de uma imagem repetida entra uma vez, e as outras
# posições levam só a referência: em 16 Mi cabem as 2.000 páginas que o PDF analisa (PAGINAS_ANALISE_MAX) com 8 mil
# caracteres cada. Acima do teto, as leituras que não couberem ficam pendentes e o nativo sai inteiro. O teto segura o
# resultado em JSON (até 4 bytes por caractere) em 64 MB, um quarto do que um token pode reter no servidor.
SAIDA_MAX = 16 * 1024 * 1024
REPETIDA = "_(mesma imagem lida acima: {})_"  # nas outras posições da mesma imagem: a leitura está na âncora citada


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
    for k, v in por_tipo.items():  # junta com o que o arquivo já declara (cabeçalho/rodapé nativo)
        meta[k] = sorted(set(meta.get(k, [])) | v)
    if plano.get("texto_oculto"):
        meta["texto_oculto_removido"] = plano["texto_oculto"]
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


def _limpar(t: str) -> str:
    t = re.sub(r"(?<=\w)--(?=\w)", "—", t)  # travessão datilografado
    t = re.sub(r"(?m)^[ \t]*\\[ \t]*$", "", t)  # parágrafo vazio vira "\" no Markdown: some
    return re.sub(r"\n{3,}", "\n\n", t)


def montar(pasta, respostas):
    plano = json.load(open(f"{pasta}/plano.json", encoding="utf-8"))
    R = {k: sem_repeticao(v) for k, v in distribuir_lotes(plano, respostas).items()}
    iguais = plano.get("iguais", {})  # a mesma imagem em outro lugar: a mesma leitura
    marca = os.urandom(8).hex()  # a âncora provisória que o documento não tem como imitar

    def lido(tid):
        leitora = iguais.get(tid, tid)
        return R.get(leitora if leitora in R else tid, "").strip()

    def bloco_ia(tid, rotulo):
        # provisório até o corpo estar pronto: na ordem do texto, a primeira posição recebe a leitura e as outras a
        # referência; o texto da IA também não passa pelo corte de cabeçalho nem pelo filtro de rótulos da figura
        return f"<!-- lido pela IA: {tid} {marca} -->" if lido(tid) else f"> [{rotulo}: pendente]"

    def leituras(corpo):
        primeira = {}

        def troca(m):
            tid = m.group(1)
            leitora = iguais.get(tid, tid)
            if leitora in primeira:
                return f"<!-- lido pela IA: {tid} -->\n" + REPETIDA.format(primeira[leitora])
            primeira[leitora] = tid
            return f"<!-- lido pela IA: {tid} -->\n{lido(tid)}"

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
        if p["rota"] == "pagina":
            ids = p.get("tarefas") or [p["tarefa"]]
            paginas.append(cab + "\n" + "\n\n".join(bloco_ia(t, "página") for t in ids))
            continue
        tabelas = p.get("tabelas", [])
        trocadas = {
            r["substitui_tabela"]: r["tarefa"] for r in p["regioes"] if "substitui_tabela" in r and lido(r["tarefa"])
        }
        formulas = p.get("formulas", [])
        f_lidas = {
            r["substitui_formula"]: r["tarefa"] for r in p["regioes"] if "substitui_formula" in r and lido(r["tarefa"])
        }

        def troca(m):  # numa passada só: o texto que entra (resposta, tabela) não é varrido de novo
            k = int(m.group(2))
            if m.group(1) == "TABELA":
                return bloco_ia(trocadas[k], "tabela") if k in trocadas else tabelas[k] if k < len(tabelas) else m[0]
            return bloco_ia(f_lidas[k], "fórmula") if k in f_lidas else formulas[k] if k < len(formulas) else m[0]

        md = re.sub(r"\{\{(TABELA|FORMULA):(\d+)\}\}", troca, p["md"])
        blocos = [b for b in md.split("\n\n") if b.strip()]
        # tira do nativo o texto que pertence à figura (rótulos de eixo etc.)
        dentro = " ".join(
            r["texto_dentro"] for r in p["regioes"] if "substitui_tabela" not in r and "substitui_formula" not in r
        )
        palavras_dentro = set(re.findall(r"\w+", dentro.lower()))
        limpos = []
        for b in blocos:
            pal = re.findall(r"\w+", b.lower())
            if (
                pal
                and palavras_dentro
                and sum(w in palavras_dentro for w in pal) / len(pal) > 0.8
                and len(pal) < 60
                and "lido pela IA" not in b
                and not b.lstrip().startswith("|")
            ):
                continue
            limpos.append(b)
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
    for pg in paginas:
        if isinstance(pg, str):
            partes.append(pg)
            continue
        cab, limpos, inserir = pg
        saida = [cab] + inserir.get(-1, [])
        for k, b in enumerate(limpos):
            if b.strip():
                saida.append(b)
            saida.extend(inserir.get(k, []))
        partes.append("\n\n".join(saida))
    return _metadados(pasta, plano, acabamento(leituras("\n\n".join(partes))), moveis)


CABECALHO_MAX = 80  # linha de cabeçalho/rodapé corrido é curta; parágrafo que o leitor juntou numa linha, não
FRACAO_MAX = 0.25  # se a remoção levaria mais que isso do texto nativo do documento, nada sai
FIXA_MIN = 0.8  # linha idêntica na borda de ao menos esta fração das páginas é cabeçalho fixo ("Confidencial")


DESLOCAMENTO_MAX = 50  # número impresso = índice da página + d (capa e páginas iniciais sem número): |d| até isto
# o rótulo logo antes do número que diz que ele é o da página, e a linha que é só o número ("3 de 12", "3/12")
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
    passam por aqui, título (linha com #) nunca sai e, se o que sairia passa de FRACAO_MAX do texto nativo do
    documento, nada sai."""
    from collections import Counter

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
        "Página 3 de 12", "iv"), ou None."""
        t = ln.strip()
        if not re.fullmatch(NUM_PAGINA, t, flags=re.I):
            return None
        m = re.search(r"\d{1,4}|[ivxlc]{1,6}", t, flags=re.I)
        v = _numero(m.group()) if m else None
        if v is None or abs(v - pagina) > DESLOCAMENTO_MAX:
            return None
        return v - pagina, t[: m.start()] + "#" + t[m.end() :]

    def fixa(ln):
        """A linha, se ela pode ser cabeçalho fixo: curta, com letra, fora de título, tabela e marca do Lince."""
        t = ln.strip()
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
    moveis = {c for c, v in freq.items() if n >= 3 and v >= minimo}
    # cabeçalho fixo: em cada posição da borda, a linha que se repete idêntica; se a parte idêntica entra no corpo
    # além das 2 linhas da borda (3ª linha do começo ou do fim também igual), são páginas iguais, e isso é conteúdo
    por_posicao = {}
    for _, blocos in nativas:
        cheias = linhas(blocos)
        for pos in (0, 1, 2, -1, -2, -3):
            if -len(cheias) <= pos < len(cheias):
                por_posicao.setdefault(pos, Counter())[cheias[pos][2].strip()] += 1
    repetidas = {
        pos: t for pos, c in por_posicao.items() for t, v in c.most_common(1) if n >= 3 and v >= max(3, FIXA_MIN * n)
    }
    terceira = {0: 2, 1: 2, -1: -3, -2: -3}  # de cada posição da borda, a primeira linha do corpo
    fixas = {(pos, t) for pos, t in repetidas.items() if pos in terceira and terceira[pos] not in repetidas and fixa(t)}
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
                tirar_fixas[(i, *cheias[pos][:2])] = (cheias[pos][2], t)
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


def tamanho_da_saida(e, respostas) -> int:
    """Quanto texto a montagem vai escrever, no máximo: cada marca vira a leitura (a da mesma imagem repetida entra uma
    vez, e as outras posições, a referência) ou a tabela e a fórmula nativas. Conta antes de montar, para a saída ter
    teto como a entrada tem."""
    if e.get("tipo") == "imagem":  # a imagem solta sai como as leituras das faixas, uma depois da outra
        return sum(len(respostas.get(t["id"], "")) + 2 for t in e.get("tarefas", []))
    from collections import Counter

    lote_de = {tid: lid for lid, itens in (e.get("lotes") or {}).items() for _, tid in itens}
    iguais, contadas = e.get("iguais", {}), set()

    def cheia(tid):
        tid = iguais.get(tid, tid)
        # a maior entre a do item (já dividida, no esforço máximo e na releitura) e a do lote: a chave de um não
        # esconde o outro
        return max(len(respostas.get(tid, "")), len(respostas.get(lote_de.get(tid), ""))) + 64

    def leitura(tid):  # a primeira posição de cada leitura a traz inteira; as outras, a âncora e a referência
        leitora = iguais.get(tid, tid)
        if leitora in contadas:
            return len(REPETIDA) + 2 * len(tid) + 2 * len(leitora) + 64
        contadas.add(leitora)
        return cheia(tid)

    total = len(e.get("md", "")) + sum(len(x) for x in e.get("extras", []))
    for tid, n in Counter(re.findall(r"\{\{IA:(\w+)\}\}", e.get("md", ""))).items():
        primeira = leitura(tid)
        total += primeira + (n - 1) * leitura(tid)  # a segunda chamada já conta a referência
    for p in e.get("paginas", []):
        total += len(p.get("md", "")) + sum(leitura(r.get("tarefa", "")) for r in p.get("regioes", []))
        if p.get("rota") == "pagina":  # página escaneada: cada metade lida entra inteira
            total += sum(leitura(t) for t in (p.get("tarefas") or [p.get("tarefa", "")]))
        tabelas, formulas = p.get("tabelas", []), p.get("formulas", [])
        for tipo, k in re.findall(r"\{\{(TABELA|FORMULA):(\d+)\}\}", p.get("md", "")):
            nativo = tabelas if tipo == "TABELA" else formulas
            k = int(k)
            total += max(
                len(nativo[k]) if k < len(nativo) else 0,
                max((cheia(r.get("tarefa", "")) for r in p.get("regioes", [])), default=0),
            )
    return total
