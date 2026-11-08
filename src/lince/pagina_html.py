"""HTML salvo (página, relatório exportado, e-mail em HTML): Markdown pelo html-to-markdown. Texto escondido sem
ambiguidade (atributo hidden; display:none, opacity:0, visibility:hidden e fonte zero no estilo da própria tag ou nos
atributos de apresentação do SVG) sai do corpo e vira alerta, assim como o conteúdo que o navegador não desenha
(<datalist>, <noembed>, <noframes>, <dialog> fechado), a não ser que o display do autor o mostre. O style é lido pelo
tinycss2, com a cascata do CSS (última declaração válida, !important vence, valor inválido descartado) e herança de
visibility e font-size.

Limites (a página não é renderizada): cor do texto contra o fundo (branco sobre azul é visível, e a cor transparente
também fica de fora), estilo de classe numa folha <style>, valor com função (calc, var, min, max, clamp), opacidade
quase zero, layout (fora da tela, text-indent negativo, tamanho zero com overflow, clip, zoom), font-size sem unidade
do modo quirks, popover fechado e rp/title no corpo (ficam no corpo), hidden="until-found" e dialog fechado (vão para
o alerta, embora a pessoa possa abri-los), altura de linha em unidade de tela e oblique com ângulo no atalho font
(recusados), HTML dentro de <foreignObject> tratado como SVG e conteúdo de <symbol>/<defs> desenhado por <use> e o
texto alternativo de <canvas>, <video> e <audio> (vai para o alerta, que guarda até 500 caracteres; a mídia fica no
corpo), e técnicas que o Lince não avalia: content-visibility, transform:scale(0), filter e clip-path. Imagem
embutida (data:) e desenho SVG (pelo resvg, a partir da árvore já sem o texto escondido, então o desenho só mostra
texto que ficou no corpo; ícone abaixo de 48 px e SVG em trecho invisível não) são lidos pela IA; imagem externa
nunca é baixada (o Lince não busca nada na rede por causa de um documento)."""

import base64
import binascii
import io
import re
import secrets

import html_to_markdown
import tinycss2
from lxml import etree
from lxml import html as lxml_html

from .desenho_svg import de_html, desenhar_lote, tamanho

IMAGEM_MAX = 20 * 1024 * 1024  # bytes de uma imagem embutida; maior que isso fica marcada, sem leitura
PROFUNDIDADE_MAX = 2048  # o libxml2 com huge_tree corta aí; o html-to-markdown recebe o mesmo teto
DESENHOS_MAX = 50  # SVGs inline desenhados por página: cada um é uma leitura da IA; o resto fica no texto
_SEM_CONTEUDO = {"script", "style", "noscript", "template", "iframe", "object", "embed", "head"}
# Estilo da própria tag lido pelo tinycss2 (a sintaxe do CSS: comentário, escape, string, url(), !important), com a
# cascata do CSS: vale a última declaração válida, e !important vence; valor inválido é descartado. O atributo de
# apresentação do SVG (display="none", font-size="0"...) só vale se o style não declara a mesma propriedade.
_PROPRIEDADES = ("display", "opacity", "visibility", "font-size", "font")
_MANTEM = {"inherit", "unset", "revert", "revert-layer"}  # em propriedade herdada, é o valor do pai
FONTE_INVISIVEL_PX = 1  # até 1px a letra não se lê: conta como escondida (como fonte minúscula no Word e no PDF)
_PX = {"px": 1, "pt": 96 / 72, "pc": 16, "in": 96, "cm": 96 / 2.54, "mm": 96 / 25.4, "q": 96 / 101.6}
_DO_PAI = {"em": 1, "ex": 0.5, "ch": 0.5, "cap": 0.7, "ic": 1, "lh": 1.2}  # múltiplos aproximados da fonte do pai
_DA_RAIZ = {"rem": 1, "rex": 0.5, "rch": 0.5, "rcap": 0.7, "ric": 1, "rlh": 1.2}
_TELA = {"vw", "vh", "vi", "vb", "vmin", "vmax", "svw", "svh", "lvw", "lvh", "dvw", "dvh", "svmin", "svmax", "lvmin",
         "lvmax", "dvmin", "dvmax", "cqw", "cqh", "cqi", "cqb", "cqmin", "cqmax"}  # fmt: skip
_TELA_PX = 10  # 1vw numa tela de 1000px: só importa saber que não é zero
_TAMANHOS = {"xx-small": 9, "x-small": 10, "small": 13, "medium": 16, "large": 18, "x-large": 24, "xx-large": 32,
             "xxx-large": 48}  # fmt: skip
_FONTES_DO_SISTEMA = {"caption", "icon", "menu", "message-box", "small-caption", "status-bar"}
_PREFIXO_FONT = {"normal": "normal", "italic": "estilo", "oblique": "estilo", "small-caps": "variante",
                 "bold": "peso", "bolder": "peso", "lighter": "peso",
                 **dict.fromkeys(["ultra-condensed", "extra-condensed", "condensed", "semi-condensed", "semi-expanded",
                                  "expanded", "extra-expanded", "ultra-expanded"], "largura")}  # fmt: skip
_GLOBAIS = _MANTEM | {"initial"}  # "default" é reservado, mas não é global: o navegador descarta a declaração
_NAO_FAMILIA = _GLOBAIS | {"default"}
# o navegador não desenha: listas de sugestão, alternativos de plugin e frame (e o dialog fechado, à parte)
_NAO_MOSTRA = {"datalist", "noembed", "noframes"}
_COM_ALTERNATIVO = {"canvas", "video", "audio"}  # a mídia fica; o texto alternativo (para navegador antigo) não
_DISPLAY = {"none", "block", "inline", "inline-block", "flex", "inline-flex", "grid", "inline-grid", "flow-root",
            "table", "inline-table", "table-row", "table-cell", "table-column", "table-row-group", "table-column-group",
            "table-header-group", "table-footer-group", "table-caption", "list-item", "contents", "run-in", "ruby",
            "ruby-base", "ruby-text", "ruby-base-container", "ruby-text-container", "math", "-webkit-box",
            "-webkit-inline-box", "-ms-flexbox", "-ms-inline-flexbox"}  # fmt: skip
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
    herdado = {}
    ocultos = _tirar_oculto(raiz, herdado)
    prefixo = _prefixo_livre(texto)
    # o desenho sai da árvore já limpa: só mostra texto que ficou no corpo; SVG que herda o invisível não é desenhado
    externos = [s for s in raiz.iter("svg") if next(s.iterancestors("svg"), None) is None]  # só o mais externo
    copias = {svg: de_html(svg) if herdado.get(svg) is False else None for svg in externos}
    for svg in externos:  # o CSS do <style> do SVG não é texto do corpo
        for estilo in list(svg.iter("style")):
            _esvaziar(estilo)
    # ícone (abaixo de 48 px) não vai para a IA; acima do teto, o desenho fica só no texto
    grandes = [s for s in externos if copias[s] is not None and (lado := tamanho(copias[s])) and min(lado) >= 48]
    if len(grandes) > DESENHOS_MAX:
        plano["ilegiveis"].append(f"svg: {len(grandes) - DESENHOS_MAX} desenhos além do teto de {DESENHOS_MAX}")
    lote = grandes[:DESENHOS_MAX]
    feitos = desenhar_lote([(copias[s], f"{pasta}/ia/svg{k + 1:02d}.png") for k, s in enumerate(lote)])
    if (cortados := feitos.count("tempo")) or feitos.count("ilegivel"):
        plano["ilegiveis"].append(
            f"svg: desenhos não lidos: {cortados} por tempo esgotado, {feitos.count('ilegivel')} fora do padrão"
        )
    resultado = {svg: (k, feito) for k, (svg, feito) in enumerate(zip(lote, feitos, strict=True))}
    # o conversor descarta SVG inline: o texto que ele desenha (rótulos de gráfico) fica, no lugar e na mesma
    # linha, para valer mesmo sem a IA; o desenho lido entra antes dele
    desenhos = {}
    for k, svg in enumerate(externos):
        rotulos = " ".join(" ".join(svg.itertext()).split())
        troca = etree.Element("span")
        troca.text, troca.tail = f" {rotulos} " if rotulos else "", svg.tail
        if (n := resultado.get(svg, (0, ""))) and n[1] == "lido":
            tid = f"svg{n[0] + 1:02d}"
            plano["tarefas"].append({"id": tid, "modo": "imagem", "ancora": f"desenho {len(desenhos) + 1}"})
            desenhos[marca := f"{prefixo}S{k}X"] = f"{{{{IA:{tid}}}}}"
            troca.text = marca + troca.text
        svg.getparent().replace(svg, troca)
    marcas = _imagens(raiz, pasta, plano["tarefas"], prefixo) | desenhos
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
        md = re.sub(prefixo + r"S?\d+X", lambda m: marcas.get(m.group(0), m.group(0)), md)
    plano["md"] = md
    if r.warnings:
        plano["ilegiveis"] += sorted({f"html: {w.message}"[:200] for w in r.warnings})
    if ocultos:
        plano["texto_oculto"].append(
            {"tecnicas": sorted({t for t, _ in ocultos}), "texto": " ".join(t for _, t in ocultos)[:500]}
        )
    return plano


def _tokens(valor) -> list:
    return [t for t in valor if t.type not in ("whitespace", "comment")]


def _classificar(nome, tokens, pai_px, raiz_px, atributo_svg=False):
    """O que a declaração diz, ou None se o navegador a descarta (aí vale a anterior).
    display: "none"/"outro"; opacity: número; visibility: "esconde"/"mostra"/"herda"; font-size: px calculado."""
    unico = tokens[0] if len(tokens) == 1 else None
    global_ = (
        unico.lower_value if unico is not None and unico.type == "ident" and unico.lower_value in _GLOBAIS else None
    )
    if nome == "display":  # uma palavra só: combinações de duas palavras raras inline ficam como inválidas
        if global_ in ("revert", "revert-layer"):
            return "revert"  # volta à folha do navegador, onde [hidden] é display:none
        if global_:
            return "outro"  # o valor do pai ou o inicial (inline): o pai está à mostra
        ok = unico is not None and unico.type == "ident" and unico.lower_value in _DISPLAY
        return ("none" if unico.lower_value == "none" else "outro") if ok else None
    if nome == "opacity":
        if global_:
            return 1.0
        if unico is not None and unico.type in ("number", "percentage"):
            return unico.value / (100 if unico.type == "percentage" else 1)
        return None
    if nome == "visibility":
        if global_:
            return "mostra" if global_ == "initial" else "herda"
        v = unico.lower_value if unico is not None and unico.type == "ident" else None
        return "esconde" if v in ("hidden", "collapse") else ("mostra" if v == "visible" else None)
    if global_:  # font-size e font
        return 16.0 if global_ == "initial" else pai_px
    if nome == "font-size":
        return _px(unico, pai_px, raiz_px, atributo_svg) if unico is not None else None
    return _px_do_atalho(tokens, pai_px, raiz_px)


def _px(t, pai_px, raiz_px, atributo_svg=False):
    """Tamanho da fonte em px como o navegador calcula; None se o valor é inválido."""
    if t.type in ("dimension", "percentage", "number") and t.value < 0:
        return None
    if t.type == "number":  # sem unidade: só o 0, a não ser no atributo de apresentação do SVG (px)
        return float(t.value) if atributo_svg or t.value == 0 else None
    if t.type == "percentage":
        return pai_px * t.value / 100
    if t.type == "dimension":
        u = t.lower_unit
        if u in _PX:
            return t.value * _PX[u]
        if u in _DO_PAI:
            return t.value * pai_px * _DO_PAI[u]
        if u in _DA_RAIZ:
            return t.value * raiz_px * _DA_RAIZ[u]
        if u in _TELA:
            return t.value * _TELA_PX
        return None
    if t.type == "ident":
        v = t.lower_value
        if v in _TAMANHOS:
            return float(_TAMANHOS[v])
        return {"smaller": pai_px / 1.2, "larger": pai_px * 1.2}.get(v)
    return None


def _px_do_atalho(tokens, pai_px, raiz_px):
    """font: [estilo, variante, peso, largura] tamanho[/altura] família[, família…], ou uma fonte do sistema. Fora
    dessa gramática, o navegador descarta a declaração."""
    if len(tokens) == 1 and tokens[0].type == "ident" and tokens[0].lower_value in _FONTES_DO_SISTEMA:
        return 13.0
    k, vistos = 0, set()
    while k < len(tokens) and k < 4:  # prefixo: estilo, variante, peso e largura, cada um no máximo uma vez
        t = tokens[k]
        chave = _PREFIXO_FONT.get(t.lower_value) if t.type == "ident" else None
        if chave is None and t.type == "number" and 1 <= t.value <= 1000:
            chave = "peso"
        if chave is None or (chave in vistos and chave != "normal"):
            break
        vistos.add(chave)
        k += 1
    if k >= len(tokens) or (tamanho := _px(tokens[k], pai_px, raiz_px)) is None:
        return None
    resto = tokens[k + 1 :]
    if resto and resto[0].type == "literal" and resto[0].value == "/":
        altura = resto[1] if len(resto) > 1 else None
        valida = (
            altura is not None
            and (altura.type not in ("number", "percentage", "dimension") or altura.value >= 0)
            and (
                altura.type in ("number", "percentage")
                or (altura.type == "dimension" and altura.lower_unit in _PX | _DO_PAI | _DA_RAIZ)
                or (altura.type == "ident" and altura.lower_value == "normal")
            )
        )
        if not valida:
            return None
        resto = resto[2:]
    itens, atual = [], []
    for r in resto + [None]:  # família: lista separada por vírgula; cada item é uma string ou idents seguidos
        if r is None or (r.type == "literal" and r.value == ","):
            itens.append(atual)
            atual = []
        else:
            atual.append(r)
    for item in itens:
        so_string = len(item) == 1 and item[0].type == "string"
        so_idents = item and all(x.type == "ident" and x.lower_value not in _NAO_FAMILIA for x in item)
        if not (so_string or so_idents):
            return None
    return tamanho


def _efetivas(el, em_svg, pai_px, raiz_px) -> dict:
    """Valor que vale para cada propriedade no próprio elemento, pela cascata do CSS."""
    normais, importantes = {}, {}
    for d in tinycss2.parse_blocks_contents(el.get("style") or "", skip_comments=True, skip_whitespace=True):
        if d.type != "declaration" or d.lower_name not in _PROPRIEDADES or not (tokens := _tokens(d.value)):
            continue
        nome = "font-size" if d.lower_name == "font" else d.lower_name
        if (valor := _classificar(d.lower_name, tokens, pai_px, raiz_px)) is not None:
            (importantes if d.important else normais)[nome] = valor
    efetivas = {**normais, **importantes}
    if em_svg:  # atributo de apresentação: abaixo de qualquer declaração do style
        for nome in ("display", "opacity", "visibility", "font-size"):
            if nome not in efetivas and (atributo := el.get(nome)) is not None:
                tokens = _tokens(tinycss2.parse_component_value_list(atributo))
                if tokens and (valor := _classificar(nome, tokens, pai_px, raiz_px, atributo_svg=True)) is not None:
                    efetivas[nome] = valor
    return efetivas


def _tirar_oculto(raiz, svgs=None) -> list:
    """Tira o que não aparece (scripts, estilos, <noscript>, partes do SVG que não são desenhadas) e o texto
    escondido, antes de olhar as imagens: imagem dentro de bloco oculto não é paga nem lida. Percurso com pilha, sem
    recursão, levando o estado herdado (tamanho da fonte em px, invisível): o texto de um nó escondido sai mesmo que
    um descendente volte a aparecer. O(1) por nó, mais o tamanho do style. Dentro do SVG a estrutura que o desenho
    usa fica (defs, gradientes, marcadores, padrões e o <style>) e só o texto escondido sai: desenhado a partir desta
    árvore, o SVG só mostra texto que ficou no corpo. Em `svgs` (opcional) fica, para cada <svg> mais externo, se ele
    herda o invisível do HTML."""
    ocultos, raiz_px = [], 16.0
    pilha = [(raiz, 16.0, False, None)]  # (elemento, fonte herdada em px, invisível, <svg> mais externo ou None)
    while pilha:
        el, px, invisivel, dono = pilha.pop()
        if not isinstance(el.tag, str):
            continue
        dono = dono if dono is not None or el.tag != "svg" else el
        em_svg = dono is not None
        if el.tag in _SEM_CONTEUDO:
            if em_svg and el.tag == "style":  # o <style> do SVG fica para o desenho só com o CSS: um elemento
                for f in list(el):  # dentro dele não passaria pela limpeza e um <use> o desenharia
                    if conteudo := " ".join(" ".join(f.itertext()).split()):
                        ocultos.append(("conteúdo que o navegador não mostra", conteudo))
                    el.remove(f)
            else:
                _esvaziar(el)
            continue
        efetivas = _efetivas(el, em_svg, px, raiz_px)
        tecnica = None
        if el.get("hidden") is not None and not em_svg and efetivas.get("display") != "outro":
            tecnica = "atributo hidden"  # display do autor vence o hidden; revert volta a ele; no SVG não vale
        elif (
            not em_svg
            and efetivas.get("display") != "outro"
            and (el.tag in _NAO_MOSTRA or (el.tag == "dialog" and el.get("open") is None))
        ):
            tecnica = "conteúdo que o navegador não mostra"  # o display do autor vence, como no hidden
        elif em_svg and el.tag in _SVG_NAO_DESENHA:
            if el.tag not in ("desc", "metadata"):  # referenciável (use, fill, marker): a estrutura fica, o texto sai
                partes = []  # o CSS de um <style> aqui dentro fica para o desenho e não é texto escondido
                for f in el.iter():
                    if isinstance(f.tag, str) and f.tag != "style" and f.text:
                        partes.append(f.text)
                        f.text = None
                    if f is not el and f.tail:
                        partes.append(f.tail)
                        f.tail = None
                if conteudo := " ".join(" ".join(partes).split()):
                    ocultos.append(("parte do SVG que não é desenhada", conteudo))
                continue
            tecnica = "parte do SVG que não é desenhada"
        elif efetivas.get("display") == "none" or efetivas.get("opacity", 1.0) <= 0:
            tecnica = "estilo que esconde"
        if tecnica:  # sem volta: nenhum descendente desfaz display:none, opacity:0 nem hidden
            if conteudo := " ".join(el.text_content().split()):
                ocultos.append((tecnica, conteudo))
            _esvaziar(el)
            continue
        if (vis := efetivas.get("visibility")) in ("esconde", "mostra"):
            invisivel = vis == "esconde"
        px = efetivas.get("font-size", px)
        if el is raiz:
            raiz_px = px
        if svgs is not None and el is dono:
            svgs[el] = invisivel
        if not em_svg and el.tag in _COM_ALTERNATIVO:
            # o texto alternativo, na ordem do documento, vai para o alerta, com a mídia visível ou não
            if conteudo := " ".join(" ".join(el.itertext()).split()):
                ocultos.append(("conteúdo que o navegador não mostra", conteudo))
            if invisivel:  # mídia invisível: sai como a imagem invisível
                _esvaziar(el)
                continue
            # a mídia fica com os atributos e os <source>/<track> que o navegador usaria: os filhos diretos e a cadeia
            # que o parser HTML4 do libxml2 aninha um dentro do outro; fonte de dentro do alternativo não conta
            faixas, cadeia = [], [f for f in reversed(el) if f.tag in ("source", "track")]
            while cadeia:  # pilha: ordem do documento e custo linear, com milhares de fontes irmãs ou aninhadas
                f = cadeia.pop()
                faixas.append((f.tag, dict(f.attrib)))
                cadeia.extend(g for g in reversed(f) if g.tag in ("source", "track"))
            for f in list(el):
                el.remove(f)
            el.text = None
            for tag, atributos in faixas:
                el.append(etree.Element(tag, atributos))
            continue
        if invisivel and el.tag in (
            "img",
            "image",
        ):  # imagem invisível não é lida nem desenhada (com fonte zero, aparece)
            _esvaziar(el)
            continue
        if px <= FONTE_INVISIVEL_PX or invisivel:  # o texto deste nó e o que vem entre os filhos estão escondidos
            tecnica = "estilo que esconde" if invisivel else "fonte zero"
            for dono_texto, campo in [(el, "text")] + [(f, "tail") for f in el]:
                if (t := getattr(dono_texto, campo)) and t.strip():
                    ocultos.append((tecnica, " ".join(t.split())))
                    setattr(dono_texto, campo, None)
        pilha.extend((f, px, invisivel, dono) for f in reversed(list(el)))
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


# entidade interna expandida sob a guarda de amplificação do libxml2 (ativa sem huge_tree: a bomba de entidades para
# a leitura); entidade externa e DTD nunca são carregados
_SVG_XML = etree.XMLParser(
    resolve_entities="internal", no_network=True, huge_tree=False, recover=True, remove_comments=True, remove_pis=True
)
_SVG_LINHA = {"text", "foreignobject", "title"}  # cada um vira uma linha (o title aninhado fica, como no inline)


def planejar_svg(path, pasta) -> dict:
    """Arquivo .svg: o desenho vai para a IA como imagem (pelo resvg) e o texto desenhado (rótulos de diagrama e
    gráfico) fica no corpo, uma linha por <text>, na ordem do documento, para valer mesmo sem a IA. <title> e <desc>
    da raiz vão para as propriedades. O oculto segue as regras do SVG inline. Lido como XML sem rede. Sem o resvg,
    a imagem embutida (data:) é lida à parte."""
    plano = {"tipo": "office", "md": "", "extras": [], "tarefas": [], "texto_oculto": [], "ilegiveis": []}
    try:
        svg = etree.fromstring(open(path, "rb").read(), _SVG_XML)
    except etree.XMLSyntaxError:  # vazio ou sem nenhum elemento
        svg = None
    if svg is None or etree.QName(svg).localname != "svg":
        return plano
    if _SVG_XML.error_log:
        plano["ilegiveis"].append("svg: XML fora do padrão; lido até onde dá")
    raiz = lxml_html.document_fromstring("<html><body></body></html>")
    pilha = [(svg, raiz.find("body"))]
    while pilha:  # cópia na árvore do HTML, sem namespace: xlink:href vira href, svg:text vira text
        el, pai = pilha.pop()
        if not isinstance(el.tag, str):  # entidade que sobrou do recover: fica de fora
            continue
        novo = etree.SubElement(pai, etree.QName(el).localname.lower())
        for nome, valor in el.attrib.items():
            novo.set(etree.QName(nome).localname, valor)
        novo.text, novo.tail = el.text, el.tail
        pilha.extend((f, novo) for f in reversed(el))
    copia = raiz.find("body/svg")
    props = {}
    for f in [f for f in copia if f.tag in ("title", "desc")]:
        if texto := " ".join(f.text_content().split()):
            props.setdefault("title" if f.tag == "title" else "description", texto)
        _esvaziar(f)
    plano["propriedades"] = props
    herdado = {}
    ocultos = _tirar_oculto(raiz, herdado)
    # desenhado da árvore já limpa: só mostra texto que ficou no corpo
    desenho = (
        desenhar_lote([(de_html(copia), f"{pasta}/ia/svg01.png")])[0] if herdado.get(copia) is False else "escondido"
    )
    for estilo in list(copia.iter("style")):  # o CSS do <style> não é texto do corpo
        _esvaziar(estilo)
    if desenho != "lido":  # sem o desenho, a <image> embutida passa pelo mesmo caminho da <img>
        for im in copia.iter("image"):
            im.tag, im.attrib["src"] = "img", im.get("href") or ""
    prefixo = _prefixo_livre(" ".join(raiz.itertext()))
    marcas = _imagens(raiz, pasta, plano["tarefas"], prefixo)
    linhas = []
    if desenho == "lido":
        plano["tarefas"].append({"id": "svg01", "modo": "imagem", "ancora": props.get("title") or "desenho"})
        linhas.append("{{IA:svg01}}")
    elif desenho != "branco":
        motivo = {
            "sem resvg": "resvg indisponível",
            "tempo": "tempo esgotado",
            "escondido": "o SVG está escondido",
        }.get(desenho, "fora do padrão")
        plano["ilegiveis"].append(f"svg: desenho não lido ({motivo}); só o texto")
    for el in copia.iter():
        if any(a.tag in _SVG_LINHA for a in el.iterancestors()):
            continue
        if el.tag in _SVG_LINHA and (texto := " ".join(" ".join(el.itertext()).split())):
            linhas.append(re.sub(prefixo + r"\d+X", lambda m: marcas.get(m.group(0), ""), texto).strip())
        elif el.tag == "span" and el.text in marcas:
            linhas.append(marcas[el.text])
    plano["md"] = "\n\n".join(x for x in linhas if x)
    if ocultos:
        plano["texto_oculto"].append(
            {"tecnicas": sorted({t for t, _ in ocultos}), "texto": " ".join(t for _, t in ocultos)[:500]}
        )
    return plano


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
