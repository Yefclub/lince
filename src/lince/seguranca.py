"""Segurança: texto oculto (branco, minúsculo, invisível, vanish) e padrões de prompt injection."""

import hashlib  # noqa: F401
import io  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import zipfile  # noqa: F401
from xml.etree import ElementTree as ET  # noqa: F401

from .limites import escala

PADROES_INJECAO = [
    # "ignore todas as instruções anteriores", "ignore all the previous rules", "ignore as orientações acima"
    r"ignor\w*\s+(?:(?:all|todas?|todos|as|os|the|any|quaisquer|suas|your)\s+){0,3}"
    r"(?:(?:previous|prior|above|anteriores|acima)\s+)?(instru\w+|regras|rules|orienta\w+|ordens|comandos)",
    r"(system|sistema)\s*prompt|prompt\s+do\s+sistema",
    r"(you are now|voc[eê] (agora )?[eé] (um|uma|o|a))\b",
    r"(revele|reveal|exfiltr\w+|envie para|send to)\b.{0,40}(prompt|senha|password|dados|token|credencia)",
    r"(d[eê]|give|atribua)\s+(a )?nota\s+(10|dez|m[aá]xima)",
    r"recomend\w+\s+(a\s+)?contrata\w+",
    r"desconsider\w+\s+(a|as|o|os|todas?|todos?)?\s*\w+",
    r"(envie|encaminhe|mande|forward|send)\b.{0,60}(e-?mail|@|para fora|externo)",
    r"(nota|instru[cç][aã]o|aviso|mensagem)\s+(para|ao|à|a)\s+(a\s+)?(ia|assistente|sistemas? de ia|modelo|llm)",
    r"system\s+override|modo\s+desenvolvedor|developer\s+mode|jailbreak",
    r"(classifique|aprove|marque)\s+(este|esta|o|a)\s+(candidat\w+|documento|pedido)\s+como",
    r"(pontua[cç][aã]o|score|nota)\s+m[aá]xima",
    r"(assistente|assistant|ia|ai|llm|chatgpt|claude)\s*[:,]\s*(ignore|desconsidere|revele|responda)",
]


def alertas_injecao(txt):
    out = []
    for pad in PADROES_INJECAO:
        for m in re.finditer(pad, txt, flags=re.I):
            ini = max(0, m.start() - 60)
            out.append(txt[ini : m.end() + 60].replace("\n", " ").strip())
    return sorted(set(out))[:20]


def texto_oculto_pdf(page, itens, imgs, W, H):
    """Texto que a IA leria e o humano não vê: invisível fora de imagem (OCR legítimo fica),
    fonte < 2,5 pt, fora da página, ou cor de texto ≈ cor do fundo."""
    from PIL import Image  # noqa: F401

    esc = escala(W, H, 0.5)
    fundo = page.render(scale=esc).to_pil().convert("RGB")
    ocultos = []
    for x in itens:
        motivo = None
        sobre_imagem = any(b[0] - 2 <= x.x <= b[2] + 2 and b[1] - 2 <= x.y <= b[3] + 2 for b in imgs)
        if x.render_mode == 3 and not sobre_imagem:
            motivo = "invisível"
        elif 0 < x.font_size < 2.5:
            motivo = "fonte minúscula"
        elif x.x > W or x.x + x.width < 0 or x.y < 0 or x.y > H:
            motivo = "fora da página"
        else:
            cor = x.fill_color or (0, 0, 0)
            if min(cor) >= 225:  # texto claro: confere o fundo logo acima da linha
                px = (
                    min(fundo.width - 1, max(0, int((x.x + 1) * esc))),
                    min(fundo.height - 1, max(0, int((H - x.y - x.height - 2) * esc))),
                )
                bg = fundo.getpixel(px)
                if sum(abs(a - b) for a, b in zip(cor, bg, strict=False)) < 60:
                    motivo = "cor igual ao fundo"
        if motivo:
            ocultos.append((motivo, x.text))
    return ocultos


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
NAO_CONFERIDO = "texto oculto não conferido: o XML do documento não pôde ser lido"
_DESTAQUES = {"white": "FFFFFF", "black": "000000", "yellow": "FFFF00", "green": "00FF00", "cyan": "00FFFF",
              "magenta": "FF00FF", "blue": "0000FF", "red": "FF0000", "darkBlue": "000080", "darkCyan": "008080",
              "darkGreen": "008000", "darkMagenta": "800080", "darkRed": "800000", "darkYellow": "808000",
              "darkGray": "808080", "lightGray": "C0C0C0"}  # fmt: skip
_TEMA = {"dark1": "dk1", "text1": "dk1", "tx1": "dk1", "light1": "lt1", "background1": "lt1", "bg1": "lt1",
         "dark2": "dk2", "text2": "dk2", "tx2": "dk2", "light2": "lt2", "background2": "lt2", "bg2": "lt2",
         "hyperlink": "hlink", "followedHyperlink": "folHlink"}  # fmt: skip


class XMLIlegivel(Exception):
    """A parte não pôde ser lida para a conferência: quem chama trata como suspeita, nunca como "nada oculto"."""


def _xml(dados):
    """Árvore da parte, tolerando DOCTYPE sem expandir entidade nenhuma (nem interna, nem externa, nem pela rede)."""
    from lxml import etree

    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
    try:
        raiz = etree.fromstring(dados, parser)
    except etree.XMLSyntaxError as e:
        raise XMLIlegivel(str(e)) from None
    if raiz is None:
        raise XMLIlegivel("vazio")
    return raiz


def _rgb(hexa):
    try:
        return tuple(int(hexa[k : k + 2], 16) for k in (0, 2, 4)) if hexa and len(hexa) == 6 else None
    except ValueError:
        return None


def _iguais(a, b):
    return a is not None and b is not None and sum(abs(x - y) for x, y in zip(a, b, strict=True)) < 60


def _esquema_do_tema(z):
    """Cores do tema (dk1, lt1, accent1...) do DrawingML, para resolver themeColor."""
    nome = next((n for n in z.namelist() if re.fullmatch(r"(word|ppt)/theme/theme\d*\.xml", n)), None)
    if nome is None:
        return {}
    cores = {}
    for el in _xml(z.read(nome)).iter(f"{_A}clrScheme"):
        for cor in el:
            filho = next(iter(cor), None)
            if filho is not None:
                cores[_local(cor)] = _rgb(filho.get("lastClr") or filho.get("val"))
        break
    return cores


def _local(el):
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""


class _EstilosWord:
    """Propriedades de run e parágrafo pela cadeia do Word: run, estilo de caractere, estilo de parágrafo (com
    basedOn) e padrão do documento."""

    def __init__(self, raiz):
        self.estilos, self.padrao = {}, None
        if raiz is None:
            return
        for st in raiz.iter(f"{_W}style"):
            base = st.find(f"{_W}basedOn")
            self.estilos[st.get(f"{_W}styleId")] = (base.get(f"{_W}val") if base is not None else None, st)
        self.padrao = raiz.find(f"{_W}docDefaults/{_W}rPrDefault/{_W}rPr")

    def cadeia(self, sid):
        vistos = set()
        while sid and sid in self.estilos and sid not in vistos:
            vistos.add(sid)
            base, st = self.estilos[sid]
            yield st
            sid = base

    def run(self, rpr, ppr, nome):
        """O primeiro valor de `nome` (filho de rPr) pela cadeia, ou None."""
        fontes = [rpr]
        if rpr is not None and (rs := rpr.find(f"{_W}rStyle")) is not None:
            fontes += [st.find(f"{_W}rPr") for st in self.cadeia(rs.get(f"{_W}val"))]
        if ppr is not None and (ps := ppr.find(f"{_W}pStyle")) is not None:
            fontes += [st.find(f"{_W}rPr") for st in self.cadeia(ps.get(f"{_W}val"))]
        fontes.append(self.padrao)
        for f in fontes:
            if f is not None and (el := f.find(f"{_W}{nome}")) is not None:
                return el
        return None

    def sombreamento_paragrafo(self, ppr):
        fontes = [ppr]
        if ppr is not None and (ps := ppr.find(f"{_W}pStyle")) is not None:
            fontes += [st.find(f"{_W}pPr") for st in self.cadeia(ps.get(f"{_W}val"))]
        for f in fontes:
            if f is not None and (el := f.find(f"{_W}shd")) is not None and _rgb(el.get(f"{_W}fill")):
                return _rgb(el.get(f"{_W}fill"))
        return None


def _ligado(el):
    return el is not None and (el.get(f"{_W}val") or "true").lower() not in ("0", "false", "off")


def texto_oculto_docx(path):
    """Texto do corpo do Word que a IA leria e o humano não vê: vanish, fonte minúscula e cor igual ao fundo dela
    (destaque, sombreamento do run ou do parágrafo, fundo da página), com a cor vinda do run, do estilo ou do tema.
    Levanta XMLIlegivel se o corpo, os estilos ou o tema não puderem ser lidos."""
    out = []
    with zipfile.ZipFile(path) as z:
        nomes = set(z.namelist())
        if "word/document.xml" not in nomes:
            return out
        raiz = _xml(z.read("word/document.xml"))
        estilos = _EstilosWord(_xml(z.read("word/styles.xml")) if "word/styles.xml" in nomes else None)
        tema = _esquema_do_tema(z)
    fundo = raiz.find(f"{_W}background")
    pagina = _rgb(fundo.get(f"{_W}color")) if fundo is not None else None
    pagina = pagina or (255, 255, 255)
    for par in raiz.iter(f"{_W}p"):
        ppr = par.find(f"{_W}pPr")
        fundo_par = estilos.sombreamento_paragrafo(ppr) or pagina
        for r in par.iter(f"{_W}r"):
            txt = "".join(t.text or "" for t in r.iter(f"{_W}t"))
            if not txt.strip():
                continue
            rpr = r.find(f"{_W}rPr")
            if _ligado(estilos.run(rpr, ppr, "vanish")):
                out.append(("oculto (vanish)", txt))
                continue
            sz = estilos.run(rpr, ppr, "sz")
            if sz is not None and (sz.get(f"{_W}val") or "").isdigit() and int(sz.get(f"{_W}val")) <= 4:
                out.append(("fonte minúscula", txt))
                continue
            cor_el = estilos.run(rpr, ppr, "color")
            cor = None
            if cor_el is not None:
                cor = tema.get(_TEMA.get(cor_el.get(f"{_W}themeColor"), cor_el.get(f"{_W}themeColor") or ""))
                cor = cor or _rgb(cor_el.get(f"{_W}val"))
            if cor is None:  # automática: o Word escolhe a que contrasta com o fundo
                continue
            destaque = estilos.run(rpr, ppr, "highlight")
            shd = estilos.run(rpr, ppr, "shd")
            atras = (
                (_rgb(_DESTAQUES.get(destaque.get(f"{_W}val"), "")) if destaque is not None else None)
                or (_rgb(shd.get(f"{_W}fill")) if shd is not None else None)
                or fundo_par
            )
            if _iguais(cor, atras):
                out.append(("cor igual ao fundo", txt))
    return out


def texto_oculto_pptx(path):
    """Texto dos slides que a IA leria e o humano não vê: caixa fora do slide, fonte minúscula e cor igual ao fundo
    (preenchimento da forma ou fundo do slide, do layout ou do mestre), com cor de tema resolvida. Levanta
    XMLIlegivel se o pacote não puder ser lido."""
    from pptx import Presentation
    from pptx.enum.dml import MSO_COLOR_TYPE, MSO_FILL
    from pptx.util import Pt

    try:
        prs = Presentation(path)
        with zipfile.ZipFile(path) as z:
            tema = _esquema_do_tema(z)
    except XMLIlegivel:
        raise
    except Exception as e:  # noqa: BLE001 — o python-pptx levanta de tudo com pacote hostil
        raise XMLIlegivel(str(e)) from None
    W, H = prs.slide_width or 0, prs.slide_height or 0

    def cor(fmt):
        try:
            if fmt.type == MSO_COLOR_TYPE.RGB:
                return _rgb(str(fmt.rgb))
            if fmt.type == MSO_COLOR_TYPE.SCHEME:
                nome = str(fmt.theme_color).split(".")[-1].split(" ")[0].lower()
                chave = {"background_1": "lt1", "text_1": "dk1", "background_2": "lt2", "text_2": "dk2"}.get(
                    nome, nome.replace("_", "")
                )
                return tema.get(chave)
        except (AttributeError, KeyError, ValueError, TypeError):
            return None
        return None

    def preenchimento(fill):
        try:
            return cor(fill.fore_color) if fill.type == MSO_FILL.SOLID else None
        except (AttributeError, TypeError, ValueError):
            return None

    def formas(shapes):
        for sh in shapes:
            yield sh
            if sh.shape_type == 6:  # grupo
                yield from formas(sh.shapes)

    out = []
    for slide in prs.slides:
        camadas = (slide, slide.slide_layout, slide.slide_layout.slide_master)
        fundos = (preenchimento(o.background.fill) for o in camadas if getattr(o, "background", None) is not None)
        fundo = next((c for c in fundos if c), (255, 255, 255))
        for sh in formas(slide.shapes):
            if not getattr(sh, "has_text_frame", False) or not sh.has_text_frame:
                continue
            txt_forma = sh.text_frame.text
            if not txt_forma.strip():
                continue
            x, y, w, h = sh.left or 0, sh.top or 0, sh.width or 0, sh.height or 0
            if W and H and (x + w <= 0 or y + h <= 0 or x >= W or y >= H):
                out.append(("fora do slide", txt_forma))
                continue
            atras = preenchimento(sh.fill) if hasattr(sh, "fill") else None
            for par in sh.text_frame.paragraphs:
                for run in par.runs:
                    if not run.text.strip():
                        continue
                    if run.font.size is not None and run.font.size <= Pt(2.5):
                        out.append(("fonte minúscula", run.text))
                    elif _iguais(cor(run.font.color), atras or fundo):
                        out.append(("cor igual ao fundo", run.text))
    return out


def remover_ocultos(md, ocultos):
    """Tira do corpo o texto oculto (juntando fragmentos seguidos) e devolve o que saiu."""
    removidos = []
    corrida = " ".join(t for _, t in ocultos).strip()
    for alvo in [corrida] + [t.strip() for _, t in ocultos]:
        if len(alvo) >= 6 and alvo in md:
            md = md.replace(alvo, "")
            removidos.append(alvo)
    return md, removidos
