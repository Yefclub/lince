"""Limites de figuras vetoriais a partir dos caminhos e do texto posicionado do PDF."""

import ctypes
import re
from collections import defaultdict
from math import ceil, floor

import pypdfium2.raw as c
from PIL import ImageDraw

_IDENTIFICADOR = re.compile(r"^(?:fig(?:ure|ura)?\.?|gráfico|chart)\s+\d+(?:\.\d+)*(?:\s*[:.]|\s*[–—-])", re.I)
_NOTA = re.compile(r"^(?:notas?|notes?|fontes?|sources?):", re.I)


def papel_retangulo(raw, caixa, W, H):
    """Só consulta o modo de pintura e os vértices nos caminhos que podem delimitar um quadro ou painel."""
    l, b, r, t = caixa
    largo, alto = r - l, t - b
    painel = largo >= W * 0.7 and alto >= H * 0.7
    painel_lateral = W >= 2.5 * H and largo >= 0.25 * W and alto >= 0.95 * H
    faixa_inferior = l <= 2 and r >= W - 2 and abs(b) <= 2 and 0.04 * H <= alto <= 0.25 * H
    quadro = largo >= 0.18 * W and alto >= 0.06 * H and largo * alto >= 0.02 * W * H
    if not (painel or painel_lateral or faixa_inferior or quadro):
        return None
    segmentos = c.FPDFPath_CountSegments(raw)
    if segmentos not in (4, 5):
        return None
    preenchimento, traco = ctypes.c_int(), ctypes.c_int()
    if not c.FPDFPath_GetDrawMode(raw, preenchimento, traco):
        return None
    if traco.value and quadro:
        papel = "quadro"
    elif traco.value or not preenchimento.value:
        return None
    elif painel:
        papel = "painel"
    elif painel_lateral:
        papel = "painel_lateral"
    elif faixa_inferior:
        papel = "faixa_inferior"
    else:
        return None
    pontos = []
    x, y = ctypes.c_float(), ctypes.c_float()
    for i in range(segmentos):
        segmento = c.FPDFPath_GetPathSegment(raw, i)
        if c.FPDFPathSegment_GetType(segmento) != (c.FPDF_SEGMENT_LINETO if i else c.FPDF_SEGMENT_MOVETO):
            return None
        if not c.FPDFPathSegment_GetPoint(segmento, x, y):
            return None
        pontos.append((round(x.value, 3), round(y.value, 3)))
    if pontos[-1] == pontos[0]:
        pontos.pop()
    elif not c.FPDFPathSegment_GetClose(segmento):
        return None
    lados = zip(pontos, pontos[1:] + pontos[:1], strict=True)
    if len(set(pontos)) != 4 or any(x1 != x2 and y1 != y2 for (x1, y1), (x2, y2) in lados):
        return None
    return papel


def _uniao(a, b):
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def _area(g):
    return (g[2] - g[0]) * (g[3] - g[1])


def _contem(g, outra, margem=0):
    return (
        g[0] - margem <= outra[0] <= outra[2] <= g[2] + margem
        and g[1] - margem <= outra[1] <= outra[3] <= g[3] + margem
    )


def _proximas(a, b, margem):
    return a[0] <= b[2] + margem and b[0] <= a[2] + margem and a[1] <= b[3] + margem and b[1] <= a[3] + margem


def _sobreposicao_horizontal(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0]))


def _alinhada(a, b):
    return _sobreposicao_horizontal(a, b) >= 0.45 * min(a[2] - a[0], b[2] - b[0])


def _linhas(textos):
    """Reúne os fragmentos na mesma base, respeitando o espaço entre colunas."""
    por_base = []
    for caixa, texto in sorted(textos, key=lambda item: -item[0][1]):
        if not por_base or por_base[-1][0][0][1] - caixa[1] > 1.5:
            por_base.append([])
        por_base[-1].append((caixa, texto))
    linhas = []
    for fragmentos in por_base:
        caixa, partes = None, []
        for g, parte in sorted(fragmentos, key=lambda item: item[0][0]):
            if caixa is not None and g[0] - caixa[2] <= min(14, g[3] - g[1] + 1):
                caixa = _uniao(caixa, g)
                partes.append(parte)
            else:
                if caixa is not None:
                    linhas.append((caixa, " ".join(partes)))
                caixa, partes = g, [parte]
        linhas.append((caixa, " ".join(partes)))
    return linhas


def _ate_fim_do_bloco(inicio, linhas, limite_direito):
    """As linhas seguintes mantêm a coluna, tamanho e entrelinha do começo da legenda."""
    l, b, _, t = inicio
    altura = t - b
    caixa, base = list(inicio), b
    for linha, texto in linhas:
        x, y, fim, topo = linha
        if y >= b or abs(x - l) > 5 or fim > limite_direito or abs(topo - y - altura) > 1.5:
            continue
        if base - y > 1.5 * altura or _IDENTIFICADOR.match(texto):
            break
        caixa = _uniao(caixa, linha)
        base = y
    return caixa


def _limites_laterais(g, grupos):
    """Um vizinho à mesma altura delimita a faixa de seus próprios rótulos."""
    esq, dir_ = g[0] - 65, g[2] + 65
    for outro in grupos:
        if min(g[3], outro[3]) - max(g[1], outro[1]) < 0.5 * min(g[3] - g[1], outro[3] - outro[1]):
            continue
        if outro[2] < g[0]:
            esq = max(esq, (outro[2] + g[0]) / 2)
        if g[2] < outro[0]:
            dir_ = min(dir_, (g[2] + outro[0]) / 2)
    return esq, dir_


def _expandir(g, moldura, textos, linhas, grupos):
    if moldura and any(_IDENTIFICADOR.match(texto) and _contem(g, caixa, 1) for caixa, texto in linhas):
        # Uma moldura que já contém sua identificação não toma o título do próximo quadro como legenda.
        return g, 1
    # Legendas abaixo podem ter a largura e várias linhas da coluna, como em artigos.
    abaixo = [
        caixa
        for caixa, texto in linhas
        if -2 <= g[1] - caixa[3] <= 32
        and (_alinhada(g, caixa) or abs(g[0] - caixa[0]) <= 32)
        and _IDENTIFICADOR.match(texto)
    ]
    if abaixo:
        legenda = max(abaixo, key=lambda caixa: caixa[3])
        _, dir_ = _limites_laterais(g, grupos)
        limite_da_legenda = max(legenda[2] + 12, dir_)
        caixa = _uniao(g, _ate_fim_do_bloco(legenda, linhas, limite_da_legenda))
        # Inclui textos curtos adjacentes ao desenho, como um eixo vertical, dentro da mesma coluna.
        rotulos = 1  # a própria legenda já identifica a figura
        for linha, texto in textos:
            if (
                len(texto) <= 24
                and caixa[0] - 5 <= linha[0] <= linha[2] <= caixa[2] + 5
                and g[1] - 5 <= linha[1] <= linha[3] <= g[3] + 2
            ):
                caixa = _uniao(caixa, linha)
                rotulos += 1
        # Títulos de painéis lado a lado (p.ex. as arquiteturas de uma rede) podem
        # estar duas linhas acima dos primeiros traços. Uma fileira de rótulos curtos
        # inteiramente dentro da largura do gráfico evita absorver a coluna de texto vizinha.
        acima = [
            rot
            for rot, texto in linhas
            if g[0] - 3 <= rot[0] < rot[2] <= g[2] + 3 and g[3] - 2 <= rot[1] <= g[3] + 26 and len(texto) <= 32
        ]
        for primeiro, segundo in zip(acima, acima[1:], strict=False):
            if abs(primeiro[1] - segundo[1]) < 2 and primeiro[2] < segundo[0]:
                caixa = _uniao(caixa, _uniao(primeiro, segundo))
        return caixa, rotulos
    if moldura:
        return g, int(any(_IDENTIFICADOR.match(texto) and _contem(g, caixa, 1) for caixa, texto in linhas))
    esq, dir_ = _limites_laterais(g, grupos)
    locais = [(caixa, texto) for caixa, texto in linhas if esq <= caixa[0] <= caixa[2] <= dir_]
    titulos = [
        caixa
        for caixa, texto in locais
        if -2 <= caixa[1] - g[3] <= 32 and _alinhada(g, caixa) and _IDENTIFICADOR.match(texto)
    ]
    if titulos:
        titulo = min(titulos, key=lambda caixa: caixa[1])
        caixa = _uniao(g, titulo)
        teto = titulo[3]
        piso = g[1] - 26
        notas = [
            n
            for n, texto in locais
            if 0 <= g[1] - n[3] <= 40 and (abs(n[0] - titulo[0]) <= 12 or _alinhada(g, n)) and _NOTA.match(texto)
        ]
        if notas:
            nota = max(notas, key=lambda n: n[3])
            caixa = _uniao(caixa, _ate_fim_do_bloco(nota, locais, dir_))
            piso = nota[3]
        for linha, _ in locais:
            if piso <= linha[1] <= linha[3] <= teto:
                caixa = _uniao(caixa, linha)
        return caixa, sum(piso <= linha[1] <= linha[3] <= teto for linha, _ in locais)
    for _ in range(2):
        # Desenhos sem um título ou legenda reconhecível mantêm os rótulos imediatos.
        perto = [
            x
            for x, _ in textos
            if max(esq, g[0] - 40) <= x[0] <= min(dir_, g[2] + 40) and g[1] - 30 <= x[1] <= g[3] + 30
        ]
        if perto:
            g = [min(g[0], min(x[0] for x in perto)), min(g[1], min(x[1] for x in perto) - 4),
                 max(g[2], max(min(x[2], dir_) for x in perto)), max(g[3], max(x[3] for x in perto))]  # fmt: skip
    return g, 0


def _molduras(caminhos, textos, area_pagina, tique):
    quadros = []
    for g, papel in caminhos:
        if papel != "quadro":
            continue
        tique()
        itens = [(x, texto) for x, texto in textos if len(texto) >= 3 and _contem(g, x, 1)]
        dentro = [x for x, _ in itens]
        if _area(g) < 0.03 * area_pagina:
            bases_valores = {round(x[1]) for x, texto in itens if re.search(r"\d", texto)}
            bases_rotulos = {round(x[1]) for x, texto in itens if ":" in texto}
            if len(bases_rotulos & bases_valores) < 3 and not any(_IDENTIFICADOR.match(texto) for _, texto in itens):
                continue  # a moldura compacta precisa de título de figura ou linhas com rótulos e valores
        if (
            len(dentro) >= 3
            and max(x[1] for x in dentro) - min(x[1] for x in dentro) >= 12
            and g[3] - max(x[3] for x in dentro) <= 24
            and not any(_alinhada(x, g) and _contem(g, x, 2) and _area(x) >= 0.8 * _area(g) for x in quadros)
        ):
            quadros.append(g)
    return quadros


def _residuo_sem_tinta(g, molduras, textos, regras, fundo, W, H):
    """O limite declarado pelo caminho pode ultrapassar a região que ele efetivamente pinta."""
    if fundo is None:
        return False
    envolventes = [
        q
        for q in molduras
        if q[0] <= g[0] <= g[2] <= q[2] and min(q[3], g[3]) - max(q[1], g[1]) >= 0.25 * (g[3] - g[1])
    ]
    if not envolventes:
        return False
    sx, sy = fundo.width / W, fundo.height / H

    def pixels(caixa, margem):
        l, b, r, t = caixa
        return (
            max(0, floor((l - margem) * sx)),
            max(0, floor((H - t - margem) * sy)),
            min(fundo.width, ceil((r + margem) * sx)),
            min(fundo.height, ceil((H - b + margem) * sy)),
        )

    l, b, r, t = pixels(g, 0)
    if l >= r or b >= t:
        return False
    trecho = fundo.crop((l, b, r, t))
    mascara = ImageDraw.Draw(trecho)
    for caixa in [*molduras, *regras, *(x for x, _ in textos)]:
        x, y, u, v = pixels(caixa, 2)
        if x < r and y < t and u > l and v > b:
            mascara.rectangle((x - l, y - b, u - l, v - b), fill="white")
    return all(minimo >= 250 for minimo, _ in trecho.getextrema())


def agrupar_figuras(objs, W, H, itens, papeis, tique, fundo=None):
    """Devolve geometria, recorte com contexto e se o texto identifica cada figura."""
    textos = [
        ((x.x, x.y, x.x + x.width, x.y + x.height), x.text.strip())
        for x in itens
        if x.width > 0 and x.height > 0 and not x.text.startswith("[Image:")
    ]
    caminhos, faixas, regras = [], defaultdict(list), []
    area_pagina = W * H
    for i, (tipo, (l, b, r, t)) in enumerate(objs):
        if tipo != c.FPDF_PAGEOBJ_PATH:
            continue
        w, h = r - l, t - b
        if h < 2.5 and w >= 0.65 * W:
            regras.append((l, b, r, t))
        if w < 2.5 or h < 2.5 or (w > 0.9 * W and h > 0.9 * H):
            continue  # filete, sublinhado ou moldura da página
        g = [l, b, r, t]
        papel = papeis.get(i)
        if papel == "painel":
            tique()
            bases = {round(x[1]) for x, texto in textos if len(texto) >= 12 and _contem(g, x)}
            if len(bases) >= 6 and max(bases) - min(bases) > h / 4:
                continue  # painel de preenchimento que serve de fundo a um bloco de texto
        if w > 0.4 * W and h < 40:
            faixas[round(l / 4)].append(g)
        else:
            caminhos.append((g, papel))
    for grupo in faixas.values():
        # Fundos de linhas têm largura repetida. Barras horizontais variam.
        if len({round((f[2] - f[0]) / 3) for f in grupo}) >= 3:
            caminhos.extend((g, None) for g in grupo)
    molduras = _molduras(caminhos, textos, area_pagina, tique)
    grupos = []
    for g, _ in caminhos:
        tique()
        if any(_contem(m, g, 1) for m in molduras):
            continue
        partes = [g]
        while True:
            for i, (gx, px) in enumerate(grupos):
                if _proximas(g, gx, 24):
                    g = _uniao(g, gx)
                    partes.extend(px)
                    del grupos[i]
                    break
            else:
                break
        grupos.append((g, partes))
    candidatos = [(g, True, False) for g in molduras]
    for g, partes in grupos:
        area = _area(g)
        if area >= 0.03 * area_pagina:
            candidatos.append((g, False, False))
        elif area >= 0.012 * area_pagina and sum(_area(p) >= 0.45 * area for p in partes) >= 3:
            candidatos.append((g, False, True))
    if not candidatos:
        return []
    linhas = _linhas(textos)
    geometrias = [g for g, _, _ in candidatos]
    resultado = []
    for g, moldura, compacto in candidatos:
        tique()
        caixa, rotulos = _expandir(g, moldura, textos, linhas, geometrias)
        if not moldura and not rotulos and _residuo_sem_tinta(g, molduras, textos, regras, fundo, W, H):
            continue
        if not compacto or rotulos >= 3:
            resultado.append((g, caixa, rotulos > 0, moldura))

    # Sem moldura ou rótulo próprio, dois grupos próximos podem ser partes do mesmo desenho.
    # A geometria conjunta preserva conteúdo entre partes, por exemplo a linha entre seções de um formulário.
    reunidos = []
    for g, caixa, identificada, moldura in resultado:
        tique()
        if not (identificada or moldura):
            restantes = []
            for gg, outra, pertence, enquadrada in reunidos:
                if not (pertence or enquadrada) and _proximas(caixa, outra, 32) and _alinhada(caixa, outra):
                    g, caixa = _uniao(g, gg), _uniao(caixa, outra)
                else:
                    restantes.append((gg, outra, pertence, enquadrada))
            reunidos = restantes
        reunidos.append((g, caixa, identificada, moldura))
    return [(g, caixa, identificada) for g, caixa, identificada, _ in reunidos]


def paineis_panorama(objs, W, H, itens, papeis, imagens, tique):
    """Separa um mapa panorâmico das colunas laterais quando o PDF explicita os limites."""
    if W < 2.5 * H or len(imagens) != 1:
        return None
    imagem = imagens[0]
    if imagem[0] > 0.15 * W or imagem[2] - imagem[0] < W * 0.35 or imagem[3] - imagem[1] < H * 0.95:
        return None
    fundos = [
        objs[i][1]
        for i, papel in papeis.items()
        if papel == "painel_lateral" and abs(objs[i][1][2] - W) <= 2 and abs(objs[i][1][0] - imagem[2]) <= 8
    ]
    if len(fundos) != 1:
        return None
    fundo = fundos[0]
    textos = [
        (x.x, x.y, x.x + x.width, x.y + x.height)
        for x in itens
        if x.width > 0 and x.text.strip() and not x.text.startswith("[Image:") and fundo[0] <= x.x < W
    ]
    cabecalhos = []
    for tipo, g in objs:
        if (
            tipo == c.FPDF_PAGEOBJ_PATH
            and _contem(fundo, g, 1)
            and g[3] >= H * 0.93
            and 8 <= g[3] - g[1] <= 24
            and g[2] - g[0] >= (W - fundo[0]) * 0.18
        ):
            tique()
            if any(_contem(g, linha, 2) for linha in textos):
                cabecalhos.append(g)
    cabecalhos.sort(key=lambda g: (g[0], -_area(g)))
    colunas = []
    for g in cabecalhos:
        if colunas and _contem(colunas[-1], g, 2):
            continue
        colunas.append(g)
    if len(colunas) < 2 or sum(g[2] - g[0] for g in colunas) < (W - fundo[0]) * 0.75:
        return None
    if max(g[3] for g in colunas) - min(g[3] for g in colunas) > 3:
        return None
    cortes = []
    for anterior, seguinte in zip(colunas, colunas[1:], strict=False):
        if seguinte[0] - anterior[2] < 10:
            return None
        cortes.append((seguinte[0] + anterior[2]) / 2)
    # Se uma coluna atravessa uma divisão, a página panorâmica é mantida inteira.
    if any(l < corte < r for l, _, r, _ in textos for corte in cortes):
        return None
    limites = [fundo[0], *cortes, W]
    return [0, 0, fundo[0], H], [[l, 0, r, H] for l, r in zip(limites, limites[1:], strict=False)]


def faixa_ilustrada(objs, W, H, itens, papeis, imagens):
    """Limite entre uma faixa preenchida com imagens na base e o restante da folha."""
    if len(imagens) != 1:
        return None
    faixas = [objs[i][1] for i, papel in papeis.items() if papel == "faixa_inferior"]
    if len(faixas) != 1:
        return None
    limite = faixas[0][3]
    l, b, r, t = imagens[0]
    if b > 2 or r - l > 0.65 * W or not limite * 0.7 <= t <= limite * 1.25:
        return None
    if any(x.y + 1 < limite < x.y + x.height - 1 for x in itens if not x.text.startswith("[Image:")):
        return None
    return [0, 0, W, limite], [0, limite, W, H]
