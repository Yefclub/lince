"""PDF: texto nativo por página, regiões visuais (raster e vetor), tabela duvidosa e roteamento."""

import ctypes
import hashlib  # noqa: F401
import io  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import zipfile  # noqa: F401
from xml.etree import ElementTree as ET  # noqa: F401

import pypdfium2.raw as c  # noqa: F401

from ._prazo import prazo_restante
from .limites import escala
from .seguranca import remover_ocultos, texto_oculto_pdf
from .texto import densidade_matematica, desifenizar, limpar_tabelas, lixo

PROMPT_VERSAO = "leitura-ia-v1"
# Teto da análise visual (desenho da página, figuras, texto oculto) por documento: medido em ~15 ms por página de
# texto, 2000 páginas cabem em ~30 s. Acima dele, e depois do prazo do documento, a página entra só com o texto.
PAGINAS_ANALISE_MAX = 2000


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
    import pdf_inspector
    import pypdfium2 as pdfium

    res = pdf_inspector.extract_pages_markdown(path)
    ocr = {p - 1 for p in res.pages_needing_ocr}
    posicoes = pdf_inspector.extract_text_with_positions(path)
    doc = pdfium.PdfDocument(path)
    paginas, tarefas = [], []
    cache = {}
    seguranca = []
    so_texto = {}  # página -> motivo de ela ter entrado só com o texto (teto ou prazo)
    for i, pg in enumerate(res.pages):
        if i >= PAGINAS_ANALISE_MAX or ((resta := prazo_restante()) is not None and resta <= 0):
            so_texto[i] = f"teto de {PAGINAS_ANALISE_MAX} páginas" if i >= PAGINAS_ANALISE_MAX else "prazo esgotado"
            md, tabelas = limpar_tabelas(desifenizar(pg.markdown))
            paginas.append({"i": i, "rota": "nativa", "densidade_mat": 0, "origem_texto": "pdf-inspector", "md": md,
                            "tabelas": [tb["md"] for tb in tabelas], "formulas": [], "regioes": []})  # fmt: skip
            continue
        page = doc[i]
        W, H = page.get_size()
        md = pg.markdown
        origem_texto = "pdf-inspector"
        if i in ocr:
            alt = texto_pdfium(page)
            if len(alt.strip()) > 200 and lixo(alt) < 0.02:
                md, origem_texto = alt, "pdfium"  # p.ex. a p.4 do manual da Falcon
        dens = densidade_matematica(md)
        md, tabelas = limpar_tabelas(desifenizar(md))
        # só tabela nativa CONFIÁVEL anula um desenho; tabela duvidosa pode ser rótulo de gráfico
        palavras_tabela = set().union(*[tb["palavras"] for tb in tabelas if not tb["complexa"]]) if tabelas else set()
        tabela_de = {}
        # regiões: raster + vetor
        regioes = []
        # Imagens vizinhas viram um grupo: texto fatiado em imagens ("Version 8 –
        # March 2025" da capa da Falcon são 6 fatias) e logotipo pequeno contam.
        imgs = [list(bx) for tipo, _raw, bx in objetos(page) if tipo == c.FPDF_PAGEOBJ_IMAGE]
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
        itens_pg = [t for t in posicoes if t.page == i + 1 and t.text.strip()]
        ocultos = texto_oculto_pdf(page, [x for x in itens_pg if not x.text.startswith("[Image:")], imgs, W, H)
        # Texto que o extrator descartou (ex.: modo de renderização invisível) mas a camada de texto tem:
        # não vai para o corpo, mas é sinal de ataque quando a página não é um scan.
        if not imgs:
            vistos = re.sub(r"\s+", " ", " ".join(x.text for x in itens_pg)).lower()
            for ln in texto_pdfium(page).splitlines():
                ln = re.sub(r"\s+", " ", ln).strip()
                if len(ln) >= 20 and ln.lower() not in vistos and ln.lower()[:20] not in vistos:
                    ocultos.append(("não renderizado (invisível)", ln))
        if ocultos:
            md, removidos = remover_ocultos(md, ocultos)
            itens_pg = [x for x in itens_pg if x.text not in {t for _, t in ocultos}]
            seguranca.append(
                {
                    "pagina": i + 1,
                    "tecnicas": sorted({m for m, _ in ocultos}),
                    "texto": " ".join(t for _, t in ocultos)[:500],
                }
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
                and sum(
                    any(g[0] - 4 <= x.x <= g[2] + 4 and g[1] - 4 <= x.y <= g[3] + 4 for g in caixas) for x in itens_tb
                )
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
        precisa_pagina = (i in ocr and origem_texto != "pdfium") or dens >= 4.0 or lixo_nativo > 0.02
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
                if i in ocr
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
                    info["regioes"].append(
                        {"tarefa": cache[chave], "depois_de": "", "texto_dentro": "", "repetida": True}
                    )
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
        paginas.append(info)
    pendentes = []
    for motivo in dict.fromkeys(so_texto.values()):
        ns = [i + 1 for i, m in so_texto.items() if m == motivo]
        faixa = f"página {ns[0]}" if len(ns) == 1 else f"páginas {ns[0]}–{ns[-1]}"
        pendentes.append(f"{faixa}: só o texto, sem figuras nem páginas escaneadas lidas ({motivo})")
        seguranca.append(  # o texto oculto dessas páginas não foi conferido: suspeita, nunca "nada oculto"
            {"tecnicas": ["não conferido"], "texto": "", "alerta": f"texto oculto não conferido: {faixa} ({motivo})"}
        )
    return {"tipo": "pdf", "paginas": paginas, "tarefas": tarefas, "texto_oculto": seguranca, "pendentes": pendentes}


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
