"""HTML salvo (página, relatório exportado, e-mail em HTML): Markdown pelo html-to-markdown. Texto escondido sem
ambiguidade (atributo hidden; display:none, opacity:0, visibility:hidden e fonte zero no estilo da própria tag ou nos
atributos de apresentação do SVG) sai do corpo e vira alerta. Na dúvida, o lado é o alerta: no mesmo elemento,
qualquer declaração que esconde, esconde.

Limites (a página não é renderizada): cor do texto contra o fundo (branco sobre azul é visível), estilo de classe numa
folha <style>, comentário e escape dentro do CSS (display:/**/none), calc(), font-size sem unidade do modo quirks,
<foreignObject> tratado como SVG, conteúdo de <symbol>/<defs> desenhado por <use> e declaração que desfaz o oculto no
mesmo elemento (display:none;display:block, visibility:hidden;visibility:inherit): esses vão para o alerta. Imagem
embutida (data:) é lida pela IA; imagem externa nunca é baixada (o Lince não busca nada na rede por causa de um
documento)."""

import base64
import binascii
import io
import re
import secrets

import html_to_markdown
from lxml import etree
from lxml import html as lxml_html

IMAGEM_MAX = 20 * 1024 * 1024  # bytes de uma imagem embutida; maior que isso fica marcada, sem leitura
PROFUNDIDADE_MAX = 2048  # o libxml2 com huge_tree corta aí; o html-to-markdown recebe o mesmo teto
_SEM_CONTEUDO = {"script", "style", "noscript", "template", "iframe", "object", "embed", "head"}
# Estilo da própria tag lido como declarações, a última vence (como no CSS); o atributo de apresentação do SVG
# (display="none", font-size="0"...) vale como declaração anterior ao style. Sem renderizar a página, só isto conta.
_PROPRIEDADES = ("display", "opacity", "visibility", "font-size", "font")
_MANTEM = {"inherit", "unset", "revert", "revert-layer"}  # em propriedade herdada, é o valor do pai
_FONTE_ZERO = re.compile(r"(?:0(?:\.0+)?(?:px|pt|em|rem|%)?|0?\.\d+px|1px)", re.I)
_TAMANHO_ABSOLUTO = re.compile(
    r"(?:\d+(?:\.\d+)?|\.\d+)(?:px|pt|pc|cm|mm|in|q|rem)|xx-small|x-small|small|medium|large|x-large|xx-large|xxx-large",
    re.I | re.ASCII,  # sem espaço antes da unidade e sem dígito Unicode: o navegador descarta os dois
)  # só valor válido desfaz o zero herdado; em, %, smaller/larger e inválidos mantêm o pai
_NUMERO_CSS = re.compile(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)%?", re.I | re.ASCII)  # ASCII: sem inf, nan, ٠
_TAMANHO_NO_ATALHO = re.compile(r"(?<![\w.])(0|[\d.]+(?:px|pt|em|rem|%))(?=\s*/|\s|$)", re.I)  # font: 0/0 a
# partes do SVG que nunca são desenhadas: o texto delas não aparece
_SVG_NAO_DESENHA = {"defs", "symbol", "clippath", "mask", "pattern", "marker", "metadata", "desc", "lineargradient",
                    "radialgradient", "filter"}  # fmt: skip
_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w.:-]+)""", re.I)
_PROLOGO = re.compile(r"^\s*<\?xml[^>]*\?>", re.I)  # XHTML: o lxml recusa str com declaração de encoding
# o que os navegadores fazem com o charset declarado (WHATWG Encoding): latin-1 e ASCII são windows-1252, e UTF-16
# declarado sem BOM é lido como UTF-8 (o próprio <meta> só é legível se o arquivo não for UTF-16)
# rótulos do WHATWG Encoding que o <meta> pode declarar (só codificações compatíveis com ASCII, as únicas em que o
# próprio <meta> é legível), com o codec que o navegador usa. Fora daqui, o rótulo é ignorado: é o que o navegador
# faz com rótulo desconhecido (x-user-defined e iso-2022-kr, raros, também caem aqui)
_COMO_NAVEGADOR = {
    **dict.fromkeys(["utf-8", "utf8", "unicode-1-1-utf-8", "unicode11utf8", "unicode20utf8", "x-unicode20utf8",
                     "utf-16", "utf-16le", "utf-16be", "unicode", "unicodefeff", "unicodefffe", "csunicode",
                     "iso-10646-ucs-2", "ucs-2"], "utf-8"),
    **dict.fromkeys(["iso-8859-1", "iso8859-1", "iso88591", "iso_8859-1", "iso_8859-1:1987", "iso-ir-100", "latin1",
                     "latin-1", "l1", "cp819", "ibm819", "csisolatin1", "ascii", "us-ascii", "ansi_x3.4-1968",
                     "windows-1252", "cp1252", "x-cp1252"], "cp1252"),
    **dict.fromkeys(["iso-8859-9", "iso8859-9", "iso88599", "iso_8859-9", "iso-ir-148", "latin5", "l5",
                     "csisolatin5"], "cp1254"),
    **dict.fromkeys(["iso-8859-11", "iso8859-11", "iso885911", "tis-620", "dos-874", "windows-874"], "cp874"),
    **{k: f"cp125{n}" for n in range(9) for k in (f"windows-125{n}", f"cp125{n}", f"x-cp125{n}")},
    **{k: f"iso8859-{n}" for n in (2, 3, 4, 5, 6, 7, 8, 10, 13, 14, 15, 16)
       for k in (f"iso-8859-{n}", f"iso8859-{n}", f"iso8859{n}", f"iso_8859-{n}")},
    **dict.fromkeys(["latin2", "l2", "csisolatin2"], "iso8859-2"), **dict.fromkeys(["latin3", "l3"], "iso8859-3"),
    **dict.fromkeys(["latin4", "l4"], "iso8859-4"), **dict.fromkeys(["latin6", "l6"], "iso8859-10"),
    **dict.fromkeys(["cyrillic", "csisolatincyrillic"], "iso8859-5"), **dict.fromkeys(["arabic"], "iso8859-6"),
    **dict.fromkeys(["greek", "greek8"], "iso8859-7"),
    **dict.fromkeys(["hebrew", "visual", "iso-8859-8-i", "logical", "csiso88598i"], "iso8859-8"),
    **dict.fromkeys(["latin9", "l9"], "iso8859-15"),
    **dict.fromkeys(["gbk", "gb2312", "gb_2312", "gb_2312-80", "csgb2312", "iso-ir-58", "gb18030", "x-gbk",
                     "chinese"], "gb18030"),
    **dict.fromkeys(["big5", "big5-hkscs", "x-x-big5", "cn-big5", "csbig5"], "big5hkscs"),
    **dict.fromkeys(["shift_jis", "shift-jis", "sjis", "windows-31j", "ms_kanji", "ms932", "x-sjis",
                     "csshiftjis"], "cp932"),
    **dict.fromkeys(["euc-jp", "x-euc-jp", "cseucpkdfmtjapanese"], "euc_jp"),
    **dict.fromkeys(["euc-kr", "ks_c_5601-1987", "ks_c_5601-1989", "ksc5601", "ksc_5601", "windows-949",
                     "cseuckr", "korean"], "cp949"),
    **dict.fromkeys(["iso-2022-jp", "csiso2022jp"], "iso2022_jp"),
    **dict.fromkeys(["koi8-r", "koi8_r", "koi", "koi8", "cskoi8r"], "koi8_r"),
    **dict.fromkeys(["koi8-u", "koi8-ru"], "koi8_u"),
    **dict.fromkeys(["macintosh", "mac", "x-mac-roman", "csmacintosh"], "mac_roman"),
    "ibm866": "cp866", "866": "cp866", "cp866": "cp866",
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
    # o conversor descarta SVG inline: o texto que ele desenha (rótulos de gráfico) fica, no lugar e na mesma linha
    for svg in [s for s in raiz.iter("svg") if next(s.iterancestors("svg"), None) is None]:  # só o mais externo
        rotulos = " ".join(" ".join(svg.itertext()).split())
        troca = etree.Element("span")
        troca.text, troca.tail = f" {rotulos} " if rotulos else "", svg.tail
        svg.getparent().replace(svg, troca)
    prefixo = _prefixo_livre(texto)
    marcas = _imagens(raiz, pasta, plano["tarefas"], prefixo)
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
        md = re.sub(prefixo + r"\d+X", lambda m: marcas.get(m.group(0), m.group(0)), md)
    plano["md"] = md
    if r.warnings:
        plano["ilegiveis"] += sorted({f"html: {w.message}"[:200] for w in r.warnings})
    if ocultos:
        plano["texto_oculto"].append(
            {"tecnicas": sorted({t for t, _ in ocultos}), "texto": " ".join(t for _, t in ocultos)[:500]}
        )
    return plano


def _declaracoes(el, em_svg) -> list:
    """(propriedade, valor) na ordem em que valem: atributos de apresentação (só no SVG; no HTML o navegador os
    ignora), depois o style."""
    saida = [] if not em_svg else [(n, el.get(n).strip().lower()) for n in _PROPRIEDADES[:4] if el.get(n) is not None]
    for parte in (el.get("style") or "").split(";"):
        nome, _, valor = parte.partition(":")
        nome = nome.strip().lower()
        if nome in _PROPRIEDADES:
            saida.append((nome, valor.lower().replace("!important", "").strip()))
    return saida


def _tirar_oculto(raiz) -> list:
    """Tira o que não aparece (scripts, estilos, <noscript>, partes do SVG que não são desenhadas) e o texto
    escondido, antes de olhar as imagens: imagem dentro de bloco oculto não é paga nem lida. Percurso com pilha, sem
    recursão, levando o estado herdado (fonte zero, invisível): o texto de um nó escondido sai mesmo que um
    descendente volte a aparecer. O(1) por nó, mais o tamanho do style."""
    ocultos, pilha = [], [(raiz, False, False, False)]  # (elemento, fonte zero, invisível, dentro de SVG)
    while pilha:
        el, zero, invisivel, em_svg = pilha.pop()
        if not isinstance(el.tag, str):
            continue
        if el.tag in _SEM_CONTEUDO:
            _esvaziar(el)
            continue
        tecnica = "atributo hidden" if el.get("hidden") is not None else None
        em_svg = em_svg or el.tag == "svg"
        tecnica = tecnica or ("parte do SVG que não é desenhada" if em_svg and el.tag in _SVG_NAO_DESENHA else None)
        # no mesmo elemento, qualquer declaração que esconde, esconde (CSS descarta valor inválido e dá precedência
        # ao !important; sem reproduzir isso, o lado seguro é o alerta, nunca deixar texto escondido no corpo). A
        # ordem só serve para desfazer o que vem do pai; inherit/unset/revert ficam com o valor do pai.
        # esconder: qualquer declaração que esconde, esconde. Desfazer o herdado: só se a ÚLTIMA declaração da
        # propriedade é um valor válido que desfaz, e o style não tem aspas nem parênteses (onde um ";" de string ou
        # url() poderia forjar uma declaração). Fora isso, fica o do pai; o erro possível vai para o alerta.
        esconde_tudo, esconde_vis, esconde_fonte, ultima_vis, ultimo_tamanho = False, False, False, None, None
        for nome, valor in _declaracoes(el, em_svg):
            if nome == "display" and valor == "none":
                esconde_tudo = True
            elif nome == "opacity" and (m := _NUMERO_CSS.fullmatch(valor)):
                esconde_tudo |= float(m.group(1)) <= 0
            elif nome == "visibility":
                esconde_vis |= valor in ("hidden", "collapse")
                ultima_vis = valor
            elif nome in ("font-size", "font"):
                tamanho = (
                    valor if nome == "font-size" else (m.group(1) if (m := _TAMANHO_NO_ATALHO.search(valor)) else valor)
                )
                esconde_fonte |= bool(_FONTE_ZERO.fullmatch(tamanho))
                ultimo_tamanho = tamanho
        pode_desfazer = not any(c in (el.get("style") or "") for c in "\"'(")
        if esconde_vis:
            invisivel = True
        elif pode_desfazer and ultima_vis == "visible":
            invisivel = False
        if esconde_fonte:
            zero = True
        elif pode_desfazer and ultimo_tamanho is not None and _TAMANHO_ABSOLUTO.fullmatch(ultimo_tamanho):
            zero = False  # tamanho próprio absoluto válido: volta a aparecer
        tecnica = tecnica or ("estilo que esconde" if esconde_tudo else None)
        if tecnica:  # sem volta: nenhum descendente desfaz display:none, opacity:0 nem hidden
            if conteudo := " ".join(el.text_content().split()):
                ocultos.append((tecnica, conteudo))
            _esvaziar(el)
            continue
        if invisivel and el.tag == "img":  # imagem invisível não é lida (com fonte zero, a imagem aparece)
            _esvaziar(el)
            continue
        if zero or invisivel:  # o texto deste nó e o que vem entre os filhos estão escondidos
            tecnica = "estilo que esconde" if invisivel else "fonte zero"
            for dono, campo in [(el, "text")] + [(f, "tail") for f in el]:
                if (t := getattr(dono, campo)) and t.strip():
                    ocultos.append((tecnica, " ".join(t.split())))
                    setattr(dono, campo, None)
        pilha.extend((f, zero, invisivel, em_svg) for f in reversed(list(el)))
    return ocultos


def _esvaziar(el):
    """Esvazia em O(1), mantendo o que vem depois (tail). Vira <span>: uma <img> ou <hr> esvaziada não deixa rastro."""
    el.clear(keep_tail=True)
    el.tag = "span"


def _imagens(raiz, pasta, tarefas, prefixo) -> dict:
    """Imagem data: vira tarefa da IA (marca sentinela no lugar, trocada depois da conversão); decorativa sai;
    externa e ilegível ficam marcadas com o alt."""
    from PIL import Image

    marcas = {}
    for el in list(raiz.iter("img")):
        src, alt = (el.get("src") or "").strip(), " ".join((el.get("alt") or "").split())  # alt com \n não abre bloco
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
        marca = f"{prefixo}{len(marcas)}X" if substituto else ""
        if marca:
            marcas[marca] = substituto
        troca = etree.Element("span")  # a imagem vira um <span> com o texto: troca em O(1), o tail fica
        troca.text, troca.tail = marca, el.tail
        el.getparent().replace(el, troca)
    return marcas


def _prefixo_livre(texto) -> str:
    """Prefixo da sentinela que não aparece no documento: o texto dele nunca é trocado por engano."""
    while (prefixo := f"LINCE{secrets.token_hex(6)}M") in texto:
        pass
    return prefixo


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
