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

SAIDA_MAX = 256 * 1024 * 1024  # caracteres que a montagem escreve (a leitura em cada lugar em que entra)


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
    for repetida, leitora in plano.get("iguais", {}).items():  # a mesma imagem em outro lugar: a mesma leitura
        if leitora in R:
            R[repetida] = R[leitora]

    def bloco_ia(tid, rotulo):
        txt = R.get(tid, "").strip()
        return f"<!-- lido pela IA: {tid} -->\n{txt}" if txt else f"> [{rotulo}: pendente]"

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
        corpo = "\n\n".join(partes)
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
            r["substitui_tabela"]: r["tarefa"]
            for r in p["regioes"]
            if "substitui_tabela" in r and R.get(r["tarefa"], "").strip()
        }
        formulas = p.get("formulas", [])
        f_lidas = {
            r["substitui_formula"]: r["tarefa"]
            for r in p["regioes"]
            if "substitui_formula" in r and R.get(r["tarefa"], "").strip()
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
    return _metadados(pasta, plano, acabamento("\n\n".join(partes)), moveis)


CABECALHO_MAX = 80  # linha de cabeçalho/rodapé corrido é curta; parágrafo que o leitor juntou numa linha, não
FRACAO_MAX = 0.25  # se a remoção levaria mais que isso do texto nativo do documento, nada sai


DESLOCAMENTO_MAX = 50  # número impresso = índice da página + d (capa e páginas iniciais sem número): |d| até isto
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
    varia ("Página 3 de 12", "Relatório anual — 3"), com um deslocamento constante entre o número impresso e o índice
    da página (a capa sem número desloca todos); a linha só com o número da página, na primeira ou na última posição,
    também sai, com o mesmo deslocamento em quase todas as páginas. Valor, data e número de documento que mudam entre
    páginas não casam, a página escaneada e o texto lido pela IA não passam por aqui, título (linha com #) nunca sai
    e, se o que sairia passa de FRACAO_MAX do texto nativo do documento, nada sai."""
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
            if v is not None and abs(v - pagina) <= DESLOCAMENTO_MAX and re.search(r"[^\W\d]", forma):
                out.add((forma, v - pagina))  # só número (sem palavra) é o caso de so_numero
        return out

    def so_numero(ln, pagina):
        """Deslocamento do número de página que é a linha inteira ("3", "- 3 -", "Página 3 de 12", "iv"), ou None."""
        t = ln.strip()
        if not re.fullmatch(NUM_PAGINA, t, flags=re.I):
            return None
        m = re.search(r"\d{1,4}|[ivxlc]{1,6}", t, flags=re.I)
        v = _numero(m.group()) if m else None
        return v - pagina if v is not None and abs(v - pagina) <= DESLOCAMENTO_MAX else None

    bordas = {}
    for i, blocos in nativas:
        cheias = linhas(blocos)
        bordas[i] = cheias[:2] + cheias[-2:]
    n = len(nativas)
    minimo = max(3, 0.6 * n)
    freq = Counter(c for i, _ in nativas for c in {c for _, _, ln in bordas[i] for c in chaves(ln, i + 1)})
    moveis = {c for c, v in freq.items() if n >= 3 and v >= minimo}
    extremos = {}
    for i, blocos in nativas:
        cheias = linhas(blocos)
        extremos[i] = [
            (k, j, ln, d) for k, j, ln in (cheias[:1] + cheias[-1:]) if (d := so_numero(ln, i + 1)) is not None
        ]
    desloc = Counter(d for e in extremos.values() for d in {x[3] for x in e})
    numeros = {d for d, v in desloc.items() if n >= 2 and v >= max(2, 0.6 * n)}
    tirar, total = {}, 0
    for i, blocos in nativas:
        cheias = linhas(blocos)
        total += sum(len(ln.strip()) for _, _, ln in cheias)
        for k, j, ln in bordas[i]:
            if achadas := [c for c in chaves(ln, i + 1) if c in moveis]:
                tirar[(i, k, j)] = (ln, achadas[0][0])
        for k, j, ln, d in extremos[i]:
            if d in numeros:
                tirar.setdefault((i, k, j), (ln, None))
    if sum(len(ln.strip()) for ln, _ in tirar.values()) > FRACAO_MAX * total:
        return set()  # cabeçalho que pesaria tanto assim no texto não é cabeçalho: o documento fica como está
    for i, blocos in nativas:
        for k in sorted({k for (pi, k, _) in tirar if pi == i}):
            partes = blocos[k].split("\n")
            blocos[k] = "\n".join(ln for j, ln in enumerate(partes) if (i, k, j) not in tirar)
    return {c for _, c in tirar.values() if c}


def tamanho_da_saida(e, respostas) -> int:
    """Quanto texto a montagem vai escrever: cada marca vira a leitura (a mesma imagem repetida recebe a mesma
    leitura em cada lugar) ou a tabela e a fórmula nativas. Conta antes de montar, para a saída ter teto como a
    entrada tem: uma resposta grande copiada em mil lugares não estoura a memória do processo."""
    if e.get("tipo") == "imagem":  # a imagem solta sai como as leituras das faixas, uma depois da outra
        return sum(len(respostas.get(t["id"], "")) + 2 for t in e.get("tarefas", []))
    from collections import Counter

    lote_de = {tid: lid for lid, itens in (e.get("lotes") or {}).items() for _, tid in itens}

    def leitura(tid):
        tid = e.get("iguais", {}).get(tid, tid)
        # a maior entre a do item (já dividida, no esforço máximo e na releitura) e a do lote: a chave de um não
        # esconde o outro, inclusive nas respostas de quem chama, antes do filtro por id
        return max(len(respostas.get(tid, "")), len(respostas.get(lote_de.get(tid), ""))) + 64

    total = len(e.get("md", "")) + sum(len(x) for x in e.get("extras", []))
    marcas = Counter(re.findall(r"\{\{IA:(\w+)\}\}", e.get("md", "")))
    total += sum(n * leitura(tid) for tid, n in marcas.items())
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
                max((leitura(r.get("tarefa", "")) for r in p.get("regioes", [])), default=0),
            )
    return total
