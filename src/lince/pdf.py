"""PDF: texto nativo por página, regiões visuais (raster e vetor), tabela duvidosa e roteamento."""

import ctypes
import hashlib  # noqa: F401
import io
import json  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import time
import zipfile  # noqa: F401
from collections import defaultdict
from xml.etree import ElementTree as ET  # noqa: F401

import pypdfium2 as pdfium
import pypdfium2.raw as c  # noqa: F401

from ._prazo import prazo_restante
from ._trava import PDFIUM
from .limites import escala
from .seguranca import texto_oculto_pdf
from .texto import densidade_matematica, desifenizar, limpar_tabelas, lixo

PROMPT_VERSAO = "leitura-ia-v1"
# Teto da análise visual (desenho da página, figuras, texto oculto) por documento: medido em ~15 ms por página de
# texto, 2000 páginas cabem em ~30 s. Acima dele, e depois do prazo do documento, a página entra só com o texto.
PAGINAS_ANALISE_MAX = 2000
# Teto de páginas de texto lidas por documento. Texto corrido denso tem ~4 mil caracteres por página: 5 mil páginas
# dão ~20 M caracteres, a ordem do maior resultado que a montagem entrega, e cabem em ~10 s só com o texto (medido em
# ~2 ms por página de 50 linhas). É o dobro do maior documento real já recebido (2.500 páginas); acima dele, as
# páginas restantes não são lidas e entram nas pendências, como os outros tetos contra documento hostil de limites.py.
PAGINAS_TEXTO_MAX = 5000
# Páginas por chamada do pdf_inspector. Ele não usa a PDFium, mas segura a GIL e, chamado com o arquivo, analisa o
# documento inteiro a cada vez, mesmo pedindo uma página (medido: 4 s por chamada num PDF de 2.500 páginas). O PDF
# maior que uma faixa vai em cópias de FAIXA páginas (~0,05 s cada): o prazo é conferido entre elas e um PDF que
# chega no meio não espera o grande.
FAIXA = 50
# Tempo máximo com a trava da PDFium antes de dar a vez a outro PDF (a trava é justa: quem espera entra antes de
# quem acabou de soltar). Uma página pesada (escaneada, renderizada em 1568 px) passa dele sozinha; o normal é ~10 ms.
VEZ_S = 0.5
# O prazo é do documento inteiro, e a montagem do texto extraído vem depois: ela custa ~1/4 do tempo da extração do
# texto corrido (medido: 0,8 s para as 650 páginas lidas em 3 s). Uma faixa nova só começa se o que resta do prazo
# cobre essa parte; a proporção vale em máquina lenta ou rápida, porque as duas etapas são CPU em Python.
RESERVA_MONTAGEM = 0.3


def texto_pdfium(page):
    tp = page.get_textpage()
    return tp.get_text_bounded()


def regioes_vetoriais(page, W, H, c):
    """Agrupa desenhos (path) que não são régua de tabela nem faixa de linha."""
    caixas, faixas = [], []
    for tipo, _raw, (l, b, r, t) in objetos(page):
        if tipo != c.FPDF_PAGEOBJ_PATH:
            continue
        w, h = r - l, t - b
        if w < 2.5 or h < 2.5:  # filete de tabela / sublinhado
            continue
        if w > 0.9 * W and h > 0.9 * H:  # moldura da página
            continue
        if w > 0.4 * W and h < 40:  # faixa larga e fina: fundo de linha de tabela OU barra horizontal
            faixas.append([l, b, r, t])
            continue
        caixas.append([l, b, r, t])
    # Fundo de linha de tabela tem a mesma largura em todas as linhas; barras de gráfico variam.
    from collections import defaultdict

    por_inicio = defaultdict(list)
    for f in faixas:
        por_inicio[round(f[0] / 4)].append(f)
    for grupo in por_inicio.values():
        larguras = {round((f[2] - f[0]) / 3) for f in grupo}
        if len(grupo) >= 3 and len(larguras) >= 3:
            caixas += grupo
    # união por proximidade (12 pt)
    grupos = []
    for bx in caixas:
        bx = list(bx)
        mudou = True
        while mudou:
            mudou = False
            for g in grupos:
                if not (bx[2] + 24 < g[0] or g[2] + 24 < bx[0] or bx[3] + 24 < g[1] or g[3] + 24 < bx[1]):
                    bx = [min(bx[0], g[0]), min(bx[1], g[1]), max(bx[2], g[2]), max(bx[3], g[3])]
                    grupos.remove(g)
                    mudou = True
                    break
        grupos.append(bx)
    return [g for g in grupos if (g[2] - g[0]) * (g[3] - g[1]) / (W * H) >= 0.03]


def planejar_pdf(path, pasta):
    """Lê o PDF em faixas de páginas, com o prazo conferido entre elas e os tetos valendo antes da leitura.

    A PDFium só é usada com a trava, tomada por pouco tempo e solta entre as vezes (`VEZ_S`): um PDF que chega
    durante a leitura de um grande entra na próxima vez. O documento é aberto e fechado em cada vez, dentro da trava,
    mesmo com exceção: o finalizador dele (e o das páginas) não pode rodar fora dela, em paralelo com outro PDF. O
    pdf_inspector não usa a PDFium e roda fora da trava."""
    inicio = time.monotonic()
    with PDFIUM:
        doc = pdfium.PdfDocument(path)
        try:
            total = len(doc)
        finally:
            doc.close()
    lidas = min(total, PAGINAS_TEXTO_MAX)
    paginas, tarefas, cache, seguranca = [], [], {}, []
    so_texto = {}  # página -> motivo de ela ter entrado só com o texto (teto ou prazo)
    nao_lidas = {}  # página -> motivo de ela não ter sido lida (prazo ou teto)
    for a in range(0, lidas, FAIXA):
        # a primeira faixa sempre entra: o documento nunca sai sem texto por causa do prazo
        if a and (resta := prazo_restante()) is not None and resta <= RESERVA_MONTAGEM * (time.monotonic() - inicio):
            nao_lidas |= {i: "prazo esgotado" for i in range(a, lidas)}
            break
        fila = range(a, min(a + FAIXA, lidas))
        md_de, ocr, itens_de = _extrair(path, fila, total)
        k = 0
        while k < len(fila):
            if motivo := _so_texto(fila[k]):
                so_texto[fila[k]] = motivo
                md, tabelas = limpar_tabelas(desifenizar(md_de.get(fila[k], "")))
                paginas.append({"i": fila[k], "rota": "nativa", "densidade_mat": 0, "origem_texto": "pdf-inspector",
                                "md": md, "tabelas": [tb["md"] for tb in tabelas], "formulas": [],
                                "regioes": []})  # fmt: skip
                k += 1
                continue
            with PDFIUM:
                doc = pdfium.PdfDocument(path)
                try:
                    fim = time.monotonic() + VEZ_S
                    while True:  # a vez acaba no tempo, no fim da faixa ou na página que entra só com o texto
                        i = fila[k]
                        paginas.append(_pagina(doc, i, md_de.get(i, ""), i in ocr, itens_de.get(i, []), pasta,
                                               tarefas, cache, seguranca))  # fmt: skip
                        k += 1
                        if k == len(fila) or time.monotonic() >= fim or _so_texto(fila[k]):
                            break
                finally:
                    doc.close()
    nao_lidas |= {i: f"teto de {PAGINAS_TEXTO_MAX} páginas de texto" for i in range(lidas, total)}
    pendentes = []
    for faixa, motivo in _faixas(so_texto):
        pendentes.append(f"{faixa}: só o texto, sem figuras nem páginas escaneadas lidas ({motivo})")
        seguranca.append(  # o texto oculto dessas páginas não foi conferido: suspeita, nunca "nada oculto"
            {"tecnicas": ["não conferido"], "texto": "", "alerta": f"texto oculto não conferido: {faixa} ({motivo})"}
        )
    pendentes += [f"{faixa}: não lidas ({motivo})" for faixa, motivo in _faixas(nao_lidas)]
    return {"tipo": "pdf", "paginas": paginas, "paginas_total": total, "tarefas": tarefas, "texto_oculto": seguranca,
            "pendentes": pendentes}  # fmt: skip


def _faixas(motivos):
    """(faixa de páginas, motivo) de {página: motivo}, na ordem em que os motivos apareceram."""
    for motivo in dict.fromkeys(motivos.values()):
        ns = [i + 1 for i, m in motivos.items() if m == motivo]
        yield (f"página {ns[0]}" if len(ns) == 1 else f"páginas {ns[0]}–{ns[-1]}"), motivo


def _vencido():
    return (resta := prazo_restante()) is not None and resta <= 0


def _so_texto(i):
    """Motivo de a página entrar só com o texto (teto da análise ou prazo), ou None."""
    if i >= PAGINAS_ANALISE_MAX:
        return f"teto de {PAGINAS_ANALISE_MAX} páginas"
    return "prazo esgotado" if _vencido() else None


def _extrair(path, fila, total):
    """Texto das páginas da faixa pelo pdf_inspector: markdown, páginas para OCR e itens com posição, por página do
    documento. Documento de até uma faixa é lido direto do arquivo; o maior vai em cópias de uma faixa cada."""
    import pdf_inspector

    if total <= FAIXA:
        res = pdf_inspector.extract_pages_markdown(path)
        posicoes = pdf_inspector.extract_text_with_positions(path)
    else:
        with PDFIUM:
            doc = pdfium.PdfDocument(path)
            try:
                dados = _copia(doc, fila)
            finally:
                doc.close()
        res = pdf_inspector.extract_pages_markdown_bytes(dados)
        posicoes = pdf_inspector.extract_text_with_positions_bytes(dados)
    a = fila[0]
    itens_de = defaultdict(list)
    for t in posicoes:
        if t.text.strip():
            itens_de[a + t.page - 1].append(t)
    return {a + pg.page: pg.markdown for pg in res.pages}, {a + p - 1 for p in res.pages_needing_ocr}, itens_de


def _copia(doc, paginas, editar=None):
    """Bytes de um PDF novo com estas páginas do documento, depois de `editar(novo)`. Chamar com a trava: o documento
    original não muda."""
    novo = pdfium.PdfDocument.new()
    try:
        novo.import_pages(doc, list(paginas))
        if editar:
            editar(novo)
        buf = io.BytesIO()
        novo.save(buf)
        return buf.getvalue()
    finally:
        novo.close()


def _sem_ocultos(doc, i, ocultos):
    """Markdown (pdf_inspector) e texto (PDFium) da página `i` tirados de uma cópia dela sem os objetos de texto dos
    itens ocultos, e os itens que não deram com nenhum objeto. Chamar com a trava; o documento original não muda.

    O oculto sai por posição, na origem: um texto visível igual a um oculto continua inteiro no corpo."""
    import pdf_inspector

    sobrou, texto = [], []

    def editar(novo):
        page = novo[0]
        l0, b0 = _origem_visivel(page)
        textos = list(_objetos_de_texto(page))
        alvos = {}
        for x in ocultos:
            achou = [o for o in textos if _coincide(o, x, l0, b0)]
            if not achou:
                sobrou.append(x)
            alvos |= {ctypes.cast(o[0], ctypes.c_void_p).value: o for o in achou}
        for raw, dono, _caixa in alvos.values():
            ok = c.FPDFFormObj_RemoveObject(dono, raw) if dono else c.FPDFPage_RemoveObject(page.raw, raw)
            if ok:
                c.FPDFPageObj_Destroy(raw)
        page.gen_content()
        texto.append(texto_pdfium(page))

    dados = _copia(doc, [i], editar)
    res = pdf_inspector.extract_pages_markdown_bytes(dados)  # uma página: rápido, dentro da trava mesmo
    return (res.pages[0].markdown if res.pages else ""), texto[0], sobrou


def _origem_visivel(page):
    """Canto inferior esquerdo da caixa visível (CropBox ∩ MediaBox), a origem das posições do pdf_inspector."""
    ml, mb, _, _ = page.get_mediabox()
    cl, cb, _, _ = page.get_cropbox()
    return max(ml, cl), max(mb, cb)


def _objetos_de_texto(page):
    """Gera (raw, forma que o contém ou None, caixa na página) de cada objeto de texto, inclusive dentro de formas."""

    def walk(dono, raws, m):
        for raw in raws:
            tipo = c.FPDFPageObj_GetType(raw)
            box = _bounds(raw)
            if tipo == c.FPDF_PAGEOBJ_TEXT:
                yield raw, dono, (_transform(box, m) if m else box)
            elif tipo == c.FPDF_PAGEOBJ_FORM:
                mm = _compose(_matrix(raw), m) if m else _matrix(raw)
                n = c.FPDFFormObj_CountObjects(raw)
                yield from walk(raw, [c.FPDFFormObj_GetObject(raw, k) for k in range(n)], mm)

    n = c.FPDFPage_CountObjects(page.raw)
    yield from walk(None, [c.FPDFPage_GetObject(page.raw, k) for k in range(n)], None)


def _coincide(obj, x, l0, b0):
    """O objeto de texto é parte do item: cai quase todo na caixa dele e não tem outra cor nem outro modo de pintura
    (um texto visível desenhado no mesmo lugar fica)."""
    raw, _dono, (l, b, r, t) = obj
    folga = 1 + 0.25 * x.height
    item = [l0 + x.x - folga, b0 + x.y - folga, l0 + x.x + x.width + folga, b0 + x.y + x.height + folga]
    if _sobrepoe([l, b, max(r, l + 0.01), max(t, b + 0.01)], item) < 0.5:
        return False
    if (c.FPDFTextObj_GetTextRenderMode(raw) == 3) != (x.render_mode == 3):
        return False
    rgba = [ctypes.c_uint() for _ in range(4)]
    if x.fill_color and c.FPDFPageObj_GetFillColor(raw, *rgba):
        return sum(abs(a - v.value) for a, v in zip(x.fill_color, rgba, strict=False)) < 150
    return True


def _pagina(doc, i, md, eh_ocr, itens_pg, pasta, tarefas, cache, seguranca):
    """Roteamento de uma página: texto, regiões visuais, tabela duvidosa, fórmula e texto oculto. Chamar com a trava."""
    page = doc[i]
    W, H = page.get_size()
    # Imagens vizinhas viram um grupo: texto fatiado em imagens ("Version 8 –
    # March 2025" da capa da Falcon são 6 fatias) e logotipo pequeno contam.
    imgs = [list(bx) for tipo, _raw, bx in objetos(page) if tipo == c.FPDF_PAGEOBJ_IMAGE]
    ocultos = texto_oculto_pdf(page, [x for x in itens_pg if not x.text.startswith("[Image:")], imgs, W, H)
    alt = texto_pdfium(page) if eh_ocr else ""
    vistos = re.sub(r"\s+", " ", " ".join(x.text for x in itens_pg)).lower()
    sobrou = []
    if ocultos:
        md, alt_limpo, sobrou = _sem_ocultos(doc, i, [x for _, x in ocultos])
        alt = alt_limpo if eh_ocr else ""
        ids = {id(x) for _, x in ocultos}
        itens_pg = [x for x in itens_pg if id(x) not in ids]
    origem_texto = "pdf-inspector"
    if eh_ocr and len(alt.strip()) > 200 and lixo(alt) < 0.02:
        md, origem_texto = alt, "pdfium"  # p.ex. a p.4 do manual da Falcon
    dens = densidade_matematica(md)
    md, tabelas = limpar_tabelas(desifenizar(md))
    # só tabela nativa CONFIÁVEL anula um desenho; tabela duvidosa pode ser rótulo de gráfico
    palavras_tabela = set().union(*[tb["palavras"] for tb in tabelas if not tb["complexa"]]) if tabelas else set()
    tabela_de = {}
    # regiões: raster + vetor
    regioes = []
    grupos = []
    for bx in imgs:
        mudou = True
        while mudou:
            mudou = False
            for g in grupos:
                if not (bx[2] + 8 < g[0] or g[2] + 8 < bx[0] or bx[3] + 2 < g[1] or g[3] + 2 < bx[1]):
                    bx = [min(bx[0], g[0]), min(bx[1], g[1]), max(bx[2], g[2]), max(bx[3], g[3])]
                    grupos.remove(g)
                    mudou = True
                    break
        grupos.append(bx)
    for l, b, r, t in sorted(grupos, key=lambda g: -g[3]):
        linha_de_texto = (t - b) >= 8 and (r - l) / max(t - b, 1) >= 4
        if (r - l) * (t - b) / (W * H) >= 0.005 or linha_de_texto:
            regioes.append(("imagem", [l, b, r, t]))
    registro = [(m, x.text) for m, x in ocultos]
    # Texto que o extrator descartou (ex.: modo de renderização invisível) mas a camada de texto tem:
    # não vai para o corpo, mas é sinal de ataque quando a página não é um scan.
    if not imgs:
        for ln in texto_pdfium(page).splitlines():
            ln = re.sub(r"\s+", " ", ln).strip()
            if len(ln) >= 20 and ln.lower() not in vistos and ln.lower()[:20] not in vistos:
                registro.append(("não renderizado (invisível)", ln))
    if registro:
        # Item oculto sem objeto de texto que o tire da cópia, ou texto não renderizado que o extrator pôs no corpo:
        # o corpo pode ter o oculto, e quem chama precisa saber.
        corpo = re.sub(r"\s+", " ", md).lower()
        ficou = sobrou or any(m == "não renderizado (invisível)" and t.lower() in corpo for m, t in registro)
        seguranca.append(
            {
                "pagina": i + 1,
                "tecnicas": sorted({m for m, _ in registro}),
                "texto": " ".join(t for _, t in registro)[:500],
            }
            | ({"alerta": f"texto oculto que pode ter ficado no corpo: página {i + 1}"} if ficou else {})
        )
    # Tabela nativa duvidosa: recorte da tabela vai para a IA (com o nativo como âncora).
    for k, tb in enumerate(tabelas):
        if not tb["complexa"]:
            continue
        dentro = [
            x
            for x in itens_pg
            if not x.text.startswith("[Image:")
            and set(re.findall(r"\w+", x.text.lower()))
            and set(re.findall(r"\w+", x.text.lower())) <= tb["palavras"]
        ]
        if len(dentro) >= 4:
            ys = sorted(x.y for x in dentro)
            lo, hi = ys[len(ys) // 20], ys[-1 - len(ys) // 20]
            caixa = [min(x.x for x in dentro) - 4, lo - 6, W - 30, hi + 14]
            caixa[0] = max(20, min(caixa[0], 60))
            tabela_de[id(caixa)] = k
            regioes.append(("tabela", caixa))
    for g in regioes_vetoriais(page, W, H, c):
        if any(_sobrepoe(g, rr) > 0.5 for tp, rr in regioes if tp == "imagem"):
            continue
        # desenho sobre "tabela duvidosa": a tabela eram os rótulos do gráfico — o desenho fica
        for tp, rr in list(regioes):
            if tp == "tabela" and _sobrepoe(rr, g) > 0.5:
                regioes.remove((tp, rr))
                tabelas[tabela_de[id(rr)]]["md"] = ""
        # Tabela desenhada já sai como tabela no nativo: não é figura.
        dentro = re.findall(
            r"\w+", " ".join(x.text for x in itens_pg if g[0] <= x.x <= g[2] and g[1] <= x.y <= g[3]).lower()
        )
        if dentro and sum(w in palavras_tabela for w in dentro) / len(dentro) > 0.6:
            continue
        # Rótulos de eixo, legenda e título ficam fora dos traços: amplia para o texto vizinho.
        for _ in range(2):
            perto = [x for x in itens_pg if g[0] - 40 <= x.x <= g[2] + 40 and g[1] - 30 <= x.y <= g[3] + 30]
            if perto:
                g = [
                    min(g[0], min(x.x for x in perto)),
                    min(g[1], min(x.y for x in perto) - 4),
                    max(g[2], max(x.x + x.width for x in perto)),
                    max(g[3], max(x.y + x.height for x in perto)),
                ]
        regioes.append(("desenho", g))
    # Um gráfico partido em pedaços (rótulos entre as barras) volta a ser uma região só.
    mudou = True
    while mudou:
        mudou = False
        des = [k for k, (tp, _) in enumerate(regioes) if tp == "desenho"]
        for a in des:
            for b in des:
                if a < b:
                    A, Bx = regioes[a][1], regioes[b][1]
                    if not (A[2] + 20 < Bx[0] or Bx[2] + 20 < A[0] or A[3] + 20 < Bx[1] or Bx[3] + 20 < A[1]):
                        regioes[a] = (
                            "desenho",
                            [min(A[0], Bx[0]), min(A[1], Bx[1]), max(A[2], Bx[2]), max(A[3], Bx[3])],
                        )
                        del regioes[b]
                        mudou = True
                        break
            if mudou:
                break
    # Tabela nativa que cai quase toda dentro de um gráfico são os rótulos dele: sai do texto
    # (a leitura do gráfico traz os valores no lugar certo).
    for k, tb in enumerate(tabelas):
        caixas = [g for tp, g in regioes if tp in ("desenho", "imagem")]
        itens_tb = [
            x
            for x in itens_pg
            if set(re.findall(r"\w+", x.text.lower())) and set(re.findall(r"\w+", x.text.lower())) <= tb["palavras"]
        ]
        if (
            caixas
            and itens_tb
            and sum(any(g[0] - 4 <= x.x <= g[2] + 4 and g[1] - 4 <= x.y <= g[3] + 4 for g in caixas) for x in itens_tb)
            / len(itens_tb)
            > 0.7
        ):
            tb["md"] = ""
            tb["complexa"] = False
            regioes[:] = [(tp, g) for tp, g in regioes if not (tp == "tabela" and tabela_de.get(id(g)) == k)]
    lixo_nativo = lixo(md) if md.strip() else 0.0
    # fórmulas escritas como texto (sub/sobrescrito, Σ, ×, ÷): o nativo embaralha a notação
    formulas = [
        b
        for b in md.split("\n\n")
        if len(b) >= 15
        and "|" not in b
        and "=" in b
        and (sum(b.count(ch) for ch in "=×÷Σ∑·±√∫≤≥≈∞^") + 3 * len(re.findall(r"<su[bp]>", b))) / len(b) >= 0.02
    ]
    precisa_pagina = (eh_ocr and origem_texto != "pdfium") or dens >= 4.0 or lixo_nativo > 0.02
    # Fórmula escrita como texto: só o RECORTE do bloco vai para a IA (a página de texto não é retranscrita).
    formula_de, originais_formula = {}, []
    if formulas and not precisa_pagina:
        itens_txt = [x for x in itens_pg if not x.text.startswith("[Image:")]
        for bloco in formulas:
            limpo = re.sub(r"</?su[bp]>|[*_]", "", bloco)
            dentro = [x for x in itens_txt if len(x.text.strip()) >= 1 and x.text.strip() in limpo]
            if not dentro:
                continue
            ys = [x.y for x in dentro]
            caixa = [
                max(20, min(x.x for x in dentro) - 6),
                min(ys) - 8,
                min(W - 10, max(x.x + x.width for x in dentro) + 6),
                max(x.y + x.height for x in dentro) + 8,
            ]
            k = len(originais_formula)
            originais_formula.append(bloco)
            md = md.replace(bloco, f"{{{{FORMULA:{k}}}}}", 1)
            formula_de[id(caixa)] = k
            regioes.append(("formula", caixa))
    rota = "pagina" if precisa_pagina else ("regioes" if regioes else "nativa")
    info = {
        "i": i,
        "rota": rota,
        "densidade_mat": round(dens, 2),
        "origem_texto": origem_texto,
        "md": md,
        "tabelas": [tb["md"] for tb in tabelas],
        "formulas": originais_formula,
        "regioes": [],
    }
    if rota == "pagina":
        # Duas metades com sobreposição: o dobro de resolução por leitura (o provedor reduz acima de ~1568 px).
        img = page.render(scale=2 * 1568 / max(W, H) if H >= W else 1568 / max(W, H)).to_pil().convert("RGB")
        metades = [(0, img.height)]
        if H >= W:
            # Linhas de texto pela projeção horizontal; só divide página densa, numa faixa sem tinta.
            cinza = img.convert("L").resize((img.width // 4, img.height // 4))
            larg, alt = cinza.size
            px = cinza.load()
            tinta = [sum(1 for x in range(0, larg, 2) if px[x, y] < 160) for y in range(alt)]
            linhas_txt = sum(1 for y in range(1, alt) if tinta[y] > 2 and tinta[y - 1] <= 2)
            if linhas_txt > 55:
                meio = alt // 2
                vazias = [y for y in range(int(alt * 0.35), int(alt * 0.65)) if tinta[y] <= 1]
                corte = 4 * (min(vazias, key=lambda y: abs(y - meio)) if vazias else meio)
                metades = [(0, min(img.height, corte + 24)), (max(0, corte - 24), img.height)]
        motivo = (
            "escaneada"
            if eh_ocr
            else ("texto nativo corrompido" if lixo_nativo > 0.02 else f"matemática densa ({dens:.1f})")
        )
        info["tarefas"] = []
        for s, (y0, y1) in zip("ab", metades, strict=False):
            tid = f"p{i + 1:03d}{s if len(metades) > 1 else ''}"
            img.crop((0, y0, img.width, y1)).save(f"{pasta}/ia/{tid}.png")
            frac = (y0 / img.height, y1 / img.height)
            tarefas.append(
                {
                    "id": tid,
                    "modo": "pagina",
                    "parte": f"{'ab'.index(s) + 1}/{len(metades)}",
                    "motivo": motivo,
                    "ancora": md[:6000],
                    "pagina": i,
                    "caixa": [0, H * (1 - frac[1]), W, H * (1 - frac[0])],
                }
            )
            info["tarefas"].append(tid)
    elif rota == "regioes":
        fator = escala(W, H, 2.0)
        full = page.render(scale=fator).to_pil().convert("RGB")
        itens = [t for t in itens_pg if not t.text.startswith("[Image:")]
        for j, (tipo, cx) in enumerate(regioes, 1):
            l, b, r, t = cx
            m = 6
            box = (
                max(0, int((l - m) * fator)),
                max(0, int((H - t - m) * fator)),
                min(full.width, int((r + m) * fator)),
                min(full.height, int((H - b + m) * fator)),
            )
            tid = f"p{i + 1:03d}-r{j}"
            recorte = full.crop(box)
            chave = hashlib.sha256(recorte.tobytes()).hexdigest()
            if chave in cache:  # mesma imagem já lida (logo do cabeçalho etc.)
                info["regioes"].append({"tarefa": cache[chave], "depois_de": "", "texto_dentro": "", "repetida": True})
                continue
            cache[chave] = tid
            recorte.save(f"{pasta}/ia/{tid}.png")
            dentro = [x for x in itens if l - 2 <= x.x <= r + 2 and b - 2 <= x.y <= t + 2]
            ancora = " ".join(x.text for x in sorted(dentro, key=lambda x: (-x.y, x.x)))
            if tipo == "tabela":
                ancora = tabelas[tabela_de[id(cx)]]["md"]
            if tipo == "formula":
                ancora = originais_formula[formula_de[id(cx)]]
            acima = [x for x in itens if x.y >= t and not (l <= x.x <= r and b <= x.y <= t)]
            linha_acima = min(acima, key=lambda x: x.y).text.strip()[:25] if acima else ""
            tarefas.append(
                {
                    "id": tid,
                    "modo": tipo,
                    "ancora": ancora[:3000],
                    "pagina": i,
                    "caixa": [l - m, b - m, r + m, t + m],
                }
            )
            info["regioes"].append(
                {
                    "tarefa": tid,
                    "depois_de": linha_acima,
                    "texto_dentro": ancora,
                    **({"substitui_tabela": tabela_de[id(cx)]} if tipo == "tabela" else {}),
                    **({"substitui_formula": formula_de[id(cx)]} if tipo == "formula" else {}),
                }
            )
    return info


def _sobrepoe(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = (a[2] - a[0]) * (a[3] - a[1]) or 1
    return ix * iy / area


def _bounds(raw):
    l, b, r, t = (ctypes.c_float() for _ in range(4))
    c.FPDFPageObj_GetBounds(raw, l, b, r, t)
    return l.value, b.value, r.value, t.value


def _matrix(raw):
    m = c.FS_MATRIX()
    c.FPDFPageObj_GetMatrix(raw, m)
    return (m.a, m.b, m.c, m.d, m.e, m.f)


def _compose(m1, m2):  # aplica m1 e depois m2
    a, b, cc, d, e, f = m1
    A, B, C, D, E, F = m2
    return (a * A + b * C, a * B + b * D, cc * A + d * C, cc * B + d * D, e * A + f * C + E, e * B + f * D + F)


def _transform(box, m):
    l, b, r, t = box
    pts = [(l, b), (l, t), (r, b), (r, t)]
    xs = [m[0] * x + m[2] * y + m[4] for x, y in pts]
    ys = [m[1] * x + m[3] * y + m[5] for x, y in pts]
    return min(xs), min(ys), max(xs), max(ys)


def objetos(page):
    """Gera (tipo, raw, caixa_na_pagina)."""

    def walk(raws, m):
        for raw in raws:
            tipo = c.FPDFPageObj_GetType(raw)
            box = _bounds(raw)
            yield tipo, raw, (_transform(box, m) if m else box)
            if tipo == c.FPDF_PAGEOBJ_FORM:
                mm = _compose(_matrix(raw), m) if m else _matrix(raw)
                n = c.FPDFFormObj_CountObjects(raw)
                yield from walk((c.FPDFFormObj_GetObject(raw, k) for k in range(n)), mm)

    n = c.FPDFPage_CountObjects(page.raw)
    yield from walk((c.FPDFPage_GetObject(page.raw, k) for k in range(n)), None)
