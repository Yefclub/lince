"""HTML salvo (página, relatório exportado, e-mail em HTML): Markdown pelo html-to-markdown, com as regras de
segurança do Office. Texto escondido por atributo ou por estilo na própria tag sai do corpo e vira alerta (estilo de
classe numa folha <style> não é avaliado: o HTML não é renderizado); imagem embutida (data:) é lida pela IA; imagem
externa nunca é baixada (o Lince não busca nada na rede por causa de um documento)."""

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
_FIM = r"\s*(?:!\s*important)?\s*(?:;|$)"
_OCULTO = [  # (técnica, padrão no atributo style) — as mesmas do Word: oculto, fonte minúscula, cor branca
    ("estilo que esconde", re.compile(r"display\s*:\s*none" + _FIM + r"|visibility\s*:\s*hidden" + _FIM, re.I)),
    ("transparente", re.compile(r"opacity\s*:\s*0(?:\.0+)?" + _FIM + r"|(?<![-\w])color\s*:\s*transparent", re.I)),
    ("fonte minúscula", re.compile(r"font-size\s*:\s*(?:0|[01](?:\.\d+)?px|0?\.\d+(?:px|pt|em|rem))" + _FIM, re.I)),
    (
        "cor branca",
        re.compile(r"(?<![-\w])color\s*:\s*(?:white|#fff(?:fff)?|#fefefe|rgba?\(\s*255\s*,\s*255\s*,\s*255\b)", re.I),
    ),
]
_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w.:-]+)""", re.I)
_PROLOGO = re.compile(r"^\s*<\?xml[^>]*\?>", re.I)  # XHTML: o lxml recusa str com declaração de encoding
# o que os navegadores fazem com o charset declarado (WHATWG Encoding): latin-1 e ASCII são windows-1252, e UTF-16
# declarado sem BOM é lido como UTF-8 (o próprio <meta> só é legível se o arquivo não for UTF-16)
_COMO_NAVEGADOR = {"iso-8859-1": "cp1252", "latin1": "cp1252", "latin-1": "cp1252", "ascii": "cp1252",
                   "us-ascii": "cp1252", "utf-16": "utf-8", "utf-16le": "utf-8", "utf-16be": "utf-8",
                   "unicode": "utf-8"}  # fmt: skip


def decodificar(dados: bytes) -> str:
    """BOM, depois o charset declarado no <meta> (como o navegador entende), depois UTF-8; sem nada disso, cp1252."""
    for bom, cod in ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if dados.startswith(bom):
            return dados[len(bom) :].decode(cod, "replace")
    if m := _CHARSET.search(dados[:4096]):
        declarado = m.group(1).decode("ascii").lower()
        try:
            return dados.decode(_COMO_NAVEGADOR.get(declarado, declarado), "replace")
        except LookupError:
            pass  # charset inventado: segue para as tentativas seguintes
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
    for marca, ia in marcas.items():
        md = md.replace(marca, ia)
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
    dentro de bloco oculto não é paga nem lida. Bloco oculto dentro de outro conta uma vez só."""
    ocultos, pilha = [], [raiz]  # percurso com pilha: subárvore removida nem é visitada, e não há recursão
    while pilha:
        el = pilha.pop()
        if not isinstance(el.tag, str):
            continue
        if el.tag in _SEM_CONTEUDO:
            _trocar_por_texto(el, "")
            continue
        tecnica = "atributo hidden" if el.get("hidden") is not None else None
        estilo = el.get("style") or ""
        tecnica = tecnica or next((t for t, padrao in _OCULTO if padrao.search(estilo)), None)
        if not tecnica:
            pilha.extend(reversed(list(el)))
            continue
        if conteudo := " ".join(el.text_content().split()):
            ocultos.append((tecnica, conteudo))
        if el is raiz:  # a página inteira escondida: nada fica no corpo
            for filho in list(raiz):
                raiz.remove(filho)
            raiz.text = None
        else:
            _trocar_por_texto(el, "")
    return ocultos


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
                    marca = f"LINCEMARCA{len(marcas):06d}X"
                    marcas[marca] = f"{{{{IA:{tid}}}}}"
                    substituto = marca
            except (binascii.Error, ValueError, OSError, Image.DecompressionBombError):
                substituto = f"[imagem: {alt or 'sem descrição'}, embutida, ilegível]"
        _trocar_por_texto(el, substituto)
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


def _trocar_por_texto(el, texto):
    """Tira o elemento e põe `texto` no lugar, sem perder o que vinha depois dele (o tail)."""
    pai = el.getparent()
    if pai is None:
        return
    resto = (texto or "") + (el.tail or "")
    anterior = el.getprevious()
    if anterior is not None:
        anterior.tail = (anterior.tail or "") + resto
    else:
        pai.text = (pai.text or "") + resto
    pai.remove(el)
