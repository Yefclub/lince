"""Segurança: texto oculto (branco, minúsculo, invisível, vanish) e padrões de prompt injection."""

import bisect
import functools
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


def texto_oculto_pdf(fundo, itens, imgs, W, H, tique=None):
    """Texto que a IA leria e o humano não vê: invisível fora de imagem (OCR legítimo fica),
    fonte < 2,5 pt, fora da página, ou cor de texto ≈ cor do fundo. Devolve (motivo, item): quem chama tira o item do
    corpo pela posição dele, nunca procurando o texto (um texto visível igual ao oculto fica).

    `fundo` é a página renderizada na escala `escala(W, H, 0.5)`, já convertida pelo Pillow: a conferência não usa a
    PDFium e roda fora da trava dela (pdf.py)."""
    esc = escala(W, H, 0.5)
    ocultos = []
    for x in itens:
        if tique:  # quem chama limita o tempo da análise da página (pdf.py)
            tique()
        motivo = None
        if x.render_mode == 3 and not any(b[0] - 2 <= x.x <= b[2] + 2 and b[1] - 2 <= x.y <= b[3] + 2 for b in imgs):
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
            ocultos.append((motivo, x))
    return ocultos


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_WPS = "{http://schemas.microsoft.com/office/word/2010/wordprocessingShape}"
_WPG = "{http://schemas.microsoft.com/office/word/2010/wordprocessingGroup}"
_V = "{urn:schemas-microsoft-com:vml}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_A14 = "{http://schemas.microsoft.com/office/drawing/2010/main}"
_PIC = "{http://schemas.openxmlformats.org/drawingml/2006/picture}"
_W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"
NAO_CONFERIDO = "texto oculto não conferido"  # o alerta e a pendência levam a parte e o motivo depois de ": "
_DESTAQUES = {"white": "FFFFFF", "black": "000000", "yellow": "FFFF00", "green": "00FF00", "cyan": "00FFFF",
              "magenta": "FF00FF", "blue": "0000FF", "red": "FF0000", "darkBlue": "000080", "darkCyan": "008080",
              "darkGreen": "008000", "darkMagenta": "800080", "darkRed": "800000", "darkYellow": "808000",
              "darkGray": "808080", "lightGray": "C0C0C0"}  # fmt: skip
_TEMA = {"dark1": "dk1", "text1": "dk1", "tx1": "dk1", "light1": "lt1", "background1": "lt1", "bg1": "lt1",
         "dark2": "dk2", "text2": "dk2", "tx2": "dk2", "light2": "lt2", "background2": "lt2", "bg2": "lt2",
         "hyperlink": "hlink", "followedHyperlink": "folHlink"}  # fmt: skip
# os namespaces cujo mc:Choice o anydoc entende (SUPPORTED_NS em formats/docx/content.rs e formats/pptx/mod.rs): o
# ramo que ele lê é o primeiro Choice que só exige esses, ou o Fallback
_LIDOS_WORD = {f"http://schemas.openxmlformats.org/{n}" for n in (
    "wordprocessingml/2006/main", "drawingml/2006/main", "drawingml/2006/picture",
    "drawingml/2006/wordprocessingDrawing", "markup-compatibility/2006", "drawingml/2006/chart",
    "drawingml/2006/diagram")} | {"urn:schemas-microsoft-com:vml", "urn:schemas-microsoft-com:office:office",
    "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"}  # fmt: skip
_LIDOS_PPTX = {"http://schemas.openxmlformats.org/presentationml/2006/main", _A[1:-1], _R[1:-1], _MC[1:-1], _A14[1:-1]}


class XMLIlegivel(Exception):
    """A parte não pôde ser lida para a conferência: quem chama trata como suspeita, nunca como "nada oculto"."""


class Limpeza(NamedTuple):
    """O texto oculto achado e o pacote sem ele (`dados` é o original quando nada saiu). `falhas` são as partes que
    não puderam ser conferidas, com o motivo: ficam como estão e quem chama as mostra no resultado. `vistas` são as
    palavras do que a conferência examinou no que o anydoc lê (e das fontes que não escondem texto por estilo), para a
    rede de segurança (`fora_da_conferencia`)."""

    ocultos: list
    dados: bytes
    falhas: list
    vistas: frozenset = frozenset()


# A rede de segurança da limpeza. Espelhar o percurso do anydoc em Python não fecha a classe de rotas para esconder
# texto: cada rodada de revisão achava outra (parte por outro nome, Id repetido, OOXML estrito, sldIdLst aninhado). A
# rede compara o resultado: o texto que o anydoc extrai do pacote limpo contra o texto que a conferência examinou. A
# palavra lida que não está em nada conferido é texto que nenhuma regra de oculto viu: vira falha, nunca "nada
# oculto". Palavras de 3 letras ou mais, sem número nem pontuação; o custo é linear (um conjunto de cada lado).
_PALAVRA = re.compile(r"[^\W\d_]{3,}")
_INVISIVEIS = re.compile("[\xad\u200b\ufeff\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")  # o que o anydoc tira (clean_text)
_AMOSTRA = 8  # palavras de amostra na falha


def palavras(texto) -> set:
    """As palavras do texto como a rede compara: minúsculas, sem os caracteres que o anydoc tira."""
    return set(_PALAVRA.findall(_INVISIVEIS.sub("", texto).casefold()))


def fora_da_conferencia(vistas, lido) -> str | None:
    """A falha da rede de segurança, ou None: as palavras de `lido` (o texto que o anydoc extraiu do pacote limpo, sem
    o que o Lince acrescenta) que não estão em `vistas`, com a contagem e uma amostra na ordem em que aparecem."""
    fora = list(dict.fromkeys(p for p in _PALAVRA.findall(_INVISIVEIS.sub("", lido).casefold()) if p not in vistas))
    if not fora:
        return None
    amostra = ", ".join(fora[:_AMOSTRA]) + (", …" if len(fora) > _AMOSTRA else "")
    return f"texto lido fora da conferência, {len(fora)} palavra{'s' if len(fora) > 1 else ''} ({amostra})"


_C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
_DGM = "{http://schemas.openxmlformats.org/drawingml/2006/diagram}"
_SEPARA = {f"{_W}br", f"{_W}cr", f"{_W}tab", f"{_W}ptab", f"{_A}br"}
_PARAGRAFOS = {f"{_W}p", f"{_A}p"}
_ENVOLVE = {f"{_W}drawing", f"{_W}pict", f"{_W}object", f"{_M}oMath", f"{_M}oMathPara", f"{_A14}m"}
_RISCADO = {f"{_W}del", f"{_W}moveFrom"}


def _juntar(el, lidos, saida):
    """O texto dos `lidos` (w:t, a:t, m:t conferidos) do parágrafo, emendado como o anydoc emenda os runs: quebra,
    tabulação, desenho, fórmula e parágrafo de dentro (caixa de texto) separam; o texto riscado (w:del, w:moveFrom),
    que o anydoc não lê, não."""
    for f in el:
        if f in lidos:
            saida.append("".join(f.itertext()))
        elif f.tag in _SEPARA or f.tag in _PARAGRAFOS:
            saida.append(" ")
        elif f.tag in _ENVOLVE:
            saida.append(" ")
            _juntar(f, lidos, saida)
            saida.append(" ")
        elif f.tag not in _RISCADO:
            _juntar(f, lidos, saida)


def _registrar(paragrafos, lidos, vistas):
    """As palavras do texto conferido: cada texto sozinho e cada parágrafo emendado (a palavra que o Word partiu em
    dois runs, como "Contr" e "ato", o anydoc lê inteira)."""
    saida = []
    for par in paragrafos:
        _juntar(par, lidos, saida)
        saida.append("\n")
    saida += ("".join(t.itertext()) + "\n" for t in lidos)
    vistas |= palavras("".join(saida))


def _fontes_sem_estilo(pac, parte, fontes, vistas):
    """O texto dos gráficos e dos SmartArt que o anydoc lê das partes deles (`fontes`: o tipo e o r:id de cada um,
    como ele os acha): conta como conferido, como a numeração e o texto alternativo, porque não tem como ser escondido
    por estilo. Só o que ele escreve (shared/drawingml.rs): no gráfico, o título, os nomes das séries, as categorias,
    os valores e o título do eixo; no SmartArt, o texto de cada ponto."""
    for tipo, rid in fontes:
        alvo = pac.alvo(parte, rid)
        if alvo and alvo not in pac.fontes:
            pac.fontes.add(alvo)
            if (arvore := pac.arvore(alvo)) is not None:
                textos = _do_grafico(arvore) if tipo == "grafico" else _do_smartart(arvore)
                vistas |= palavras("\n".join(textos))


def _texto(el) -> str:
    return "".join(el.itertext()) if el is not None else ""


def _texto_de_desenho(el) -> str:
    """drawing_text do anydoc: os a:p com texto, separados por espaço; sem eles, o texto todo."""
    partes = [t for p in el.iter(f"{_A}p") if (t := _texto(p)).strip()]
    return " ".join(partes) if partes else _texto(el)


def _primeiro(el, tag):
    return next(el.iter(tag), None) if el is not None else None


def _do_grafico(raiz) -> list:
    out = [_texto_de_desenho(titulo)] if (titulo := _primeiro(raiz, f"{_C}title")) is not None else []
    categorias, series = [], []
    for ser in raiz.iter(f"{_C}ser"):
        cats = [_texto(v) for v in _todos(ser.find(f"{_C}cat"), f"{_C}v")]
        categorias = categorias or cats
        series.append((_texto(_primeiro(ser.find(f"{_C}tx"), f"{_C}v")), _todos(ser.find(f"{_C}val"), f"{_C}v")))
    if series and categorias:  # a tabela de categorias por séries só sai com as duas
        eixo = _primeiro(raiz, f"{_C}catAx")
        if eixo is not None and (titulo := eixo.find(f"{_C}title")) is not None:
            out.append(_texto_de_desenho(titulo))
        out += [nome for nome, _ in series] + categorias
        out += [_texto(v) for _, valores in series for v in valores[: len(categorias)]]
    return out


def _todos(el, tag) -> list:
    return list(el.iter(tag)) if el is not None else []


def _do_smartart(raiz) -> list:
    return [_texto(pt.find(f"{_DGM}t")) for pt in raiz.iter(f"{_DGM}pt")]


class _Emitido(NamedTuple):
    """O que o anydoc emite de uma parte do Word: os w:t e m:t, o pPr de cada parágrafo (para o rótulo da numeração)
    e os gráficos e SmartArt, como (tipo, r:id)."""

    textos: set
    pprs: list
    fontes: list


_EMBRULHOS = {f"{_W}{n}" for n in ("smartTag", "ins", "bdo", "dir", "moveTo", "customXml")}  # o anydoc entra neles
_MATEMATICA = (f"{_M}oMathPara", f"{_M}oMath")
_RECUSAS_DE_NOTA = ("separator", "continuationSeparator", "continuationNotice")


def _emitido_word(raiz, papel) -> _Emitido:
    """O percurso do anydoc numa parte do Word (formats/docx/content.rs), para a rede comparar só o que ele de fato
    escreve: o corpo (w:body) ou cada nota que não é separador; nos blocos, parágrafo, tabela (sem a célula coberta
    pela mesclagem vertical), sdt e customXml; no parágrafo, o run, o hiperlink, o campo simples e os embrulhos que
    ele percorre (w:del e w:moveFrom, não); no run, o w:t filho, fora da instrução de um campo (entre o begin e o
    separate); no desenho, as caixas de texto e, sem elas, o gráfico ou o SmartArt."""
    out = _Emitido(set(), [], [])

    def blocos(el):
        for f in el:
            if f.tag == f"{_MC}AlternateContent":
                if (ramo := _ramo_lido(f, _LIDOS_WORD)) is not None:
                    blocos(ramo)
            elif f.tag in _MATEMATICA:
                out.textos.update(f.iter(f"{_M}t"))
            elif f.tag == f"{_W}p":
                out.pprs.append(f.find(f"{_W}pPr"))
                inline(f, [], True)
            elif f.tag == f"{_W}tbl":
                tabela(f)
            elif f.tag == f"{_W}sdt":
                if (conteudo := f.find(f"{_W}sdtContent")) is not None:
                    blocos(conteudo)
            elif f.tag == f"{_W}customXml":
                blocos(f)

    def inline(el, campos, mostra):
        """`campos`: em cada campo aberto, se já passou do separate; o texto só sai com todos no resultado."""
        for f in el:
            if f.tag == f"{_MC}AlternateContent":
                if (ramo := _ramo_lido(f, _LIDOS_WORD)) is not None:
                    inline(ramo, campos, mostra)
            elif f.tag == f"{_M}oMathPara":
                out.textos.update(f.iter(f"{_M}t"))
            elif f.tag == f"{_M}oMath":
                if mostra and all(campos):
                    out.textos.update(f.iter(f"{_M}t"))
            elif f.tag == f"{_W}r":
                run(f, campos, mostra)
            elif f.tag in (f"{_W}hyperlink", f"{_W}fldSimple"):  # leitura própria, que sai inteira no lugar
                inline(f, [], mostra and all(campos))
            elif f.tag == f"{_W}sdt":
                if (conteudo := f.find(f"{_W}sdtContent")) is not None:
                    inline(conteudo, campos, mostra)
            elif f.tag in _EMBRULHOS:
                inline(f, campos, mostra)

    def run(r, campos, mostra):
        for f in r:
            if f.tag == f"{_MC}AlternateContent":
                if (ramo := _ramo_lido(f, _LIDOS_WORD)) is not None:
                    run(ramo, campos, mostra)
            elif f.tag == f"{_W}t":
                if mostra and all(campos):
                    out.textos.add(f)
            elif f.tag in (f"{_W}drawing", f"{_W}pict", f"{_W}object"):
                desenho(f)
            elif f.tag == f"{_W}fldChar":
                tipo = _attr(f, _W[1:-1], "fldCharType")
                if tipo == "begin":
                    campos.append(False)
                elif tipo == "separate" and campos:
                    campos[-1] = True
                elif tipo == "end" and campos:
                    campos.pop()

    def desenho(el):
        caixas = []
        _caixas_de_texto(el, caixas)
        for caixa in caixas:
            blocos(caixa)
        if caixas:
            return
        grafico = _primeiro(el, f"{_C}chart")
        if grafico is not None and (rid := _qualificado(grafico, "id")):
            out.fontes.append(("grafico", rid))
        elif (smartart := _primeiro(el, f"{_DGM}relIds")) is not None and (rid := _qualificado(smartart, "dm")):
            out.fontes.append(("smartart", rid))

    def tabela(tbl):
        matriz = []
        for tr in tbl.iterfind(f"{_W}tr"):
            trpr = tr.find(f"{_W}trPr")
            linha = [[None, [], 1, False] for _ in range(_grade(trpr, "gridBefore"))]
            _celulas(tr, linha)
            matriz.append(linha + [[None, [], 1, False] for _ in range(_grade(trpr, "gridAfter"))])
        ativas = set()  # as colunas com uma célula de origem acima (a mesclagem vertical continua nelas)
        for linha in matriz:
            coluna, proximas = 0, set()
            for celula in linha:
                _, _, largura, coberta = celula
                if not coberta or coluna in ativas:
                    proximas.update(range(coluna, coluna + largura))
                else:
                    celula[3] = False  # continuação sem origem: o anydoc lê
                coluna += largura
            ativas = proximas
        for celula in (c for linha in matriz for c in linha if not c[3]):
            for el in ([celula[0]] if celula[0] is not None else []) + celula[1]:
                blocos(el)

    if papel == "corpo":
        if (corpo := raiz.find(f"{_W}body")) is not None:
            blocos(corpo)
    elif papel == "nota" and _local(raiz) in ("footnotes", "endnotes"):
        for nota in raiz.iterfind(f"{_W}{_local(raiz)[:-1]}"):
            if _attr(nota, _W[1:-1], "type") not in _RECUSAS_DE_NOTA and _attr(nota, _W[1:-1], "id") is not None:
                blocos(nota)
    return out


def _qualificado(el, local):
    return el.get(f"{_R}{local}")


def _caixas_de_texto(el, saida):
    """collect_text_boxes do anydoc: os w:txbxContent do desenho, fora do mc:Fallback."""
    for f in el:
        if f.tag == f"{_MC}Fallback":
            continue
        if f.tag == f"{_W}txbxContent":
            saida.append(f)
        else:
            _caixas_de_texto(f, saida)


def _grade(trpr, nome) -> int:
    el = trpr.find(f"{_W}{nome}") if trpr is not None else None
    n = _inteiro(_attr(el, _W[1:-1], "val")) if el is not None else None
    return min(n, 1000) if n is not None and n >= 0 else 0


def _inteiro(valor):
    """Inteiro sem sinal como o Rust lê (`"12"`, nunca `" 12"` nem `"-1"`), ou None."""
    return int(valor) if valor is not None and valor.isascii() and valor.isdigit() else None


def _celulas(pai, linha):
    """collect_row_cells do anydoc: [célula, mescladas na horizontal, largura, coberta na vertical]."""
    for f in pai:
        if f.tag == f"{_W}tc":
            tcpr = f.find(f"{_W}tcPr")
            vmerge = tcpr.find(f"{_W}vMerge") if tcpr is not None else None
            span = tcpr.find(f"{_W}gridSpan") if tcpr is not None else None
            largura = min(max(_inteiro(_attr(span, _W[1:-1], "val")) or 1, 1), 1000) if span is not None else 1
            hmerge = tcpr.find(f"{_W}hMerge") if tcpr is not None else None
            if hmerge is not None and _attr(hmerge, _W[1:-1], "val") != "restart" and linha and not linha[-1][3]:
                linha[-1][2] += largura
                linha[-1][1].append(f)
                continue
            coberta = vmerge is not None and _attr(vmerge, _W[1:-1], "val") != "restart"
            linha.append([f, [], largura, coberta])
        elif f.tag == f"{_W}sdt":
            if (conteudo := f.find(f"{_W}sdtContent")) is not None:
                _celulas(conteudo, linha)
        elif f.tag == f"{_W}customXml":
            _celulas(f, linha)


def _rotulos(numeracao, estilos, pprs) -> set:
    """As palavras do rótulo de numeração dos parágrafos que o anydoc emite (resolve_numbering e numbering.rs): o
    lvlText do nível de cada parágrafo numerado, pela instância (w:num, com lvlOverride e numStyleLink), e os romanos
    quando um nível citado no rótulo (%1, %2...) é romano. O nível que nenhum parágrafo usa não conta."""
    abstratos, diretos, instancias, out, romano = {}, {}, {}, [], False
    for ab in numeracao.iterfind(f"{_W}abstractNum"):
        if (aid := _attr(ab, _W[1:-1], "abstractNumId")) is None:
            continue
        niveis = {}
        for lvl in ab.iterfind(f"{_W}lvl"):
            ilvl = _inteiro(_attr(lvl, _W[1:-1], "ilvl"))
            niveis[0 if ilvl is None else ilvl] = lvl
        link = ab.find(f"{_W}numStyleLink")
        abstratos[aid] = (niveis, _attr(link, _W[1:-1], "val") if link is not None else None)
    for num in numeracao.iterfind(f"{_W}num"):
        ref = num.find(f"{_W}abstractNumId")
        nid, aid = _inteiro(_attr(num, _W[1:-1], "numId")), _attr(ref, _W[1:-1], "val") if ref is not None else None
        if nid is not None and aid is not None:
            diretos[nid] = (aid, num)

    def num_do_estilo(sid, herda):
        cadeia = estilos._cadeia(sid) if herda else [estilos.estilos.get(sid, (None, None))[1]]
        for st in cadeia:
            el = st.find(f"{_W}pPr/{_W}numPr/{_W}numId") if st is not None else None
            if el is not None and (n := _inteiro(_attr(el, _W[1:-1], "val"))) is not None:
                return n
        return None

    def instancia(nid):
        if nid not in instancias:
            instancias[nid] = None
            aid, num = diretos.get(nid, (None, None))
            vistos = set()
            while aid in abstratos and aid not in vistos:
                vistos.add(aid)
                link = abstratos[aid][1]
                proximo = diretos.get(num_do_estilo(link, False)) if link else None
                if proximo is None:
                    break
                aid = proximo[0]
            if aid in abstratos and num is not None:
                niveis = dict(abstratos[aid][0])
                for sobre in num.iterfind(f"{_W}lvlOverride"):
                    ilvl = _inteiro(_attr(sobre, _W[1:-1], "ilvl"))
                    if (lvl := sobre.find(f"{_W}lvl")) is not None:
                        niveis[0 if ilvl is None else ilvl] = lvl
                instancias[nid] = niveis
        return instancias[nid]

    def valor(el, caminho):
        achado = el.find(caminho) if el is not None else None
        return _attr(achado, _W[1:-1], "val") if achado is not None else None

    for ppr in pprs:
        nid, ilvl = _inteiro(valor(ppr, f"{_W}numPr/{_W}numId")), _inteiro(valor(ppr, f"{_W}numPr/{_W}ilvl"))
        estilo = valor(ppr, f"{_W}pStyle")
        if nid is None and estilo is not None:
            nid = num_do_estilo(estilo, True)
        if not nid or (niveis := instancia(nid)) is None:
            continue
        if ilvl is None and estilo is not None:
            ligados = {valor(lvl, f"{_W}pStyle"): k for k, lvl in sorted(niveis.items(), reverse=True) if k < 9}
            cadeia = (st.get(f"{_W}styleId") for st in estilos._cadeia(estilo))
            ilvl = next((ligados[sid] for sid in cadeia if sid in ligados), 0)
        lvl = niveis.get(min(ilvl or 0, 8))
        if lvl is None or (valor(lvl, f"{_W}numFmt") or "bullet") in ("bullet", "none"):
            continue  # o marcador do tópico não é rótulo; o nível sem numeração não escreve nada
        texto = valor(lvl, f"{_W}lvlText") or ""
        out.append(texto)
        citados = (niveis.get(int(k) - 1) for k in re.findall(r"%([1-9])", texto))
        romano |= any(valor(c, f"{_W}numFmt") in ("lowerRoman", "upperRoman") for c in citados if c is not None)
    return palavras(" ".join(out)) | (_romanos() if romano else set())


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


def _serializar(raiz, original: bytes) -> bytes:
    """A parte regravada: o que vem antes da raiz (declaração, DOCTYPE com as entidades sem expandir, comentários)
    sai como estava no arquivo, na mesma codificação, e a raiz pela árvore. O lxml perde o DOCTYPE quando a raiz tem
    prefixo (w:document), por isso o prólogo não passa por ele."""
    from lxml import etree

    codificacao = raiz.getroottree().docinfo.encoding or "UTF-8"
    texto = original.decode(codificacao)
    corpo = etree.tostring(raiz, encoding="unicode") + "".join(
        etree.tostring(irmao, encoding="unicode") for irmao in raiz.itersiblings()
    )
    return (texto[: _fim_do_prologo(texto)] + corpo).encode(codificacao)


def _fim_do_prologo(texto) -> int:
    """Onde começa o elemento raiz: depois da declaração, de comentários, instruções e do DOCTYPE (com o subconjunto
    interno, aspas e comentários dentro dele)."""
    i = 0
    while i < len(texto):
        if texto[i] in " \t\r\n\ufeff":
            i += 1
        elif texto.startswith("<?", i):
            i = texto.index("?>", i) + 2
        elif texto.startswith("<!--", i):
            i = texto.index("-->", i) + 3
        elif texto.startswith("<!DOCTYPE", i):
            aspas, subconjunto = None, False
            while texto[i] != ">" or aspas or subconjunto:
                if aspas:
                    aspas = None if texto[i] == aspas else aspas
                elif texto.startswith("<!--", i):
                    i = texto.index("-->", i) + 2
                elif texto[i] in "\"'":
                    aspas = texto[i]
                elif texto[i] in "[]":
                    subconjunto = texto[i] == "["
                i += 1
            i += 1
        else:
            break
    return i


def _regravar(z, limpas: dict) -> bytes:
    """O pacote com as partes limpas no lugar; as outras vão com o mesmo conteúdo."""
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as zo:
        for info in z.infolist():
            zo.writestr(info, limpas[info.filename] if info.filename in limpas else z.read(info))
    return saida.getvalue()


def _motivo(e) -> str:
    return str(e)[:200] if isinstance(e, XMLIlegivel) else f"{type(e).__name__}: {str(e)[:200]}"


def _esvaziar(t):
    """Tira o texto do elemento e só ele: atributos (xml:space) e o que vem depois dele no XML ficam."""
    t.text = None
    for filho in list(t):
        t.remove(filho)


def _resolver(base, alvo) -> str | None:
    """A parte para onde a relação aponta, como o anydoc resolve (package/path.rs): relativa à pasta da parte de
    origem, ou absoluta a partir da raiz; "..", fragmento e consulta como no OPC. O segmento que, decodificado, tem
    barra ou é "." ou ".." (`%2E%2E`, `a%2Fb`) não resolve: None, como o anydoc, que pula a relação."""
    from urllib.parse import unquote

    alvo = alvo.split("#", 1)[0].split("?", 1)[0]
    if not alvo:
        return base
    partes = [] if alvo.startswith("/") else [p for p in base.split("/")[:-1] if p]
    for seg in alvo.split("/"):
        if seg == "..":
            if partes:
                partes.pop()
        elif seg not in ("", "."):
            seg = unquote(seg, errors="replace")
            if "/" in seg or "\\" in seg or seg in (".", ".."):
                return None
            partes.append(seg)
    return "/".join(partes)


_ESTRITO = "http://purl.oclc.org/ooxml/"
_RELACAO = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"


def _transicional(uri):
    """O namespace (ou o tipo de relação) do OOXML estrito no de transição, como o anydoc lê (normalize_ooxml_uri em
    package/xml.rs): http://purl.oclc.org/ooxml/<família>/<resto> vira
    http://schemas.openxmlformats.org/<família>/2006/<resto>."""
    if uri and uri.startswith(_ESTRITO) and "/" in (resto := uri[len(_ESTRITO) :]):
        familia, _, cauda = resto.partition("/")
        return f"http://schemas.openxmlformats.org/{familia}/2006/{cauda}"
    return uri


def _normalizar(raiz, dados):
    """A árvore com os elementos e atributos do OOXML estrito nos namespaces de transição, para a conferência ler o
    arquivo estrito como o anydoc lê. Só percorre a parte que cita o namespace estrito (UTF-8 ou UTF-16)."""
    if not any(m in dados for m in _MARCAS_ESTRITO):
        return raiz
    for el in raiz.iter():
        if not isinstance(el.tag, str):
            continue
        if el.tag.startswith("{" + _ESTRITO):
            uri, _, local = el.tag[1:].partition("}")
            el.tag = f"{{{_transicional(uri)}}}{local}"
        for chave in [k for k in el.attrib if k.startswith("{" + _ESTRITO)]:
            uri, _, local = chave[1:].partition("}")
            el.attrib[f"{{{_transicional(uri)}}}{local}"] = el.attrib.pop(chave)
    return raiz


_MARCAS_ESTRITO = (_ESTRITO.encode(), _ESTRITO.encode("utf-16-le"), _ESTRITO.encode("utf-16-be"))


def _attr(el, ns, local):
    """O atributo como o anydoc lê (Element::attr): o do namespace `ns`, senão o sem namespace."""
    valor = el.get(f"{{{ns}}}{local}")
    return el.get(local) if valor is None else valor


def _qualquer(el, local):
    """O primeiro atributo com o nome local `local`, em qualquer namespace (Element::attr_any do anydoc)."""
    return next((v for k, v in el.attrib.items() if k.rpartition("}")[2] == local), None)


class _Pacote:
    """As partes e as relações do pacote, lidas uma vez. A parte (ou a relação) que não abre vai para `falhas`."""

    def __init__(self, z):
        self.z, self.nomes, self.falhas, self._arvores, self._rels = z, set(z.namelist()), [], {}, {}
        self.fontes = set()  # partes de gráfico e de SmartArt já contadas na rede
        self.pixels = 0  # pixels das imagens decodificadas para saber a cor delas (_PIXELS_MAX)

    def falhou(self, nome, e):
        self.falhas.append(f"{nome} ({_motivo(e)})")

    def arvore(self, nome):
        """A árvore da parte (a mesma a cada chamada: a limpeza muda essa), ou None se não existe ou não abre."""
        if nome not in self._arvores:
            self._arvores[nome] = None
            if nome in self.nomes:
                try:
                    dados = self.z.read(nome)
                    self._arvores[nome] = _normalizar(_xml(dados), dados)
                except MemoryError:
                    raise
                except Exception as e:  # noqa: BLE001 — XML hostil ou CRC errado: a parte fica como está
                    self.falhou(nome, e)
        return self._arvores[nome]

    def relacoes(self, parte) -> dict:
        """Id -> (tipo, alvo) das relações da parte, como o anydoc lê (package/relationships.rs): Id repetido vale
        pela última; Id, Target, Type e TargetMode valem com qualquer namespace; o tipo é o URI inteiro (o do OOXML
        estrito no de transição); a relação externa fica sem alvo, e a que não resolve também (None)."""
        if parte not in self._rels:
            pasta, _, arquivo = parte.rpartition("/")
            nome = f"{pasta}/_rels/{arquivo}.rels" if pasta else f"_rels/{arquivo}.rels"
            raiz, out = self.arvore(nome), {}
            for rel in raiz.iter(f"{_PKG}Relationship") if raiz is not None else ():
                rid, alvo = _qualquer(rel, "Id"), _qualquer(rel, "Target")
                if rid is None or alvo is None:
                    continue
                externa = (_qualquer(rel, "TargetMode") or "").lower() == "external"
                tipo = _transicional(_qualquer(rel, "Type") or "")
                out[rid] = (None, None) if externa else (tipo, _resolver(parte, alvo))
            self._rels[parte] = out
        return self._rels[parte]

    def do_tipo(self, parte, tipo):
        """O alvo da relação interna do tipo com o menor Id (first_of_type do anydoc), ou None."""
        rels = self.relacoes(parte)
        rid = min((i for i, (t, _) in rels.items() if t == _RELACAO + tipo), default=None)
        return rels[rid][1] if rid is not None else None

    def de_todos(self, parte, tipo) -> list:
        return [a for i, (t, a) in sorted(self.relacoes(parte).items()) if t == _RELACAO + tipo and a]

    def alvo(self, parte, rid):
        return self.relacoes(parte).get(rid, (None, None))[1]

    def principal(self, padrao):
        return self.do_tipo("", "officeDocument") or padrao


def _ramo_lido(alt, suportados):
    """O ramo do mc:AlternateContent que o anydoc lê (shared/mc.rs): o primeiro Choice cujo Requires só exige
    namespaces que ele entende, ou o Fallback."""
    for ramo in alt:
        if ramo.tag == f"{_MC}Choice":
            exige = (_attr(ramo, _MC[1:-1], "Requires") or "").split()
            if all(_transicional(ramo.nsmap.get(p)) in suportados for p in exige):
                return ramo
    return alt.find(f"{_MC}Fallback")


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
    if not isinstance(a, tuple) or not isinstance(b, tuple) or max(abs(x - y) for x, y in zip(a, b, strict=True)) > 64:
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


_INCERTO = object()  # o que o Lince não sabe dizer (fundo de estilo de tabela, foto, cor com tom): não decide nada
_CADEIA_MAX = 64  # profundidade de basedOn: o Word não passa disso; arquivo forjado com mais não custa mais


def _ligado(el):
    return el is not None and el is not _INCERTO and (el.get(f"{_W}val") or "true").lower() not in _DESLIGADO


_DESLIGADO = ("0", "false", "off", "none")


def _tag(nome):
    return nome if nome.startswith("{") else f"{_W}{nome}"


def _mesmo(a, b) -> bool:
    return a is b or (a is not None and b is not None and dict(a.attrib) == dict(b.attrib))


class _Lugar(NamedTuple):
    """Onde o run está: as propriedades do parágrafo e, dentro de célula, o estilo da tabela e as condições dele
    (firstRow, band1Horz...) que se aplicam à célula, cada uma certa ou só possível."""

    ppr: object
    tabela: tuple | None = None


class _EstilosWord:
    """Propriedades de run e parágrafo pela cadeia do Word: run, estilo de caractere, estilo de parágrafo (o padrão,
    quando o parágrafo não diz qual) com basedOn, estilo da tabela (condicional e inteiro) e padrão do documento.
    Memoriza cada propriedade por estilo: o custo é linear no documento, por mais longa que seja a cadeia."""

    def __init__(self, raiz, ilegivel=False):
        self.estilos, self.padrao, self.memoria, self.ilegivel = {}, None, {}, ilegivel
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

    def _memo(self, chave, calcula):
        if chave not in self.memoria:
            self.memoria[chave] = calcula()
        return self.memoria[chave]

    def _do_estilo(self, sid, caminho):
        """O primeiro `caminho` (rPr/x ou pPr/x) pela cadeia de basedOn do estilo, memorizado."""
        return self._memo(
            (sid, caminho), lambda: next((el for st in self._cadeia(sid) if (el := st.find(caminho)) is not None), None)
        )

    def _paridade(self, sid, nome) -> bool:
        """Propriedade alternante (vanish, b, i, caps: ECMA-376 §17.7.3) pela cadeia de basedOn do estilo: cada
        "ligado" inverte o herdado e "desligado" não muda nada, como o anydoc (run_toggles)."""
        return self._memo(
            (sid, "paridade", nome),
            lambda: sum(_ligado(st.find(f"{_W}rPr/{_W}{nome}")) for st in self._cadeia(sid)) % 2 == 1,
        )

    def _condicional(self, sid, tipo, nome):
        """O `nome` do tblStylePr do `tipo` pela cadeia do estilo de tabela."""

        def calcula():
            for st in self._cadeia(sid):
                for cond in st.iterfind(f"{_W}tblStylePr"):
                    if cond.get(f"{_W}type") == tipo and (el := cond.find(f"{_W}rPr/{_tag(nome)}")) is not None:
                        return el
            return None

        return self._memo((sid, tipo, nome), calcula)

    def estilo_da_tabela(self, tbl):
        """O estilo que vale na tabela: o citado, ou o padrão quando ela não cita ou cita um que não existe."""
        st = tbl.find(f"{_W}tblPr/{_W}tblStyle")
        sid = st.get(f"{_W}val") if st is not None else None
        return sid if sid in self.estilos else self.tabela_padrao

    def faixa(self, sid, nome) -> int:
        el = self._do_estilo(sid, f"{_W}tblPr/{_W}{nome}") if sid else None
        return max(_num(el.get(f"{_W}val")) or 1, 1) if el is not None else 1

    def _da_tabela(self, tabela, nome):
        """O `nome` que o estilo da tabela dá à célula: a condição mais forte que se aplica e o define, senão o do
        estilo inteiro. Se uma condição só possível (tblLook ausente) daria outro valor, é _INCERTO."""
        sid, condicoes = tabela
        achado, possiveis = None, []
        for tipo, certa in condicoes:
            if (el := self._condicional(sid, tipo, nome)) is None:
                continue
            if certa:
                achado = el
                break
            possiveis.append(el)
        if achado is None:
            achado = self._do_estilo(sid, f"{_W}rPr/{_tag(nome)}")
        return _INCERTO if any(not _mesmo(el, achado) for el in possiveis) else achado

    def _estilo_do_paragrafo(self, ppr):
        ps = ppr.find(f"{_W}pStyle") if ppr is not None else None
        return ps.get(f"{_W}val") if ps is not None else self.paragrafo_padrao

    def run(self, rpr, lugar, nome):
        """O valor de `nome` (filho de rPr; com namespace, como o w14:textFill, ou sem, no do Word) pela cadeia,
        None, ou _INCERTO."""
        tag = _tag(nome)
        if rpr is not None and (el := rpr.find(tag)) is not None:
            return el
        if rpr is not None and (rs := rpr.find(f"{_W}rStyle")) is not None:
            if (el := self._do_estilo(rs.get(f"{_W}val"), f"{_W}rPr/{tag}")) is not None:
                return el
        if (el := self._do_estilo(self._estilo_do_paragrafo(lugar.ppr), f"{_W}rPr/{tag}")) is not None:
            return el
        if lugar.tabela is not None and (el := self._da_tabela(lugar.tabela, nome)) is not None:
            return el
        return self.padrao.find(tag) if self.padrao is not None else None

    def alternante(self, rpr, lugar, nome):
        """True, False ou None (incerto) da propriedade alternante: a do run vale como está; nos estilos, cada camada
        (tabela, parágrafo, caractere) inverte a anterior sobre o padrão do documento."""
        if rpr is not None and (el := rpr.find(f"{_W}{nome}")) is not None:
            return _ligado(el)
        valor = _ligado(self.padrao.find(f"{_W}{nome}")) if self.padrao is not None else False
        if lugar.tabela is not None:
            if (el := self._da_tabela(lugar.tabela, nome)) is _INCERTO:
                return None
            valor ^= _ligado(el)
        valor ^= self._paridade(self._estilo_do_paragrafo(lugar.ppr), nome)
        if rpr is not None and (rs := rpr.find(f"{_W}rStyle")) is not None:
            valor ^= self._paridade(rs.get(f"{_W}val"), nome)
        return valor

    def sombreamento_paragrafo(self, ppr):
        el = ppr.find(f"{_W}shd") if ppr is not None else None
        if el is None:
            el = self._do_estilo(self._estilo_do_paragrafo(ppr), f"{_W}pPr/{_W}shd")
        return _fundo_do_sombreamento(el)

    def tabela_pinta(self, sid) -> bool:
        """O estilo da tabela pinta alguma célula: há sombreamento na cadeia, inteiro ou condicional (primeira linha,
        faixas). Table Grid e os estilos só de borda não pintam; com styles.xml ilegível, nenhum estilo é certo."""
        if self.ilegivel:
            return True
        if sid is None:
            return False
        return self._memo(
            (sid, "pinta"), lambda: any(_pinta(shd) for st in self._cadeia(sid) for shd in st.iter(f"{_W}shd"))
        )


_CONDICOES = (
    "nwCell",
    "neCell",
    "swCell",
    "seCell",
    "firstRow",
    "lastRow",
    "firstCol",
    "lastCol",
    "band1Horz",
    "band2Horz",
    "band1Vert",
    "band2Vert",
)  # fmt: skip — da mais forte à mais fraca
_VISUAL = {
    "firstRow": 0x20,
    "lastRow": 0x40,
    "firstColumn": 0x80,
    "lastColumn": 0x100,
    "noHBand": 0x200,
    "noVBand": 0x400,
}  # fmt: skip — bits de tblLook w:val


def _filhos(el, tag):
    """Os filhos `tag`, atravessando sdt, sdtContent e customXml (não a tabela aninhada)."""
    for f in el:
        if f.tag == tag:
            yield f
        elif f.tag in (f"{_W}sdt", f"{_W}sdtContent", f"{_W}customXml"):
            yield from _filhos(f, tag)


def _visual(tbl) -> dict:
    """As opções de formatação condicional da tabela (tblLook): True, False, ou None quando ela não diz."""
    look = tbl.find(f"{_W}tblPr/{_W}tblLook")
    if look is None:
        return dict.fromkeys(_VISUAL)
    if any(look.get(f"{_W}{n}") is not None for n in _VISUAL):
        return {n: _ligado_attr(look.get(f"{_W}{n}")) for n in _VISUAL}
    try:
        bits = int(look.get(f"{_W}val") or "", 16)
    except ValueError:
        return dict.fromkeys(_VISUAL)
    return {n: bool(bits & b) for n, b in _VISUAL.items()}


def _ligado_attr(v) -> bool:
    return v is not None and v.lower() not in _DESLIGADO


class _Tabela:
    """Posição de cada célula da tabela e as condições do estilo que valem nela. A tabela sem tblLook pode ter as
    opções ligadas ou não: a condição que depende delas é só possível."""

    def __init__(self, tbl, estilos):
        self.sid, self.visual, self.pos = estilos.estilo_da_tabela(tbl), _visual(tbl), {}
        self.faixa_linha, self.faixa_coluna = (
            estilos.faixa(self.sid, "tblStyleRowBandSize"),
            estilos.faixa(self.sid, "tblStyleColBandSize"),
        )
        linhas = list(_filhos(tbl, f"{_W}tr"))
        self.linhas, self.colunas = len(linhas), len(tbl.findall(f"{_W}tblGrid/{_W}gridCol"))
        for i, tr in enumerate(linhas):
            antes = tr.find(f"{_W}trPr/{_W}gridBefore")
            j = max(_num(antes.get(f"{_W}val")) or 0, 0) if antes is not None else 0
            for tc in _filhos(tr, f"{_W}tc"):
                span = tc.find(f"{_W}tcPr/{_W}gridSpan")
                span = max(_num(span.get(f"{_W}val")) or 1, 1) if span is not None else 1
                self.pos[tc] = (i, j, span)
                j += span
            self.colunas = max(self.colunas, j)
        self.memo = {}

    def condicoes(self, tc) -> tuple:
        if self.sid is None or tc not in self.pos:
            return None
        i, j, span = self.pos[tc]
        # o que decide as condições, com a faixa de cada opção de primeira linha e coluna: poucas chaves por tabela
        faixas = [(i - f) // self.faixa_linha % 2 for f in (0, 1)] + [(j - f) // self.faixa_coluna % 2 for f in (0, 1)]
        chave = (i == 0, i == self.linhas - 1, j == 0, j + span >= self.colunas, *faixas)
        if chave not in self.memo:
            incertas = [n for n, v in self.visual.items() if v is None]
            mundos = []
            for k in range(2 ** len(incertas)):  # cada combinação possível das opções que a tabela não diz
                v = dict(self.visual) | {n: bool(k >> b & 1) for b, n in enumerate(incertas)}
                mundos.append(self._aplicadas(v, i, j, span))
            self.memo[chave] = (
                self.sid,
                [(c, all(c in m for m in mundos)) for c in _CONDICOES if any(c in m for m in mundos)],
            )
        return self.memo[chave]

    def _aplicadas(self, v, i, j, span) -> set:
        primeira, ultima = v["firstRow"] and i == 0, v["lastRow"] and i == self.linhas - 1
        pcol, ucol = v["firstColumn"] and j == 0, v["lastColumn"] and j + span >= self.colunas
        out = {n for n, sim in (("firstRow", primeira), ("lastRow", ultima), ("firstCol", pcol), ("lastCol", ucol),
                                ("nwCell", primeira and pcol), ("neCell", primeira and ucol),
                                ("swCell", ultima and pcol), ("seCell", ultima and ucol)) if sim}  # fmt: skip
        if not v["noHBand"] and not primeira and not ultima:
            out.add(f"band{(i - v['firstRow']) // self.faixa_linha % 2 + 1}Horz")
        if not v["noVBand"] and not pcol and not ucol:
            out.add(f"band{(j - v['firstColumn']) // self.faixa_coluna % 2 + 1}Vert")
        return out


def _fundo_do_sombreamento(shd):
    """Cor que o sombreamento pinta atrás do texto: `fill`, ou `color` no padrão sólido; None se não houver."""
    if shd is None:
        return None
    if shd is _INCERTO:
        return _INCERTO
    if (shd.get(f"{_W}val") or "").lower() == "solid":
        return _rgb(shd.get(f"{_W}color"))
    return _rgb(shd.get(f"{_W}fill"))


def _pinta(shd) -> bool:
    return _fundo_do_sombreamento(shd) is not None or (shd.get(f"{_W}val") or "clear").lower() not in ("clear", "nil")


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


def _fundo_do_run(run, lugar, pagina, estilos, tema, embaixo):
    """O que fica atrás do run: o sombreamento do parágrafo, a forma da caixa de texto (ou, se ela não pinta e flutua,
    o desenho que pode estar embaixo dela), a célula, a linha ou a tabela em que ele está, a imagem atrás do texto que
    cobre o parágrafo, a página. Cada um vale só para o que está nele."""
    if lugar.fundo is not None:
        return lugar.fundo
    coberto, el = False, run.getparent()
    while el is not None:
        if el.tag == f"{_W}p":
            coberto = coberto or el in embaixo.cobertos
        elif el.tag == f"{_W}txbxContent":  # a forma fica por cima da imagem atrás do texto
            if (cor := _fundo_da_caixa(el, tema)) is not None:
                return cor
            if embaixo.sob_a_caixa(el):
                return _INCERTO
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
            if estilos.tabela_pinta(estilos.estilo_da_tabela(el)):
                return _INCERTO
        el = el.getparent()
    return _INCERTO if coberto else pagina


# letras que o Word desenha com as propriedades de escrita complexa (szCs): hebraico, árabe, siríaco, thaana, n'ko,
# samaritano, mandaico, as formas de apresentação e o tailandês
_ESCRITA_COMPLEXA = re.compile("[\u0590-\u08ff\u0e00-\u0e7f\ufb1d-\ufdff\ufe70-\ufeff]")


def _meios_pontos(estilos, rpr, lugar, nome):
    """Tamanho em meios pontos pela cadeia (10 pt quando ninguém diz), ou None quando é incerto."""
    el = estilos.run(rpr, lugar, nome)
    if el is _INCERTO:
        return None
    return 20 if el is None or (n := _num(el.get(f"{_W}val"))) is None else n


def _tamanhos(estilos, rpr, lugar, txt) -> list:
    """Os tamanhos com que o Word desenha o run: o run de escrita complexa (w:cs, w:rtl) inteiro em szCs; os outros em
    sz, e as letras árabes, hebraicas etc. dele em szCs."""
    complexo = [estilos.run(rpr, lugar, n) for n in ("cs", "rtl")]
    if any(_ligado(el) for el in complexo):
        nomes = ["szCs"]
    elif any(el is _INCERTO for el in complexo):
        nomes = ["sz", "szCs"]
    else:
        letras = [_ESCRITA_COMPLEXA.match(c) is not None for c in txt if not c.isspace()]
        nomes = ["sz"] * (not all(letras)) + ["szCs"] * any(letras)
    return [_meios_pontos(estilos, rpr, lugar, n) for n in nomes]


def _motivo_do_run(r, lugar, estilos, tema, pagina, embaixo, txt):
    """Por que o humano não vê o run (vanish, fonte minúscula, escala minúscula, cor igual ao fundo), ou None. Só
    conclui "cor igual ao fundo" quando as duas cores são certas: com tom de tema vale a cor que o Word gravou; com
    fundo ou cor incertos, o texto fica."""
    rpr = r.find(f"{_W}rPr")
    if estilos.alternante(rpr, lugar, "vanish"):
        return "oculto (vanish)"
    tamanhos = _tamanhos(estilos, rpr, lugar, txt)
    if all(t is not None and t <= 4 for t in tamanhos):
        return "fonte minúscula"
    escala_x = estilos.run(rpr, lugar, "w")
    escala_x = _num(escala_x.get(f"{_W}val")) if escala_x is not None and escala_x is not _INCERTO else None
    if escala_x is not None and escala_x <= 10 and all(t is not None and t <= 8 for t in tamanhos):  # 10% em 4 pt
        return "escala minúscula"
    if _sem_tinta(estilos.run(rpr, lugar, f"{_W14}textFill")):
        contorno = estilos.run(rpr, lugar, f"{_W14}textOutline")
        if contorno is None or (contorno is not _INCERTO and _sem_tinta(contorno)):
            return "invisível"
    cor_el = estilos.run(rpr, lugar, "color")
    if cor_el is None or cor_el is _INCERTO:  # automática: o Word escolhe a que contrasta com o fundo
        return None
    cor = _rgb(cor_el.get(f"{_W}val"))
    if (nome := cor_el.get(f"{_W}themeColor")) and not (cor_el.get(f"{_W}themeTint") or cor_el.get(f"{_W}themeShade")):
        cor = tema.get(_TEMA.get(nome, nome))  # tema sem tom: a cor é a do tema; com tom, a que o Word gravou em val
    destaque = estilos.run(rpr, lugar, "highlight")
    if destaque is _INCERTO:
        return None
    atras = _rgb(_DESTAQUES.get(destaque.get(f"{_W}val"), "")) if destaque is not None else None
    atras = (
        atras
        or _fundo_do_sombreamento(estilos.run(rpr, lugar, "shd"))
        or _fundo_do_run(r, lugar, pagina, estilos, tema, embaixo)
    )
    return "cor igual ao fundo" if atras is not _INCERTO and _iguais(cor, atras) else None


# Até 8% de opacidade, nem o preto sobre o branco passa de 1,21:1, abaixo do contraste em que o humano separa as
# cores (1,25:1, em _iguais): o texto quase transparente é invisível sobre qualquer fundo. No DrawingML, a:alpha é a
# opacidade (100000 = opaca); no w14 do Word, w14:alpha é a transparência (100000 = invisível).
_OPACIDADE_MAX = 8000


def _milesimos(valor):
    """Percentual do DrawingML em milésimos ("8000", ou "8%" no OOXML estrito), ou None."""
    try:
        return int(float(valor[:-1]) * 1000) if valor.endswith("%") else int(valor)
    except (AttributeError, ValueError):
        return None


def _transparente(cor) -> bool:
    """A cor (srgbClr, schemeClr... do DrawingML ou do w14) tem opacidade de até 8%. Com alphaMod ou alphaOff a
    opacidade final não é certa: não conta."""
    if cor is None or any(_local(f) in ("alphaMod", "alphaOff") for f in cor):
        return False
    alfa = next((f for f in cor if _local(f) == "alpha"), None)
    valor = _milesimos(_qualquer(alfa, "val")) if alfa is not None else None
    if valor is None:
        return False
    return valor >= 100_000 - _OPACIDADE_MAX if cor.tag.startswith(_W14) else valor <= _OPACIDADE_MAX


_TINTAS = ("noFill", "solidFill", "gradFill", "pattFill", "blipFill", "grpFill")


def _sem_tinta(el) -> bool:
    """O preenchimento do texto (w14:textFill, a:rPr) ou do contorno (w14:textOutline, a:ln) não pinta nada: noFill,
    ou cor sólida quase transparente. Sem preenchimento declarado, ou com degradê, pinta."""
    if el is None or el is _INCERTO:
        return False
    tinta = next((f for f in el if _local(f) in _TINTAS), None)
    if tinta is None:
        return False
    return _local(tinta) == "noFill" or (_local(tinta) == "solidFill" and _transparente(next(iter(tinta), None)))


class _Atras(NamedTuple):
    """Imagem ou forma atrás do texto: onde está presa e o que se sabe do tamanho e da posição vertical (EMU)."""

    el: object
    cx: int | None
    cy: int | None
    rel: str | None  # a que a posição é relativa: page, margin, topMargin, bottomMargin, paragraph, line
    desloc: int | None  # deslocamento a partir de `rel`; None quando a forma é alinhada (topo, centro, base)
    alinha: str | None


# VML: as formas que o Word desenha e a posição relativa no nome do DrawingML
_FORMAS_VML = tuple(f"{_V}{n}" for n in ("shape", "rect", "roundrect", "oval", "image", "group", "line", "polyline",
                                          "arc", "curve"))  # fmt: skip
_RELATIVO_VML = {"page": "page", "margin": "margin", "top-margin-area": "topMargin",
                 "bottom-margin-area": "bottomMargin", "text": "paragraph", "line": "line"}  # fmt: skip
_UNIDADES = {"pt": 12700, "in": 914400, "cm": 360000, "mm": 36000, "pc": 152400, "px": 9525, "": 9525}


def _medida(valor):
    """Medida CSS do VML em EMU ("612pt", "8.5in"), ou None."""
    m = re.fullmatch(r"\s*(-?\d{1,9}(?:\.\d{1,9})?)\s*([a-z]{0,2})\s*", valor or "")
    return int(float(m.group(1)) * _UNIDADES[m.group(2)]) if m and m.group(2) in _UNIDADES else None


def _desenha(dados, imagem, fundo) -> bool:
    """O conteúdo da âncora (a:graphicData) muda o fundo do texto. A imagem toda transparente não pinta nada, e a de
    uma cor só igual ao fundo da página é o próprio fundo; a de outra cor pode estar ou não embaixo do texto (a área
    vem do fluxo), e o texto perto dela fica incerto, como perto de uma foto."""
    if dados is None or not len(dados):
        return False
    blip = dados.find(f"{_PIC}pic/{_PIC}blipFill/{_A}blip")
    if blip is None:
        return True  # forma, gráfico, grupo: o Lince não diz o que eles pintam
    cor = imagem(blip)
    return cor is not None and not (isinstance(cor, tuple) and isinstance(fundo, tuple) and _iguais(cor, fundo))


def _atras_do_texto(raiz, imagem, fundo) -> list:
    """As imagens atrás do texto da parte: âncora DrawingML com behindDoc e forma VML com z-index negativo. Ficam de
    fora as que não desenham nada (sem a:graphic, ou com extensão 0) e as que não mudam o `fundo` da página
    (`imagem` dá a cor da imagem pelo a:blip)."""
    out = []
    for a in raiz.iter(f"{_WP}anchor"):
        if (a.get("behindDoc") or "").lower() in ("1", "true"):
            ext, pv = a.find(f"{_WP}extent"), a.find(f"{_WP}positionV")
            cx, cy = (_num(ext.get("cx")), _num(ext.get("cy"))) if ext is not None else (None, None)
            dados = a.find(f"{_A}graphic/{_A}graphicData")
            if 0 in (cx, cy) or not _desenha(dados, imagem, fundo):
                continue
            desloc = pv.find(f"{_WP}posOffset") if pv is not None else None
            alinha = pv.find(f"{_WP}align") if pv is not None else None
            out.append(_Atras(
                a, cx, cy, pv.get("relativeFrom") if pv is not None else None,
                _num(desloc.text) if desloc is not None else None,
                (alinha.text or "").strip() if alinha is not None else None,
            ))  # fmt: skip
    for v in raiz.iter(*_FORMAS_VML):
        if v.getparent() is not None and v.getparent().tag in _FORMAS_VML:  # dentro de um grupo: vale o grupo
            continue
        css = dict(
            (k.strip().lower(), x.strip()) for k, _, x in (d.partition(":") for d in (v.get("style") or "").split(";"))
        )
        z = re.fullmatch(r"-?\d{1,12}", css.get("z-index", ""))
        if z is None or int(z.group()) >= 0:
            continue
        alinha = css.get("mso-position-vertical")
        desloc = None if alinha not in (None, "absolute") else _medida(css.get("margin-top") or css.get("top") or "0")
        relativo = _RELATIVO_VML.get(css.get("mso-position-vertical-relative", "text"))
        cx, cy = _medida(css.get("width")), _medida(css.get("height"))
        if 0 not in (cx, cy):
            out.append(_Atras(v, cx, cy, relativo, desloc, alinha))
    return out


def _fluxo(raiz, estilos, altura_texto) -> list:
    """(parágrafo, topo, altura, topo na página, quebra) de cada parágrafo da parte, em EMU, na ordem em que o Word os
    empilha: as células de uma linha ficam lado a lado (a linha tem a altura da maior). A altura é a mínima (uma linha
    do maior tamanho de fonte), o que aproxima os parágrafos e só exagera a área que uma imagem pode cobrir. A página
    recomeça na quebra de página, na quebra que o Word gravou ao desenhar (lastRenderedPageBreak), no parágrafo que
    começa página e depois de quebra de seção; fora disso, a cada altura da área do texto."""
    saida, inicio, nova = [], [0], [False]

    def bloco(el, y):
        for f in el:
            if f.tag == f"{_W}p":
                ppr = f.find(f"{_W}pPr")
                quebra = any(
                    x.tag != f"{_W}br" or x.get(f"{_W}type") == "page"
                    for x in f.iter(f"{_W}lastRenderedPageBreak", f"{_W}br")
                )
                antes = ppr.find(f"{_W}pageBreakBefore") if ppr is not None else None
                if antes is None:
                    antes = estilos._do_estilo(estilos._estilo_do_paragrafo(ppr), f"{_W}pPr/{_W}pageBreakBefore")
                if quebra or nova[0] or _ligado(antes):
                    inicio[0], nova[0] = y, False
                h = _altura(f, estilos)
                saida.append((f, y, h, (y - inicio[0]) % altura_texto, quebra))
                nova[0] = ppr is not None and ppr.find(f"{_W}sectPr") is not None
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
    lugar = _Lugar(ppr)
    tamanhos = [_meios_pontos(estilos, ppr.find(f"{_W}rPr") if ppr is not None else None, lugar, "sz")]
    tamanhos += [
        _meios_pontos(estilos, r.find(f"{_W}rPr"), lugar, "sz")
        for r in p.iter(f"{_W}r")
        if r.find(f"{_W}t") is not None
    ]
    return max(max((t for t in tamanhos if t is not None), default=20), 1) * 6350  # meio ponto em EMU


def _juntas(faixas) -> tuple:
    """As faixas (início, fim) unidas e ordenadas, com a lista dos inícios para busca binária."""
    juntas = []
    for ini, fim in sorted(faixas):
        if juntas and ini <= juntas[-1][1]:
            juntas[-1][1] = max(juntas[-1][1], fim)
        else:
            juntas.append([ini, fim])
    return juntas, [ini for ini, _ in juntas]


def _toca(juntas, inicios, ini, fim) -> bool:
    k = bisect.bisect_left(inicios, fim) - 1  # a faixa que começa por último antes do fim é a única que pode tocar
    return k >= 0 and juntas[k][1] > ini


def _cobertos(raiz, estilos, geometria, faixas, imagem, fundo) -> set:
    """Parágrafos que uma imagem atrás do texto pode cobrir. Na própria parte: o parágrafo âncora e os vizinhos até a
    altura da imagem somada ao deslocamento dela (o Word não grava a posição do texto na página: a distância vem do
    fluxo da parte; imagem sem tamanho declarado cobre a parte inteira). `faixas` é o que as imagens do cabeçalho e
    do rodapé cobrem da área do texto em cada página: vale pela posição do parágrafo na página."""
    ancoras = _atras_do_texto(raiz, imagem, fundo)
    if not ancoras and not faixas:
        return set()
    largura, altura, cima, baixo, *_ = geometria
    fluxo = _fluxo(raiz, estilos, max(altura - cima - baixo, 1))
    onde = {p: (y, h) for p, y, h, _, _ in fluxo}
    perto = []
    for a in ancoras:
        p = next((x for x in a.el.iterancestors(f"{_W}p") if x in onde), None)
        if p is None or a.cy is None:
            return set(raiz.iter(f"{_W}p"))
        desloc = altura if a.desloc is None else a.desloc  # alinhada: pode estar em qualquer altura da página
        y, h = onde[p]
        raio = abs(a.cy) + abs(desloc)
        perto.append((y - raio, y + h + raio))
    perto, ini_perto = _juntas(perto)
    faixas, ini_faixas = _juntas(faixas)
    return {
        p
        for p, y, h, yp, quebra in fluxo
        if _toca(perto, ini_perto, y, y + h) or (faixas and (quebra or _toca(faixas, ini_faixas, yp, yp + h)))
    }


def _geometria(corpo) -> tuple:
    """Largura e altura da página, margens (cima, baixo, esquerda, direita) e distância do cabeçalho e do rodapé à
    borda, da última seção, em EMU; Carta com margens de 1" e bordas a 0,5" quando o arquivo não diz."""
    sec = corpo.find(f"{_W}body/{_W}sectPr") if corpo is not None else None
    tam = sec.find(f"{_W}pgSz") if sec is not None else None
    mar = sec.find(f"{_W}pgMar") if sec is not None else None

    def v(el, nome, padrao):
        n = _num(el.get(f"{_W}{nome}")) if el is not None else None
        return abs(padrao if n is None else n) * 635  # twip em EMU

    lados = ("top", "bottom", "left", "right", "header", "footer")
    return v(tam, "w", 12240), v(tam, "h", 15840), *(v(mar, lado, 720 if lado in lados[4:] else 1440) for lado in lados)


def _faixa_da_borda(a, geometria, cabecalho):
    """O que a imagem atrás do texto do cabeçalho (ou do rodapé) cobre da área do texto de cada página, como
    (início, fim) a partir do topo dessa área; None se ela não chega lá; "tudo" para o papel timbrado (um quarto da
    área do texto ou mais) e para a imagem cuja posição o Lince não sabe dizer."""
    largura, altura, cima, baixo, esquerda, direita, dist_cab, dist_rod = geometria
    if a.cx is None or a.cy is None:
        return "tudo"
    base = {"page": 0, "topMargin": 0, "margin": cima, "bottomMargin": altura - baixo}.get(a.rel)
    referencia = {"page": altura, "topMargin": cima, "margin": altura - cima - baixo, "bottomMargin": baixo}.get(a.rel)
    if a.rel in ("paragraph", "line"):  # o parágrafo do cabeçalho começa na distância dele; o do rodapé, na margem
        base, referencia = (dist_cab if cabecalho else altura - baixo), None
    if base is None:
        return "tudo"
    if a.desloc is not None:
        topo = base + a.desloc
    elif referencia is not None and a.alinha in ("top", "center", "bottom"):
        topo = base + {"top": 0, "center": (referencia - abs(a.cy)) // 2, "bottom": referencia - abs(a.cy)}[a.alinha]
    else:
        return "tudo"
    ini, fim = max(topo, cima), min(topo + abs(a.cy), altura - baixo)
    if ini >= fim:
        return None
    if 4 * abs(a.cx) * abs(a.cy) >= (largura - esquerda - direita) * (altura - cima - baixo):
        return "tudo"
    return ini - cima, fim - cima


class _Embaixo:
    """O que pode estar embaixo do texto da parte: os parágrafos que uma imagem atrás do texto pode cobrir e, sob a
    caixa de texto flutuante que não pinta, as outras âncoras, figuras e formas da parte."""

    def __init__(self, raiz, cobertos, geometria):
        self.raiz, self.cobertos, self.geometria, self._desenhos, self._memo = raiz, cobertos, geometria, None, {}

    def sob_a_caixa(self, txbx) -> bool:
        """A caixa de texto (w:txbxContent) sem preenchimento pode estar sobre outro desenho: a de posição conhecida,
        só se os retângulos se tocam; a sem posição, sobre qualquer um. A caixa na linha do texto (wp:inline) fica no
        fluxo: só as imagens atrás do texto (`cobertos`) ficam embaixo dela."""
        dono = next(txbx.iterancestors(f"{_WP}anchor", f"{_WP}inline", *_FORMAS_VML), None)
        if dono is None or dono.tag == f"{_WP}inline":
            return False
        if dono.tag in _FORMAS_VML and "absolute" not in (dono.get("style") or "").replace(" ", "").lower():
            return False
        if dono not in self._memo:
            self._memo[dono] = self._sobre_outro(txbx, dono)
        return self._memo[dono]

    def _sobre_outro(self, txbx, dono) -> bool:
        if next(txbx.iterancestors(f"{_WPG}wgp", f"{_V}group"), None) is not None:
            return True  # no grupo, as outras formas dele podem estar embaixo
        if self._desenhos is None:
            self._desenhos = _desenhos(self.raiz, self.geometria)
        if len(self._desenhos) > _FORMAS_MAX:  # comparar cada caixa com cada desenho é quadrático: incerto
            return True
        caixa, ramos = _retangulo(dono, self.geometria), _ramos(dono)
        for el, ret in self._desenhos:
            if el is dono or any(ramos.get(alt, ramo) is not ramo for alt, ramo in _ramos(el).items()):
                continue  # a própria caixa, ou outro ramo do mesmo mc:AlternateContent (a cópia em VML dela)
            if caixa is None or ret is None or (caixa[0] < ret[2] and ret[0] < caixa[2] and caixa[1] < ret[3]
                                                 and ret[1] < caixa[3]):  # fmt: skip
                return True
        return False


def _ramos(el) -> dict:
    """mc:AlternateContent -> o ramo dele (Choice ou Fallback) em que `el` está."""
    return {ramo.getparent(): ramo for ramo in el.iterancestors(f"{_MC}Choice", f"{_MC}Fallback")}


def _desenhos(raiz, geometria) -> list:
    """(elemento, retângulo ou None) de cada âncora, figura na linha do texto e forma VML da parte que desenha
    alguma coisa, fora do mc:Fallback que tem Choice."""
    out = []
    for el in raiz.iter(f"{_WP}anchor", f"{_WP}inline", *_FORMAS_VML):
        if any(r.getparent().find(f"{_MC}Choice") is not None for r in el.iterancestors(f"{_MC}Fallback")):
            continue  # o Word desenha o Choice (a forma nova), e a cópia do Fallback não conta duas vezes
        if el.tag in _FORMAS_VML:
            if el.getparent() is None or el.getparent().tag not in _FORMAS_VML:  # no grupo, vale o grupo
                out.append((el, None))
            continue
        ext = el.find(f"{_WP}extent")
        dados = el.find(f"{_A}graphic/{_A}graphicData")
        if dados is not None and len(dados) and (ext is None or 0 not in (_num(ext.get("cx")), _num(ext.get("cy")))):
            out.append((el, _retangulo(el, geometria)))
    return out


def _retangulo(el, geometria):
    """(x0, y0, x1, y1) da âncora na página, em EMU, quando a posição é relativa à página ou à margem e dada em
    deslocamento; None para o resto (alinhada, relativa ao parágrafo ou à coluna, figura na linha, VML)."""
    if el.tag != f"{_WP}anchor" or (ext := el.find(f"{_WP}extent")) is None:
        return None
    if (el.get("simplePos") or "").lower() in ("1", "true"):
        pos = el.find(f"{_WP}simplePos")
        x, y = (_num(pos.get("x")), _num(pos.get("y"))) if pos is not None else (None, None)
    else:
        x = _deslocamento(el.find(f"{_WP}positionH"), geometria[4])
        y = _deslocamento(el.find(f"{_WP}positionV"), geometria[2])
    cx, cy = _num(ext.get("cx")), _num(ext.get("cy"))
    return None if None in (x, y, cx, cy) else (x, y, x + abs(cx), y + abs(cy))


def _deslocamento(pos, margem):
    base = {"page": 0, "margin": margem}.get(pos.get("relativeFrom")) if pos is not None else None
    desloc = pos.find(f"{_WP}posOffset") if base is not None else None
    n = _num(desloc.text) if desloc is not None else None
    return None if n is None else base + n


_BORDA = re.compile(r"word/(header|footer)\d*\.xml")


def limpar_docx(dados: bytes) -> Limpeza:
    """Texto do Word que a IA leria e o humano não vê, esvaziado no próprio XML antes da conversão: corpo, caixas de
    texto, notas de rodapé e de fim, comentários, cabeçalho e rodapé, achados pelas relações do pacote, como o anydoc
    acha. Só as partes alteradas são regravadas; texto visível igual a um oculto fica onde está. A parte que não pode
    ser lida vai para `falhas` e fica como estava."""
    try:
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            ocultos, limpas, falhas, vistas = _conferir_word(z)
            return Limpeza(ocultos, _regravar(z, limpas) if limpas else dados, falhas, frozenset(vistas))
    except MemoryError:
        raise
    except Exception as e:  # noqa: BLE001 — zip que não abre ou parte cifrada: nada conferido, nada muda
        return Limpeza([], dados, [f"pacote ({_motivo(e)})"])


def bordas_sem_oculto(z) -> dict:
    """Cabeçalho, rodapé e comentários sem o texto oculto (só as partes que mudaram), para os metadados: o que sai do
    corpo não volta por lá. A falha já aparece pela conferência do corpo; aqui a parte só fica como está."""
    try:
        return _conferir_word(z, so_metadados=True)[1]
    except MemoryError:
        raise
    except Exception:  # noqa: BLE001 — a mesma falha vai no resultado pela leitura do corpo
        return {}


def _conferir_word(z, so_metadados=False) -> tuple:
    """As partes que o anydoc lê (a principal pelo officeDocument; estilos e notas pelas relações dela) e as que os
    metadados leem (cabeçalho, rodapé e comentários, pelas relações e pelo nome)."""
    pac = _Pacote(z)
    corpo_nome = pac.principal("word/document.xml")

    def tipo(t, padrao):
        return pac.do_tipo(corpo_nome, t) or _resolver(corpo_nome, padrao)

    estilos_nome = tipo("styles", "styles.xml")
    raiz = pac.arvore(estilos_nome)
    estilos = _EstilosWord(raiz, ilegivel=raiz is None and estilos_nome in pac.nomes)
    tema = pac.do_tipo(corpo_nome, "theme") or next(
        (n for n in sorted(pac.nomes) if re.fullmatch(r"word/theme/theme\d*\.xml", n)), None
    )
    tema = _esquema(raiz) if tema and (raiz := pac.arvore(tema)) is not None else {}
    corpo = pac.arvore(corpo_nome)
    papeis = {corpo_nome: "corpo", tipo("footnotes", "footnotes.xml"): "nota", tipo("endnotes", "endnotes.xml"): "nota"}
    papeis |= dict.fromkeys([tipo("comments", "comments.xml"), "word/comments.xml"], "comentario")
    bordas = {n: "cabecalho" for n in pac.de_todos(corpo_nome, "header")}
    bordas |= {n: "rodape" for n in pac.de_todos(corpo_nome, "footer")}
    bordas |= {n: ("cabecalho" if "header" in n else "rodape") for n in pac.nomes if _BORDA.fullmatch(n)}
    papeis |= bordas
    if so_metadados:  # os metadados leem cabeçalho, rodapé e comentários pelo nome
        papeis = {n: p for n, p in papeis.items() if _BORDA.fullmatch(n) or n == "word/comments.xml"}
    partes = {n: pac.arvore(n) for n in sorted(papeis, key=lambda n: (n != corpo_nome, n)) if n in pac.nomes}
    fundo = corpo.find(f"{_W}background") if corpo is not None else None
    pagina = (_rgb(fundo.get(f"{_W}color")) if fundo is not None else None) or (255, 255, 255)
    if fundo is not None and len(fundo):  # fundo de imagem ou degradê
        pagina = _INCERTO
    geometria = _geometria(corpo)
    faixas, imagens, cor_da_pagina = [], {}, pagina  # faixas: o que as imagens atrás do texto do cabeçalho e do
    # rodapé cobrem da área do texto; imagens: a cor de cada imagem já decodificada

    def imagem(parte):
        return lambda blip: _cor_da_imagem(blip, pac, parte, imagens)

    for nome in pac.de_todos(corpo_nome, "header") + pac.de_todos(corpo_nome, "footer"):  # as que o Word desenha
        raiz = pac.arvore(nome)
        for a in _atras_do_texto(raiz, imagem(nome), cor_da_pagina) if raiz is not None else ():
            if (faixa := _faixa_da_borda(a, geometria, bordas[nome] == "cabecalho")) == "tudo":
                pagina = _INCERTO  # o papel timbrado fica atrás de tudo o que está na página
            elif faixa is not None:
                faixas.append(faixa)
    ocultos, limpas, vistas, pprs = [], {}, set(), []
    for nome, raiz in partes.items():
        if raiz is None:
            continue
        papel = papeis[nome]
        atras = (255, 255, 255) if papel == "comentario" else pagina  # o comentário fica no balão, fora da página
        cobre = faixas if papel == "corpo" else [(-1, 1 << 62)] if faixas and papel == "nota" else ()
        # o anydoc lê o corpo e as notas; o resto, só os metadados
        emitido = _emitido_word(raiz, papel) if papel in ("corpo", "nota") else None
        achados, mudou = _conferir_parte(
            raiz, estilos, tema, atras, geometria, cobre, papel, imagem(nome), emitido, vistas
        )
        ocultos += achados
        if mudou:
            limpas[nome] = _serializar(raiz, z.read(nome))
        if emitido is not None:
            pprs += emitido.pprs
            _fontes_sem_estilo(pac, nome, emitido.fontes, vistas)
    if not so_metadados and (numeracao := pac.arvore(tipo("numbering", "numbering.xml"))) is not None:
        vistas |= _rotulos(numeracao, estilos, pprs)  # o rótulo do título numerado ("Cláusula 1.", "IV.")
    return ocultos, limpas, pac.falhas, vistas


@functools.cache
def _romanos() -> frozenset:
    """Os números romanos de 1 a 3999 (o anydoc escreve os maiores em algarismos), em minúsculas."""
    valores = ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
               (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"))  # fmt: skip
    out = set()
    for n in range(1, 4000):
        romano = ""
        for valor, letras in valores:
            while n >= valor:
                romano, n = romano + letras, n - valor
        out.add(romano)
    return frozenset(out)


class _Paragrafo(NamedTuple):
    ppr: object
    tabela: tuple | None
    fundo: object


def _do_paragrafo(par, estilos, tabelas) -> _Paragrafo:
    ppr = par.find(f"{_W}pPr") if par is not None else None
    tc = next(par.iterancestors(f"{_W}tc", f"{_W}txbxContent"), None) if par is not None else None
    tabela = None
    if tc is not None and tc.tag == f"{_W}tc" and (tbl := next(tc.iterancestors(f"{_W}tbl"), None)) is not None:
        if tbl not in tabelas:
            tabelas[tbl] = _Tabela(tbl, estilos)
        tabela = tabelas[tbl].condicoes(tc)
    return _Paragrafo(ppr, tabela, estilos.sombreamento_paragrafo(ppr))


def _lido(r, papel, ramos) -> bool:
    """O run está no que é lido: no corpo e nas notas, no ramo do mc:AlternateContent que o anydoc usa (dentro do
    desenho, ele pega todo Choice e pula o Fallback); no cabeçalho, no rodapé e nos comentários, fora do Fallback,
    como os metadados leem. A cópia que ninguém lê também é esvaziada, mas não conta."""
    for ramo in r.iterancestors(f"{_MC}Choice", f"{_MC}Fallback"):
        alt = ramo.getparent()
        if alt is None:
            continue
        if papel not in ("corpo", "nota"):
            if ramo.tag == f"{_MC}Fallback":
                return False
            continue
        if alt not in ramos:
            dono = next(alt.iterancestors(f"{_W}drawing", f"{_W}pict", f"{_W}object", f"{_W}txbxContent",
                                          f"{_W}p"), None)  # fmt: skip
            ramos[alt] = "desenho" if dono is not None and dono.tag in (f"{_W}drawing", f"{_W}pict", f"{_W}object") \
                else _ramo_lido(alt, _LIDOS_WORD)  # fmt: skip
        lido = ramos[alt]
        if (lido == "desenho" and ramo.tag == f"{_MC}Fallback") or (lido != "desenho" and ramo is not lido):
            return False
    return True


def _conferir_parte(raiz, estilos, tema, pagina, geometria, faixas, papel, imagem, emitido, vistas) -> tuple:
    """Esvazia o texto dos runs ocultos da parte (w:t do run, em todos os ramos de mc:AlternateContent dele, e m:t da
    fórmula); devolve o que saiu do que é lido e se a parte mudou. Com o que o anydoc emite da parte (`emitido`), junta
    em `vistas` as palavras do que foi conferido e ele escreve, depois da limpeza."""
    embaixo = _Embaixo(raiz, _cobertos(raiz, estilos, geometria, faixas, imagem, pagina), geometria)
    runs = {}
    for t in raiz.iter(f"{_W}t", f"{_M}t"):  # o texto de uma caixa dentro do run é do run de dentro
        if (r := next(t.iterancestors(f"{_W}r", f"{_M}r"), None)) is not None:
            runs.setdefault(r, []).append(t)
    achados, mudou, pars, tabelas, ramos = [], False, {}, {}, {}
    for r, textos in runs.items():
        txt = "".join("".join(t.itertext()) for t in textos)
        if not txt.strip():
            continue
        par = next(r.iterancestors(f"{_W}p"), None)
        if par not in pars:
            pars[par] = _do_paragrafo(par, estilos, tabelas)
        if motivo := _motivo_do_run(r, pars[par], estilos, tema, pagina, embaixo, txt):
            for t in textos:
                _esvaziar(t)
            mudou = True
            if _lido(r, papel, ramos):
                achados.append((motivo, txt))
    if emitido is not None:
        lidos = emitido.textos & {t for textos in runs.values() for t in textos}  # o lido que foi conferido
        _registrar(raiz.iter(f"{_W}p"), lidos, vistas)
    return achados, mudou


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


def _preenchimento(el, tema, mapa, imagem=None):
    """Cor de um spPr/bgPr/tcPr: a do solidFill quando é certa, ou a da imagem de uma cor só (`imagem(blip)`);
    _INCERTO para foto, degradê ou cor incerta; None sem preenchimento declarado."""
    if el is None:
        return None
    if el.find(f"{_A}noFill") is not None:
        return None
    if (solido := el.find(f"{_A}solidFill")) is not None:
        return _cor_drawingml(next(iter(solido), None), tema, mapa) or _INCERTO
    if (blip := el.find(f"{_A}blipFill")) is not None:
        return imagem(blip.find(f"{_A}blip")) if imagem is not None else _INCERTO
    if any(el.find(f"{_A}{t}") is not None for t in ("gradFill", "pattFill", "grpFill")):
        return _INCERTO
    return None


# Decodificar a imagem custa de 10 a 30 ns por pixel (PNG e JPEG de 12 MP: 0,15 s e 0,4 s). Um documento com
# centenas de fotos grandes passaria segundos só nisso: até 50 milhões de pixels por documento (~1 s), cada imagem nova
# é decodificada; dali em diante, ela fica incerta, como foto, sem ser decodificada.
_PIXELS_MAX = 50_000_000


def _cor_da_imagem(blip, pac, parte, imagens):
    """A cor da imagem quando ela é toda de uma cor só (a imagem branca que esconde a letra branca), None quando ela é
    toda transparente (não pinta nada); _INCERTO para foto, transparência parcial, efeito de cor, imagem que não abre
    ou acima do teto de pixels do documento."""
    from PIL import Image

    if blip is None or any(_local(f) != "extLst" for f in blip):  # duotone, lum, alphaModFix: a cor muda
        return _INCERTO
    alvo = pac.alvo(parte, blip.get(f"{_R}embed"))
    if alvo is None or alvo not in pac.nomes:
        return _INCERTO
    if alvo not in imagens:
        imagens[alvo] = _INCERTO
        try:
            with Image.open(io.BytesIO(pac.z.read(alvo))) as im:
                pac.pixels += im.width * im.height  # pelo cabeçalho, antes de decodificar
                if pac.pixels > _PIXELS_MAX:
                    return _INCERTO
                im.thumbnail((512, 512))  # a cor uniforme sobrevive à redução; o custo fica pequeno
                faixas = im.convert("RGBA").getextrema()
        except MemoryError:
            raise
        except Exception:  # noqa: BLE001, S110 — EMF, SVG, CRC errado ou imagem quebrada: o fundo fica incerto
            pass
        else:
            if faixas[3][1] == 0:
                imagens[alvo] = None
            elif faixas[3][0] == 255 and all(alto - baixo <= 8 for baixo, alto in faixas[:3]):
                imagens[alvo] = tuple((baixo + alto) // 2 for baixo, alto in faixas[:3])
    return imagens[alvo]


# Comparar a caixa de cada forma com texto com as já desenhadas é quadrático no slide (~1 µs por comparação). Um
# slide real tem dezenas de formas (SmartArt e gráfico ficam em partes próprias); acima de 500 (125 mil comparações,
# ~0,1 s) o slide vira falha e fica como está, em vez de travar a leitura com um slide forjado.
_FORMAS_MAX = 500
# O mesmo custo somado na apresentação: 400 KB de zip com slides no teto custavam 19 s. 3 milhões de comparações
# (~3 s) cobrem 600 slides de 100 formas; daí em diante cada slide vira falha, sem nem ser aberto.
_COMPARACOES_MAX = 3_000_000
_FORMAS = tuple(f"{_P}{n}" for n in ("sp", "cxnSp", "pic", "graphicFrame", "grpSp", "contentPart"))
_SEM_ESTILO = {"{2D5ABB26-0587-4C30-8999-92F81FD0307C}", "{5940675A-B579-460E-94D1-54222C63F5DA}"}  # sem pintura
_PREENCHE_TEXTO = tuple(f"{_A}{n}" for n in ("solidFill", "noFill", "gradFill", "blipFill", "pattFill", "grpFill"))


class _Teto(XMLIlegivel):
    """O slide passou de um teto de custo: fica como está e vira falha, com o teto no motivo."""


class _Placeholders:
    """Os placeholders de um layout ou mestre, para a herança pelo `ph` (type e idx), como o anydoc casa."""

    def __init__(self, arvore_formas):
        self.idx, self.tipo, self.classe = {}, {}, {}  # o primeiro de cada, na ordem do arquivo
        for sp in arvore_formas.iter(f"{_P}sp") if arvore_formas is not None else ():
            if (ph := _ph(sp)) is not None:
                tipo, idx = ph
                if idx is not None:
                    self.idx.setdefault(idx, sp)
                self.tipo.setdefault(tipo, sp)
                self.classe.setdefault(_classe(tipo), sp)

    def casar(self, tipo, idx):
        """Pelo idx; senão pelo tipo (os de título valem entre si); senão o primeiro da mesma classe."""
        if idx is not None and idx in self.idx:
            return self.idx[idx]
        classe = _classe(tipo)
        if classe == "body" and tipo in self.tipo:
            return self.tipo[tipo]
        return self.classe.get(classe)


def _classe(tipo):
    return "title" if tipo in ("title", "ctrTitle") else "body"


def _nv(el):
    """O cNvPr e o nvPr da forma (nvSpPr, nvPicPr, nvGraphicFramePr...)."""
    nv = next((x for x in el if _local(x).startswith("nv")), None)
    return (nv.find(f"{_P}cNvPr"), nv.find(f"{_P}nvPr")) if nv is not None else (None, None)


def _ph(el):
    nvpr = _nv(el)[1]
    ph = nvpr.find(f"{_P}ph") if nvpr is not None else None
    return (ph.get("type") or "body", ph.get("idx")) if ph is not None else None


class _Camada(NamedTuple):
    """Slide, layout, mestre ou página de anotações: a parte, a árvore, os placeholders e o que ela herda."""

    nome: str
    raiz: object
    phs: object
    pai: object  # o layout do slide, o mestre do layout, None no mestre


class _Apresentacao:
    """O que vale para todos os slides: o pacote, o tamanho, o estilo de texto padrão e os layouts e mestres lidos."""

    def __init__(self, pac):
        self.pac, self.camadas, self.temas, self.imagens, self.comparacoes = pac, {}, {}, {}, 0
        self.nome = pac.principal("ppt/presentation.xml")
        self.raiz = pac.arvore(self.nome)
        if self.raiz is None:
            raise XMLIlegivel("a apresentação não abre") if self.nome in pac.nomes else KeyError(self.nome)
        tam = self.raiz.find(f"{_P}sldSz")
        self.W, self.H = (_num(tam.get("cx")) or 0, _num(tam.get("cy")) or 0) if tam is not None else (0, 0)
        self.padrao = next(self.raiz.iter(f"{_P}defaultTextStyle"), None)
        estilos = pac.arvore(pac.do_tipo(self.nome, "tableStyles") or "")
        self.tabela_padrao = estilos.get("def") if estilos is not None else None

    def slides(self) -> list:
        """Os slides como o anydoc acha: o primeiro sldIdLst da apresentação, em qualquer profundidade, e os sldId
        dele, pelo r:id."""
        lista = next(self.raiz.iter(f"{_P}sldIdLst"), None)
        ids = lista.iterfind(f"{_P}sldId") if lista is not None else ()
        return [a for s in ids if (a := self.pac.alvo(self.nome, s.get(f"{_R}id")))]

    def camada(self, nome, tipo_pai):
        """O layout (ou o mestre) `nome`, com o pai dele (`tipo_pai`: slideMaster, notesMaster ou None)."""
        if nome not in self.camadas:
            raiz = self.pac.arvore(nome) if nome else None
            if raiz is None:
                self.camadas[nome] = None
            else:
                pai = self.camada(self.pac.do_tipo(nome, tipo_pai), None) if tipo_pai else None
                self.camadas[nome] = _Camada(nome, raiz, _Placeholders(raiz.find(f"{_P}cSld/{_P}spTree")), pai)
        return self.camadas[nome]

    def tema(self, mestre) -> tuple:
        """O esquema de cores do tema do mestre e o mapa de cores dele."""
        if mestre is None:
            return {}, {}
        if mestre.nome not in self.temas:
            raiz = self.pac.arvore(self.pac.do_tipo(mestre.nome, "theme") or "")
            clr = mestre.raiz.find(f"{_P}clrMap")
            self.temas[mestre.nome] = (
                _esquema(raiz) if raiz is not None else {},
                dict(clr.attrib) if clr is not None else {},
            )
        return self.temas[mestre.nome]

    def imagem(self, parte):
        return lambda blip: _cor_da_imagem(blip, self.pac, parte, self.imagens)


def limpar_pptx(dados: bytes) -> Limpeza:
    """Texto dos slides e das anotações que a IA leria e o humano não vê, esvaziado no próprio XML antes da conversão:
    o run oculto e, se a forma estiver fora do slide ou oculta, o texto inteiro dela. O percurso é o do anydoc: os
    slides do sldIdLst, cada forma do spTree (com mc:AlternateContent e grupos), as células de tabela e as anotações.
    Só as partes alteradas são regravadas; texto visível igual a um oculto fica onde está. A parte que não pode ser
    conferida fica como estava e vai para `falhas`."""
    try:
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            ocultos, limpas, falhas, vistas = _conferir_pptx(z)
            return Limpeza(ocultos, _regravar(z, limpas) if limpas else dados, falhas, frozenset(vistas))
    except MemoryError:
        raise
    except Exception as e:  # noqa: BLE001 — zip que não abre, apresentação sem presentation.xml: nada conferido
        return Limpeza([], dados, [f"apresentação ({_motivo(e)})"])


def _conferir_pptx(z) -> tuple:
    pac = _Pacote(z)
    try:
        ap = _Apresentacao(pac)
    except XMLIlegivel:  # a falha da parte já está em pac.falhas
        return [], {}, pac.falhas, set()
    ocultos, limpas, vistas = [], {}, set()
    for nome in ap.slides():
        if ap.comparacoes > _COMPARACOES_MAX:
            pac.falhou(nome, _Teto(f"sobreposição de formas acima do teto de {_COMPARACOES_MAX} comparações"))
            continue
        slide = ap.pac.arvore(nome)
        if slide is None:
            continue
        layout = ap.camada(pac.do_tipo(nome, "slideLayout"), "slideMaster")
        registro = []  # os corpos de texto que o anydoc lê, conferidos
        try:
            achados, mudou = _conferir_slide(ap, _Camada(nome, slide, None, layout), registro)
        except MemoryError:
            raise
        except Exception as e:  # noqa: BLE001 — teto de custo ou XML forjado que o percurso não previu: o slide
            pac.falhou(nome, e)  # fica como está (a árvore só muda no fim da conferência, que não chegou)
        else:
            ocultos += achados
            if mudou:  # também quando só a cópia que o anydoc não lê saiu
                limpas[nome] = _serializar(slide, z.read(nome))
            _registrar_corpos(registro, vistas)
            _fontes_sem_estilo(pac, nome, _fontes_do_slide(slide.find(f"{_P}cSld/{_P}spTree")), vistas)
        if (notas := pac.do_tipo(nome, "notesSlide")) and (raiz := pac.arvore(notas)) is not None:
            mestre, registro = ap.camada(pac.do_tipo(notas, "notesMaster"), None), []
            achados, mudou = _conferir_anotacoes(ap, _Camada(notas, raiz, None, mestre), registro)
            ocultos += achados
            if mudou:
                limpas[notas] = _serializar(raiz, z.read(notas))
            _registrar_corpos(registro, vistas)
    return ocultos, limpas, pac.falhas, vistas


def _registrar_corpos(corpos, vistas):
    """As palavras dos corpos de texto lidos, como o anydoc os lê (parse_para_inlines): do a:r e do a:fld, só o
    primeiro a:t filho; da fórmula, todo m:t."""
    for corpo in corpos:
        lidos = set()
        for r, *_ in _runs_do_corpo(corpo):
            if r.tag in (f"{_A}r", f"{_A}fld"):
                lidos.update(t for t in [r.find(f"{_A}t")] if t is not None)
            else:
                lidos.update(_textos(r))
        _registrar(corpo.iterfind(f"{_A}p"), lidos, vistas)


def _fontes_do_slide(arvore) -> list:
    """(tipo, r:id) dos gráficos e SmartArt que o anydoc lê do slide (parse_shapes, parse_graphic_frame): o quadro
    que não é tabela nem objeto OLE, no ramo do mc:AlternateContent que ele lê e dentro dos grupos."""
    out = []
    for f in arvore if arvore is not None else ():
        if f.tag == f"{_MC}AlternateContent":
            out += _fontes_do_slide(_ramo_lido(f, _LIDOS_PPTX))
        elif f.tag == f"{_P}grpSp":
            out += _fontes_do_slide(f)
        elif f.tag == f"{_P}graphicFrame" and _primeiro(f, f"{_A}tbl") is None and _primeiro(f, f"{_P}oleObj") is None:
            if (grafico := _primeiro(f, f"{_C}chart")) is not None and (rid := _qualificado(grafico, "id")):
                out.append(("grafico", rid))
            elif (smartart := _primeiro(f, f"{_DGM}relIds")) is not None and (rid := _qualificado(smartart, "dm")):
                out.append(("smartart", rid))
    return out


def _lida(el) -> bool:
    """O anydoc lê o texto da forma: ele só pula o placeholder de número, data e rodapé, e decide pelo primeiro p:ph
    de dentro da forma, onde quer que esteja (parse_shape). A conferência confere a forma de qualquer jeito."""
    ph = next(el.iter(f"{_P}ph"), None)
    return ph is None or (_attr(ph, _P[1:-1], "type") or "body") not in ("sldNum", "dt", "ftr")


def _mestre(camada):
    """O fim da cadeia de herança (o mestre do layout do slide), ou None sem layout."""
    c = camada.pai
    while c is not None and c.pai is not None:
        c = c.pai
    return c


def _fundo_da_camada(ap, camadas, tema, mapa):
    """O fundo do slide: o primeiro de slide, layout e mestre que declara um."""
    for camada in camadas:
        if camada is None or (bg := camada.raiz.find(f"{_P}cSld/{_P}bg")) is None:
            continue
        bgpr, bgref = bg.find(f"{_P}bgPr"), bg.find(f"{_P}bgRef")
        if bgpr is not None:
            return _preenchimento(bgpr, tema, mapa, ap.imagem(camada.nome)) or _INCERTO
        if bgref is not None and _num(bgref.get("idx") or "0") in (0, 1001):  # sólido com a cor do bgRef é certo
            return _cor_drawingml(next(iter(bgref), None), tema, mapa) or _INCERTO
        return _INCERTO  # estilo de fundo do tema
    return (255, 255, 255)


def _mapa(camadas, mapa):
    """O mapa de cores do mestre, ou o que o slide (ou o layout) põe no lugar dele (clrMapOvr)."""
    for camada in camadas:
        if camada is not None and (ovr := camada.raiz.find(f"{_P}clrMapOvr/{_A}overrideClrMapping")) is not None:
            return dict(ovr.attrib)
    return mapa


def _formas(pai, lido=True, desenhado=True):
    """(forma, lida pelo anydoc, desenhada) dos filhos do spTree ou do grupo, na ordem de desenho. No
    mc:AlternateContent, o anydoc lê o ramo que entende e o PowerPoint desenha o primeiro Choice."""
    for f in pai:
        if f.tag == f"{_MC}AlternateContent":
            lido_ramo, ramos = _ramo_lido(f, _LIDOS_PPTX), [x for x in f if x.tag in (f"{_MC}Choice", f"{_MC}Fallback")]
            for ramo in ramos:
                yield from _formas(ramo, lido and ramo is lido_ramo, desenhado and ramo is ramos[0])
        elif f.tag in _FORMAS:
            yield f, lido, desenhado


def _oculta(el) -> bool:
    """A forma está oculta (painel de seleção): o PowerPoint não a desenha, e o anydoc lê o texto dela."""
    pr = _nv(el)[0]
    return pr is not None and (pr.get("hidden") or "").lower() in ("1", "true")


def _caixa(el, herdadas=()):
    """(x0, y0, x1, y1) da forma em EMU, a do placeholder de onde ela herda quando não tem a sua, ou None."""
    for f in (el, *herdadas):
        if f is None:
            continue
        xfrm = f.find(f"{_P}xfrm") if f.tag == f"{_P}graphicFrame" else None
        if xfrm is None:
            pr = next((x for x in f if _local(x) in ("spPr", "grpSpPr")), None)
            xfrm = pr.find(f"{_A}xfrm") if pr is not None else None
        off, ext = (xfrm.find(f"{_A}off"), xfrm.find(f"{_A}ext")) if xfrm is not None else (None, None)
        if off is not None and ext is not None:
            x, y, cx, cy = (_num(off.get("x")), _num(off.get("y")), _num(ext.get("cx")), _num(ext.get("cy")))
            if None not in (x, y, cx, cy):
                return x, y, x + cx, y + cy
    return None


def _herdadas(el, camada):
    """Os placeholders do layout e do mestre de onde a forma herda (posição, preenchimento, texto)."""
    if (ph := _ph(el)) is None:
        return []
    out, c = [], camada.pai
    while c is not None:
        out.append(c.phs.casar(*ph))
        c = c.pai
    return out


def _pintura(el, herdadas, tema, mapa, imagem):
    """O que a forma pinta embaixo do que vem por cima: a cor certa (preenchimento sólido, imagem de uma cor só),
    _INCERTO (foto, gráfico, tabela, grupo, estilo do tema) ou None (sem preenchimento, oculta)."""
    if _oculta(el):
        return None
    if el.tag == f"{_P}pic":
        return imagem(el.find(f"{_P}blipFill/{_A}blip"))
    if el.tag in (f"{_P}graphicFrame", f"{_P}grpSp", f"{_P}contentPart"):
        return _INCERTO
    return _preenchimento_da_forma(el, herdadas, tema, mapa, imagem)


def _preenchimento_da_forma(el, herdadas, tema, mapa, imagem):
    """O preenchimento da forma (spPr, estilo do tema), ou o do placeholder de onde ela herda quando não declara."""
    for f in (el, *herdadas):
        if f is None:
            continue
        sppr = f.find(f"{_P}spPr")
        if sppr is not None and any(x.tag in _PREENCHE_TEXTO for x in sppr):
            return _preenchimento(sppr, tema, mapa, imagem if f is el else None)
        ref = f.find(f"{_P}style/{_A}fillRef")
        if ref is not None:
            return _INCERTO if _num(ref.get("idx") or "0") else None
    return None


def _embaixo(ap, caixa, fundo, desenhadas) -> list:
    """O que pode estar atrás da caixa: o fundo do slide e cada forma já desenhada que a sobrepõe; a que a cobre
    inteira esconde o que veio antes dela. Caixa desconhecida pode estar sob qualquer uma."""
    ap.comparacoes += len(desenhadas)
    if ap.comparacoes > _COMPARACOES_MAX:
        raise _Teto(f"sobreposição de formas acima do teto de {_COMPARACOES_MAX} comparações")
    fundos = [fundo]
    for d, cor in desenhadas:
        if caixa is None or d is None:
            fundos.append(cor)
        elif caixa[0] < d[2] and d[0] < caixa[2] and caixa[1] < d[3] and d[1] < caixa[3]:
            cobre = d[0] <= caixa[0] and d[1] <= caixa[1] and caixa[2] <= d[2] and caixa[3] <= d[3]
            fundos = [cor] if cobre else [*fundos, cor]
    return fundos


class _Texto(NamedTuple):
    """O que decide o tamanho e a cor do texto de uma forma: de dentro para fora, o lstStyle dela, a cor do estilo
    (fontRef), os placeholders do layout e do mestre, o estilo de texto do mestre (title, body, other ou notes) e o
    padrão da apresentação. `parar` encerra a cadeia como incerta (o estilo de tabela, que o Lince não lê)."""

    niveis: list
    fonte: object
    parar: bool = False


def _cadeia_de_texto(ap, el, herdadas, mestre, notas=False) -> _Texto:
    lst = el.find(f"{_P}txBody/{_A}lstStyle")
    niveis = [lst] + [h.find(f"{_P}txBody/{_A}lstStyle") for h in herdadas if h is not None]
    ph = _ph(el)
    if mestre is not None:
        estilos = mestre.raiz.find(f"{_P}notesStyle") if notas else mestre.raiz.find(f"{_P}txStyles")
        if notas:
            niveis.append(estilos if ph is not None else None)
        elif estilos is not None:
            nome = "otherStyle" if ph is None else ("titleStyle" if _classe(ph[0]) == "title" else "bodyStyle")
            niveis.append(estilos.find(f"{_P}{nome}"))
    niveis.append(ap.padrao)
    return _Texto(niveis, el.find(f"{_P}style/{_A}fontRef"))


_INVISIVEL = object()  # a cor do texto que não pinta: noFill, ou sólida quase transparente


def _declarado(el, nome, tema, mapa):
    """O que o rPr/defRPr/fontRef declara: o tamanho (centésimos de ponto), a cor (_INVISIVEL quando não pinta) ou o
    contorno (`ln`: True se pinta, False se não); _INCERTO, ou None se não declara."""
    if el is None:
        return None
    if el.tag == f"{_A}fontRef":
        filho = next(iter(el), None)
        if nome != "cor" or filho is None:
            return None
        return _INVISIVEL if _transparente(filho) else (_cor_drawingml(filho, tema, mapa) or _INCERTO)
    if nome == "sz":
        return _num(el.get("sz"))
    if nome == "ln":
        return None if (ln := el.find(f"{_A}ln")) is None else not _sem_tinta(ln)
    f = next((x for x in el if x.tag in _PREENCHE_TEXTO), None)
    if f is None:
        return None
    if _sem_tinta(el):
        return _INVISIVEL
    return (_cor_drawingml(next(iter(f), None), tema, mapa) or _INCERTO) if f.tag == f"{_A}solidFill" else _INCERTO


def _do_texto(rpr, ppr, nivel, texto, nome, tema, mapa):
    """`nome` (sz ou cor) do run pela cadeia; _INCERTO quando não é certo; None quando ninguém declara. O defRPr do
    próprio parágrafo deixa incerto: o PowerPoint não o aplica sempre."""
    if (v := _declarado(rpr, nome, tema, mapa)) is not None:
        return v
    if ppr is not None and _declarado(ppr.find(f"{_A}defRPr"), nome, tema, mapa) is not None:
        return _INCERTO
    fontes = [texto.niveis[0], texto.fonte, *texto.niveis[1:]] if texto.fonte is not None else texto.niveis
    for lst in fontes:
        d = lst if lst is None or lst.tag == f"{_A}fontRef" else lst.find(f"{_A}lvl{nivel + 1}pPr/{_A}defRPr")
        if (v := _declarado(d, nome, tema, mapa)) is not None:
            return v
    return _INCERTO if texto.parar else None


def _escala(corpo) -> float:
    """O fator da autoajuste (normAutofit fontScale, em milésimos de por cento ou "62.5%")."""
    ajuste = corpo.find(f"{_A}bodyPr/{_A}normAutofit")
    valor = (ajuste.get("fontScale") or "") if ajuste is not None else ""
    try:
        return float(valor[:-1]) / 100 if valor.endswith("%") else (int(valor) / 100_000 if valor else 1.0)
    except ValueError:
        return 1.0


def _runs_do_corpo(corpo):
    """(run, rPr, pPr, nível) do que o anydoc lê do corpo de texto: a:r e a:fld filhos de a:p, e os runs da fórmula."""
    for p in corpo.iterfind(f"{_A}p"):
        ppr = p.find(f"{_A}pPr")
        nivel = min(max(_num(ppr.get("lvl")) or 0, 0), 8) if ppr is not None else 0
        for r in p:
            if r.tag in (f"{_A}r", f"{_A}fld"):
                yield r, r.find(f"{_A}rPr"), ppr, nivel
            elif r.tag == f"{_A14}m":
                for mr in r.iter(f"{_M}r"):
                    yield mr, mr.find(f"{_A}rPr"), ppr, nivel


def _textos(el):
    return [t for t in el.iter(f"{_A}t", f"{_M}t")]


def _conferir_corpo(corpo, texto, fundos, tema, mapa, todo=None, registro=None) -> list:
    """(motivo, texto, [a:t]) do que está oculto no corpo de texto. `todo` é o motivo que vale para o texto inteiro
    (forma oculta ou fora do slide); senão, run a run: fonte minúscula, ou cor igual à de todos os `fundos`. O corpo
    entra em `registro` (o que o anydoc lê) quando há registro."""
    if registro is not None:
        registro.append(corpo)
    out = []
    escala = _escala(corpo)
    certos = not any(f is _INCERTO for f in fundos)
    for r, rpr, ppr, nivel in _runs_do_corpo(corpo):
        textos = _textos(r)
        txt = "".join("".join(t.itertext()) for t in textos)
        if not txt.strip():
            continue
        if todo:
            out.append((todo, txt, textos))
            continue
        sz = _do_texto(rpr, ppr, nivel, texto, "sz", tema, mapa)
        sz = 1800 if sz is None else sz
        cor = _do_texto(rpr, ppr, nivel, texto, "cor", tema, mapa)
        if sz is not _INCERTO and sz * escala <= 250:  # até 2,5 pt
            out.append(("fonte minúscula", txt, textos))
        elif cor is _INVISIVEL and _do_texto(rpr, ppr, nivel, texto, "ln", tema, mapa) in (None, False):
            out.append(("invisível", txt, textos))  # sem tinta e sem contorno: invisível sobre qualquer fundo
        elif certos and isinstance(cor, tuple) and all(_iguais(cor, f) for f in fundos):
            out.append(("cor igual ao fundo", txt, textos))
    return out


def _conferir_slide(ap, slide, registro) -> tuple:
    """O texto oculto do slide que o anydoc lê e se a árvore mudou (o texto sai de todos os ramos): forma oculta, caixa
    fora do slide, fonte minúscula (com a autoajuste) e cor igual ao fundo (preenchimento da forma ou da célula, formas
    embaixo, fundo do slide, do layout ou do mestre, com o tema e o mapa de cores do mestre). Só conclui "cor igual ao
    fundo" com as cores certas."""
    arvore = slide.raiz.find(f"{_P}cSld/{_P}spTree")
    if arvore is None:
        return [], False
    layout = slide.pai
    mestre = _mestre(slide)
    camadas = [slide, layout, mestre]
    tema, mapa = ap.tema(mestre)
    mapa = _mapa(camadas[:2], mapa)
    fundo = _fundo_da_camada(ap, camadas, tema, mapa)
    # embaixo do slide vêm as formas do mestre e do layout que não são placeholder (faixa, foto, logo), a menos que o
    # slide (ou o layout, para as do mestre) diga showMasterSp="0"
    de_baixo = []
    if slide.raiz.get("showMasterSp") not in ("0", "false") and layout is not None:
        if layout.raiz.get("showMasterSp") not in ("0", "false") and mestre is not None:
            de_baixo.append(mestre)
        de_baixo.append(layout)
    de_baixo = [(c, a) for c in de_baixo if (a := c.raiz.find(f"{_P}cSld/{_P}spTree")) is not None]
    formas = sum(1 for _ in arvore.iter(*_FORMAS))
    formas += sum(1 for _, a in de_baixo for el in a.iter(*_FORMAS) if _ph(el) is None)  # os placeholders não vêm
    if formas > _FORMAS_MAX:
        raise _Teto(f"{formas} formas, acima do teto de {_FORMAS_MAX} por slide")
    desenhadas = []  # (caixa, o que ela pinta) das formas já desenhadas, de baixo para cima
    for camada, formas_de_baixo in de_baixo:
        for el, _, desenhado in _formas(formas_de_baixo):
            if desenhado and _ph(el) is None:
                if (cor := _pintura(el, [], tema, mapa, ap.imagem(camada.nome))) is not None:
                    desenhadas.append((_caixa(el), cor))
    out, imagem = [], ap.imagem(slide.nome)
    for el, lido, desenhado in _formas(arvore):
        herdadas = _herdadas(el, slide)
        caixa = _caixa(el, herdadas)
        if el.tag == f"{_P}grpSp":  # no grupo, posição e camadas são do grupo: só o preenchimento certo decide
            for f, lido_f, oculta in _no_grupo(el, lido, _oculta(el)):
                achados = _conferir_forma(ap, f, slide, [], None, [_INCERTO], tema, mapa, imagem,
                                          "forma oculta" if oculta else None, registro if lido_f else None)  # fmt: skip
                out += [(m, t, ts, lido_f and lida) for m, t, ts, lida in achados]
        elif next(el.iter(f"{_A}t", f"{_M}t"), None) is not None:  # texto ou só fórmula (a14:m)
            todo = "forma oculta" if _oculta(el) else None
            if todo is None and caixa is not None and ap.W and ap.H:
                if caixa[2] <= 0 or caixa[3] <= 0 or caixa[0] >= ap.W or caixa[1] >= ap.H:
                    todo = "fora do slide"
            fundos = _embaixo(ap, caixa, fundo, desenhadas) if todo is None else [_INCERTO]
            achados = _conferir_forma(ap, el, slide, herdadas, caixa, fundos, tema, mapa, imagem, todo,
                                      registro if lido else None)  # fmt: skip
            out += [(m, t, ts, lido and lida) for m, t, ts, lida in achados]
        if desenhado and (cor := _pintura(el, herdadas, tema, mapa, imagem)) is not None:
            desenhadas.append((caixa, cor))
    for _, _, textos, _ in out:
        for t in textos:
            _esvaziar(t)
    return [(m, t) for m, t, _, lido in out if lido], bool(out)


def _no_grupo(grupo, lido, oculta):
    """(forma, lida, oculta) de dentro do grupo, em qualquer profundidade: o grupo oculto esconde o que tem dentro."""
    for f, lido_f, _ in _formas(grupo, lido):
        if f.tag == f"{_P}grpSp":
            yield from _no_grupo(f, lido_f, oculta or _oculta(f))
        else:
            yield f, lido_f, oculta or _oculta(f)


def _conferir_forma(ap, el, camada, herdadas, caixa, fundos, tema, mapa, imagem, todo, registro) -> list:
    """(motivo, texto, [a:t], lido pelo anydoc) do oculto do texto da forma (sp, cxnSp) ou das células da tabela
    (graphicFrame). Confere também o que o anydoc pula (placeholder de rodapé, célula mesclada): esvaziar não custa
    nada, e o critério do anydoc só decide o que entra no registro."""
    mestre = _mestre(camada)
    if el.tag in (f"{_P}sp", f"{_P}cxnSp"):
        corpo = el.find(f"{_P}txBody")
        if corpo is None:
            return []
        atras = _preenchimento_da_forma(el, herdadas, tema, mapa, imagem)
        texto = _cadeia_de_texto(ap, el, herdadas, mestre)
        lida = _lida(el)
        achados = _conferir_corpo(corpo, texto, fundos if atras is None else [atras], tema, mapa, todo,
                                  registro if lida else None)  # fmt: skip
        return [(*a, lida) for a in achados]
    if el.tag != f"{_P}graphicFrame" or (tbl := next(el.iter(f"{_A}tbl"), None)) is None:
        return []
    estilo = tbl.find(f"{_A}tblPr/{_A}tableStyleId")
    estilo = (estilo.text or "").strip() if estilo is not None else ap.tabela_padrao
    pinta = estilo is not None and estilo not in _SEM_ESTILO  # o estilo de tabela pinta a célula e a letra
    tblpr = tbl.find(f"{_A}tblPr")
    debaixo = _preenchimento(tblpr, tema, mapa) if tblpr is not None else None
    debaixo = fundos if debaixo is None else [debaixo]
    outro = mestre.raiz.find(f"{_P}txStyles/{_P}otherStyle") if mestre is not None else None
    out = []
    for tr in tbl.iterfind(f"{_A}tr"):
        for tc in tr.iterfind(f"{_A}tc"):
            lida = not any(_attr(tc, _A[1:-1], m) in ("1", "true") for m in ("hMerge", "vMerge"))  # a coberta não
            corpo = tc.find(f"{_A}txBody")
            if corpo is None:
                continue
            tcpr = tc.find(f"{_A}tcPr")
            cor = (
                _preenchimento(tcpr, tema, mapa)
                if tcpr is not None and any(x.tag in _PREENCHE_TEXTO for x in tcpr)
                else None
            )
            if cor is None and tcpr is not None and tcpr.find(f"{_A}noFill") is not None:
                atras = debaixo
            elif cor is not None:
                atras = [cor]
            else:
                atras = [_INCERTO] if pinta else debaixo
            texto = _Texto([corpo.find(f"{_A}lstStyle")] + ([] if pinta else [outro, ap.padrao]), None, pinta)
            achados = _conferir_corpo(corpo, texto, atras, tema, mapa, todo, registro if lida else None)
            out += [(*a, lida) for a in achados]
    return out


def _conferir_anotacoes(ap, notas, registro) -> tuple:
    """O oculto das anotações do orador, que o anydoc lê depois do slide, e se a árvore mudou: todo p:sp da página,
    com o fundo da página e a cadeia do mestre de anotações. A imagem do slide, o número, o cabeçalho, o rodapé e a
    data, que o anydoc pula (pelo primeiro p:ph de dentro da forma), são conferidos, mas não entram no registro."""
    mestre = notas.pai
    tema, mapa = ap.tema(mestre)
    fundo = _fundo_da_camada(ap, [notas, mestre], tema, mapa)
    imagem, out = ap.imagem(notas.nome), []
    for sp in notas.raiz.iter(f"{_P}sp"):
        corpo = sp.find(f"{_P}txBody")
        if corpo is None:
            continue
        lido = next(sp.iter(f"{_P}ph"), None)
        lido = lido is None or (_attr(lido, _P[1:-1], "type") or "body") not in ("sldImg", "sldNum", "hdr", "ftr", "dt")
        ph = _ph(sp)
        herdadas = [mestre.phs.casar(*ph)] if ph is not None and mestre is not None else []
        atras = _preenchimento_da_forma(sp, herdadas, tema, mapa, imagem)
        texto = _cadeia_de_texto(ap, sp, herdadas, mestre, notas=True)
        achados = _conferir_corpo(corpo, texto, [fundo] if atras is None else [atras], tema, mapa,
                                  "forma oculta" if _oculta(sp) else None, registro if lido else None)  # fmt: skip
        out += [(*a, lido) for a in achados]
    for _, _, textos, _ in out:
        for t in textos:
            _esvaziar(t)
    return [(m, t) for m, t, _, lido in out if lido], bool(out)
