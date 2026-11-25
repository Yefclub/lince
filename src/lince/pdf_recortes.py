"""Área visível das imagens raster, no referencial da página PDF."""

import ctypes
import math

import pypdfium2.raw as c

from .limites import escala


def interseccao(a, b):
    return max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])


def janela_retangular(raw):
    """Caixa conservadora dos clips retangulares expressos no referencial do pai.
    Ignora caminhos compostos e curvas; quatro vértices isolados não provam um retângulo."""
    clip = c.FPDFPageObj_GetClipPath(raw)
    if not clip:
        return None
    janela = None
    for i in range(min(16, max(0, c.FPDFClipPath_CountPaths(clip)))):
        n = c.FPDFClipPath_CountPathSegments(clip, i)
        if n not in (4, 5):
            continue
        pontos = []
        for j in range(n):
            segmento = c.FPDFClipPath_GetPathSegment(clip, i, j)
            esperado = c.FPDF_SEGMENT_MOVETO if j == 0 else c.FPDF_SEGMENT_LINETO
            if not segmento or c.FPDFPathSegment_GetType(segmento) != esperado:
                break
            x, y = ctypes.c_float(), ctypes.c_float()
            if not c.FPDFPathSegment_GetPoint(segmento, ctypes.byref(x), ctypes.byref(y)):
                break
            pontos.append((x.value, y.value))
        if len(pontos) != n or (n == 5 and pontos[-1] != pontos[0]):
            continue
        xs, ys = zip(*pontos, strict=True)
        if len(set(xs)) != 2 or len(set(ys)) != 2 or len(set(pontos)) != 4 or not all(map(math.isfinite, (*xs, *ys))):
            continue
        if not all(x == u or y == v for (x, y), (u, v) in zip(pontos, pontos[1:] + pontos[:1], strict=True)):
            continue
        ret = (min(xs), min(ys), max(xs), max(ys))
        janela = ret if janela is None else interseccao(janela, ret)
    return janela


def validar_regiao(regiao):
    if (
        not isinstance(regiao, (list, tuple))
        or len(regiao) != 4
        or not all(isinstance(v, (float, int)) and not isinstance(v, bool) and 0 <= v <= 1 for v in regiao)
    ):
        raise ValueError("a região precisa de quatro coordenadas entre 0 e 1")
    x0, y0, x1, y1 = regiao
    if x0 >= x1 or y0 >= y1:
        raise ValueError("a região precisa de largura e altura positivas")


def renderizar_regiao(page, lado, regiao):
    """Renderiza a janela diretamente do original; o bitmap alocado contém só o detalhe."""
    W, H = page.get_size()
    rotacao = page.rotacao_exibida
    w, h = (H, W) if rotacao in (90, 270) else (W, H)
    x0, y0, x1, y1 = regiao
    fator = escala(W, H, lado / max((x1 - x0) * w, (y1 - y0) * h, 1e-6))
    largura, altura = math.ceil(w * fator), math.ceil(h * fator)
    # PDFium arredonda as margens para cima. Arredondar as bordas da região para fora preserva os glifos limítrofes.
    l = min(largura - 1, math.floor(x0 * largura))
    t = min(altura - 1, math.floor(y0 * altura))
    r = min(largura, max(l + 1, math.ceil(x1 * largura)))
    b = min(altura, max(t + 1, math.ceil(y1 * altura)))
    margens = tuple(math.nextafter(n / fator, 0) for n in (l, altura - b, largura - r, t))
    img = page.render(scale=fator, rotation=rotacao, crop=margens).to_pil().convert("RGB")
    img.thumbnail((lado, lado))
    return img
