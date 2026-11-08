"""Desenho de SVG em PNG pelo resvg, para a IA ler o diagrama como ela lê uma imagem.

O SVG nunca busca nada fora dele: toda referência que não seja a um trecho do próprio documento (#id) ou a dado
embutido (data:) é tirada antes de desenhar, porque o resvg leria um arquivo local que o documento apontasse.
Sem o resvg instalado, quem chama segue com o texto do SVG.
"""

import copy
import io
import re

from lxml import etree

LADO_PX = 1600  # o lado maior do desenho: legível para a IA sem gerar imagem enorme
SVG_NS, XLINK_NS = "http://www.w3.org/2000/svg", "http://www.w3.org/1999/xlink"
_IMPORT = re.compile(r"@import[^;]*;?", re.I)
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


def desenhar(svg, destino) -> str:
    """Desenha `svg` (elemento lxml com namespace) em PNG no `destino`. Devolve "lido" (há o que ler), "branco"
    (nada desenhado), "sem resvg" (não instalado) ou "ilegivel" (o resvg não desenha este SVG)."""
    try:
        import resvg_py
    except ImportError:
        return "sem resvg"
    from PIL import Image

    svg = copy.deepcopy(svg)
    for el in svg.iter():
        if not isinstance(el.tag, str):
            continue
        for nome in [n for n in el.attrib if etree.QName(n).localname == "href"]:
            valor = el.get(nome).strip()
            if not (valor.startswith("#") or valor.lower().startswith("data:")):
                del el.attrib[nome]  # arquivo local ou endereço externo: nunca entra no desenho
        if etree.QName(el).localname == "style" and el.text:
            el.text = _IMPORT.sub("", el.text)
    w, h = tamanho(svg) or (LADO_PX, LADO_PX)
    if min(w, h) * LADO_PX / max(w, h) < 1:  # proporção absurda: o outro lado sumiria
        return "branco"
    lado = {"width": LADO_PX} if w >= h else {"height": LADO_PX}
    try:
        png = resvg_py.svg_to_bytes(
            svg_string=etree.tostring(svg, encoding="unicode"), background="#ffffff", dpi=96.0, **lado
        )
        im = Image.open(io.BytesIO(bytes(png))).convert("RGB")
    except (ValueError, OSError):
        return "ilegivel"
    if Image.eval(im, lambda v: 255 - v).getbbox() is None:  # tudo branco: nada a ler
        return "branco"
    im.save(destino)
    return "lido"
