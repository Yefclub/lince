"""Segurança: texto oculto (branco, minúsculo, invisível, vanish) e padrões de prompt injection."""

import bisect
import hashlib  # noqa: F401
import io  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import zipfile  # noqa: F401
from typing import NamedTuple
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


def _limitado(padrao: str) -> re.Pattern:
    """O padrão com toda repetição de \\w e \\s limitada e possessiva: cada começo de casamento custa no máximo
    algumas dezenas de passos, e o texto inteiro, linear ("ignorignor..." de 1 MB não trava a leitura). Palavra
    acima de 40 letras e espaço acima de 10 não existem nas frases que os padrões procuram."""
    for de, para in ((r"\w*", r"\w{0,40}+"), (r"\w+", r"\w{1,40}+"), (r"\s*", r"\s{0,10}+"), (r"\s+", r"\s{1,10}+")):
        padrao = padrao.replace(de, para)
    return re.compile(padrao, re.I)


_PADROES = [_limitado(p) for p in PADROES_INJECAO]


def alertas_injecao(txt):
    out = []
    for pad in _PADROES:
        for m in pad.finditer(txt):
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
_WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_WPS = "{http://schemas.microsoft.com/office/word/2010/wordprocessingShape}"
_V = "{urn:schemas-microsoft-com:vml}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
NAO_CONFERIDO = "texto oculto não conferido"  # o alerta e a pendência levam a parte e o motivo depois de ": "
_DESTAQUES = {"white": "FFFFFF", "black": "000000", "yellow": "FFFF00", "green": "00FF00", "cyan": "00FFFF",
              "magenta": "FF00FF", "blue": "0000FF", "red": "FF0000", "darkBlue": "000080", "darkCyan": "008080",
              "darkGreen": "008000", "darkMagenta": "800080", "darkRed": "800000", "darkYellow": "808000",
              "darkGray": "808080", "lightGray": "C0C0C0"}  # fmt: skip
_TEMA = {"dark1": "dk1", "text1": "dk1", "tx1": "dk1", "light1": "lt1", "background1": "lt1", "bg1": "lt1",
         "dark2": "dk2", "text2": "dk2", "tx2": "dk2", "light2": "lt2", "background2": "lt2", "bg2": "lt2",
         "hyperlink": "hlink", "followedHyperlink": "folHlink"}  # fmt: skip


class XMLIlegivel(Exception):
    """A parte não pôde ser lida para a conferência: quem chama trata como suspeita, nunca como "nada oculto"."""


class Limpeza(NamedTuple):
    """O texto oculto achado e o pacote sem ele (`dados` é o original quando nada saiu). `falhas` são as partes que
    não puderam ser conferidas, com o motivo: ficam como estão e quem chama as mostra no resultado."""

    ocultos: list
    dados: bytes
    falhas: list


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


def _serializar(raiz) -> bytes:
    """A parte regravada pela árvore inteira: o DOCTYPE (com as entidades sem expandir) continua o mesmo."""
    from lxml import etree

    arvore = raiz.getroottree()
    return etree.tostring(arvore, xml_declaration=True, encoding="UTF-8", standalone=arvore.docinfo.standalone)


def _regravar(z, limpas: dict) -> bytes:
    """O pacote com as partes limpas no lugar; as outras vão com o mesmo conteúdo."""
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as zo:
        for info in z.infolist():
            zo.writestr(info, limpas[info.filename] if info.filename in limpas else z.read(info))
    return saida.getvalue()


def _motivo(e) -> str:
    return str(e)[:200] if isinstance(e, XMLIlegivel) else f"{type(e).__name__}: {str(e)[:200]}"


def _num(valor):
    """Inteiro de um atributo (o "%" do OOXML estrito sai), ou None: arquivo forjado não derruba a conferência."""
    try:
        return int((valor or "").strip().rstrip("%"))
    except ValueError:
        return None


def _rgb(hexa):
    try:
        return tuple(int(hexa[k : k + 2], 16) for k in (0, 2, 4)) if hexa and len(hexa) == 6 else None
    except ValueError:
        return None


def _luminancia(cor) -> float:
    """Luminância relativa (WCAG 2) de uma cor sRGB."""
    lin = [v / 255 / 12.92 if v <= 10 else ((v / 255 + 0.055) / 1.055) ** 2.4 for v in cor]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _iguais(a, b):
    """O humano não separa as duas cores: contraste (WCAG) abaixo de 1,25:1 entre cores do mesmo tom (nenhum canal
    a mais de 64). E8E8E8 sobre branco fica em 1,22:1; o cinza D9D9D9 (1,41:1) e a letra branca na faixa azul (4,7:1)
    se leem, e o amarelo puro sobre branco tem o tom que o separa."""
    if a is None or b is None or max(abs(x - y) for x, y in zip(a, b, strict=True)) > 64:
        return False
    la, lb = _luminancia(a), _luminancia(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05) < 1.25


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
        self.estilos, self.padrao, self.memoria = {}, None, {}
        self.paragrafo_padrao = self.tabela_padrao = None
        if raiz is None:
            return
        for st in raiz.iter(f"{_W}style"):
            base = st.find(f"{_W}basedOn")
            self.estilos[st.get(f"{_W}styleId")] = (base.get(f"{_W}val") if base is not None else None, st)
            if st.get(f"{_W}default") in ("1", "true"):
                if st.get(f"{_W}type") == "paragraph":
                    self.paragrafo_padrao = st.get(f"{_W}styleId")
                elif st.get(f"{_W}type") == "table":
                    self.tabela_padrao = st.get(f"{_W}styleId")
        self.padrao = raiz.find(f"{_W}docDefaults/{_W}rPrDefault/{_W}rPr")

    def _cadeia(self, sid):
        """Os estilos da cadeia de basedOn, do próprio à base."""
        vistos = set()
        while sid and sid in self.estilos and sid not in vistos and len(vistos) < _CADEIA_MAX:
            vistos.add(sid)
            base, st = self.estilos[sid]
            yield st
            sid = base

    def _do_estilo(self, sid, caminho):
        """O primeiro `caminho` (rPr/x ou pPr/x) pela cadeia de basedOn do estilo, memorizado."""
        chave = (sid, caminho)
        if chave not in self.memoria:
            self.memoria[chave] = next((el for st in self._cadeia(sid) if (el := st.find(caminho)) is not None), None)
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

    def tabela_pinta(self, sid) -> bool:
        """O estilo da tabela (o padrão, sem tblStyle) pinta alguma célula: há sombreamento na cadeia, inteiro ou
        condicional (primeira linha, faixas). Table Grid e os estilos só de borda não pintam. Estilo citado e não
        definido também conta como pintado: não há como saber o que o autor quis."""
        if sid is None and (sid := self.tabela_padrao) is None:
            return False
        if sid not in self.estilos:
            return True
        chave = (sid, "pinta")
        if chave not in self.memoria:
            self.memoria[chave] = any(_pinta(shd) for st in self._cadeia(sid) for shd in st.iter(f"{_W}shd"))
        return self.memoria[chave]


def _fundo_do_sombreamento(shd):
    """Cor que o sombreamento pinta atrás do texto: `fill`, ou `color` no padrão sólido; None se não houver."""
    if shd is None:
        return None
    if (shd.get(f"{_W}val") or "").lower() == "solid":
        return _rgb(shd.get(f"{_W}color"))
    return _rgb(shd.get(f"{_W}fill"))


def _pinta(shd) -> bool:
    return _fundo_do_sombreamento(shd) is not None or (shd.get(f"{_W}val") or "clear").lower() not in ("clear", "nil")


def _ligado(el):
    return el is not None and (el.get(f"{_W}val") or "true").lower() not in ("0", "false", "off")


_INCERTO = object()  # fundo que o Lince não sabe dizer (estilo de tabela, imagem): a cor não decide nada


def _cor_vml(valor):
    """fillcolor do VML ("#1F3864", "#1F3864 [3204]", "white"); _INCERTO para o que não dá para ler."""
    valor = (valor.split() or [""])[0].lower()
    if valor.startswith("#"):
        return _rgb(valor[1:7]) or _INCERTO
    return _rgb({"white": "FFFFFF", "black": "000000"}.get(valor, "")) or _INCERTO


def _fundo_da_caixa(txbx, tema):
    """O preenchimento declarado da forma da caixa de texto (DrawingML ou VML): a cor, _INCERTO quando ela pinta de
    um jeito que o Lince não sabe dizer, None quando não pinta (vale o que está embaixo da forma)."""
    pai = txbx.getparent()
    forma = pai.getparent() if pai is not None else None
    if forma is None:
        return None
    if pai.tag == f"{_WPS}txbx":
        cor = _preenchimento(forma.find(f"{_WPS}spPr"), tema, {})
        ref = forma.find(f"{_WPS}style/{_A}fillRef")
        return _INCERTO if cor is None and ref is not None and _num(ref.get("idx")) else cor
    if pai.tag != f"{_V}textbox" or (forma.get("filled") or "t").lower() in ("f", "false"):
        return None
    fill = forma.find(f"{_V}fill")
    if fill is not None and (fill.get("on") or "t").lower() in ("f", "false"):
        return None
    if fill is not None and ((fill.get("type") or "solid") != "solid" or fill.get("opacity")):
        return _INCERTO
    return _cor_vml(forma.get("fillcolor") or "white")  # o VML pinta de branco quando não diz a cor


def _fundo_do_run(run, fundo_par, pagina, estilos, tema, cobertos):
    """O que fica atrás do run: o sombreamento do parágrafo, a forma da caixa de texto, a célula, a linha ou a tabela
    em que ele está, a imagem atrás do texto que cobre o parágrafo, a página. Cada um vale só para o que está nele."""
    if fundo_par is not None:
        return fundo_par
    coberto, el = False, run.getparent()
    while el is not None:
        if el.tag == f"{_W}p":
            coberto = coberto or el in cobertos
        elif el.tag == f"{_W}txbxContent":  # a forma fica por cima da imagem atrás do texto
            if (cor := _fundo_da_caixa(el, tema)) is not None:
                return cor
        elif el.tag == f"{_W}tc":
            if (cor := _fundo_do_sombreamento(el.find(f"{_W}tcPr/{_W}shd"))) is not None:
                return cor
        elif el.tag == f"{_W}tr":
            if (cor := _fundo_do_sombreamento(el.find(f"{_W}tblPrEx/{_W}shd"))) is not None:
                return cor
        elif el.tag == f"{_W}tbl":
            tbl = el.find(f"{_W}tblPr")
            if tbl is not None and (cor := _fundo_do_sombreamento(tbl.find(f"{_W}shd"))) is not None:
                return cor
            st = tbl.find(f"{_W}tblStyle") if tbl is not None else None
            if estilos.tabela_pinta(st.get(f"{_W}val") if st is not None else None):
                return _INCERTO
        el = el.getparent()
    return _INCERTO if coberto else pagina


def _motivo_do_run(r, ppr, fundo_par, estilos, tema, pagina, cobertos):
    """Por que o humano não vê o run (vanish, fonte minúscula, escala minúscula, cor igual ao fundo), ou None. Só
    conclui "cor igual ao fundo" quando as duas cores são certas: com tom de tema vale a cor que o Word gravou; com
    fundo incerto, o texto fica."""
    rpr = r.find(f"{_W}rPr")
    if _ligado(estilos.run(rpr, ppr, "vanish")):
        return "oculto (vanish)"
    sz = estilos.run(rpr, ppr, "sz")
    sz = _num(sz.get(f"{_W}val")) if sz is not None else None
    if sz is not None and sz <= 4:
        return "fonte minúscula"
    escala_x = estilos.run(rpr, ppr, "w")
    escala_x = _num(escala_x.get(f"{_W}val")) if escala_x is not None else None
    if escala_x is not None and escala_x <= 10 and (sz if sz is not None else 20) <= 8:  # até 10% em até 4 pt
        return "escala minúscula"
    cor_el = estilos.run(rpr, ppr, "color")
    if cor_el is None:  # automática: o Word escolhe a que contrasta com o fundo
        return None
    cor = _rgb(cor_el.get(f"{_W}val"))
    if (nome := cor_el.get(f"{_W}themeColor")) and not (cor_el.get(f"{_W}themeTint") or cor_el.get(f"{_W}themeShade")):
        cor = tema.get(_TEMA.get(nome, nome))  # tema sem tom: a cor é a do tema; com tom, a que o Word gravou em val
    destaque = estilos.run(rpr, ppr, "highlight")
    atras = _rgb(_DESTAQUES.get(destaque.get(f"{_W}val"), "")) if destaque is not None else None
    atras = (
        atras
        or _fundo_do_sombreamento(estilos.run(rpr, ppr, "shd"))
        or _fundo_do_run(r, fundo_par, pagina, estilos, tema, cobertos)
    )
    return "cor igual ao fundo" if atras is not _INCERTO and _iguais(cor, atras) else None


def _atras_do_texto(raiz):
    return (a for a in raiz.iter(f"{_WP}anchor") if (a.get("behindDoc") or "").lower() in ("1", "true"))


def _fluxo(raiz, estilos) -> list:
    """(parágrafo, topo, altura) de cada parágrafo da parte, em EMU, na ordem em que o Word os empilha: as células de
    uma linha ficam lado a lado (a linha tem a altura da maior). A altura é a mínima (uma linha do maior tamanho de
    fonte), o que aproxima os parágrafos e só exagera a área que uma imagem pode cobrir."""
    saida = []

    def bloco(el, y):
        for f in el:
            if f.tag == f"{_W}p":
                saida.append((f, y, h := _altura(f, estilos)))
                y += h
            elif f.tag == f"{_W}tr":
                y = max([bloco(tc, y) for tc in f if tc.tag == f"{_W}tc"], default=y)
            elif isinstance(f.tag, str):
                y = bloco(f, y)
        return y

    bloco(raiz, 0)
    return saida


def _altura(p, estilos) -> int:
    ppr = p.find(f"{_W}pPr")
    marca = ppr.find(f"{_W}rPr") if ppr is not None else None
    tamanhos = [estilos.run(marca, ppr, "sz")]
    tamanhos += [estilos.run(r.find(f"{_W}rPr"), ppr, "sz") for r in p.iter(f"{_W}r") if r.find(f"{_W}t") is not None]
    meios_pontos = max(
        (n for el in tamanhos if el is not None and (n := _num(el.get(f"{_W}val"))) is not None), default=20
    )
    return max(meios_pontos, 1) * 6350  # meio ponto em EMU


def _cobertos(raiz, estilos, geometria) -> set:
    """Parágrafos que uma imagem atrás do texto (behindDoc) pode cobrir: o parágrafo âncora e os vizinhos até a
    altura da imagem somada ao deslocamento dela. O Word não grava a posição do texto na página: a distância vem do
    fluxo da parte. Imagem sem tamanho declarado cobre a parte inteira."""
    ancoras = list(_atras_do_texto(raiz))
    if not ancoras:
        return set()
    fluxo = _fluxo(raiz, estilos)
    onde = {p: (y, h) for p, y, h in fluxo}
    faixas = []
    for a in ancoras:
        p = next((x for x in a.iterancestors(f"{_W}p") if x in onde), None)
        ext = a.find(f"{_WP}extent")
        cy = _num(ext.get("cy")) if ext is not None else None
        if p is None or cy is None:
            return set(raiz.iter(f"{_W}p"))
        desloc = a.find(f"{_WP}positionV/{_WP}posOffset")
        desloc = _num(desloc.text) if desloc is not None else None
        if desloc is None:  # alinhada (topo, centro, base) ou posição simples: pode estar em qualquer altura da página
            desloc = geometria[1]
        y, h = onde[p]
        raio = abs(cy) + abs(desloc)
        faixas.append((y - raio, y + h + raio))
    juntas = []
    for ini, fim in sorted(faixas):
        if juntas and ini <= juntas[-1][1]:
            juntas[-1][1] = max(juntas[-1][1], fim)
        else:
            juntas.append([ini, fim])
    inicios = [ini for ini, _ in juntas]
    out = set()
    for p, y, h in fluxo:  # a faixa que começa por último antes do fim do parágrafo é a única que pode tocá-lo
        k = bisect.bisect_left(inicios, y + h) - 1
        if k >= 0 and juntas[k][1] > y:
            out.add(p)
    return out


def _geometria(corpo) -> tuple:
    """Largura e altura da página e margens (cima, baixo, esquerda, direita) da última seção, em EMU; Carta com
    margens de 1" quando o arquivo não diz."""
    sec = corpo.find(f"{_W}body/{_W}sectPr") if corpo is not None else None
    tam = sec.find(f"{_W}pgSz") if sec is not None else None
    mar = sec.find(f"{_W}pgMar") if sec is not None else None

    def v(el, nome, padrao):
        n = _num(el.get(f"{_W}{nome}")) if el is not None else None
        return abs(padrao if n is None else n) * 635  # twip em EMU

    return v(tam, "w", 12240), v(tam, "h", 15840), *(v(mar, lado, 1440) for lado in ("top", "bottom", "left", "right"))


def _timbrado(a, geometria) -> bool:
    """A imagem atrás do texto do cabeçalho ou do rodapé é papel timbrado: cobre ao menos um quarto da área do texto e
    chega até ela. O logo pequeno no cabeçalho só deixa incerto o parágrafo dele."""
    largura, altura, cima, baixo, esquerda, direita = geometria
    ext = a.find(f"{_WP}extent")
    cx, cy = (_num(ext.get("cx")), _num(ext.get("cy"))) if ext is not None else (None, None)
    if cx is None or cy is None:
        return True
    if 4 * abs(cx) * abs(cy) < (largura - esquerda - direita) * (altura - cima - baixo):
        return False
    pv = a.find(f"{_WP}positionV")
    base = {"page": 0, "topMargin": 0, "margin": cima, "bottomMargin": altura - baixo}.get(
        pv.get("relativeFrom") if pv is not None else None
    )
    desloc = pv.find(f"{_WP}posOffset") if pv is not None else None
    desloc = _num(desloc.text) if desloc is not None else None
    if base is None or desloc is None:  # alinhada ou presa ao parágrafo: pode chegar ao texto
        return True
    return base + desloc < altura - baixo and base + desloc + abs(cy) > cima


_PARTES_WORD = re.compile(r"word/(document|header\d*|footer\d*|footnotes|endnotes|comments)\.xml")
_BORDA = re.compile(r"word/(header|footer)\d*\.xml")


def limpar_docx(dados: bytes) -> Limpeza:
    """Texto do Word que a IA leria e o humano não vê, esvaziado no próprio XML antes da conversão: corpo, caixas de
    texto, notas de rodapé e de fim, comentários, cabeçalho e rodapé. Só as partes alteradas são regravadas; texto
    visível igual a um oculto fica onde está. A parte que não pode ser lida vai para `falhas` e fica como estava."""
    try:
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            ocultos, limpas, falhas = _conferir_word(z, set(z.namelist()))
            return Limpeza(ocultos, _regravar(z, limpas) if limpas else dados, falhas)
    except MemoryError:
        raise
    except Exception as e:  # noqa: BLE001 — zip que não abre ou parte cifrada: nada conferido, nada muda
        return Limpeza([], dados, [f"pacote ({_motivo(e)})"])


def bordas_sem_oculto(z) -> dict:
    """Cabeçalho, rodapé e comentários sem o texto oculto (só as partes que mudaram), para os metadados: o que sai do
    corpo não volta por lá. A falha já aparece pela conferência do corpo; aqui a parte só fica como está."""
    try:
        return _conferir_word(z, {n for n in z.namelist() if _BORDA.fullmatch(n) or n == "word/comments.xml"})[1]
    except MemoryError:
        raise
    except Exception:  # noqa: BLE001 — a mesma falha vai no resultado pela leitura do corpo
        return {}


def _conferir_word(z, alvo) -> tuple:
    nomes, falhas = set(z.namelist()), []

    def arvore(nome):
        try:
            return _xml(z.read(nome))
        except MemoryError:
            raise
        except Exception as e:  # noqa: BLE001 — XML hostil ou CRC errado: a parte fica como está e a falha aparece
            falhas.append(f"{nome} ({_motivo(e)})")
            return None

    estilos = _EstilosWord(arvore("word/styles.xml") if "word/styles.xml" in nomes else None)
    tema = next((n for n in sorted(nomes) if re.fullmatch(r"word/theme/theme\d*\.xml", n)), None)
    tema = _esquema(raiz) if tema and (raiz := arvore(tema)) is not None else {}
    corpo = arvore("word/document.xml") if "word/document.xml" in nomes else None
    partes = sorted((n for n in nomes & alvo if _PARTES_WORD.fullmatch(n)), key=lambda n: (n != "word/document.xml", n))
    partes = {n: corpo if n == "word/document.xml" else arvore(n) for n in partes}
    fundo = corpo.find(f"{_W}background") if corpo is not None else None
    pagina = (_rgb(fundo.get(f"{_W}color")) if fundo is not None else None) or (255, 255, 255)
    if fundo is not None and len(fundo):  # fundo de imagem ou degradê
        pagina = _INCERTO
    geometria = _geometria(corpo)
    for nome, raiz in partes.items():
        if _BORDA.fullmatch(nome) and raiz is not None and any(_timbrado(a, geometria) for a in _atras_do_texto(raiz)):
            pagina = _INCERTO  # o papel timbrado fica atrás de tudo o que está na página
    ocultos, limpas = [], {}
    for nome, raiz in partes.items():
        if raiz is None:
            continue
        atras = (255, 255, 255) if nome == "word/comments.xml" else pagina  # o comentário fica no balão, fora da página
        achados, mudou = _conferir_parte(raiz, estilos, tema, atras, geometria)
        ocultos += achados
        if mudou:
            limpas[nome] = _serializar(raiz)
    return ocultos, limpas, falhas


def _conferir_parte(raiz, estilos, tema, pagina, geometria) -> tuple:
    """Esvazia o texto dos runs ocultos da parte; devolve o que saiu e se a parte mudou. A cópia do mc:Fallback (a
    caixa de texto em VML) também é esvaziada, mas não conta de novo."""
    cobertos = _cobertos(raiz, estilos, geometria)
    achados, mudou, sombras = [], False, {}
    for r in raiz.iter(f"{_W}r"):
        textos = r.findall(f"{_W}t")  # só os do run: o texto de uma caixa dentro dele é de outro run
        txt = "".join("".join(t.itertext()) for t in textos)
        if not txt.strip():
            continue
        par = next(r.iterancestors(f"{_W}p"), None)
        ppr = par.find(f"{_W}pPr") if par is not None else None
        if par not in sombras:
            sombras[par] = estilos.sombreamento_paragrafo(ppr)
        if motivo := _motivo_do_run(r, ppr, sombras[par], estilos, tema, pagina, cobertos):
            for t in textos:
                t.clear()
            mudou = True
            if not any(a.tag == _MC_FALLBACK for a in r.iterancestors()):
                achados.append((motivo, txt))
    return achados, mudou


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


def _preenchimento(el, tema, mapa, parte=None, imagens=None):
    """Cor de um spPr/bgPr: a do solidFill quando é certa, ou a da imagem de uma cor só; _INCERTO para foto, degradê
    ou cor incerta; None sem preenchimento declarado. `parte` é a do slide, para achar a imagem."""
    if el is None:
        return None
    if el.find(f"{_A}noFill") is not None:
        return None
    if (solido := el.find(f"{_A}solidFill")) is not None:
        return _cor_drawingml(next(iter(solido), None), tema, mapa) or _INCERTO
    if (blip := el.find(f"{_A}blipFill")) is not None:
        return _cor_da_imagem(blip.find(f"{_A}blip"), parte, imagens) if parte is not None else _INCERTO
    if any(el.find(f"{_A}{t}") is not None for t in ("gradFill", "pattFill", "grpFill")):
        return _INCERTO
    return None


def _cor_da_imagem(blip, parte, imagens):
    """A cor da imagem quando ela é toda de uma cor só (a imagem branca que esconde a letra branca); _INCERTO para
    foto, transparência, efeito de cor ou imagem que não abre."""
    from PIL import Image

    if blip is None or any(_local(f) != "extLst" for f in blip):  # duotone, lum, alphaModFix: a cor muda
        return _INCERTO
    try:
        alvo = parte.related_part(blip.get(f"{_R}embed"))
    except Exception:  # noqa: BLE001 — relação quebrada ou para fora do pacote
        return _INCERTO
    chave = str(alvo.partname)
    if chave not in imagens:
        imagens[chave] = _INCERTO
        try:
            with Image.open(io.BytesIO(alvo.blob)) as im:
                im.thumbnail((512, 512))  # a cor uniforme sobrevive à redução; o custo fica pequeno
                faixas = im.convert("RGBA").getextrema()
        except MemoryError:
            raise
        except Exception:  # noqa: BLE001, S110 — EMF, SVG ou imagem quebrada: o fundo fica incerto
            pass
        else:
            if faixas[3][0] == 255 and all(alto - baixo <= 8 for baixo, alto in faixas[:3]):
                imagens[chave] = tuple((baixo + alto) // 2 for baixo, alto in faixas[:3])
    return imagens[chave]


def limpar_pptx(dados: bytes) -> Limpeza:
    """Texto dos slides que a IA leria e o humano não vê, esvaziado no próprio XML antes da conversão: o run oculto
    e, se a caixa estiver fora do slide, a caixa inteira. Só os slides alterados são regravados; texto visível igual a
    um oculto fica onde está. Pacote que não pode ser conferido fica como estava, com a parte em `falhas`."""
    try:
        ocultos, alvos = _conferir_pptx(dados)
        if not alvos:
            return Limpeza(ocultos, dados, [])
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            limpas = {}
            for parte, elementos in alvos.items():
                raiz = _xml(z.read(parte))
                for caminho, tag in elementos:
                    el = raiz
                    for k in caminho:
                        el = el[k]
                    if el.tag != tag:
                        raise XMLIlegivel(f"{parte}: a forma não está onde o leitor a viu")
                    for t in el.iter(f"{_A}t"):
                        t.clear()
                limpas[parte] = _serializar(raiz)
            return Limpeza(ocultos, _regravar(z, limpas), [])
    except MemoryError:
        raise
    except Exception as e:  # noqa: BLE001 — o python-pptx levanta de tudo com pacote hostil: não conferido
        return Limpeza([], dados, [f"{_parte_ilegivel(dados) or 'apresentação'} ({_motivo(e)})"])


def _parte_ilegivel(dados):
    """A primeira parte XML da apresentação que não abre: é ela que o alerta aponta."""
    try:
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            for nome in z.namelist():
                if nome.endswith((".xml", ".rels")):
                    try:
                        _xml(z.read(nome))
                    except Exception:  # noqa: BLE001 — XML hostil, CRC errado, parte cifrada
                        return nome
    except Exception:  # noqa: BLE001 — nem o zip abre: o alerta fala da apresentação
        return None
    return None


def _caminho(el) -> list:
    """Posição do elemento na árvore da parte, filho a filho desde a raiz."""
    out = []
    while (pai := el.getparent()) is not None:
        out.append(pai.index(el))
        el = pai
    return out[::-1]


def _conferir_pptx(dados) -> tuple:
    """O texto oculto dos slides e onde ele está ({parte: [(caminho, tag)]}): caixa fora do slide, fonte minúscula e
    cor igual ao fundo (preenchimento da forma, formas embaixo, fundo do slide, do layout ou do mestre, com o tema e
    o mapa de cores do mestre). Só conclui "cor igual ao fundo" quando as cores são certas: foto, degradê, estilo do
    tema ou cor com transformação deixam o texto onde está."""
    from pptx import Presentation
    from pptx.util import Pt

    prs = Presentation(io.BytesIO(dados))
    W, H = prs.slide_width or 0, prs.slide_height or 0
    out, alvos, temas, imagens = [], {}, {}, {}
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
                fundo = _preenchimento(bgpr, tema, mapa, camada.part, imagens) or _INCERTO
            else:  # bgRef: estilo de fundo do tema; sólido com a cor do bgRef é certo, o resto não
                fundo = _INCERTO
                if bgref is not None and _num(bgref.get("idx") or "0") in (0, 1001):
                    fundo = _cor_drawingml(next(iter(bgref), None), tema, mapa) or _INCERTO
            break
        desenhadas = []  # (caixa, o que ela pinta) das formas já desenhadas, de baixo para cima
        # embaixo do slide vêm as formas do mestre e do layout que não são placeholder (faixa, foto, logo), a
        # menos que o slide (ou o layout, para as do mestre) diga showMasterSp="0"
        camadas_de_baixo = []
        if slide._element.get("showMasterSp") not in ("0", "false"):
            if slide.slide_layout._element.get("showMasterSp") not in ("0", "false"):
                camadas_de_baixo.append(mestre)
            camadas_de_baixo.append(slide.slide_layout)
        for camada in camadas_de_baixo:
            for sh in camada.shapes:
                if not sh.is_placeholder and (cor := _pintura(sh, camada.part, tema, mapa, imagens)) is not None:
                    desenhadas.append((_caixa(sh), cor))
        parte = str(slide.part.partname).lstrip("/")
        for sh in slide.shapes:  # na ordem de desenho: o que vem antes fica atrás
            caixa = _caixa(sh)
            embaixo = _embaixo(caixa, fundo, desenhadas)
            achados = _oculto_na_forma(sh, True, W, H, embaixo, tema, mapa, Pt, slide.part, imagens)
            for f in _dentro_do_grupo(sh):  # no grupo, posição e camadas são do grupo: só o preenchimento certo
                achados += _oculto_na_forma(f, False, W, H, [_INCERTO], tema, mapa, Pt, slide.part, imagens)
            for motivo, texto, el in achados:
                out.append((motivo, texto))
                alvos.setdefault(parte, []).append((_caminho(el), el.tag))
            if (cor := _pintura(sh, slide.part, tema, mapa, imagens)) is not None:
                desenhadas.append((caixa, cor))
    return out, alvos


def _caixa(sh) -> tuple:
    return (sh.left or 0, sh.top or 0, (sh.left or 0) + (sh.width or 0), (sh.top or 0) + (sh.height or 0))


def _sobrepoe(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _embaixo(caixa, fundo, desenhadas) -> list:
    """O que pode estar atrás da caixa: o fundo do slide e cada forma já desenhada que a sobrepõe; a que a cobre
    inteira esconde o que veio antes dela."""
    fundos = [fundo]
    for d, cor in desenhadas:
        if _sobrepoe(caixa, d):
            cobre = d[0] <= caixa[0] and d[1] <= caixa[1] and caixa[2] <= d[2] and caixa[3] <= d[3]
            fundos = [cor] if cobre else [*fundos, cor]
    return fundos


def _pintura(sh, parte, tema, mapa, imagens):
    """O que a forma pinta embaixo do que vem por cima: a cor certa (preenchimento sólido, imagem de uma cor só),
    _INCERTO (foto, gráfico, tabela, grupo, estilo do tema) ou None, para a caixa sem preenchimento nenhum."""
    el = sh._element
    if el.tag == f"{_P}pic":
        return _cor_da_imagem(el.find(f"{_P}blipFill/{_A}blip"), parte, imagens)
    if sh.shape_type in (6, 13) or el.tag in (f"{_P}graphicFrame", f"{_P}grpSp"):
        return _INCERTO
    if (cor := _preenchimento(el.find(f"{_P}spPr"), tema, mapa, parte, imagens)) is not None:
        return cor
    ref = el.find(f"{_P}style/{_A}fillRef")
    return _INCERTO if ref is not None and _num(ref.get("idx") or "0") else None


def _dentro_do_grupo(sh):
    if sh.shape_type == 6:  # grupo
        for f in sh.shapes:
            yield f
            yield from _dentro_do_grupo(f)


def _oculto_na_forma(sh, solta, W, H, fundos, tema, mapa, Pt, parte, imagens) -> list:
    """(motivo, texto, elemento a esvaziar) do que está oculto na forma. `fundos` é o que pode estar atrás dela: o
    texto só é da cor do fundo se for da cor de todos."""
    if not getattr(sh, "has_text_frame", False) or not sh.text_frame.text.strip():
        return []
    x, y, w, h = sh.left or 0, sh.top or 0, sh.width or 0, sh.height or 0
    if solta and W and H and (x + w <= 0 or y + h <= 0 or x >= W or y >= H):
        return [("fora do slide", sh.text_frame.text, sh._element)]
    atras = _preenchimento(sh._element.find(f"{_P}spPr"), tema, mapa, parte, imagens)
    if atras is None and (ref := sh._element.find(f"{_P}style/{_A}fillRef")) is not None:
        atras = _INCERTO if _num(ref.get("idx") or "0") else None  # o estilo do tema pinta a forma
    fundos = fundos if atras is None else [atras]
    certos = not any(f is _INCERTO for f in fundos)
    out = []
    for par in sh.text_frame.paragraphs:
        for run in par.runs:
            if not run.text.strip():
                continue
            if run.font.size is not None and run.font.size <= Pt(2.5):
                out.append(("fonte minúscula", run.text, run._r))
                continue
            rpr = run._r.find(f"{_A}rPr")
            solido = rpr.find(f"{_A}solidFill") if rpr is not None else None
            cor = _cor_drawingml(next(iter(solido), None), tema, mapa) if solido is not None else None
            if certos and cor is not None and all(_iguais(cor, f) for f in fundos):
                out.append(("cor igual ao fundo", run.text, run._r))
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
