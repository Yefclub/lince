"""PDF: texto nativo por página, regiões visuais (raster e vetor), tabela duvidosa e roteamento."""

import ctypes
import hashlib  # noqa: F401
import io
import json  # noqa: F401
import math
import os  # noqa: F401
import re  # noqa: F401
import time
import zipfile  # noqa: F401
from collections import defaultdict, deque
from xml.etree import ElementTree as ET  # noqa: F401

import pypdfium2 as pdfium
import pypdfium2.raw as c  # noqa: F401

from ._prazo import prazo_restante
from ._trava import PDFIUM
from .limites import escala
from .seguranca import NAO_CONFERIDO, texto_oculto_pdf
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
# documento inteiro a cada vez, mesmo pedindo uma página (medido: 4 s por chamada num PDF de 2.500 páginas). Ele só lê
# cópias de FAIXA páginas feitas pela PDFium (~0,05 s cada), nunca o arquivo, e roda fora da trava: o prazo é
# conferido entre elas, um PDF que chega no meio não espera o grande, e as páginas lidas são as que a PDFium abre, as
# mesmas que o leitor mostra.
FAIXA = 50
# Tempo máximo com a trava da PDFium antes de dar a vez a outro PDF (a trava é justa: quem espera entra antes de
# quem acabou de soltar). Com a trava só rodam chamadas à PDFium, de custo limitado por ela mesma: abrir, coletar os
# objetos, extrair o texto, copiar e renderizar (no máximo LADO_MAX_PX). Toda a análise em Python roda fora dela. Uma
# página pesada (escaneada, renderizada em 1568 px) passa dele sozinha; a de texto corrido usa ~2 ms.
VEZ_S = 0.5
# O prazo é do documento inteiro, e a montagem do texto extraído vem depois: ela custa ~1/4 do tempo da extração do
# texto corrido (medido: 0,8 s para as 650 páginas lidas em 3 s). Uma faixa nova só começa se o que resta do prazo
# cobre essa parte; a proporção vale em máquina lenta ou rápida, porque as duas etapas são CPU em Python.
RESERVA_MONTAGEM = 0.3
# Teto do trabalho para tirar o texto oculto de uma página pela posição, contado em células da grade visitadas e em
# comparações entre um item oculto e um objeto de texto. A grade compara cada item só com os objetos cujo centro cai
# na caixa dele: 10 mil itens ocultos espalhados numa página custam ~40 mil. O teto segura a página hostil, com
# milhares de itens sobrepostos no mesmo lugar: acima dele, a página não é copiada (nenhum objeto sai pela PDFium)
# e fica com o texto como está, e o oculto, com o alerta e a pendência.
OCULTOS_COMPARACOES_MAX = 100_000
# Lado da célula da grade dos objetos de texto, em pontos: menos que uma linha de texto corrido.
CELULA = 8
# Teto de objetos por página (com os de dentro das formas, contados a cada vez que a forma é desenhada): coletar cada
# um pela PDFium custa ~3 µs com a trava (medido: 200 mil desenhos em 0,5 s), e uma forma desenhada mil vezes
# multiplica o percurso. Página real tem poucos milhares; acima do teto, ela entra só com o texto, com a pendência e o
# alerta de texto oculto não conferido, e o próximo PDF não espera.
OBJETOS_MAX = 100_000
# Teto de pixels dos fundos guardados de uma vez: a coleta das páginas de uma vez fica na memória até a análise de cada
# uma, e uma folha gigante tem um fundo de até LADO_MAX_PX × LADO_MAX_PX (300 MB em RGB). 20 milhões (~60 MB) cobrem
# as 50 páginas A4 de uma faixa (0,13 milhão cada); passando dele, a vez acaba, e a página seguinte fica para a próxima.
FUNDOS_PX_MAX = 20_000_000
PESADA = "conteúdo demais para a análise visual"


def texto_pdfium(page):
    tp = page.get_textpage()
    return tp.get_text_bounded()


def regioes_vetoriais(objs, W, H):
    """Agrupa desenhos (path) que não são régua de tabela nem faixa de linha."""
    caixas, faixas = [], []
    for tipo, (l, b, r, t) in objs:
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
    durante a leitura de um grande entra na próxima vez. Com a trava só rodam chamadas à PDFium; cada página passa por
    fases: a coleta (objetos, fundo renderizado, texto pela PDFium; `_coleta`, com a trava), a análise e a decisão da
    rota e das regiões (`_pagina`, sem a trava), a cópia sem o texto oculto ou a renderização na escala da leitura
    (com a trava, se preciso) e os recortes e os arquivos (sem a trava). O documento é aberto uma vez só, na vez que
    lê a contagem de páginas, as propriedades e a cópia da primeira faixa (abrir um PDF com a tabela de referências
    danificada a reconstrói, e isso não pode se repetir a cada vez), e fechado dentro da trava, mesmo com exceção: o
    finalizador dele (e o das páginas) não pode rodar fora dela, em paralelo com outro PDF. O pdf_inspector não usa a
    PDFium e roda fora da trava, sobre as cópias."""
    inicio = time.monotonic()
    doc = None
    try:
        with PDFIUM:
            fim = time.monotonic() + VEZ_S
            doc = pdfium.PdfDocument(path)
            # O /Count declarado: a PDFium confia nele, e as páginas do arquivo que ficam fora dele não abrem (como num
            # visualizador). Elas não são lidas nem contadas: só a árvore de páginas inteira diria quantas são, e
            # percorrê-la é o que o pdf_inspector faz com o arquivo, sem teto nem prazo.
            total = len(doc)
            propriedades = _propriedades(doc)
            lidas = min(total, PAGINAS_TEXTO_MAX)
            vez = _vez_da_faixa(doc, range(min(FAIXA, lidas)), fim) if lidas else None  # na mesma vez
        paginas, tarefas, cache, seguranca, pendentes = [], [], {}, [], []
        so_texto, nao_lidas = [], []  # [início, fim, motivo]: páginas só com o texto e páginas não lidas
        parada = None  # a primeira página que a PDFium não abre
        for a in range(0, lidas, FAIXA):
            # a primeira faixa sempre entra: o documento nunca sai sem texto por causa do prazo
            resta = prazo_restante()
            if a and resta is not None and resta <= RESERVA_MONTAGEM * (time.monotonic() - inicio):
                _marcar(nao_lidas, a, lidas, "prazo esgotado")
                break
            faixa = range(a, min(a + FAIXA, lidas))
            if a:
                with PDFIUM:
                    vez = _vez_da_faixa(doc, faixa, time.monotonic() + VEZ_S)
            (dados, abertas), coletas = vez
            md_de, ocr, itens_de = _extrair(dados, a)  # o pdf_inspector, fora da trava
            fila = faixa[:abertas]
            if abertas < len(faixa):
                parada = faixa[abertas]
            k = 0
            while k < len(fila):
                i = fila[k]
                if motivo := _so_texto(i):  # a análise não começa depois do prazo, mesmo com a página já coletada
                    coletas.clear()  # as seguintes também entram só com o texto
                    _marcar(so_texto, i, i + 1, motivo)
                    paginas.append(_so_o_texto(i, md_de.get(i, "")))
                    k += 1
                    continue
                if not coletas:
                    with PDFIUM:  # a vez: a coleta desta página e das seguintes, até o fim da vez
                        coletas = _coletas(doc, fila[k:], time.monotonic() + VEZ_S)
                col = coletas.popleft()
                if col is None:  # a página não abre: a leitura para nela
                    parada, fila = i, fila[:k]
                    break
                if col == PESADA:
                    _marcar(so_texto, i, i + 1, PESADA)
                    paginas.append(_so_o_texto(i, md_de.get(i, "")))
                    recorte = None
                else:
                    info, recorte = _pagina(doc, i, col, md_de.get(i, ""), i in ocr, itens_de.get(i, []), pasta,
                                            tarefas, cache, seguranca, pendentes)  # fmt: skip
                    paginas.append(info)
                k += 1
                ultima = k == len(fila) and (parada is not None or faixa[-1] + 1 == lidas)
                if recorte or ultima:
                    with PDFIUM:
                        img = _renderizar(doc, i, recorte[0]) if recorte else None
                        if ultima:
                            doc.close()  # na mesma vez: um PDF curto pega a trava só duas vezes
                            doc = None
                    if recorte:
                        recorte[1](img)  # os recortes e os arquivos, fora da trava, antes da próxima página
            if parada is not None:
                _marcar(nao_lidas, parada, total, f"a página {parada + 1} não abre")
                break
        if parada is None:
            _marcar(nao_lidas, lidas, total, f"teto de {PAGINAS_TEXTO_MAX} páginas de texto")
    finally:
        if doc is not None:
            with PDFIUM:
                doc.close()
    for faixa, motivo in _faixas(so_texto):
        pendentes.append(f"{faixa}: só o texto, sem figuras nem páginas escaneadas lidas ({motivo})")
        seguranca.append(  # o texto oculto dessas páginas não foi conferido: suspeita, nunca "nada oculto"
            {"tecnicas": ["não conferido"], "texto": "", "alerta": f"{NAO_CONFERIDO}: {faixa} ({motivo})"}
        )
    pendentes += [f"{faixa}: não lidas ({motivo})" for faixa, motivo in _faixas(nao_lidas)]
    plano = {"tipo": "pdf", "paginas": paginas, "paginas_total": total, "tarefas": tarefas, "texto_oculto": seguranca,
             "pendentes": pendentes}  # fmt: skip
    if propriedades:
        plano["propriedades"] = propriedades
    return plano


def _vez_da_faixa(doc, faixa, fim):
    """A cópia da faixa para o pdf_inspector (`_copia_da_faixa`) e, na mesma vez, a coleta das primeiras páginas dela
    (`_coletas`), que não depende do texto dele. Chamar com a trava."""
    copia = _copia_da_faixa(doc, faixa)
    abertas = copia[1]
    coletas = _coletas(doc, faixa[:abertas], fim) if abertas and not _so_texto(faixa[0]) else deque()
    return copia, coletas


def _coletas(doc, paginas, fim):
    """A coleta (`_coleta`) da primeira página e das seguintes enquanto a vez não acaba (`fim`) e os fundos cabem em
    FUNDOS_PX_MAX, até a que entra só com o texto, que fica para quem chama, e até a que não abre, que fica no fim, como
    None. Chamar com a trava."""
    feitas, pixels = deque(), 0
    for k, i in enumerate(paginas):
        if k and (time.monotonic() >= fim or pixels >= FUNDOS_PX_MAX or _so_texto(i)):
            break
        feitas.append(col := _coleta(doc, i))
        if col is None:
            break
        if col != PESADA:
            pixels += col["fundo"].width * col["fundo"].height
    return feitas


def _coleta(doc, i):
    """O que a análise da página `i` precisa da PDFium: o tamanho, a origem das posições, o tipo e a caixa de cada
    objeto, a contagem dos objetos da página, o fundo renderizado (o texto oculto) e o texto pela PDFium. Só dados do
    Python, sem nada que dependa da PDFium: a análise roda depois, fora da trava. Chamar com a trava; a página é
    fechada aqui. None se a página não abre (o /Count declarado passa das páginas do arquivo); PESADA acima de
    OBJETOS_MAX."""
    try:
        page = pagina_visivel(doc, i)
    except pdfium.PdfiumError:
        return None
    try:
        objs = []
        for tipo, _raw, caixa in objetos(page):  # para no primeiro objeto além do teto
            if len(objs) == OBJETOS_MAX:
                return PESADA
            objs.append((tipo, caixa))
        W, H = page.get_size()
        return {
            "W": W,
            "H": H,
            "origem": _origem_visivel(page),
            "objs": objs,
            "n": c.FPDFPage_CountObjects(page.raw),
            "fundo": page.render(scale=escala(W, H, 0.5)).to_pil().convert("RGB"),  # uma cópia, sem a PDFium
            "texto": texto_pdfium(page),
        }
    finally:
        page.close()


def _renderizar(doc, i, fator):
    """A página `i` renderizada na escala `fator` e convertida pelo Pillow: uma cópia, que não depende mais da PDFium.
    Chamar com a trava."""
    page = pagina_visivel(doc, i)
    try:
        return page.render(scale=fator).to_pil().convert("RGB")
    finally:
        page.close()


def _propriedades(doc):
    """Propriedades do PDF (título, autor, produtor), sem as vazias. Chamar com a trava."""
    try:
        return {k: v for k, v in doc.get_metadata_dict().items() if v}
    except Exception:  # noqa: BLE001 — PDF sem propriedades legíveis: segue sem elas
        return {}


def _marcar(intervalos, a, b, motivo):
    """Junta as páginas [a, b) com o motivo aos intervalos [início, fim, motivo], emendando no último quando seguem
    dele com o mesmo motivo. Guarda intervalos, não páginas: um /Count de um milhão não vira um milhão de entradas."""
    if a >= b:
        return
    if intervalos and intervalos[-1][1] == a and intervalos[-1][2] == motivo:
        intervalos[-1][1] = b
    else:
        intervalos.append([a, b, motivo])


def _faixas(intervalos):
    """(faixa de páginas, motivo) de cada intervalo [início, fim, motivo]."""
    for a, b, motivo in intervalos:
        yield (f"página {a + 1}" if b - a == 1 else f"páginas {a + 1}–{b}"), motivo


def _vencido():
    return (resta := prazo_restante()) is not None and resta <= 0


def _so_texto(i):
    """Motivo de a página entrar só com o texto (teto da análise ou prazo), ou None."""
    if i >= PAGINAS_ANALISE_MAX:
        return f"teto de {PAGINAS_ANALISE_MAX} páginas"
    return "prazo esgotado" if _vencido() else None


def _so_o_texto(i, md):
    """A página só com o texto do pdf_inspector: sem figuras, páginas escaneadas nem texto oculto conferido."""
    md, tabelas = limpar_tabelas(desifenizar(md))
    return {"i": i, "rota": "nativa", "densidade_mat": 0, "origem_texto": "pdf-inspector", "md": md,
            "tabelas": [tb["md"] for tb in tabelas], "formulas": [], "regioes": []}  # fmt: skip


def _extrair(dados, a):
    """Texto das páginas da cópia de uma faixa (`dados`, de `_copia_da_faixa`) pelo pdf_inspector: markdown, páginas
    para OCR e itens com posição, pelo número da página no documento (a faixa começa na página `a`). Ele lê a cópia,
    nunca o arquivo, e não usa a PDFium: roda fora da trava."""
    import pdf_inspector

    if not dados:
        return {}, set(), {}
    res = pdf_inspector.extract_pages_markdown_bytes(dados)
    posicoes = pdf_inspector.extract_text_with_positions_bytes(dados)
    itens_de = defaultdict(list)
    for t in posicoes:
        if t.text.strip():
            itens_de[a + t.page - 1].append(t)
    return {a + pg.page: pg.markdown for pg in res.pages}, {a + p - 1 for p in res.pages_needing_ocr}, itens_de


def _copia_da_faixa(doc, faixa):
    """Bytes da cópia das páginas da faixa e quantas entraram: até a anterior à primeira que não abre, quando o /Count
    declarado passa das páginas que o arquivo tem. Chamar com a trava."""
    try:
        return _copia(doc, faixa), len(faixa)
    except pdfium.PdfiumError:
        abertas = next((k for k, i in enumerate(faixa) if not _abre(doc, i)), None)
        if abertas is None:  # todas abrem: a cópia falhou por outro motivo
            raise
        return (_copia(doc, faixa[:abertas]) if abertas else b""), abertas


def _abre(doc, i):
    try:
        doc[i].close()
    except pdfium.PdfiumError:
        return False
    return True


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


def _alvos_ocultos(doc, i, origem, ocultos):
    """Índices, na página `i`, dos objetos de texto dos itens ocultos, e os itens que não saem pela posição: sem objeto
    de texto que coincida, ou com um dentro de uma forma (Form XObject). A PDFium não regrava o conteúdo da forma, e a
    mesma forma pode estar desenhada também num lugar visível. `origem` é a da coleta (`_origem_visivel`).

    Os objetos de texto são coletados numa vez com a trava; a comparação roda fora dela. Cada item só é comparado com
    os objetos cujo centro cai na caixa dele (quem cobre metade de um objeto cobre o centro dele), achados numa grade.
    Acima de `OCULTOS_COMPARACOES_MAX`, nenhum item sai pela posição."""
    with PDFIUM:
        textos = _objetos_de_texto(doc, i)
    l0, b0 = origem
    grade = defaultdict(list)
    for o in textos:
        l, b, r, t = o[1]
        cx, cy = (l + max(r, l + 0.01)) / 2, (b + max(t, b + 0.01)) / 2
        if math.isfinite(cx) and math.isfinite(cy):
            grade[int(cx // CELULA), int(cy // CELULA)].append(o)
    alvos, sobrou, custo = set(), [], 0
    for x in ocultos:
        l, b, r, t = _caixa_do_item(x, l0, b0)
        n = ((r - l) / CELULA + 2) * ((t - b) / CELULA + 2)  # células na caixa; inf ou nan num item hostil
        if n <= len(grade):
            celulas = [(cx, cy) for cx in range(int(l // CELULA), int(r // CELULA) + 1)
                       for cy in range(int(b // CELULA), int(t // CELULA) + 1)]  # fmt: skip
        else:  # caixa maior que a grade: percorre as células ocupadas
            celulas = [k for k in grade if k[0] * CELULA <= r and (k[0] + 1) * CELULA >= l
                       and k[1] * CELULA <= t and (k[1] + 1) * CELULA >= b]  # fmt: skip
        perto = [grade[cel] for cel in celulas if cel in grade]
        custo += (len(celulas) if n <= len(grade) else len(grade)) + sum(map(len, perto))
        if custo > OCULTOS_COMPARACOES_MAX:
            return set(), list(ocultos)
        achou = [o for objs in perto for o in objs if _coincide(o, x, l0, b0)]
        if achou and all(k is not None for k, *_ in achou):
            alvos |= {k for k, *_ in achou}
        else:
            sobrou.append(x)
    return alvos, sobrou


def _sem_ocultos(doc, i, alvos, n):
    """Markdown (pdf_inspector) e texto (PDFium) da página `i` tirados de uma cópia dela sem os objetos de texto
    `alvos` (índices na página de `n` objetos), ou None se a cópia não tem os mesmos objetos. O documento original não
    muda. O oculto sai por posição, na origem: um texto visível igual a ele fica inteiro.

    A cópia, a remoção dos objetos (no máximo os `n` da página, abaixo de OBJETOS_MAX), a geração do conteúdo, a
    gravação e o texto pela PDFium pegam a trava; o pdf_inspector lê os bytes da cópia depois, fora dela."""
    import pdf_inspector

    texto = []

    def editar(novo):
        page = novo[0]
        if c.FPDFPage_CountObjects(page.raw) != n:
            return
        for raw in [c.FPDFPage_GetObject(page.raw, k) for k in sorted(alvos)]:  # antes: remover muda os índices
            if c.FPDFPage_RemoveObject(page.raw, raw):
                c.FPDFPageObj_Destroy(raw)
        page.gen_content()
        texto.append(texto_pdfium(page))

    with PDFIUM:
        dados = _copia(doc, [i], editar)
    if not texto:
        return None
    res = pdf_inspector.extract_pages_markdown_bytes(dados)
    return (res.pages[0].markdown if res.pages else ""), texto[0]


def pagina_visivel(doc, i):
    """A página `i` (chamar com a trava). A CropBox que não sobrepõe a MediaBox vale a MediaBox, só na memória: a PDFium
    daria tamanho zero à página, e o pdf_inspector, como outros leitores, a ignora."""
    page = doc[i]
    caixa, cropbox_valeu = _caixa_visivel(page)
    if not cropbox_valeu:
        page.set_cropbox(*caixa)
    return page


def _caixa_visivel(page):
    """A caixa visível da página, como o pdf_inspector mede as posições: CropBox ∩ MediaBox, ou a MediaBox quando a
    CropBox não a sobrepõe (ela é ignorada). Devolve a caixa e se a CropBox valeu."""
    ml, mb, mr, mt = page.get_mediabox()
    cl, cb, cr, ct = page.get_cropbox()
    l, b, r, t = max(ml, cl), max(mb, cb), min(mr, cr), min(mt, ct)
    return ((l, b, r, t), True) if l < r and b < t else ((ml, mb, mr, mt), False)


def _origem_visivel(page):
    """Canto inferior esquerdo da caixa visível, a origem das posições do pdf_inspector."""
    (l, b, _r, _t), _ = _caixa_visivel(page)
    return l, b


def _objetos_de_texto(doc, i):
    """(índice na página ou None dentro de uma forma, caixa na página, modo de pintura, cor de preenchimento RGBA ou
    None) de cada objeto de texto da página `i`, inclusive os de dentro das formas. Chamar com a trava; a página é
    fechada aqui, e a lista não depende mais da PDFium. A coleta já conferiu o teto de objetos da página."""

    def dados(raw, k, caixa):
        rgba = [ctypes.c_uint() for _ in range(4)]
        cor = tuple(v.value for v in rgba) if c.FPDFPageObj_GetFillColor(raw, *rgba) else None
        return k, caixa, c.FPDFTextObj_GetTextRenderMode(raw), cor

    def walk(raws, m):
        for raw in raws:
            tipo = c.FPDFPageObj_GetType(raw)
            if tipo == c.FPDF_PAGEOBJ_TEXT:
                yield dados(raw, None, _transform(_bounds(raw), m))
            elif tipo == c.FPDF_PAGEOBJ_FORM:
                yield from walk(_filhos(raw), _compose(_matrix(raw), m))

    def todos(page):
        for k in range(c.FPDFPage_CountObjects(page.raw)):
            raw = c.FPDFPage_GetObject(page.raw, k)
            tipo = c.FPDFPageObj_GetType(raw)
            if tipo == c.FPDF_PAGEOBJ_TEXT:
                yield dados(raw, k, _bounds(raw))
            elif tipo == c.FPDF_PAGEOBJ_FORM:
                yield from walk(_filhos(raw), _matrix(raw))

    page = pagina_visivel(doc, i)
    try:
        return list(todos(page))
    finally:
        page.close()


def _filhos(forma):
    return [c.FPDFFormObj_GetObject(forma, k) for k in range(c.FPDFFormObj_CountObjects(forma))]


def _caixa_do_item(x, l0, b0):
    """Caixa do item do pdf_inspector na página, com folga para a diferença entre as medidas dele e as da PDFium."""
    folga = 1 + 0.25 * x.height
    return l0 + x.x - folga, b0 + x.y - folga, l0 + x.x + x.width + folga, b0 + x.y + x.height + folga


def _coincide(obj, x, l0, b0):
    """O objeto de texto (de `_objetos_de_texto`) é parte do item: cai quase todo na caixa dele e não tem outra cor nem
    outro modo de pintura (um texto visível desenhado no mesmo lugar fica)."""
    _k, (l, b, r, t), modo, cor = obj
    if _sobrepoe([l, b, max(r, l + 0.01), max(t, b + 0.01)], _caixa_do_item(x, l0, b0)) < 0.5:
        return False
    if (modo == 3) != (x.render_mode == 3):
        return False
    if x.fill_color and cor is not None:
        return sum(abs(a - v) for a, v in zip(x.fill_color, cor, strict=False)) < 150
    return True


def _pagina(doc, i, col, md, eh_ocr, itens_pg, pasta, tarefas, cache, seguranca, pendentes):
    """Roteamento de uma página: texto, regiões visuais, tabela duvidosa, fórmula e texto oculto, sobre a coleta dela
    (`_coleta`). Roda sem a trava: a análise em Python, por mais que custe numa página hostil, não faz outro PDF
    esperar. Só a cópia sem o texto oculto pega a trava, nas vezes dela (`_alvos_ocultos` e `_sem_ocultos`). Devolve a
    página e, se ela vai para a IA, a escala da renderização e a função que faz os recortes, os arquivos e as tarefas
    dela com a imagem renderizada: quem chama renderiza com a trava e recorta sem ela, antes da próxima página."""
    W, H, objs = col["W"], col["H"], col["objs"]
    # Imagens vizinhas viram um grupo: texto fatiado em imagens ("Version 8 –
    # March 2025" da capa da Falcon são 6 fatias) e logotipo pequeno contam.
    imgs = [list(bx) for tipo, bx in objs if tipo == c.FPDF_PAGEOBJ_IMAGE]
    ocultos = texto_oculto_pdf(col["fundo"], [x for x in itens_pg if not x.text.startswith("[Image:")], imgs, W, H)
    alt = col["texto"] if eh_ocr else ""
    vistos = re.sub(r"\s+", " ", " ".join(x.text for x in itens_pg)).lower()
    sobrou = []
    if ocultos:
        alvos, sobrou = _alvos_ocultos(doc, i, col["origem"], [x for _, x in ocultos])
        if alvos and (limpo := _sem_ocultos(doc, i, alvos, col["n"])):
            md, alt_limpo = limpo
            alt = alt_limpo if eh_ocr else ""
        elif alvos:  # a cópia não tem os mesmos objetos: nenhum item sai
            sobrou = [x for _, x in ocultos]
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
        linhas = col["texto"].splitlines()
        for ln in linhas:
            ln = re.sub(r"\s+", " ", ln).strip()
            if len(ln) >= 20 and ln.lower() not in vistos and ln.lower()[:20] not in vistos:
                registro.append(("não renderizado (invisível)", ln))
    if registro:
        # Item oculto que não saiu da cópia (sem objeto de texto, dentro de forma, acima do teto), ou texto não
        # renderizado que o extrator pôs no corpo: o corpo pode ter o oculto, e quem chama precisa saber.
        corpo = re.sub(r"\s+", " ", md).lower()
        ficou = sobrou or any(m == "não renderizado (invisível)" and t.lower() in corpo for m, t in registro)
        alerta = f"texto oculto que pode ter ficado no corpo: página {i + 1}" if ficou else None
        seguranca.append(
            {
                "pagina": i + 1,
                "tecnicas": sorted({m for m, _ in registro}),
                "texto": " ".join(t for _, t in registro)[:500],
            }
            | ({"alerta": alerta} if ficou else {})
        )
        if ficou:
            pendentes.append(alerta)
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
    for g in regioes_vetoriais(objs, W, H):
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
    if rota == "nativa":
        return info, None
    fator = (2 * 1568 / max(W, H) if H >= W else 1568 / max(W, H)) if rota == "pagina" else escala(W, H, 2.0)

    def recortar(img):
        """Recortes, arquivos e tarefas da página, com a imagem renderizada na escala `fator`: quem chama roda fora da
        trava, na ordem das páginas (as tarefas e o cache de recortes repetidos saem na mesma ordem de antes)."""
        if rota == "pagina":
            # Duas metades com sobreposição: o dobro de resolução por leitura (o provedor reduz acima de ~1568 px).
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
        else:
            full = img
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

    return info, (fator, recortar)


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
