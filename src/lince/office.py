"""Office (DOCX, PPTX, XLSX, ODF, RTF, EPUB, legados): anydoc + gráficos nativos, setas de fluxograma e imagens."""

import hashlib  # noqa: F401
import io  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import zipfile  # noqa: F401
from xml.etree import ElementTree as ET  # noqa: F401

from .limites import xml_ou_nada
from .seguranca import remover_ocultos, texto_oculto_docx

NS = {
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
}


TIPOS_GRAFICO = {
    "barChart": "barras",
    "bar3DChart": "barras 3D",
    "lineChart": "linhas",
    "pieChart": "pizza",
    "doughnutChart": "rosca",
    "areaChart": "área",
    "scatterChart": "dispersão",
    "radarChart": "radar",
    "bubbleChart": "bolhas",
}


def graficos_ooxml(path):
    """Gráfico nativo do Office: tipo, título, séries e valores do cache — dado exato."""
    out = []
    wb = None
    if path.lower().endswith(".xlsx"):
        from openpyxl import load_workbook

        wb = load_workbook(path, data_only=True)

    def resolver(el):
        """Sem cache no gráfico: lê o intervalo referenciado direto da planilha."""
        f = el.find("c:f", NS) if el is not None else None
        if f is None or wb is None:
            return []
        m = re.match(r"^'?(.+?)'?!(.+)$", f.text or "")
        if not m or m.group(1) not in wb.sheetnames:
            return []
        cel = wb[m.group(1)][m.group(2).replace("$", "")]
        if not isinstance(cel, tuple):
            return [cel.value]
        return [c.value for linha in cel for c in (linha if isinstance(linha, tuple) else (linha,))]

    with zipfile.ZipFile(path) as z:
        for nome in sorted(n for n in z.namelist() if re.search(r"/charts/chart\d+\.xml$", n)):
            root = xml_ou_nada(z.read(nome))
            if root is None or root.find(".//c:plotArea", NS) is None:
                continue  # gráfico malformado ou hostil: a leitura segue sem ele
            titulo = " ".join(t.text or "" for t in root.findall(".//c:title//a:t", NS)).strip()
            plot = root.find(".//c:plotArea", NS)
            for tipo_el in list(plot):
                tag = tipo_el.tag.split("}")[1]
                if tag not in TIPOS_GRAFICO:
                    continue
                series = []
                for ser in tipo_el.findall("c:ser", NS):
                    nome_s = " ".join(v.text or "" for v in ser.findall("c:tx//c:v", NS))
                    if not nome_s:
                        nome_s = " ".join(str(v) for v in resolver(ser.find("c:tx/c:strRef", NS))) or "série"
                    cats = [v.text for v in ser.findall("c:cat//c:pt/c:v", NS)]
                    if not cats:
                        cats = [
                            str(v)
                            for v in resolver(ser.find("c:cat/c:strRef", NS))
                            or resolver(ser.find("c:cat/c:numRef", NS))
                        ]
                    vals = [v.text for v in ser.findall("c:val//c:pt/c:v", NS)]
                    if not vals:
                        vals = [str(v) for v in resolver(ser.find("c:val/c:numRef", NS))]
                    ref = ser.find("c:val//c:f", NS)
                    series.append(
                        {"nome": nome_s, "cats": cats, "vals": vals, "ref": ref.text if ref is not None else None}
                    )
                out.append({"parte": nome, "tipo": TIPOS_GRAFICO[tag], "titulo": titulo, "series": series})
    return out


def grafico_md(g):
    linhas = [f"**Gráfico ({g['tipo']}): {g['titulo'] or 'sem título'}**"]
    ss = g["series"]
    if ss and ss[0]["vals"]:
        cats = ss[0]["cats"] or [str(k + 1) for k in range(len(ss[0]["vals"]))]
        linhas.append("| Categoria | " + " | ".join(s["nome"] for s in ss) + " |")
        linhas.append("|" + " --- |" * (len(ss) + 1))
        for k, cat in enumerate(cats):
            linhas.append(f"| {cat} | " + " | ".join(s["vals"][k] if k < len(s["vals"]) else "" for s in ss) + " |")
    else:
        refs = ", ".join(f"{s['nome']} ({s['ref']})" for s in ss)
        linhas.append(f"Séries: {refs} — sem valores em cache; dados na planilha.")
    return "\n".join(linhas)


def fluxos_pptx(path):
    """Diagrama de formas: caixas e setas (conectores ligados) viram arestas, com o rótulo da seta
    (caixa de texto curta solta perto do conector, ex.: Sim/Não)."""
    from pptx import Presentation

    prs = Presentation(path)
    out = {}
    for n, slide in enumerate(prs.slides, 1):
        formas = list(slide.shapes)
        nomes = {sh.shape_id: (sh.text_frame.text.strip() if sh.has_text_frame else sh.name) for sh in formas}
        conectores, ligados = [], set()
        for sh in formas:
            el = sh._element
            if el.tag.endswith("}cxnSp"):
                st, en = el.find(".//a:stCxn", NS), el.find(".//a:endCxn", NS)
                if st is not None and en is not None:
                    a_, b_ = int(st.get("id")), int(en.get("id"))
                    ligados |= {a_, b_}
                    conectores.append((sh, a_, b_))
        rotulos = [
            sh
            for sh in formas
            if sh.shape_id not in ligados
            and sh.has_text_frame
            and 0 < len(sh.text_frame.text.strip()) <= 15
            and not sh._element.tag.endswith("}cxnSp")
            and sh.width is not None
            and sh.left is not None
        ]
        de_rotulo = {}
        for r in rotulos:
            cx, cy = r.left + r.width / 2, r.top + r.height / 2

            def dist(c):
                x0, y0 = min(c.begin_x, c.end_x), min(c.begin_y, c.end_y)
                x1, y1 = max(c.begin_x, c.end_x), max(c.begin_y, c.end_y)
                return max(x0 - cx, 0, cx - x1) + max(y0 - cy, 0, cy - y1)

            if conectores:
                c = min(conectores, key=lambda t: dist(t[0]))
                if dist(c[0]) < 731520:  # 0,8 polegada
                    de_rotulo.setdefault(id(c[0]), r.text_frame.text.strip())
        arestas = []
        for c, a_, b_ in conectores:
            rot = de_rotulo.get(id(c))
            arestas.append(f"{nomes.get(a_, a_)} → {nomes.get(b_, b_)}" + (f" ({rot})" if rot else ""))
        if arestas:
            out[n] = arestas
    return out


def mermaid_do_fluxo(arestas):
    """Arestas "A → B" em flowchart Mermaid (rótulos das caixas preservados)."""
    ids, linhas = {}, ["```mermaid", "flowchart LR"]

    def no(nome):
        if nome not in ids:
            ids[nome] = f"n{len(ids) + 1}"
            rot = str(nome).replace('"', "'")
            linhas.append(f'    {ids[nome]}["{rot}"]')
        return ids[nome]

    pares = []
    for ar in arestas:
        a, b = ar.split(" → ", 1)
        m = re.match(r"^(.*) \(([^()]{1,15})\)$", b)
        b, rot = (m.group(1), m.group(2)) if m else (b, None)
        pares.append((a, b, rot))
    for a, b, _ in pares:
        no(a), no(b)
    linhas += [f"    {ids[a]} -->{'|' + rot + '|' if rot else ''} {ids[b]}" for a, b, rot in pares]
    return "\n".join(linhas + ["```"])


def planejar_office(path, pasta, ext=None):
    """`ext` é o formato já decidido pelo leitor (pela extensão ou pelo conteúdo)."""
    import anydoc

    ext = ext or os.path.splitext(path)[1].lower()
    # CSV e TSV não têm assinatura: o formato vai explícito (o anydoc detecta separador e codificação)
    doc = anydoc.to_document(open(path, "rb").read(), format="csv" if ext in (".csv", ".tsv") else None)
    tarefas = []

    def ler_imagem(data, mime, onde):
        tid = f"img{len(tarefas) + 1:02d}"
        from PIL import Image

        Image.open(io.BytesIO(data)).convert("RGB").save(f"{pasta}/ia/{tid}.png")
        tarefas.append({"id": tid, "modo": "imagem", "ancora": onde})
        return f"{{{{IA:{tid}}}}}"

    md, _ = serializar(doc, ler_imagem)
    seguranca = []
    if ext == ".docx":
        ocultos = texto_oculto_docx(path)
        if ocultos:
            md, _ = remover_ocultos(md, ocultos)
            seguranca.append(
                {"tecnicas": sorted({m for m, _ in ocultos}), "texto": " ".join(t for _, t in ocultos)[:500]}
            )
    extras, ilegiveis = [], []
    if ext == ".xlsx":  # o anydoc não extrai imagem de planilha: openpyxl, com aba e célula
        from openpyxl import load_workbook

        def imagens_planilha():
            nonlocal md
            for ws in load_workbook(path).worksheets:
                for im in getattr(ws, "_images", []):
                    a = im.anchor._from
                    onde = f"aba {ws.title}, célula {chr(65 + a.col) if a.col < 26 else a.col}{a.row + 1}"
                    md += f"\n\n**Imagem ({onde}):**\n\n" + ler_imagem(im._data(), "image/png", onde)

        _complemento(imagens_planilha, "imagens da planilha", ilegiveis)
    if ext in (".xlsx", ".docx"):
        _complemento(lambda: extras.extend(grafico_md(g) for g in graficos_ooxml(path)), "gráficos", ilegiveis)
    if ext == ".pptx":

        def diagramas():
            for n, arestas in fluxos_pptx(path).items():
                extras.append(
                    f"**Diagrama do slide {n}:**\n\n"
                    + mermaid_do_fluxo(arestas)
                    + "\n\n"
                    + "\n".join(f"- {a}" for a in arestas)
                )

        _complemento(diagramas, "diagramas de formas", ilegiveis)
        # gráfico do pptx: anydoc já entrega os dados; acrescenta tipo e título
        _complemento(
            lambda: extras.extend(
                f"_(gráfico de {g['tipo']} acima: {g['titulo'] or 'sem título'})_" for g in graficos_ooxml(path)
            ),
            "gráficos",
            ilegiveis,
        )
    return {
        "tipo": "office",
        "md": md,
        "extras": extras,
        "tarefas": tarefas,
        "texto_oculto": seguranca,
        "ilegiveis": ilegiveis,
    }


def _complemento(funcao, nome, ilegiveis):
    """Enriquecimento além do texto (gráficos, diagramas, imagens de planilha). Se a parte for malformada ou
    hostil, o documento segue sem ele e o que ficou de fora é registrado; o corpo lido nunca se perde por isso."""
    try:
        funcao()
    except MemoryError:  # falta de memória não é parte ruim: derruba o documento
        raise
    except Exception as e:  # noqa: BLE001
        ilegiveis.append(f"{nome}: {type(e).__name__}")


def _tam(data):
    try:
        from PIL import Image

        return Image.open(io.BytesIO(data)).size
    except Exception:  # noqa: BLE001 — formato que o Pillow não abre conta como imagem grande (não é descartada)
        return (999, 999)


def serializar(doc, ler_imagem):
    cobertura = {"pendente": []}
    assets = {a.id: a for a in doc.assets}
    n_img = {"n": 0}

    def inl(xs):
        out = []
        for x in xs or []:
            k = x.kind
            if k == "text":
                out.append(x.text or "")
            elif k == "link":
                out.append(inl(x.content))
            elif k == "image":
                n_img["n"] += 1
                src = x.source
                onde = f"imagem {n_img['n']}"
                if src and src.kind == "asset" and src.asset_id in assets:
                    a = assets[src.asset_id]
                    if not a.media_type.startswith("image/"):
                        cobertura["pendente"].append(f"{onde}: {a.media_type}")
                        out.append(f"[{onde}: objeto {a.media_type}]")
                    elif min(_tam(a.data)) < 48:
                        cobertura["pendente"].append(f"{onde}: decorativa")
                    else:
                        out.append(ler_imagem(a.data, a.media_type, onde))
                else:
                    out.append(f"[{onde}: {x.alt or 'sem fonte'}]")
            elif k == "line_break":
                out.append(" ")
            elif k == "math":
                out.append(f"${x.text}$")
            elif k == "note_ref":
                out.append(f"[^{x.note_id}]")
            elif k == "checkbox":
                out.append("[x]" if x.checked else "[ ]")
        return "".join(out)

    def blocos(bs, prof=0):
        out = []
        for b in bs or []:
            k = b.kind
            if k == "heading":
                out.append("#" * (b.level or 1) + " " + inl(b.content))
            elif k == "paragraph":
                txt = inl(b.content)
                out.append("\\" + txt if txt[:1] in ">#-+|" else txt)
            elif k == "list":
                for i, it in enumerate(b.list.items):
                    corpo = blocos(it.blocks, prof + 1).strip().replace("\n", "\n  ")
                    marca = "-" if b.list.marker == "bullet" else f"{b.list.start + i}."
                    out.append("  " * prof + f"{marca} {corpo}")
            elif k == "table":
                linhas = []
                for row in b.table.grid:
                    cel = []
                    for s in row:
                        t = blocos(s.cell.blocks).replace("\n", " ").strip() if s.kind == "origin" and s.cell else ""
                        cel.append(t.replace("|", "\\|"))
                    linhas.append("| " + " | ".join(cel) + " |")
                if linhas:
                    ncol = linhas[0].count(" | ") + 1
                    linhas.insert(1, "|" + " --- |" * ncol)
                out.append("\n".join(linhas))
            elif k == "block_quote":
                out.append("> " + blocos(b.blocks).replace("\n", "\n> "))
            elif k == "code_block":
                out.append(f"```{b.lang or ''}\n{b.text}\n```")
            elif k == "rule":
                out.append("---")
            elif k == "math":
                out.append(f"$$\n{b.text}\n$$")
        return "\n\n".join(x for x in out if x.strip())

    md = blocos(doc.blocks)
    if doc.notes:
        md += "\n\n" + "\n".join(f"[^{n.id}]: {blocos(n.blocks)}" for n in doc.notes)
    return md, n_img["n"]
