"""HTML salvo (página, relatório exportado, e-mail em HTML): Markdown pelo html-to-markdown, com as mesmas regras do
Office. Texto que a pessoa não vê sai do corpo e vira alerta; imagem embutida (data:) é lida pela IA; imagem externa
nunca é baixada (o Lince não busca nada na rede por causa de um documento)."""

import base64
import binascii
import io
import re

import html_to_markdown
from lxml import html as lxml_html

IMAGEM_MAX = 20 * 1024 * 1024  # bytes de uma imagem embutida; maior que isso fica como pendência registrada
_SEM_CONTEUDO = ("script", "style", "noscript", "template", "iframe", "object", "embed", "head")
_OCULTO = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?:\.0+)?(?:px|pt|em|rem|%)?\s*(?:;|$)"
    r"|opacity\s*:\s*0(?:\.0+)?\s*(?:;|$)",
    re.I,
)
_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w.:-]+)""", re.I)


def decodificar(dados: bytes) -> str:
    """BOM, depois o charset declarado no <meta>, depois UTF-8; sem nada disso, cp1252 (o padrão dos navegadores)."""
    for bom, cod in ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if dados.startswith(bom):
            return dados[len(bom) :].decode(cod, "replace")
    if m := _CHARSET.search(dados[:4096]):
        try:
            return dados.decode(m.group(1).decode("ascii"), "replace")
        except LookupError:
            pass  # charset inventado: segue para as tentativas seguintes
    try:
        return dados.decode("utf-8")
    except UnicodeDecodeError:
        return dados.decode("cp1252", "replace")


def planejar_html(path, pasta) -> dict:
    """Plano no formato do Office: Markdown com marcas {{IA:id}} onde vai a leitura de cada imagem embutida."""
    from PIL import Image

    texto = decodificar(open(path, "rb").read())
    raiz = lxml_html.document_fromstring(texto or "<html></html>")
    tarefas, ocultos, marcas = [], [], {}

    for el in list(raiz.iter("img")):
        src, alt = (el.get("src") or "").strip(), (el.get("alt") or "").strip()
        substituto = f"[imagem: {alt or 'sem descrição'}, externa, não lida]"
        if src.lower().startswith("data:image/"):
            try:
                dados = base64.b64decode(src.split(",", 1)[1], validate=False) if "," in src else b""
                if len(dados) > IMAGEM_MAX:
                    raise ValueError("imagem embutida grande demais")
                im = Image.open(io.BytesIO(dados))
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

    for el in list(raiz.iter()):
        if not isinstance(el.tag, str):
            continue
        if el.tag in _SEM_CONTEUDO:
            _trocar_por_texto(el, "")
        elif el.get("hidden") is not None or _OCULTO.search(el.get("style") or ""):
            if conteudo := " ".join(el.text_content().split()):
                tecnica = "atributo hidden" if el.get("hidden") is not None else "estilo que esconde"
                ocultos.append((tecnica, conteudo))
            _trocar_por_texto(el, "")

    corpo = lxml_html.tostring(raiz, encoding="unicode")
    opcoes = html_to_markdown.ConversionOptions(skip_images=True, max_depth=256)
    md = html_to_markdown.convert(corpo, opcoes).content.strip()
    for marca, ia in marcas.items():
        md = md.replace(marca, ia)
    seguranca = []
    if ocultos:
        seguranca.append({"tecnicas": sorted({t for t, _ in ocultos}), "texto": " ".join(t for _, t in ocultos)[:500]})
    return {"tipo": "office", "md": md, "extras": [], "tarefas": tarefas, "texto_oculto": seguranca, "ilegiveis": []}


def metadados_html(path) -> dict:
    """Título, autor, descrição e palavras-chave do <head>."""
    raiz = lxml_html.document_fromstring(decodificar(open(path, "rb").read()) or "<html></html>")
    props = {}
    if (titulo := raiz.findtext(".//title")) and titulo.strip():
        props["title"] = " ".join(titulo.split())
    for meta in raiz.iter("meta"):
        nome, valor = (meta.get("name") or "").lower(), (meta.get("content") or "").strip()
        chave = {"author": "creator", "description": "description", "keywords": "keywords"}.get(nome)
        if chave and valor:
            props.setdefault(chave, valor)
    return {"propriedades": props} if props else {}


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
