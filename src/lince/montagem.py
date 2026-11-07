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


def montar(pasta, respostas):
    plano = json.load(open(f"{pasta}/plano.json", encoding="utf-8"))
    R = {k: sem_repeticao(v) for k, v in distribuir_lotes(plano, respostas).items()}

    def bloco_ia(tid, rotulo):
        txt = R.get(tid, "").strip()
        return f"<!-- lido pela IA: {tid} -->\n{txt}" if txt else f"> [{rotulo}: pendente]"

    def acabamento(txt):
        txt = re.sub(r"(?<=\w)--(?=\w)", "—", txt)  # travessão datilografado
        txt = re.sub(r"(?m)^[ \t]*\\[ \t]*$", "", txt)  # parágrafo vazio vira "\" no Markdown: some
        return re.sub(r"\n{3,}", "\n\n", txt)

    partes = []
    if plano["tipo"] == "office":
        md = re.sub(r"\{\{IA:(\w+)\}\}", lambda m: "\n\n" + bloco_ia(m.group(1), "imagem") + "\n\n", plano["md"])
        partes.append(md)
        partes.extend(plano["extras"])
        corpo = "\n\n".join(partes)
        # rodapé de slide/página repetido e número de slide: saem do corpo, vão para os metadados
        from collections import Counter

        linhas = [ln.strip() for ln in corpo.splitlines()]
        # sem páginas: só a repetição EXATA de linha curta conta (rodapé de slide); número não é trocado,
        # para não confundir linhas de dado parecidas ("Lote 12 aprovado" / "Lote 13 aprovado")
        freq = Counter(
            ln for ln in linhas if 10 <= len(ln) <= 120 and not ln.startswith(("|", "#", "<!--", "-", "*", ">"))
        )
        moveis = {k for k, v in freq.items() if v >= 3}
        corpo = "\n".join(
            ln
            for ln in corpo.splitlines()
            if ln.strip() not in moveis and not re.fullmatch(r"\s*\d{1,3}\s*/\s*\d{1,3}\s*", ln)
        )
        return _metadados(pasta, plano, acabamento(corpo), moveis)
    for p in plano["paginas"]:
        cab = f"<!-- página {p['i'] + 1} · {p['rota']} -->"
        if p["rota"] == "pagina":
            ids = p.get("tarefas") or [p["tarefa"]]
            partes.append(cab + "\n" + "\n\n".join(bloco_ia(t, "página") for t in ids))
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
        saida = [cab] + inserir.get(-1, [])
        for k, b in enumerate(limpos):
            saida.append(b)
            saida.extend(inserir.get(k, []))
        partes.append("\n\n".join(saida))
    # Cabeçalho/rodapé corrido: linha que se repete em todas as páginas (ou em muitas) sai do corpo.
    from collections import Counter

    n_pag = len(plano["paginas"])

    def chave(ln):  # nº de página e números que mudam por página não impedem reconhecer a repetição
        return re.sub(r"\d+", "#", re.sub(r"\s*(\d+|[ivxlc]+)$", "", ln.strip(), flags=re.I))

    def bordas(pt, n=3):
        """Só as primeiras e as últimas linhas da página podem ser cabeçalho/rodapé corrido."""
        cheias = [ln for ln in pt.splitlines() if ln.strip() and not ln.startswith("<!--")]
        return set(cheias[:n] + cheias[-n:])

    freq = Counter(k for pt in partes for k in {chave(ln) for ln in bordas(pt)} if len(k) >= 15)
    moveis = {
        k
        for k, v in freq.items()
        if n_pag >= 2 and (v == n_pag or v >= max(3, 0.3 * n_pag)) and not k.startswith(("<!--", "|", "#", "> ["))
    }
    originais = {}
    for pt in partes:
        for ln in bordas(pt):
            if chave(ln) in moveis:
                originais.setdefault(chave(ln), re.sub(r"\s*(\d+|[ivxlc]+)$", "", ln.strip(), flags=re.I))

    def limpa(pt):
        borda = bordas(pt)
        linhas = [ln for ln in pt.splitlines() if not (ln in borda and chave(ln) in moveis)]
        cheias = [k for k, ln in enumerate(linhas) if ln.strip() and not ln.startswith("<!--")]
        extremos = set(cheias[:1] + cheias[-1:])  # nº de página só vale na 1ª e na última linha da página
        return "\n".join(
            ln for k, ln in enumerate(linhas) if not (k in extremos and re.fullmatch(NUM_PAGINA, ln, flags=re.I))
        )

    partes = [limpa(pt) for pt in partes]
    return _metadados(pasta, plano, acabamento("\n\n".join(partes)), set(originais.values()))
