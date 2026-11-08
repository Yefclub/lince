"""HTML salvo (página, relatório exportado, e-mail em HTML): Markdown pelo html-to-markdown. Texto escondido sem
ambiguidade (atributo hidden; display:none, opacity:0, visibility:hidden e fonte zero no estilo da própria tag) sai
do corpo e vira alerta. O que depende de renderizar a página fica de fora: cor do texto contra o fundo e estilo de
classe numa folha <style> (branco sobre fundo azul é visível; removê-lo apagaria conteúdo). Imagem embutida (data:)
é lida pela IA; imagem externa nunca é baixada (o Lince não busca nada na rede por causa de um documento)."""

import base64
import binascii
import io
import re

import html_to_markdown
from lxml import etree
from lxml import html as lxml_html

IMAGEM_MAX = 20 * 1024 * 1024  # bytes de uma imagem embutida; maior que isso fica marcada, sem leitura
PROFUNDIDADE_MAX = 2048  # o libxml2 com huge_tree corta aí; o html-to-markdown recebe o mesmo teto
_SEM_CONTEUDO = {"script", "style", "noscript", "template", "iframe", "object", "embed", "head"}
_FIM = r"\s*(?:!\s*important\s*)?(?:;|$)"  # um \s* por trecho: dois seguidos tornariam o regex quadrático
_ATRIBUTO = r"(?<![-\w])"  # a propriedade em si, não background-color, stroke-opacity, --color
# escondem a subárvore inteira, sem volta: nenhum descendente desfaz display:none nem opacity:0
_SEMPRE = re.compile(_ATRIBUTO + r"(?:display\s*:\s*none|opacity\s*:\s*0(?:\.0+)?)" + _FIM, re.I)
# herdáveis: escondem só se nenhum descendente redefine a propriedade (layout de e-mail usa font-size:0 no <td>)
_HERDAVEIS = [
    ("visibility", re.compile(_ATRIBUTO + r"visibility\s*:\s*(?:hidden|collapse)" + _FIM, re.I)),
    (
        "font-size",
        re.compile(_ATRIBUTO + r"font-size\s*:\s*(?:0(?:\.0+)?(?:px|pt|em|rem|%)?|0?\.\d+px|1px)" + _FIM, re.I),
    ),
]
_MARCA = re.compile(r"LINCEMARCA\d+X")
_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w.:-]+)""", re.I)
_PROLOGO = re.compile(r"^\s*<\?xml[^>]*\?>", re.I)  # XHTML: o lxml recusa str com declaração de encoding
# o que os navegadores fazem com o charset declarado (WHATWG Encoding): latin-1 e ASCII são windows-1252, e UTF-16
# declarado sem BOM é lido como UTF-8 (o próprio <meta> só é legível se o arquivo não for UTF-16)
# rótulos do WHATWG Encoding que o <meta> pode declarar (só codificações compatíveis com ASCII, as únicas em que o
# próprio <meta> é legível), com o codec que o navegador usa; rótulo fora daqui é ignorado, como no navegador
_COMO_NAVEGADOR = {
    **dict.fromkeys(["utf-8", "utf8", "unicode-1-1-utf-8", "utf-16", "utf-16le", "utf-16be", "unicode"], "utf-8"),
    **dict.fromkeys(["iso-8859-1", "iso8859-1", "latin1", "latin-1", "l1", "cp819", "ascii", "us-ascii",
                     "windows-1252", "cp1252", "x-cp1252"], "cp1252"),
    **{f"windows-125{n}": f"cp125{n}" for n in range(9)},
    **{f"iso-8859-{n}": f"iso8859-{n}" for n in (2, 3, 4, 5, 6, 7, 8, 10, 13, 14, 15, 16)},
    **dict.fromkeys(["gbk", "gb2312", "gb18030", "x-gbk", "chinese"], "gb18030"),
    **dict.fromkeys(["big5", "big5-hkscs", "x-x-big5"], "big5hkscs"),
    **dict.fromkeys(["shift_jis", "shift-jis", "sjis", "windows-31j", "ms_kanji", "x-sjis"], "cp932"),
    **dict.fromkeys(["euc-jp", "x-euc-jp"], "euc_jp"),
    **dict.fromkeys(["euc-kr", "ks_c_5601-1987", "windows-949"], "cp949"),
    "iso-2022-jp": "iso2022_jp", "koi8-r": "koi8_r", "koi8-u": "koi8_u", "macintosh": "mac_roman",
    "windows-874": "cp874", "tis-620": "cp874",
}  # fmt: skip


def decodificar(dados: bytes) -> str:
    """BOM, depois o charset declarado no <meta> (como o navegador entende), depois UTF-8; sem nada disso, cp1252."""
    for bom, cod in ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if dados.startswith(bom):
            return dados[len(bom) :].decode(cod, "replace")
    if (m := _CHARSET.search(dados[:4096])) and (cod := _COMO_NAVEGADOR.get(m.group(1).decode("ascii").lower())):
        return dados.decode(cod, "replace")
    try:
        return dados.decode("utf-8")
    except UnicodeDecodeError:
        return dados.decode("cp1252", "replace")


def planejar_html(path, pasta) -> dict:
    """Plano no formato do Office: Markdown com marcas {{IA:id}} onde vai a leitura de cada imagem embutida, e as
    propriedades do <head> (lidas no mesmo parse)."""
    texto = _PROLOGO.sub("", decodificar(open(path, "rb").read()))
    plano = {"tipo": "office", "md": "", "extras": [], "tarefas": [], "texto_oculto": [], "ilegiveis": []}
    if not texto.strip():
        return plano
    parser = lxml_html.HTMLParser(huge_tree=True)  # sem huge_tree, texto acima de 10 MB some e o fundo é cortado
    try:
        raiz = lxml_html.document_fromstring(texto, parser=parser)
    except etree.ParserError:  # só comentário, ou nada que vire elemento
        return plano
    if any(e.type_name == "ERR_RESOURCE_LIMIT" for e in parser.error_log):
        plano["ilegiveis"].append(f"html: aninhado além de {PROFUNDIDADE_MAX} níveis; o resto ficou de fora")
    plano["propriedades"] = _propriedades(raiz)
    ocultos = _tirar_oculto(raiz)
    marcas = _imagens(raiz, pasta, plano["tarefas"])
    for a in raiz.iter("a"):  # link para data: carregaria o arquivo inteiro em base64 para o texto
        if (a.get("href") or "").strip().lower().startswith("data:"):
            del a.attrib["href"]
    opcoes = html_to_markdown.ConversionOptions(
        skip_images=True,
        inline_data_media="alt_text_only",  # <video>/<audio>/<source> com data: não viram base64 no texto
        hidden_content="reachable",  # <details> fechado e aba inativa: a pessoa chega a ver, então entram
        extract_metadata=False,
        max_depth=PROFUNDIDADE_MAX,
    )
    r = html_to_markdown.convert(lxml_html.tostring(raiz, encoding="unicode"), opcoes)
    md = (r.content or "").strip()
    if marcas:  # uma passada só: um replace por marca seria quadrático com milhares de imagens
        md = _MARCA.sub(lambda m: marcas.get(m.group(0), m.group(0)), md)
    plano["md"] = md
    if r.warnings:
        plano["ilegiveis"] += sorted({f"html: {w.message}"[:200] for w in r.warnings})
    if ocultos:
        plano["texto_oculto"].append(
            {"tecnicas": sorted({t for t, _ in ocultos}), "texto": " ".join(t for _, t in ocultos)[:500]}
        )
    return plano


def _tirar_oculto(raiz) -> list:
    """Tira o que não aparece (scripts, estilos, <noscript>…) e o texto escondido, antes de olhar as imagens: imagem
    dentro de bloco oculto não é paga nem lida. Percurso com pilha, sem recursão; a subárvore esvaziada não é visitada,
    então oculto dentro de oculto conta uma vez. Esvaziar com clear(keep_tail=True) custa O(1) por elemento."""
    ocultos, pilha = [], [raiz]
    while pilha:
        el = pilha.pop()
        if not isinstance(el.tag, str):
            continue
        if el.tag in _SEM_CONTEUDO:
            el.clear(keep_tail=True)
            continue
        estilo = el.get("style") or ""
        tecnica = "atributo hidden" if el.get("hidden") is not None else None
        if not tecnica and _SEMPRE.search(estilo):
            tecnica = "estilo que esconde"
        if not tecnica:
            for prop, padrao in _HERDAVEIS:
                if padrao.search(estilo) and not _redefinida(el, prop):
                    tecnica = "fonte zero" if prop == "font-size" else "estilo que esconde"
                    break
        if not tecnica:
            pilha.extend(reversed(list(el)))
            continue
        if conteudo := " ".join(el.text_content().split()):
            ocultos.append((tecnica, conteudo))
        el.clear(keep_tail=True)
    return ocultos


def _redefinida(el, prop) -> bool:
    """Algum descendente volta a declarar a propriedade (por exemplo font-size:14px dentro do <td> com font-size:0)?
    Só é chamada para os poucos elementos com estilo de esconder."""
    alvo = re.compile(_ATRIBUTO + prop + r"\s*:", re.I)
    return any(alvo.search(d.get("style") or "") for d in el.iterdescendants() if isinstance(d.tag, str))


def _imagens(raiz, pasta, tarefas) -> dict:
    """Imagem data: vira tarefa da IA (marca sentinela no lugar, trocada depois da conversão); decorativa sai;
    externa e ilegível ficam marcadas com o alt."""
    from PIL import Image

    marcas = {}
    for el in list(raiz.iter("img")):
        src, alt = (el.get("src") or "").strip(), (el.get("alt") or "").strip()
        substituto = f"[imagem: {alt or 'sem descrição'}, externa, não lida]"
        if src.lower().startswith("data:image/"):
            carga = src.split(",", 1)[1] if "," in src else ""
            try:
                if len(carga) * 3 // 4 > IMAGEM_MAX:  # estimado antes de decodificar: nem aloca o que não lerá
                    raise ValueError("imagem embutida grande demais")
                im = Image.open(io.BytesIO(base64.b64decode(carga, validate=False)))
                if min(im.size) < 48:  # ícone ou separador: decorativo, como no Office
                    substituto = ""
                else:
                    tid = f"img{len(tarefas) + 1:02d}"
                    im.convert("RGB").save(f"{pasta}/ia/{tid}.png")
                    tarefas.append({"id": tid, "modo": "imagem", "ancora": alt or f"imagem {len(tarefas) + 1}"})
                    substituto = f"{{{{IA:{tid}}}}}"
            except (binascii.Error, ValueError, OSError, Image.DecompressionBombError):
                substituto = f"[imagem: {alt or 'sem descrição'}, embutida, ilegível]"
        # o texto entra como sentinela sem pontuação e volta depois da conversão: colchetes e chaves deixam o
        # conversor quadrático em parágrafo com milhares de imagens, e ele poderia escapar a marca da IA
        marca = f"LINCEMARCA{len(marcas)}X" if substituto else ""
        if marca:
            marcas[marca] = substituto
        troca = etree.Element("span")  # a imagem vira um <span> com o texto: troca em O(1), o tail fica
        troca.text, troca.tail = marca, el.tail
        el.getparent().replace(el, troca)
    return marcas


def _propriedades(raiz) -> dict:
    """Título, autor, descrição e palavras-chave do <head>."""
    props = {}
    if (titulo := raiz.findtext(".//title")) and titulo.strip():
        props["title"] = " ".join(titulo.split())
    for meta in raiz.iter("meta"):
        nome, valor = (meta.get("name") or "").lower(), (meta.get("content") or "").strip()
        chave = {"author": "creator", "description": "description", "keywords": "keywords"}.get(nome)
        if chave and valor:
            props.setdefault(chave, valor)
    return props
