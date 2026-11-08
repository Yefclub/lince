"""Desenho de SVG em PNG pelo resvg, para a IA ler o diagrama como ela lê uma imagem.

O SVG nunca busca nada fora dele: toda referência que não seja a um trecho do próprio documento (#id) ou a imagem
raster embutida (data: com PNG, JPEG, GIF ou WebP) é tirada antes de desenhar, porque o resvg leria um arquivo local
que o documento apontasse, e um SVG embutido traria texto que a limpeza do oculto não vê. O <tref> também sai. O
desenho roda num processo à parte (`_desenhar.py`), com teto de tempo e de memória.
Sem o resvg instalado, quem chama segue com o texto do SVG.
"""

import copy
import importlib.util
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from lxml import etree

from ._prazo import prazo_restante
from ._trava import sem_pdfium

LADO_PX = 1600  # o lado maior do desenho: legível para a IA sem gerar imagem enorme
LADO_MIN_PX = 800  # desenho pequeno é ampliado até aqui (4x o tamanho declarado, no máximo LADO_PX)
FILHO = Path(__file__).with_name("_desenhar.py")
SEGUNDOS_MAX = 30  # teto do lote de desenhos de um documento: filtro hostil prende o resvg por minutos
SVG_NS, XLINK_NS = "http://www.w3.org/2000/svg", "http://www.w3.org/1999/xlink"
# só imagem raster embutida: um data: com SVG (ou sem tipo) traria texto que a limpeza do oculto não vê
_RASTER = re.compile(r"data:image/(png|jpe?g|gif|webp)[;,]", re.I)
_MEDIDA = re.compile(r"^\s*([0-9]*\.?[0-9]+(?:e[+-]?\d+)?)\s*([a-z]*)\s*$", re.I)
_UNIDADES = {
    "": 1.0,
    "px": 1.0,
    "pt": 96 / 72,
    "pc": 16.0,
    "in": 96.0,
    "cm": 96 / 2.54,
    "mm": 96 / 25.4,
    "q": 96 / 101.6,
}
# o parser HTML põe nome de tag e de atributo em minúsculas; o SVG distingue (viewBox, linearGradient...)
_TAGS = (
    "altGlyph altGlyphDef altGlyphItem animateColor animateMotion animateTransform clipPath feBlend "
    "feColorMatrix feComponentTransfer feComposite feConvolveMatrix feDiffuseLighting feDisplacementMap "
    "feDistantLight feDropShadow feFlood feFuncA feFuncB feFuncG feFuncR feGaussianBlur feImage feMerge "
    "feMergeNode feMorphology feOffset fePointLight feSpecularLighting feSpotLight feTile feTurbulence "
    "foreignObject glyphRef linearGradient radialGradient textPath"
)
_ATRIBUTOS = (
    "attributeName attributeType baseFrequency baseProfile calcMode clipPathUnits diffuseConstant "
    "edgeMode filterUnits glyphRef gradientTransform gradientUnits kernelMatrix kernelUnitLength "
    "keyPoints keySplines keyTimes lengthAdjust limitingConeAngle markerHeight markerUnits markerWidth "
    "maskContentUnits maskUnits numOctaves pathLength patternContentUnits patternTransform patternUnits "
    "pointsAtX pointsAtY pointsAtZ preserveAlpha preserveAspectRatio primitiveUnits refX refY repeatCount "
    "repeatDur requiredExtensions requiredFeatures specularConstant specularExponent spreadMethod "
    "startOffset stdDeviation stitchTiles surfaceScale systemLanguage tableValues targetX targetY "
    "textLength viewBox viewTarget xChannelSelector yChannelSelector zoomAndPan"
)
CASO_TAG = {t.lower(): t for t in _TAGS.split()}
CASO_ATRIBUTO = {a.lower(): a for a in _ATRIBUTOS.split()}


def de_html(el) -> etree._Element:
    """SVG embutido no HTML (sem namespace, nomes em minúsculas) como SVG de verdade, para o resvg."""
    raiz = etree.Element(f"{{{SVG_NS}}}svg", nsmap={None: SVG_NS, "xlink": XLINK_NS})
    pilha = [(el, raiz)]
    while pilha:
        origem, destino = pilha.pop()
        for nome, valor in origem.attrib.items():
            nome = CASO_ATRIBUTO.get(nome, nome)
            if nome == "xlink:href":
                nome = "href"
            if ":" not in nome and nome != "xmlns":
                destino.set(nome, valor)
        destino.text = origem.text
        for filho in reversed(origem):
            if isinstance(filho.tag, str):
                novo = etree.Element(f"{{{SVG_NS}}}{CASO_TAG.get(filho.tag, filho.tag)}")
                novo.tail = filho.tail
                pilha.append((filho, novo))
                destino.insert(0, novo)
    return raiz


def _px(valor) -> float | None:
    """Comprimento do SVG em px (96 por polegada); porcentagem e unidade desconhecida não têm tamanho próprio."""
    if (m := _MEDIDA.match(valor or "")) and (fator := _UNIDADES.get(m.group(2).lower())):
        return float(m.group(1)) * fator
    return None


def tamanho(svg) -> tuple[float, float] | None:
    """Largura e altura em px pelos atributos width e height ou, sem eles, pelo viewBox."""
    w, h = _px(svg.get("width")), _px(svg.get("height"))
    caixa = (svg.get("viewBox") or "").replace(",", " ").split()
    try:
        cw, ch = (float(caixa[2]), float(caixa[3])) if len(caixa) == 4 else (0.0, 0.0)
    except ValueError:
        cw = ch = 0.0
    if w and h and w > 0 and h > 0:
        return w, h
    if cw > 0 and ch > 0:  # um lado só declarado: o outro segue a proporção da caixa
        return (w, w * ch / cw) if w else (h * cw / ch, h) if h else (cw, ch)
    return None


def _preparar(svg) -> tuple[str, dict] | None:
    """O SVG pronto para o resvg e o lado do desenho, ou None se não há o que desenhar (proporção absurda)."""
    svg = copy.deepcopy(svg)
    for el in list(svg.iter()):
        if not isinstance(el.tag, str):
            continue
        if etree.QName(el).localname == "tref":  # desenharia o texto de qualquer elemento, até o CSS do <style>
            el.getparent().remove(el)
            continue
        for nome in [n for n in el.attrib if etree.QName(n).localname == "href"]:
            valor = el.get(nome).strip()
            if not (valor.startswith("#") or _RASTER.match(valor)):
                del el.attrib[nome]  # arquivo local, endereço externo ou SVG embutido: nunca entra no desenho
    w, h = tamanho(svg) or (LADO_PX, LADO_PX)
    maior = min(LADO_PX, max(LADO_MIN_PX, 4 * max(w, h)))  # ícone não vira imagem de 1600 px
    if min(w, h) * maior / max(w, h) < 1:  # proporção absurda: o outro lado sumiria
        return None
    return etree.tostring(svg, encoding="unicode"), {"width": round(maior)} if w >= h else {"height": round(maior)}


def desenhar_lote(itens) -> list[str]:
    """Desenha cada (svg, destino) em PNG, num processo à parte com teto de tempo e de memória. Para cada um:
    "lido" (há o que ler), "branco" (nada desenhado), "ilegivel" (o resvg não o desenha), "tempo" (cortado pelo
    teto ou pelo prazo do documento) ou "sem resvg" (não instalado)."""
    if not itens:
        return []
    if importlib.util.find_spec("resvg_py") is None:
        return ["sem resvg"] * len(itens)
    resultados, trabalho, indices = ["branco"] * len(itens), [], []
    for i, (svg, destino) in enumerate(itens):
        if (pronto := _preparar(svg)) is not None:
            trabalho.append([pronto[0], pronto[1], str(destino)])
            indices.append(i)
    if not trabalho:
        return resultados
    inicio = time.monotonic()
    teto = SEGUNDOS_MAX if (resta := prazo_restante()) is None else max(0.1, min(SEGUNDOS_MAX, resta))
    feitos = {}  # posição no trabalho -> resultado
    k = 0
    while k < len(trabalho):  # o filho que morre (memória, abort) perde só o desenho em que estava
        if (sobra := teto - (time.monotonic() - inicio)) <= 0:
            break
        try:
            with sem_pdfium():  # o desenho não usa a PDFium: os outros documentos seguem enquanto ele roda
                fim = subprocess.run(  # noqa: S603 — o próprio Python e um script do pacote; o SVG vai pela entrada
                    [sys.executable, "-I", str(FILHO)],
                    input=json.dumps(trabalho[k:]),
                    capture_output=True,
                    text=True,
                    timeout=sobra,
                )
            saida, morreu, cortado = fim.stdout, fim.returncode != 0, False
        except subprocess.TimeoutExpired as e:  # o run mata o filho; o que ele já concluiu vale
            saida = e.stdout.decode() if isinstance(e.stdout, bytes) else e.stdout or ""
            morreu, cortado = False, True
        ultimo = k - 1  # o último desenho que o filho concluiu nesta rodada
        for linha in saida.splitlines():
            n, _, resultado = linha.partition(" ")
            if n.isdigit() and resultado in ("lido", "branco", "ilegivel") and k + int(n) < len(trabalho):
                feitos[k + int(n)] = resultado
                ultimo = max(ultimo, k + int(n))
        if cortado or not morreu:
            break  # sem tempo para o resto, ou o filho terminou o lote
        feitos[ultimo + 1] = "ilegivel"  # o desenho seguinte ao último concluído derrubou o filho
        k = ultimo + 2
    for posicao, i in enumerate(indices):
        resultados[i] = feitos.get(posicao, "tempo")
    return resultados
