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
    if alertas:
        meta["alertas_injecao"] = alertas
    with open(f"{pasta}/metadados.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    return corpo.strip() + "\n"


def acabamento(txt: str) -> str:
    """Limpeza final do corpo: travessão, parágrafo vazio solto e linhas em branco demais; blocos de código
    (``` ou ~~~) ficam como estão."""
    pedacos = re.split(r"(?ms)(^(?:```|~~~).*?^(?:```|~~~)[ \t]*$)", txt)
    for k in range(0, len(pedacos), 2):
        t = re.sub(r"(?<=\w)--(?=\w)", "—", pedacos[k])  # travessão datilografado
        t = re.sub(r"(?m)^[ \t]*\\[ \t]*$", "", t)  # parágrafo vazio vira "\" no Markdown: some
        pedacos[k] = re.sub(r"\n{3,}", "\n\n", t)
    return "".join(pedacos)


def montar(pasta, respostas):
    plano = json.load(open(f"{pasta}/plano.json", encoding="utf-8"))
    R = {k: sem_repeticao(v) for k, v in distribuir_lotes(plano, respostas).items()}

    def bloco_ia(tid, rotulo):
        txt = R.get(tid, "").strip()
        return f"<!-- lido pela IA: {tid} -->\n{txt}" if txt else f"> [{rotulo}: pendente]"

    partes = []
    if plano["tipo"] == "office":
        md = re.sub(r"\{\{IA:(\w+)\}\}", lambda m: "\n\n" + bloco_ia(m.group(1), "imagem") + "\n\n", plano["md"])
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
        md = re.sub(
            r"\{\{TABELA:(\d+)\}\}",
            lambda m: (
                bloco_ia(trocadas[int(m.group(1))], "tabela")
                if int(m.group(1)) in trocadas
                else tabelas[int(m.group(1))]
            ),
            p["md"],
        )
        formulas = p.get("formulas", [])
        f_lidas = {
            r["substitui_formula"]: r["tarefa"]
            for r in p["regioes"]
            if "substitui_formula" in r and R.get(r["tarefa"], "").strip()
        }
        md = re.sub(
            r"\{\{FORMULA:(\d+)\}\}",
            lambda m: (
                bloco_ia(f_lidas[int(m.group(1))], "fórmula")
                if int(m.group(1)) in f_lidas
                else formulas[int(m.group(1))]
            ),
            md,
        )
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


def _sem_cabecalho_corrido(nativas) -> set[str]:
    """Tira das páginas nativas (índice, blocos) o cabeçalho/rodapé corrido e devolve o que saiu, para os metadados.
    Só sai a linha curta, na borda da página, igual em quase todas as páginas, em que só o número da própria página
    varia ("Página 3 de 12", "Relatório anual — 3"); a linha só com o número da página na primeira ou na última
    posição também sai. Valor, data e número de documento que mudam entre páginas não casam, a página escaneada e o
    texto lido pela IA não passam por aqui, título (linha com #) nunca sai e, se o que sairia passa de FRACAO_MAX do
    texto nativo do documento, nada sai."""
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
        """Cada forma da linha com uma ocorrência do número da página trocada por #."""
        t = ln.strip()
        if len(t) > CABECALHO_MAX or t.startswith(("#", "|", "<!--", "> [")):  # título ("Capítulo 3") é conteúdo
            return set()
        achados = list(re.finditer(rf"(?<![\d.,/]){pagina}(?![\d.,/])", t))
        return {t[: m.start()] + "#" + t[m.end() :] for m in achados}

    bordas = {}
    for i, blocos in nativas:
        cheias = linhas(blocos)
        bordas[i] = cheias[:2] + cheias[-2:]
    n = len(nativas)
    freq = Counter(c for i, _ in nativas for c in {c for _, _, ln in bordas[i] for c in chaves(ln, i + 1)})
    moveis = {c for c, v in freq.items() if n >= 3 and v >= max(3, 0.6 * n)}
    tirar, total = {}, 0
    for i, blocos in nativas:
        cheias = linhas(blocos)
        total += sum(len(ln.strip()) for _, _, ln in cheias)
        for k, j, ln in bordas[i]:
            if achadas := [c for c in chaves(ln, i + 1) if c in moveis]:
                tirar[(i, k, j)] = (ln, achadas[0])
        for k, j, ln in (cheias[:1] + cheias[-1:]) if cheias else []:
            if re.fullmatch(NUM_PAGINA, ln.strip(), flags=re.I):
                tirar.setdefault((i, k, j), (ln, None))
    if sum(len(ln.strip()) for ln, _ in tirar.values()) > FRACAO_MAX * total:
        return set()  # cabeçalho que pesaria tanto assim no texto não é cabeçalho: o documento fica como está
    for i, blocos in nativas:
        for k in sorted({k for (pi, k, _) in tirar if pi == i}):
            partes = blocos[k].split("\n")
            blocos[k] = "\n".join(ln for j, ln in enumerate(partes) if (i, k, j) not in tirar)
    saiu = {c for _, c in tirar.values() if c}
    return saiu
