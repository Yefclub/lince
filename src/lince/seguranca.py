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


def _esquema(raiz) -> dict:
    """Cores do esquema de um tema DrawingML (dk1, lt1, accent1...)."""
    cores = {}
    for el in raiz.iter(f"{_A}clrScheme"):
        for cor in el:
            filho = next(iter(cor), None)
            if filho is not None:
                cores[_local(cor)] = _rgb(filho.get("lastClr") or filho.get("val"))
        break
    return cores


def _local(el):
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""


_CADEIA_MAX = 64  # profundidade de basedOn: o Word não passa disso; arquivo forjado com mais não custa mais


class _EstilosWord:
    """Propriedades de run e parágrafo pela cadeia do Word: run, estilo de caractere, estilo de parágrafo (o padrão,
    quando o parágrafo não diz qual) com basedOn, e padrão do documento. Memoriza cada propriedade por estilo: o custo
    é linear no documento, por mais longa que seja a cadeia."""

    def __init__(self, raiz):
        self.estilos, self.padrao, self.paragrafo_padrao, self.memoria = {}, None, None, {}
        if raiz is None:
            return
        for st in raiz.iter(f"{_W}style"):
            base = st.find(f"{_W}basedOn")
            self.estilos[st.get(f"{_W}styleId")] = (base.get(f"{_W}val") if base is not None else None, st)
            if st.get(f"{_W}type") == "paragraph" and st.get(f"{_W}default") in ("1", "true"):
                self.paragrafo_padrao = st.get(f"{_W}styleId")
        self.padrao = raiz.find(f"{_W}docDefaults/{_W}rPrDefault/{_W}rPr")

    def _do_estilo(self, sid, caminho):
        """O primeiro `caminho` (rPr/x ou pPr/x) pela cadeia de basedOn do estilo, memorizado."""
        chave = (sid, caminho)
        if chave not in self.memoria:
            achado, vistos, atual = None, set(), sid
            while atual and atual in self.estilos and atual not in vistos and len(vistos) < _CADEIA_MAX:
                vistos.add(atual)
                base, st = self.estilos[atual]
                if (achado := st.find(caminho)) is not None:
                    break
                atual = base
            self.memoria[chave] = achado
        return self.memoria[chave]

    def _estilo_do_paragrafo(self, ppr):
        ps = ppr.find(f"{_W}pStyle") if ppr is not None else None
        return ps.get(f"{_W}val") if ps is not None else self.paragrafo_padrao

    def run(self, rpr, ppr, nome):
        """O valor de `nome` (filho de rPr) pela cadeia, ou None."""
        if rpr is not None and (el := rpr.find(f"{_W}{nome}")) is not None:
            return el
        if rpr is not None and (rs := rpr.find(f"{_W}rStyle")) is not None:
            if (el := self._do_estilo(rs.get(f"{_W}val"), f"{_W}rPr/{_W}{nome}")) is not None:
                return el
        if (el := self._do_estilo(self._estilo_do_paragrafo(ppr), f"{_W}rPr/{_W}{nome}")) is not None:
            return el
        return self.padrao.find(f"{_W}{nome}") if self.padrao is not None else None

    def sombreamento_paragrafo(self, ppr):
        el = ppr.find(f"{_W}shd") if ppr is not None else None
        if el is None:
            el = self._do_estilo(self._estilo_do_paragrafo(ppr), f"{_W}pPr/{_W}shd")
        return _fundo_do_sombreamento(el)


def _fundo_do_sombreamento(shd):
    """Cor que o sombreamento pinta atrás do texto: `fill`, ou `color` no padrão sólido; None se não houver."""
    if shd is None:
        return None
    if (shd.get(f"{_W}val") or "").lower() == "solid":
        return _rgb(shd.get(f"{_W}color"))
    return _rgb(shd.get(f"{_W}fill"))


def _ligado(el):
    return el is not None and (el.get(f"{_W}val") or "true").lower() not in ("0", "false", "off")


_INCERTO = object()  # fundo que o Lince não sabe dizer (estilo de tabela, imagem): a cor não decide nada


def _fundo_do_run(run, fundo_par, pagina):
    """O que fica atrás do run: o sombreamento do parágrafo, o da célula (ou da tabela) em que ele está, a página.
    Tabela com estilo e sem sombreamento próprio é incerta: o estilo pode pintar a célula."""
    if fundo_par is not None:
        return fundo_par
    el = run.getparent()
    while el is not None:
        if el.tag == f"{_W}txbxContent":  # caixa de texto: o fundo é o da forma (DrawingML ou VML), que pode pintar
            return _INCERTO
        if el.tag == f"{_W}tc":
            tc = el.find(f"{_W}tcPr/{_W}shd")
            if (cor := _fundo_do_sombreamento(tc)) is not None:
                return cor
        if el.tag == f"{_W}tbl":
            tbl = el.find(f"{_W}tblPr")
            if tbl is not None and (cor := _fundo_do_sombreamento(tbl.find(f"{_W}shd"))) is not None:
                return cor
            if tbl is not None and tbl.find(f"{_W}tblStyle") is not None:
                return _INCERTO
        el = el.getparent()
    return pagina


def texto_oculto_docx(path):
    """Texto do corpo do Word que a IA leria e o humano não vê: vanish, fonte minúscula e cor igual ao fundo dela
    (destaque, sombreamento do run, do parágrafo, da célula, fundo da página), com a cor vinda do run, do estilo ou
    do tema. Só conclui "cor igual ao fundo" quando as duas cores são certas: com tom de tema, vale a cor que o Word
    gravou; com fundo incerto, o texto fica. Levanta XMLIlegivel se o corpo, os estilos ou o tema não puderem ser
    lidos."""
    out = []
    with zipfile.ZipFile(path) as z:
        nomes = set(z.namelist())
        if "word/document.xml" not in nomes:
            return out
        raiz = _xml(z.read("word/document.xml"))
        estilos = _EstilosWord(_xml(z.read("word/styles.xml")) if "word/styles.xml" in nomes else None)
        tema = next((n for n in sorted(nomes) if re.fullmatch(r"word/theme/theme\d*\.xml", n)), None)
        tema = _esquema(_xml(z.read(tema))) if tema else {}
    fundo = raiz.find(f"{_W}background")
    pagina = (_rgb(fundo.get(f"{_W}color")) if fundo is not None else None) or (255, 255, 255)
    _WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
    atras_do_texto = any(a.get("behindDoc") in ("1", "true") for a in raiz.iter(f"{_WP}anchor"))
    if (fundo is not None and len(fundo)) or atras_do_texto:  # fundo de imagem ou figura atrás do texto: incerto
        pagina = _INCERTO
    for par in raiz.iter(f"{_W}p"):
        ppr = par.find(f"{_W}pPr")
        fundo_par = estilos.sombreamento_paragrafo(ppr)
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
            if cor_el is None:  # automática: o Word escolhe a que contrasta com o fundo
                continue
            cor = _rgb(cor_el.get(f"{_W}val"))
            if (nome := cor_el.get(f"{_W}themeColor")) and not (
                cor_el.get(f"{_W}themeTint") or cor_el.get(f"{_W}themeShade")
            ):  # tema sem tom: a cor é a do tema; com tom, a que o Word gravou em val
                cor = tema.get(_TEMA.get(nome, nome))
            destaque = estilos.run(rpr, ppr, "highlight")
            atras = _rgb(_DESTAQUES.get(destaque.get(f"{_W}val"), "")) if destaque is not None else None
            atras = atras or _fundo_do_sombreamento(estilos.run(rpr, ppr, "shd")) or _fundo_do_run(r, fundo_par, pagina)
            if atras is not _INCERTO and _iguais(cor, atras):
                out.append(("cor igual ao fundo", txt))
    return out


_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_MAPA_PADRAO = {"bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2"}


def _cor_drawingml(el, tema, mapa):
    """Cor de um elemento de cor DrawingML (srgbClr, sysClr, schemeClr), ou None quando não é certa (tem
    transformação de luminosidade, tom ou transparência, ou é a cor do marcador phClr)."""
    if el is None or len(el):  # lumMod, lumOff, tint, shade, alpha...: a cor final não é a do esquema
        return None
    nome = _local(el)
    if nome == "srgbClr":
        return _rgb(el.get("val"))
    if nome == "sysClr":
        return _rgb(el.get("lastClr"))
    if nome == "schemeClr":
        val = el.get("val")
        return tema.get(mapa.get(val, _MAPA_PADRAO.get(val, val)))
    return None


def _preenchimento(el, tema, mapa):
    """Cor de um spPr/bgPr: a do solidFill quando é certa; _INCERTO para imagem, degradê ou cor incerta; None sem
    preenchimento declarado."""
    if el is None:
        return None
    if el.find(f"{_A}noFill") is not None:
        return None
    if (solido := el.find(f"{_A}solidFill")) is not None:
        return _cor_drawingml(next(iter(solido), None), tema, mapa) or _INCERTO
    if any(el.find(f"{_A}{t}") is not None for t in ("blipFill", "gradFill", "pattFill", "grpFill")):
        return _INCERTO
    return None


def texto_oculto_pptx(path):
    """Texto dos slides que a IA leria e o humano não vê: caixa fora do slide, fonte minúscula e cor igual ao fundo
    (preenchimento da forma, ou fundo do slide, do layout ou do mestre, com o tema e o mapa de cores do mestre do
    slide). Só conclui "cor igual ao fundo" quando as duas cores são certas: fundo de imagem, degradê, estilo do tema
    ou cor com transformação deixam o texto onde está. Levanta XMLIlegivel se o pacote não puder ser lido."""
    from pptx import Presentation
    from pptx.util import Pt

    try:
        prs = Presentation(path)
        W, H = prs.slide_width or 0, prs.slide_height or 0
        out, temas = [], {}
        for slide in prs.slides:
            mestre = slide.slide_layout.slide_master
            if id(mestre) not in temas:
                rel = next((r for r in mestre.part.rels.values() if r.reltype.endswith("/theme")), None)
                tema = _esquema(_xml(rel.target_part.blob)) if rel is not None else {}
                clr = mestre._element.find(f"{_P}clrMap")
                mapa = dict(clr.attrib) if clr is not None else {}
                temas[id(mestre)] = (tema, mapa)
            tema, mapa = temas[id(mestre)]
            fundo = (255, 255, 255)
            for camada in (slide, slide.slide_layout, mestre):  # o primeiro que declara fundo vale
                bg = camada._element.find(f"{_P}cSld/{_P}bg")
                if bg is None:
                    continue
                bgpr, bgref = bg.find(f"{_P}bgPr"), bg.find(f"{_P}bgRef")
                if bgpr is not None:
                    fundo = _preenchimento(bgpr, tema, mapa) or _INCERTO
                else:  # bgRef: estilo de fundo do tema; sólido com a cor do bgRef é certo, o resto não
                    fundo = _INCERTO
                    if bgref is not None and int(bgref.get("idx") or 0) in (0, 1001):
                        fundo = _cor_drawingml(next(iter(bgref), None), tema, mapa) or _INCERTO
                break
            desenhadas = []  # caixas das formas já desenhadas que podem pintar o que vem por cima
            for sh in slide.shapes:  # na ordem de desenho: o que vem antes fica atrás
                caixa = (sh.left or 0, sh.top or 0, (sh.left or 0) + (sh.width or 0), (sh.top or 0) + (sh.height or 0))
                atras = fundo if not any(_sobrepoe(caixa, d) for d in desenhadas) else _INCERTO
                out += _oculto_na_forma(sh, True, W, H, atras, tema, mapa, Pt)
                for f in _dentro_do_grupo(sh):  # no grupo, posição e camadas são do grupo: só o preenchimento certo
                    out += _oculto_na_forma(f, False, W, H, _INCERTO, tema, mapa, Pt)
                if _pode_pintar(sh, tema, mapa):
                    desenhadas.append(caixa)
        return out
    except XMLIlegivel:
        raise
    except MemoryError:
        raise
    except Exception as e:  # noqa: BLE001 — o python-pptx levanta de tudo com pacote hostil: não conferido
        raise XMLIlegivel(f"{type(e).__name__}: {str(e)[:200]}") from None


def _sobrepoe(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _pode_pintar(sh, tema, mapa) -> bool:
    """A forma pode cobrir o fundo do que vem por cima: imagem, gráfico, tabela, grupo, ou forma com preenchimento
    próprio ou do estilo do tema. Só a caixa sem preenchimento nenhum é transparente."""
    if sh.shape_type in (6, 13) or sh._element.tag in (f"{_P}pic", f"{_P}graphicFrame", f"{_P}grpSp"):
        return True
    if _preenchimento(sh._element.find(f"{_P}spPr"), tema, mapa) is not None:
        return True
    ref = sh._element.find(f"{_P}style/{_A}fillRef")
    return ref is not None and bool(int(ref.get("idx") or 0))


def _dentro_do_grupo(sh):
    if sh.shape_type == 6:  # grupo
        for f in sh.shapes:
            yield f
            yield from _dentro_do_grupo(f)


def _oculto_na_forma(sh, solta, W, H, fundo, tema, mapa, Pt):
    if not getattr(sh, "has_text_frame", False) or not sh.text_frame.text.strip():
        return []
    x, y, w, h = sh.left or 0, sh.top or 0, sh.width or 0, sh.height or 0
    if solta and W and H and (x + w <= 0 or y + h <= 0 or x >= W or y >= H):
        return [("fora do slide", sh.text_frame.text)]
    sppr = sh._element.find(f"{_P}spPr")
    atras = _preenchimento(sppr, tema, mapa)
    if atras is None and sh._element.find(f"{_P}style/{_A}fillRef") is not None:
        ref = sh._element.find(f"{_P}style/{_A}fillRef")
        atras = _INCERTO if int(ref.get("idx") or 0) else None  # o estilo do tema pinta a forma
    atras = fundo if atras is None else atras
    out = []
    for par in sh.text_frame.paragraphs:
        for run in par.runs:
            if not run.text.strip():
                continue
            if run.font.size is not None and run.font.size <= Pt(2.5):
                out.append(("fonte minúscula", run.text))
                continue
            rpr = run._r.find(f"{_A}rPr")
            solido = rpr.find(f"{_A}solidFill") if rpr is not None else None
            cor = _cor_drawingml(next(iter(solido), None), tema, mapa) if solido is not None else None
            if atras is not _INCERTO and _iguais(cor, atras):
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
