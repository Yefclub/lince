"""Office (DOCX, PPTX, XLSX, ODF, RTF, EPUB, legados): anydoc + gráficos nativos, setas de fluxograma e imagens."""

import copy
import hashlib
import html
import io  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import posixpath
import re
import secrets
import zipfile
from typing import NamedTuple
from xml.etree import ElementTree as ET  # noqa: F401

from lxml import etree

from . import legados, perfil_tabular, xlsx_anotacoes
from .limites import DocumentoRecusado, xml_ou_nada
from .metadados import BINARIOS_OLE, ERROS_DE_PARTE, ODF
from .seguranca import NAO_CONFERIDO, XMLIlegivel, _xml, fora_da_conferencia, limpar_docx, limpar_pptx
from .texto import romano

NS = {
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
}


TIPOS_GRAFICO = {
    "barChart": "barras",
    "bar3DChart": "barras 3D",
    "lineChart": "linhas",
    "pieChart": "pizza",
    "doughnutChart": "rosca",
    "areaChart": "área",
    "scatterChart": "dispersão",
    "radarChart": "radar",
    "bubbleChart": "bolhas",
}


_CELULAS_SEM_CACHE_MAX = 100_000  # intervalo de série sem cache lido da planilha; um gráfico real tem centenas
_UNIAO_SEM_CACHE_MAX = 1_000_000  # retângulo que cobre os intervalos de uma aba, lido de uma vez
# Total de células das tabelas dos gráficos OOXML. idx e ptCount vêm do arquivo e podem declarar bilhões de lacunas.
_CELULAS_GRAFICOS_MAX = 1_000_000


class _Pontos(NamedTuple):
    valores: dict[int, str]
    quantidade: int


def _indice(el, atributo):
    try:
        n = int(el.get(atributo, ""))
        if 0 <= n <= 4_294_967_295:  # unsignedInt no OOXML
            return n
    except ValueError:
        pass
    raise ValueError(f"{el.tag.rsplit('}', 1)[-1]} com {atributo} ausente ou inválido")


def _pontos(campo):
    if campo is None:
        return _Pontos({}, 0)
    qtd = campo.find(".//c:ptCount", NS)
    tamanho = _indice(qtd, "val") if qtd is not None else 0
    pontos = {}
    # Nas categorias hierárquicas, cada nível pode ter um rótulo no mesmo índice.
    for pt in campo.iterfind(".//c:pt", NS):
        idx = _indice(pt, "idx")
        pontos.setdefault(idx, []).append(pt.findtext("c:v", default="", namespaces=NS) or "")
        tamanho = max(tamanho, idx + 1)
    return _Pontos({idx: " / ".join(reversed(rotulos)) for idx, rotulos in pontos.items()}, tamanho)


def _intervalo(el, nomes_das_abas):
    """(aba, col1, lin1, col2, lin2) da referência c:f de um gráfico sem cache, ou None: fora do padrão, aba que não
    existe, coluna ou linha inteira, ou acima de _CELULAS_SEM_CACHE_MAX células."""
    from openpyxl.utils import range_boundaries

    f = el.find("c:f", NS) if el is not None else None
    m = re.match(r"^'?(.+?)'?!(.+)$", (f.text or "") if f is not None else "")
    if not m or (aba := m.group(1).replace("''", "'")) not in nomes_das_abas:
        return None
    try:
        c1, l1, c2, l2 = range_boundaries(m.group(2).replace("$", ""))
    except ValueError:
        return None
    if None in (c1, l1, c2, l2) or c2 < c1 or l2 < l1 or (c2 - c1 + 1) * (l2 - l1 + 1) > _CELULAS_SEM_CACHE_MAX:
        return None
    return aba, c1, l1, c2, l2


def _valores_sem_cache(path, refs) -> dict:
    """Valores de cada intervalo em `refs`, lidos da planilha em modo somente leitura (carregar a planilha inteira
    custaria minutos e gigabytes numa base grande). Cada aba é percorrida uma vez, no retângulo que cobre todos os
    intervalos dela; se ele passar de _UNIAO_SEM_CACHE_MAX, cada intervalo é lido à parte."""
    from openpyxl import load_workbook

    out, por_aba = {}, {}
    for r in refs:
        por_aba.setdefault(r[0], set()).add(r)
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        for aba, rs in por_aba.items():
            c1, l1 = min(r[1] for r in rs), min(r[2] for r in rs)
            c2, l2 = max(r[3] for r in rs), max(r[4] for r in rs)
            if (c2 - c1 + 1) * (l2 - l1 + 1) <= min(_UNIAO_SEM_CACHE_MAX, _CELULAS_GRAFICOS_MAX):
                grade = list(wb[aba].iter_rows(min_row=l1, max_row=l2, min_col=c1, max_col=c2, values_only=True))
                for r in rs:
                    out[r] = [v for linha in grade[r[2] - l1 : r[4] - l1 + 1] for v in linha[r[1] - c1 : r[3] - c1 + 1]]
            else:
                for r in rs:
                    linhas = wb[aba].iter_rows(min_row=r[2], max_row=r[4], min_col=r[1], max_col=r[3], values_only=True)
                    out[r] = [v for linha in linhas for v in linha]
    finally:
        wb.close()
    return out


def _titulo_grafico(el):
    if el is None:
        return ""
    return " ".join(t.text or "" for t in el.findall(".//a:t", NS)).strip() or el.findtext(".//c:v", "", NS)


def _area_intervalo(r):
    return (r[3] - r[1] + 1) * (r[4] - r[2] + 1)


def _series_ooxml(tipo_el, xy, abas, pendentes):
    series = []
    for ser in tipo_el.findall("c:ser", NS):
        cat = ser.find("c:xVal" if xy else "c:cat", NS)
        val = ser.find("c:yVal" if xy else "c:val", NS)
        ref = val.findtext(".//c:f", namespaces=NS) if val is not None else None
        s = {"nome": ser.findtext("c:tx//c:v", default="", namespaces=NS), "ref": ref}
        for campo, el in [("cats", cat), ("vals", val)]:
            s[campo] = _pontos(el)
            if abas and not s[campo].quantidade and el is not None:
                for fonte in el:
                    if r := _intervalo(fonte, abas):
                        s[campo] = _Pontos({}, _area_intervalo(r))
                        pendentes.append((s, campo, r))
                        break
        if abas and not s["nome"] and (r := _intervalo(ser.find("c:tx/c:strRef", NS), abas)):
            pendentes.append((s, "nome", r))
        series.append(s)
    return series


def graficos_ooxml(path, partes=None):
    """Gráfico nativo do Office: tipo, título, séries e valores do cache — dado exato. Sem cache (gráfico gravado
    por biblioteca), os valores vêm do intervalo na planilha, lidos depois de todos os gráficos, uma vez por aba."""
    saldo_intervalos = saldo_tabelas = _CELULAS_GRAFICOS_MAX
    out, pendentes = [], []  # (série, campo, intervalo) a completar com os valores da planilha
    planilha = path.lower().endswith((".xlsx", ".xlsm"))
    with zipfile.ZipFile(path) as z:
        abas = set()
        if planilha and "xl/workbook.xml" in z.namelist():
            livro = xml_ou_nada(z.read("xl/workbook.xml"))
            abas = (
                {a.get("name") for a in livro.iter() if a.tag.rsplit("}", 1)[-1] == "sheet"}
                if livro is not None
                else set()
            )
        if partes is None:
            partes = sorted(n for n in z.namelist() if re.search(r"/charts/chart\d+\.xml$", n))
        for nome in partes:
            root = xml_ou_nada(z.read(nome))
            if root is None or root.find(".//c:plotArea", NS) is None:
                continue  # gráfico malformado ou hostil: a leitura segue sem ele
            titulo = _titulo_grafico(root.find(".//c:chart/c:title", NS))
            plot = root.find(".//c:plotArea", NS)
            eixos = {
                eixo.find("c:axId", NS).get("val"): _titulo_grafico(eixo.find("c:title", NS))
                for eixo in plot
                if eixo.find("c:axPos", NS) is not None and eixo.find("c:axId", NS) is not None
            }
            tipos = [e.tag.rsplit("}", 1)[-1] for e in plot if e.tag.endswith("Chart")]
            completo = all(t in TIPOS_GRAFICO and t != "bubbleChart" for t in tipos)
            for tipo_el in plot:
                tag = tipo_el.tag.rsplit("}", 1)[-1]
                if tag not in TIPOS_GRAFICO:
                    continue
                xy = tag == "scatterChart"
                ids = [e.get("val") for e in tipo_el.findall("c:axId", NS)]
                g = {
                    "parte": nome,
                    "tipo": TIPOS_GRAFICO[tag],
                    "titulo": titulo,
                    "xy": xy,
                    "series": [],
                    "completo": completo,
                    "eixo": eixos.get(ids[0], "") if ids else "",
                    "eixo_y": eixos.get(ids[1], "") if len(ids) > 1 else "",
                }
                out.append(g)
                faltantes = []
                try:
                    g["series"] = _series_ooxml(tipo_el, xy, abas, faltantes)
                except ValueError as erro:
                    g["falha"] = str(erro)
                    continue
                if planilha:
                    # Cobrar cada cópia do intervalo e todas as tabelas anteriores, mesmo as que tinham cache.
                    custo_intervalos = sum(_area_intervalo(r) for _, _, r in faltantes)
                    custo_tabela = _dimensoes_grafico(g)[-1]
                    if custo_intervalos > saldo_intervalos or custo_tabela > saldo_tabelas:
                        g["falha"] = "teto de células dos gráficos OOXML"
                        continue
                    saldo_intervalos -= custo_intervalos
                    saldo_tabelas -= custo_tabela
                    pendentes.extend(faltantes)
    if pendentes:
        valores = _valores_sem_cache(path, {r for _, _, r in pendentes})
        for s, campo, r in pendentes:
            lidos = valores.get(r, [])
            if campo == "nome":
                s[campo] = " ".join(str(v) for v in lidos if v is not None)
            else:
                tamanho = _area_intervalo(r)
                s[campo] = _Pontos({i: str(v) for i, v in enumerate(lidos) if v is not None}, tamanho)
    for g in out:
        for s in g["series"]:
            s["nome"] = s["nome"] or "série"
    return out


_ODF_CHART = {"bar": "barras", "line": "linhas", "circle": "pizza", "ring": "rosca", "area": "área",
              "scatter": "dispersão", "radar": "radar", "filled-radar": "radar", "bubble": "bolhas",
              "stock": "ações", "gantt": "Gantt"}  # fmt: skip
_CH = "urn:oasis:names:tc:opendocument:xmlns:chart:1.0"
_TB = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_TX = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_LINHAS_GRAFICO_MAX = 1000  # linhas da tabela de dados de um gráfico; o resto fica de fora, registrado
_SERIES_MAX = 64  # séries (colunas) de um gráfico: o custo da tabela é séries × linhas
_CARACTERES_GRAFICOS_MAX = 8 * 1024 * 1024  # de todos os gráficos ODF do documento; 1.000 linhas × 64 séries de
# rótulos curtos cabem com folga várias vezes


class _SemSaldo(Exception):
    """O orçamento de caracteres dos gráficos do documento acabou."""


def _celulas(linha, teto, cortes, saldo) -> tuple[list[str], bool]:
    """Texto das primeiras `teto` células de uma linha da tabela local (table:number-columns-repeated conta, e valor
    inválido vale 1), e se a linha pedia mais células do que isso. Para no teto: o custo não passa dele. Cada texto
    fica em legados._TEXTO_MAX caracteres (rótulo e valor de gráfico são curtos); o corte entra em `cortes`. O texto
    vezes as cópias sai de `saldo` ([caracteres]), comum a todos os gráficos do documento: a repetição não amplifica
    a saída além dele, por mais objetos que o arquivo tenha."""
    out, pedidas = [], 0
    for c in linha.iterfind(f"{{{_TB}}}table-cell"):
        try:
            vezes = max(1, int(c.get(f"{{{_TB}}}number-columns-repeated", "1")))
        except ValueError:
            vezes = 1
        pedidas += vezes
        texto = " ".join(" ".join(t for p in c.findall(f"{{{_TX}}}p") for t in p.itertext()).split())  # sem o svg:desc
        if len(texto) > legados._TEXTO_MAX:
            texto = texto[: legados._TEXTO_MAX] + "…"
            cortes.add("texto")
        copias = min(vezes, teto - len(out))
        saldo[0] -= (len(texto) + 3) * copias  # o " | " de cada célula na tabela do Markdown
        if saldo[0] < 0:
            raise _SemSaldo
        out += [texto] * copias
        if pedidas > teto:
            return out, True
    return out, False


def graficos_odf(path, ilegiveis) -> list[dict]:
    """Gráfico do LibreOffice (.ods, .odp, .odt): cada objeto embutido com office:chart traz tipo, título e a tabela
    local com os dados que ele desenha. Mesmo formato de graficos_ooxml."""
    out, saldo = [], [_CARACTERES_GRAFICOS_MAX]
    with zipfile.ZipFile(path) as z:
        for nome in sorted(n for n in z.namelist() if re.fullmatch(r"(?:[^/]+/)*Object \d+/content\.xml", n)):
            try:
                g = _grafico_odf(z, nome, ilegiveis, saldo)
                if g is not None:  # o que vai para a leitura é a tabela pronta: ela paga o orçamento inteira
                    saldo[0] -= len(grafico_md(g))
                    if saldo[0] < 0:
                        raise _SemSaldo
            except _SemSaldo:
                ilegiveis.append(f"gráficos: mais de {_CARACTERES_GRAFICOS_MAX} caracteres; os demais ficaram de fora")
                break
            if g is not None:
                out.append(g)
    return out


def _grafico_odf(z, nome, ilegiveis, saldo) -> dict | None:
    """Um objeto de gráfico do pacote ODF, ou None se não for gráfico ou estiver quebrado."""
    raiz = xml_ou_nada(z.read(nome))
    grafico = raiz.find(f".//{{{_CH}}}chart") if raiz is not None else None
    if grafico is None:
        return None
    tipo = (grafico.get(f"{{{_CH}}}class") or "").rsplit(":", 1)[-1]
    titulo = (
        " ".join(" ".join(grafico.find(f"{{{_CH}}}title").itertext()).split())
        if grafico.find(f"{{{_CH}}}title") is not None
        else ""
    )
    tabela = grafico.find(f".//{{{_TB}}}table")
    linhas = list(tabela.iter(f"{{{_TB}}}table-row")) if tabela is not None else []
    if len(linhas) > _LINHAS_GRAFICO_MAX + 1:
        ilegiveis.append(f"gráfico {titulo or nome}: mais de {_LINHAS_GRAFICO_MAX} linhas; o resto ficou de fora")
    cortes = set()
    cabecalho, largo = _celulas(linhas[0], _SERIES_MAX + 1, cortes, saldo) if linhas else ([], False)
    if largo:
        ilegiveis.append(f"gráfico {titulo or nome}: mais de {_SERIES_MAX} séries; o resto ficou de fora")
    dados = [_celulas(r, len(cabecalho), cortes, saldo)[0] for r in linhas[1 : _LINHAS_GRAFICO_MAX + 1]]
    if cortes:
        ilegiveis.append(f"gráfico {titulo or nome}: célula com mais de {legados._TEXTO_MAX} caracteres, cortada")
    series = [
            {"nome": cabecalho[k] or f"série {k}", "cats": [d[0] if d else "" for d in dados],
             "vals": [d[k] if k < len(d) else "" for d in dados], "ref": None}
            for k in range(1, len(cabecalho))
        ]  # fmt: skip
    return {
        "parte": nome,
        "tipo": _ODF_CHART.get(tipo, tipo or "desconhecido"),
        "titulo": titulo,
        "series": series,
    }


def _dimensoes_grafico(g):
    series = g["series"]
    eixos = [s["cats"] for s in series if s["cats"].quantidade]
    alturas = [max(s["cats"].quantidade, s["vals"].quantidade) for s in series]
    linhas = max(alturas, default=0)
    separar = g["xy"] or any(e != eixos[0] for e in eixos[1:])  # os eixos X podem ser independentes
    custo = 2 * sum(alturas) if separar else linhas * (len(series) + 1)
    return eixos, linhas, separar, custo


def _tabela_ooxml(g, saldo, ilegiveis):
    """Renderiza cada coordenada pelo índice no arquivo. Um ptCount alto não aloca uma lista de vazios."""
    if "falha" in g:
        ilegiveis.append(f"gráfico {g['parte']}: {g['falha']}")
        return "[tabela do gráfico não lida]"
    ss = g["series"]
    eixos, linhas, separar, custo = _dimensoes_grafico(g)
    if not linhas:
        refs = ", ".join(s["nome"] + (f" ({s['ref']})" if s["ref"] else "") for s in ss)
        return f"Séries: {refs} — sem valores em cache; dados na planilha."
    if custo > saldo[0]:
        ilegiveis.append(f"gráfico {g['parte']}: teto de células dos gráficos OOXML")
        return "[tabela do gráfico não lida]"
    saldo[0] -= custo
    if separar:
        partes = []
        for s in ss:
            cats, vals = s["cats"], s["vals"]
            x = _celula(g["eixo"] or ("X" if g["xy"] else "Categoria"))
            y = _celula(g["eixo_y"] or ("Y" if g["xy"] else "Valor"))
            cabecalho = f"| {x} | {y} |"
            tabela = [f"**{_celula(s['nome'])}**", "", cabecalho, "| --- | --- |"]
            for k in range(max(cats.quantidade, vals.quantidade)):
                tabela.append(f"| {_celula(cats.valores.get(k))} | {_celula(vals.valores.get(k))} |")
            partes.append("\n".join(tabela))
        return "\n\n".join(partes)
    tabela = [
        "| " + " | ".join([_celula(g["eixo"] or "Categoria"), *(_celula(s["nome"]) for s in ss)]) + " |",
        "|" + " --- |" * (len(ss) + 1),
    ]
    if g["eixo_y"]:
        tabela[:0] = [f"Eixo de valores: {_celula(g['eixo_y'])}", ""]
    for k in range(linhas):
        cat = _celula(eixos[0].valores.get(k)) if eixos else str(k + 1)
        tabela.append("| " + " | ".join([cat, *(_celula(s["vals"].valores.get(k)) for s in ss)]) + " |")
    return "\n".join(tabela)


def grafico_md(g, saldo=None, ilegiveis=None):
    linhas = [f"**Gráfico ({g['tipo']}): {g['titulo'] or 'sem título'}**"]
    if "xy" in g:
        return (
            linhas[0]
            + "\n"
            + _tabela_ooxml(
                g, saldo if saldo is not None else [_CELULAS_GRAFICOS_MAX], ilegiveis if ilegiveis is not None else []
            )
        )
    ss = g["series"]
    if ss and ss[0]["vals"]:
        cats = ss[0]["cats"] or [str(k + 1) for k in range(len(ss[0]["vals"]))]
        linhas.append("| Categoria | " + " | ".join(s["nome"] for s in ss) + " |")
        linhas.append("|" + " --- |" * (len(ss) + 1))
        for k, cat in enumerate(cats):
            linhas.append(f"| {cat} | " + " | ".join(s["vals"][k] if k < len(s["vals"]) else "" for s in ss) + " |")
    else:
        refs = ", ".join(f"{s['nome']} ({s['ref']})" for s in ss)
        linhas.append(f"Séries: {refs} — sem valores em cache; dados na planilha.")
    return "\n".join(linhas)


def fluxos_pptx(path):
    """Diagrama de formas: caixas e setas (conectores ligados) viram arestas, com o rótulo da seta
    (caixa de texto curta solta perto do conector, ex.: Sim/Não)."""
    from pptx import Presentation

    prs = Presentation(path)
    out = {}
    for n, slide in enumerate(prs.slides, 1):
        formas = list(slide.shapes)
        nomes = {sh.shape_id: (sh.text_frame.text.strip() if sh.has_text_frame else sh.name) for sh in formas}
        conectores, ligados = [], set()
        for sh in formas:
            el = sh._element
            if el.tag.endswith("}cxnSp"):
                st, en = el.find(".//a:stCxn", NS), el.find(".//a:endCxn", NS)
                if st is not None and en is not None:
                    a_, b_ = int(st.get("id")), int(en.get("id"))
                    ligados |= {a_, b_}
                    conectores.append((sh, a_, b_))
        rotulos = [
            sh
            for sh in formas
            if sh.shape_id not in ligados
            and sh.has_text_frame
            and 0 < len(sh.text_frame.text.strip()) <= 15
            and not sh._element.tag.endswith("}cxnSp")
            and sh.width is not None
            and sh.left is not None
        ]
        de_rotulo = {}
        for r in rotulos:
            cx, cy = r.left + r.width / 2, r.top + r.height / 2

            def dist(c):
                x0, y0 = min(c.begin_x, c.end_x), min(c.begin_y, c.end_y)
                x1, y1 = max(c.begin_x, c.end_x), max(c.begin_y, c.end_y)
                return max(x0 - cx, 0, cx - x1) + max(y0 - cy, 0, cy - y1)

            if conectores:
                c = min(conectores, key=lambda t: dist(t[0]))
                if dist(c[0]) < 731520:  # 0,8 polegada
                    de_rotulo.setdefault(id(c[0]), r.text_frame.text.strip())
        arestas = []
        for c, a_, b_ in conectores:
            rot = de_rotulo.get(id(c))
            pontas = []
            for tag in ("headEnd", "tailEnd"):
                el = c._element.find(f"p:spPr/a:ln/a:{tag}", NS)
                pontas.append(el is not None and el.get("type") in {"arrow", "stealth", "triangle"})
            inicio, fim = pontas
            if inicio and not fim:
                a_, b_ = b_, a_
            seta = "↔" if inicio and fim else "→" if inicio or fim else "—"
            arestas.append((nomes.get(a_, a_), seta, nomes.get(b_, b_), rot))
        if arestas:
            out[n] = arestas
    return out


_ODF_DRAW = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}"
_ODF_SVG = "{urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0}"
_ODF_STYLE = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"
_ODF_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
_ODF_XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
_CM = {"cm": 1.0, "mm": 0.1, "in": 2.54, "pt": 2.54 / 72, "pc": 2.54 / 6, "px": 2.54 / 96}
_ROTULO_DISTANCIA_CM = 2.03  # 0,8 polegada, a mesma distância do rótulo de seta do .pptx


def _cm(valor):
    m = re.fullmatch(r"\s*(-?[\d.]+)\s*([a-z]*)\s*", valor or "")
    try:
        return float(m[1]) * _CM.get(m[2] or "cm", 1.0) if m else None
    except ValueError:
        return None


def fluxos_odp(path):
    """Diagrama de formas do Impress: as formas com id e os conectores ligados a elas (draw:start-shape e
    draw:end-shape) viram arestas, com a direção pela ponta de seta do estilo do conector e o rótulo pelo texto do
    próprio conector ou por uma forma curta, sem ligação, perto dele (Sim/Não). Mesmo formato de `fluxos_pptx`."""
    with zipfile.ZipFile(path) as z:
        if "content.xml" not in z.namelist():
            return {}
        raiz = xml_ou_nada(z.read("content.xml"))
    if raiz is None:
        return {}
    setas = {}
    for estilo in raiz.iter(f"{_ODF_STYLE}style"):
        props = estilo.find(f"{_ODF_STYLE}graphic-properties")
        if props is not None:
            setas[estilo.get(f"{_ODF_STYLE}name")] = (
                bool(props.get(f"{_ODF_DRAW}marker-start")),
                bool(props.get(f"{_ODF_DRAW}marker-end")),
            )

    def texto(el):
        return " ".join(" ".join(el.itertext()).split())

    out = {}
    for n, pagina in enumerate(raiz.iter(f"{_ODF_DRAW}page"), 1):
        nomes, conectores, soltas = {}, [], []
        for el in pagina:
            if not isinstance(el.tag, str) or not el.tag.startswith(_ODF_DRAW):
                continue
            if el.tag == f"{_ODF_DRAW}connector":
                a_, b_ = el.get(f"{_ODF_DRAW}start-shape"), el.get(f"{_ODF_DRAW}end-shape")
                pontos = [_cm(el.get(f"{_ODF_SVG}{k}")) for k in ("x1", "y1", "x2", "y2")]
                if a_ and b_ and None not in pontos:
                    conectores.append((el, a_, b_, pontos))
                continue
            ident = el.get(f"{_ODF_DRAW}id") or el.get(_ODF_XML_ID)
            if ident:
                nomes[ident] = texto(el) or ident
            elif 0 < len(t := texto(el)) <= 15:
                x, y, w, h = (_cm(el.get(f"{_ODF_SVG}{k}")) for k in ("x", "y", "width", "height"))
                if None not in (x, y, w, h):
                    soltas.append((t, x + w / 2, y + h / 2))
        if not conectores:
            continue
        rotulos = {}
        for t, cx, cy in soltas:

            def dist(c):
                x1, y1, x2, y2 = c[3]
                return max(min(x1, x2) - cx, 0, cx - max(x1, x2)) + max(min(y1, y2) - cy, 0, cy - max(y1, y2))

            perto = min(conectores, key=dist)
            if dist(perto) < _ROTULO_DISTANCIA_CM:
                rotulos.setdefault(id(perto[0]), t)
        arestas = []
        for el, a_, b_, _ in conectores:
            inicio, fim = setas.get(el.get(f"{_ODF_DRAW}style-name"), (False, False))
            if inicio and not fim:
                a_, b_ = b_, a_
            seta = "↔" if inicio and fim else "→" if inicio or fim else "—"
            rotulo = texto(el) or rotulos.get(id(el))
            arestas.append((nomes.get(a_, a_), seta, nomes.get(b_, b_), rotulo))
        out[n] = arestas
    return out


def mermaid_do_fluxo(arestas):
    """Diagrama Mermaid com a direção e o rótulo de cada ligação."""
    ids, linhas = {}, ["```mermaid", "flowchart LR"]

    def no(nome):
        if nome not in ids:
            ids[nome] = f"n{len(ids) + 1}"
            rot = str(nome).replace('"', "'")
            linhas.append(f'    {ids[nome]}["{rot}"]')
        return ids[nome]

    operadores = {"→": "-->", "↔": "<-->", "—": "---"}
    for a, _, b, _ in arestas:
        no(a), no(b)
    for a, seta, b, rot in arestas:
        linhas.append(f"    {ids[a]} {operadores[seta]}{'|' + rot + '|' if rot else ''} {ids[b]}")
    return "\n".join(linhas + ["```"])


def planejar_office(path, pasta, ext=None):
    """`ext` é o formato já decidido pelo leitor (pela extensão ou pelo conteúdo)."""
    import anydoc

    ext = ext or os.path.splitext(path)[1].lower()
    # CSV e TSV não têm assinatura: o formato vai explícito (o anydoc detecta separador e codificação)
    grande = ext in (".xlsx", ".xlsm") and _celulas_declaradas(path) > _CELULAS_ANYDOC_MAX
    # planilha grande não passa pelo anydoc, que monta o documento inteiro na memória (1,2 GB para 100 mil linhas)
    marca = f"LINCE{secrets.token_hex(8)}D"  # início de cada slide, trocado por <!-- slide N --> depois da leitura
    ilegiveis, graficos_pptx = [], {}
    saldo_graficos = [_CELULAS_GRAFICOS_MAX]
    limpeza = None  # o texto oculto sai do próprio XML, antes da conversão: nunca por busca no Markdown montado
    if grande:
        doc = None
    else:
        dados = open(path, "rb").read()
        # a limpeza segue o formato que o anydoc vai ler, pelo conteúdo (um Word gravado como .doc, ou uma apresentação
        # como .docx, é conferido), e o anydoc recebe esse formato explícito
        formato = "csv" if ext in (".csv", ".tsv") else anydoc.format_from_bytes(dados)
        letras = None
        if formato == "docx":
            marca_letra = marca + "A"
            dados = (limpeza := limpar_docx(dados, formatar_texto=True, marca_letra=marca_letra)).dados
            letras = re.compile(rf"{marca_letra}([a-zA-Z]+|0):")
        elif formato == "pptx":
            dados = (limpeza := limpar_pptx(dados)).dados
        if ext in PPTX:

            def tabelas_pptx():
                for g in graficos_ooxml(path):
                    graficos_pptx.setdefault(g["parte"], []).append(g)

            _complemento(tabelas_pptx, "gráficos", ilegiveis)
            marcas_graficos = {
                parte: (f"{marca}G{i}X", grupos[0]["completo"])
                for i, (parte, grupos) in enumerate(graficos_pptx.items())
            }
            dados = _com_marcas_de_slide(dados, marca, marcas_graficos)
        try:
            doc = anydoc.to_document(dados, format=formato if formato in ("csv", "docx", "pptx") else None)
        except anydoc.ConvertError as recusa:
            if ext != ".xlsb":
                raise
            doc = None  # .xlsb fora da especificação (gravadores antigos): lido registro a registro
            try:
                md_xlsb = _xlsb_tolerante(path, ilegiveis_xlsb := [])
            except (MemoryError, DocumentoRecusado):  # o teto do leitor tolerante diz o motivo a quem chama
                raise
            except Exception:  # noqa: BLE001 — o leitor tolerante também não abre: vale o erro do leitor do corpo
                raise recusa from None
    # o conteúdo da aba oculta não vai para o corpo (como o texto oculto): vai para os metadados, conferido como ele
    ocultas = _abas_ocultas(path, ext, ilegiveis) if ext in PLANILHAS - {".csv", ".tsv"} else []
    tarefas, pendentes = [], []  # pendentes: o que entra em cobertura.pendentes, com o motivo
    svgs, sentinela = [], f"LINCE{secrets.token_hex(8)}S"  # desenhos vetoriais, juntos no fim, num lote só
    vistas = set()  # imagens já lidas (sha256): o armazém dos binários não as repete

    alternativas = {}

    def ler_imagem(data, mime, onde, alt=""):
        """A imagem vira tarefa da IA; a que não abre (formato quebrado) fica marcada no lugar e registrada, nunca
        derruba o documento. SVG, EMF e WMF ficam com uma marca provisória e são desenhados no fim, num lote só."""
        from PIL import Image

        if alt:
            alternativas[onde] = alt
        vistas.add(hashlib.sha256(data).digest())
        tid = f"img{len(tarefas) + 1:02d}"
        try:
            Image.open(io.BytesIO(data)).convert("RGB").save(f"{pasta}/ia/{tid}.png")
        except MemoryError:  # falta de memória não é imagem ruim: derruba o documento, como no resto da leitura
            raise
        except Exception:  # noqa: BLE001 — decodificador do Pillow com arquivo hostil levanta de tudo
            if "svg" in (mime or "").lower() or _metafile(data, mime):
                svgs.append((data, mime, onde))
                return f"{sentinela}{len(svgs) - 1}X"
            return _nao_lida(onde, mime, ilegiveis) + (f" [texto alternativo: {alt}]" if alt else "")
        tarefas.append({"id": tid, "modo": "imagem", "ancora": onde})
        return f"{{{{IA:{tid}}}}}"

    limites_formulas = {} if grande else None
    if grande:
        md, perfis = _xlsx_em_fluxo(path, ilegiveis, limites_formulas)
    elif doc is None:
        (md, perfis), ilegiveis[:0] = md_xlsb, ilegiveis_xlsb
    else:
        if ext == ".ods" and ocultas:  # o anydoc entrega a aba oculta do LibreOffice no corpo; a do Excel, não
            doc = _sem_abas(doc, {nome for nome, _ in ocultas})
        md = serializar(doc, ler_imagem, formato=formato, marca_slide=marca if ext in PPTX else None, letras=letras)[0]
        perfis = _perfis(doc) if ext in PLANILHAS - {".xlsx", ".xlsm"} else []
        if limpeza is not None:  # a rede de segurança: o que o anydoc leu e a conferência não viu é falha
            lido = re.sub(rf"{marca}G?\d+X", " ", texto_lido(doc))
            if letras is not None:
                lido = letras.sub(" ", lido)  # a marca aleatória e o contador vêm da máscara, fora do corpo
            if falha := fora_da_conferencia(limpeza.vistas, lido):
                limpeza.falhas.append(falha)
        if ext in PPTX:  # o texto do slide que imita a marca não vira marca: só a do Lince numera os slides
            # Só a ocorrência da forma cobra o orçamento; slides distintos podem apontar para a mesma parte.
            graficos = list(graficos_pptx.values())
            caracteres = _CARACTERES_GRAFICOS_MAX

            def substituir_grafico(m):
                nonlocal caracteres
                textos = []
                for g in graficos[int(m[1])]:
                    titulo = g["titulo"]
                    nota = f"_(gráfico de {g['tipo']} acima: {titulo or 'sem título'})_"
                    texto = nota
                    if g["completo"] and caracteres:
                        tabela = _tabela_ooxml(g, saldo_graficos, ilegiveis)
                        texto = "\n\n".join(filter(None, (titulo, tabela, nota)))
                    if len(texto) > caracteres:
                        caracteres = 0
                        ilegiveis.append(f"gráfico {g['parte']}: teto de caracteres dos gráficos")
                        return "[gráfico não lido]"
                    caracteres -= len(texto)
                    textos.append(texto)
                return "\n\n".join(textos)

            md = re.sub(rf"(?m)^{marca}G(\d+)X$", substituir_grafico, md)
            md = re.sub(r"(?m)^(<!-- slide \d+ -->)$", r"\\\1", md)
            md = re.sub(rf"(?m)^{marca}(\d+)X$", r'<!-- slide \1 -->\n\n<a id="slide-\1"></a>', md)
    if ext in (".ppt", ".pps", ".pot") and doc is not None:  # o leitor do corpo entrega cada célula como parágrafo

        def tabelas_legado():
            nonlocal md
            import olefile

            if olefile.isOleFile(path):
                with olefile.OleFileIO(path) as o:
                    for tabela in legados.tabelas_ppt(o, os.path.getsize(path)):
                        md = _tabela_no_lugar_das_celulas(md, tabela)

        _complemento(tabelas_legado, "tabelas dos slides", ilegiveis)
    if ext in BINARIOS_OLE:  # o leitor do corpo quase não entrega as imagens do .doc, .xls e .ppt: o armazém, sim

        def imagens_legado():
            nonlocal md
            import olefile

            if not olefile.isOleFile(path):  # RTF ou HTML salvo com a extensão antiga: não há armazém
                return
            with olefile.OleFileIO(path) as o:
                achadas, corte = legados.imagens(o, os.path.getsize(path))
            if corte:
                ilegiveis.append(f"imagens do documento: {corte}")
            novas = [(m, d) for m, d in achadas if hashlib.sha256(d).digest() not in vistas and min(_tam(d)) >= 48]
            if novas:
                md += "\n\n**Imagens do documento** (o formato não guarda a posição delas no texto):\n\n"
                md += "\n\n".join(ler_imagem(d, m, f"imagem do documento {n}") for n, (m, d) in enumerate(novas, 1))

        _complemento(imagens_legado, "imagens do documento", ilegiveis)
    if ext == ".rtf":  # o leitor do corpo não usa o estilo de título do RTF: o parágrafo sai como texto comum

        def titulos():
            nonlocal md
            from .metadados import _linhas_rtf

            md = _com_titulos(md, legados.titulos_rtf(open(path, "rb").read(), _linhas_rtf))

        _complemento(titulos, "títulos", ilegiveis)
    if ext in (".docx", ".docm", ".rtf") or ext in ODF:  # o leitor do corpo não entrega a imagem do cabeçalho

        def imagens_bordas():
            nonlocal md
            achadas, novas = _imagens_das_bordas(path, ext, ilegiveis), []
            for m, d in achadas:  # a mesma imagem em várias seções (ou também no corpo) é lida uma vez
                if (marca := hashlib.sha256(d).digest()) not in vistas and min(_tam(d)) >= 48:
                    vistas.add(marca)
                    novas.append((m, d))
            if novas:
                md += "\n\n**Imagens do cabeçalho e do rodapé:**\n\n"
                md += "\n\n".join(
                    ler_imagem(d, m, f"imagem do cabeçalho/rodapé {n}") for n, (m, d) in enumerate(novas, 1)
                )

        _complemento(imagens_bordas, "imagens do cabeçalho e do rodapé", ilegiveis)
    seguranca = []
    if limpeza is not None:
        for falha in limpeza.falhas:  # parte que não pôde ser conferida: suspeita, com a parte e o motivo à vista
            pendentes.append(f"{NAO_CONFERIDO}: {falha}")
            seguranca.append({"tecnicas": ["não conferido"], "texto": "", "alerta": f"{NAO_CONFERIDO}: {falha}"})
        if ocultos := limpeza.ocultos:
            seguranca.append(
                {"tecnicas": sorted({m for m, _ in ocultos}), "texto": " ".join(t for _, t in ocultos)[:500]}
            )
    for nome, texto in ocultas:
        seguranca.append({"tecnicas": ["aba oculta"], "texto": f"aba {nome}: {texto}"[:500]})
    extras, anotacoes = [], []
    if ext in (".xls", ".ods"):  # notas de célula: o anydoc não as entrega, e o .xlsx as tem pelo leitor dele
        notas = xlsx_anotacoes.notas_xls if ext == ".xls" else xlsx_anotacoes.notas_ods
        nomes_ocultos = {nome for nome, _ in ocultas}
        _complemento(lambda: notas(path, nomes_ocultos, anotacoes, pendentes), "notas de célula", pendentes)
    if ext in (".xlsx", ".xlsm"):  # o anydoc não extrai imagem de planilha: as partes de desenho, com aba e célula

        def anotacoes_planilha():
            nonlocal perfis
            cortadas = {p["aba"] for p in perfis if p.get("extracao_cortada")}
            grades = xlsx_anotacoes.extrair(path, limites_formulas, cortadas, anotacoes, pendentes)
            if doc is not None:
                perfis = _perfis(doc, grades)

        _complemento(anotacoes_planilha, "anotações da planilha", pendentes)
        if doc is not None and not perfis:
            perfis = _perfis(doc)
        _complemento(
            lambda: _formulas_xlsx(path, limites_formulas, extras, pendentes), "fórmulas da planilha", ilegiveis
        )

        def imagens_planilha():
            nonlocal md
            for onde, mime, dados in _imagens_xlsx(path, ilegiveis):
                md += f"\n\n**Imagem ({onde}):**\n\n" + ler_imagem(dados, mime, onde)

        _complemento(imagens_planilha, "imagens da planilha", ilegiveis)
    if ext == ".ods":  # o anydoc também não extrai imagem da planilha do LibreOffice: content.xml, com a aba

        def imagens_ods():
            nonlocal md
            for onde, mime, dados in _imagens_ods(path, ilegiveis):
                if (marca := hashlib.sha256(dados).digest()) in vistas or min(_tam(dados)) < 48:
                    continue
                vistas.add(marca)
                md += f"\n\n**Imagem ({onde}):**\n\n" + ler_imagem(dados, mime, onde)

        _complemento(imagens_ods, "imagens da planilha", ilegiveis)
    if ext in (".xlsx", ".xlsm", ".docx"):
        _complemento(
            lambda: extras.extend(grafico_md(g, saldo_graficos, ilegiveis) for g in graficos_ooxml(path)),
            "gráficos",
            ilegiveis,
        )
    if ext == ".xlsb":
        falhas_desenhos = []
        _complemento(
            lambda: _desenhos_xlsb(path, extras, falhas_desenhos, saldo_graficos),
            "desenhos da planilha",
            falhas_desenhos,
        )
        ilegiveis.extend(falhas_desenhos)
        pendentes.extend(falhas_desenhos)
    if ext == ".xls":  # o gráfico do Excel 97-2003: tipo, título e séries; os valores já estão nas células

        def graficos_xls():
            import olefile

            if not olefile.isOleFile(path):
                return
            with olefile.OleFileIO(path) as o:
                graficos, corte = legados.graficos_xls(o, os.path.getsize(path))
            if corte:
                ilegiveis.append(f"gráficos: {corte}")
            for g in graficos:
                linhas = [f"**Gráfico ({g['tipo'] or 'tipo não identificado'}): {g['titulo'] or 'sem título'}**"]
                if g["series"]:
                    linhas.append("Séries: " + ", ".join(g["series"]) + ".")
                linhas.append("_Os valores estão nas células da planilha, acima._")
                extras.append("\n".join(linhas))

        _complemento(graficos_xls, "gráficos", ilegiveis)
    if ext in ODF:  # o leitor do corpo não entrega o gráfico do LibreOffice: tipo, título e os dados que ele desenha
        _complemento(lambda: extras.extend(grafico_md(g) for g in graficos_odf(path, ilegiveis)), "gráficos", ilegiveis)
    if ext == ".odp":  # o leitor do corpo entrega as caixas soltas, sem as ligações

        def diagramas_odp():
            for n, arestas in fluxos_odp(path).items():
                extras.append(
                    f"**Diagrama do slide {n}:**\n\n"
                    + mermaid_do_fluxo(arestas)
                    + "\n\n"
                    + "\n".join(f"- {a} {seta} {b}" + (f" ({rot})" if rot else "") for a, seta, b, rot in arestas)
                )

        _complemento(diagramas_odp, "diagramas", ilegiveis)
    if ext in PPTX:

        def diagramas():
            nonlocal md
            partes = re.split(r"(?m)^(<!-- slide \d+ -->)$", md)
            inicio = {int(re.search(r"\d+", partes[k]).group()): k + 1 for k in range(1, len(partes), 2)}
            for n, arestas in fluxos_pptx(io.BytesIO(limpeza.dados)).items():
                texto = (
                    f"**Diagrama do slide {n}:**\n\n"
                    + mermaid_do_fluxo(arestas)
                    + "\n\n"
                    + "\n".join(f"- {a} {seta} {b}" + (f" ({rot})" if rot else "") for a, seta, b, rot in arestas)
                )
                if n in inicio:
                    k = inicio[n]
                    partes[k] = partes[k].rstrip("\n") + "\n\n" + texto + "\n\n"
                else:
                    extras.append(texto)
            md = "".join(partes)

        _complemento(diagramas, "diagramas de formas", ilegiveis)
    if svgs:
        md = re.sub(rf"{sentinela}(\d+)X", _desenhar_svgs(svgs, pasta, tarefas, ilegiveis, pendentes, alternativas), md)
    return {
        "tipo": "office",
        "md": md,
        "extras": extras,
        "anotacoes_planilha": "\n".join(anotacoes),
        "tarefas": tarefas,
        "texto_oculto": seguranca,
        "ilegiveis": ilegiveis,
        "perfil_tabular": perfis,
        **(
            {"alt_das_tarefas": {t["id"]: alternativas[t["ancora"]] for t in tarefas if t["ancora"] in alternativas}}
            if alternativas
            else {}
        ),
        "pendentes": pendentes,
        "slides": ext in PPTX,  # só a apresentação tem <!-- slide N --> com a posição na estrutura
    }


_FORMATOS = {"image/x-emf": "EMF", "image/emf": "EMF", "image/x-wmf": "WMF", "image/wmf": "WMF",
             "image/svg+xml": "SVG"}  # fmt: skip


_MIDIA = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "bmp": "image/bmp",
          "tif": "image/tiff", "tiff": "image/tiff", "emf": "image/x-emf", "wmf": "image/x-wmf",
          "svg": "image/svg+xml"}  # fmt: skip
_ODF_BORDAS = {"header", "footer", "header-left", "footer-left", "header-first", "footer-first"}
_XLINK = "{http://www.w3.org/1999/xlink}href"


def _mime(nome) -> str:
    return _MIDIA.get(nome.rsplit(".", 1)[-1].lower(), "image/png")


def _imagens_das_bordas(path, ext, ilegiveis) -> list[tuple[str, bytes]]:
    """Imagens do cabeçalho e do rodapé, que o leitor do corpo não entrega: no Word, as relações de imagem das
    partes header*/footer*; no ODF, as draw:image dentro do cabeçalho e do rodapé das páginas mestras (styles.xml);
    no RTF, o \\pict dos grupos \\header e \\footer. O zip já foi conferido pelos limites antes da leitura."""
    if ext == ".rtf":
        achadas, completo = legados.imagens_rtf(open(path, "rb").read())
        if not completo:
            ilegiveis.append("imagens do cabeçalho e do rodapé (parcial)")
        return achadas
    alvos = []  # nomes no zip, sem repetir: o mesmo logotipo em várias seções ou páginas mestras é lido uma vez
    with zipfile.ZipFile(path) as z:
        nomes = set(z.namelist())
        if ext in ODF:
            raiz = xml_ou_nada(z.read("styles.xml")) if "styles.xml" in nomes else None
            for borda in raiz.iter() if raiz is not None else ():
                if borda.tag.rsplit("}", 1)[-1] in _ODF_BORDAS:
                    alvos += [im.get(_XLINK) for im in borda.iter() if im.tag.rsplit("}", 1)[-1] == "image"]
        else:
            for parte in sorted(n for n in nomes if re.fullmatch(r"word/_rels/(header|footer)\d*\.xml\.rels", n)):
                rels = xml_ou_nada(z.read(parte))
                alvos += [
                    "word/" + (r.get("Target") or "").lstrip("/").removeprefix("word/")
                    for r in (rels if rels is not None else ())
                    if (r.get("Type") or "").endswith("/image")
                    and r.get("TargetMode") != "External"
                    and not (r.get("Target") or "").lower().endswith(".svg")  # o Office grava o PNG de reserva junto
                ]
        alvos = [a for a in dict.fromkeys(alvos) if a in nomes]
        if len(alvos) > legados.IMAGENS_MAX:
            ilegiveis.append(f"imagens do cabeçalho e do rodapé: mais de {legados.IMAGENS_MAX}; o resto ficou de fora")
        return [(_mime(a), z.read(a)) for a in alvos[: legados.IMAGENS_MAX]]


_DRAW = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"


def _imagens_ods(path, ilegiveis) -> list[tuple[str, str, bytes]]:
    """(onde, MIME, bytes) das imagens das abas do .ods, na ordem do documento: draw:image dentro de draw:frame que
    não é objeto embutido (o gráfico traz a prévia em ObjectReplacements e é lido à parte), só a primeira de cada
    frame (as demais são representações alternativas da mesma imagem). content.xml é lido em
    streaming, limpando cada elemento: a planilha pode ser enorme. Mesma imagem, uma vez; até legados.IMAGENS_MAX."""
    from defusedxml.ElementTree import iterparse

    achadas, alvos, aba, objeto, imagem_no_frame = [], set(), "", 0, False
    with zipfile.ZipFile(path) as z:
        nomes = set(z.namelist())
        if "content.xml" not in nomes:
            return []
        with z.open("content.xml") as arquivo:
            for evento, el in iterparse(arquivo, events=("start", "end")):
                if evento == "start":
                    if el.tag == f"{{{_TB}}}table":
                        aba = el.get(f"{{{_TB}}}name") or aba
                    elif el.tag == f"{{{_DRAW}}}object" or el.tag == f"{{{_DRAW}}}object-ole":
                        objeto += 1
                    elif el.tag == f"{{{_DRAW}}}image" and not objeto and not imagem_no_frame:
                        imagem_no_frame = True  # as seguintes do mesmo frame são alternativas (PNG de reserva do SVG)
                        alvo = (el.get(_XLINK) or "").removeprefix("./")
                        if alvo in nomes and alvo not in alvos:
                            if len(alvos) >= legados.IMAGENS_MAX:
                                ilegiveis.append(
                                    f"imagens da planilha: mais de {legados.IMAGENS_MAX}; o resto ficou de fora"
                                )
                                break
                            alvos.add(alvo)
                            achadas.append((f"aba {aba}" if aba else "planilha", _mime(alvo), z.read(alvo)))
                    continue
                if el.tag == f"{{{_DRAW}}}frame":
                    objeto, imagem_no_frame = 0, False  # o frame acabou: a próxima imagem é outra
                el.clear()
    return achadas


_XDR = "{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_A_BLIP = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"


def _formulas_xlsx(path, limites, extras, pendentes):
    """Localiza as células de fórmula e distingue <v> salvo de cache ausente. Na rota grande, até a última linha
    lida de cada aba. Os endereços são amostras limitadas por aba, mesmo se todas as células forem fórmulas."""
    from openpyxl.utils import column_index_from_string, get_column_letter

    with zipfile.ZipFile(path) as z:
        nomes = set(z.namelist())
        parte_livro = next(iter(_alvos(z, "", nomes, "officeDocument").values()), "xl/workbook.xml")
        if parte_livro not in nomes:
            return
        livro = xml_ou_nada(z.read(parte_livro))
        alvos = _alvos(z, parte_livro, nomes)
        for aba in livro.findall(".//{*}sheets/{*}sheet") if livro is not None else ():
            rid = aba.get(f"{_REL}id") or aba.get("{http://purl.oclc.org/ooxml/officeDocument/relationships}id")
            nome, parte = aba.get("name"), alvos.get(rid)
            if (
                aba.get("state", "visible") != "visible"
                or parte not in nomes
                or (limites is not None and nome not in limites)
            ):
                continue
            totais, enderecos = [0, 0], [[], []]
            linha = 0
            mostrar_expressao, depois_da_grade = True, False
            with z.open(parte) as arquivo:
                for _, el in etree.iterparse(
                    arquivo,
                    resolve_entities=False,
                    load_dtd=False,
                    no_network=True,
                ):
                    if el.tag.endswith("}sheetProtection") and el.get("sheet") in ("1", "true"):
                        mostrar_expressao = False
                        break
                    if el.tag.endswith("}sheetData"):
                        depois_da_grade = True
                    if not el.tag.endswith("}row"):
                        if depois_da_grade:
                            el.clear()
                            while el.getprevious() is not None:
                                del el.getparent()[0]
                        continue
                    linha = int(el.get("r", linha + 1))
                    if limites is not None and linha > limites[nome]:
                        # A proteção fica depois da grade. Preserva os endereços sem percorrer a aba cortada até o fim.
                        mostrar_expressao = False
                        break
                    ultima_ref, sem_ref = "", 0
                    for c in el.iterfind("{*}c"):
                        if endereco := c.get("r"):
                            ultima_ref, sem_ref = endereco, 0
                        else:
                            sem_ref += 1
                        formula = c.find("{*}f")
                        if formula is not None:
                            v = c.find("{*}v")
                            salvo = v is not None and (v.text is not None or c.get("t") == "str")
                            totais[salvo] += 1
                            if len(enderecos[salvo]) < 32:
                                if not endereco:
                                    coluna = (
                                        column_index_from_string(ultima_ref.rstrip("0123456789")) if ultima_ref else 0
                                    )
                                    endereco = f"{get_column_letter(coluna + sem_ref)}{linha}"
                                expressao_md = ""
                                if formula.text:
                                    expressao = " ".join(formula.text.splitlines())
                                    if len(expressao) > CARACTERES_POR_CELULA_MAX:
                                        expressao = expressao[:CARACTERES_POR_CELULA_MAX] + "…"
                                    crases = max((len(m) for m in re.findall(r"`+", expressao)), default=0) + 1
                                    marca = "`" * crases
                                    expressao_md = f" {marca} ={expressao} {marca}"
                                enderecos[salvo].append((endereco, expressao_md))
                    el.clear()
                    while el.getprevious() is not None:
                        del el.getparent()[0]
            for salvo, quantidade in enumerate(totais):
                if quantidade:
                    estado = "com resultado salvo no arquivo (sem recálculo)" if salvo else "sem resultado salvo"
                    itens = [e + f if mostrar_expressao else e for e, f in enderecos[salvo]]
                    amostra = ", ".join(itens) + (", …" if quantidade > len(itens) else "")
                    aviso = f"Aba {nome} — células de fórmula {estado} ({quantidade}): {amostra}."
                    extras.append(aviso)
                    if not salvo:
                        pendentes.append(aviso)


def _alvos(z, parte, nomes, *tipos, externos=False) -> dict:
    """Id -> parte do zip das relações de `parte` (alvo relativo à pasta dela, ou absoluto a partir da raiz)."""
    pasta, arquivo = posixpath.split(parte)
    rels = posixpath.join(pasta, "_rels", arquivo + ".rels")
    raiz = xml_ou_nada(z.read(rels)) if rels in nomes else None
    out = {}
    for r in raiz.iter(f"{_PKG}Relationship") if raiz is not None else ():
        alvo = r.get("Target") or ""
        if (r.get("TargetMode") == "External") == externos and (
            not tipos or r.get("Type", "").rsplit("/", 1)[-1] in tipos
        ):
            out[r.get("Id")] = (
                alvo
                if externos
                else (alvo.lstrip("/") if alvo.startswith("/") else posixpath.normpath(posixpath.join(pasta, alvo)))
            )
    return out


def _objetos_visiveis_xlsb(raiz):
    """Formas e quadros gráficos na ordem do desenho. Um grupo oculto esconde também todo o seu conteúdo."""
    fila = [raiz]
    while fila:
        el = fila.pop()
        if el.tag in (f"{_XDR}grpSp", f"{_XDR}sp", f"{_XDR}graphicFrame"):
            prop = el.find(f"*/{_XDR}cNvPr")
            if prop is not None and prop.get("hidden") in ("1", "true"):
                continue
            if el.tag != f"{_XDR}grpSp":
                yield el
                continue
        fila.extend(reversed(el))


def _desenhos_xlsb(path, extras, ilegiveis, saldo):
    graficos, vistos = {}, set()
    with zipfile.ZipFile(path) as z:
        nomes = set(z.namelist())
        livro = "xl/workbook.bin"
        if livro not in nomes:
            return
        abas = legados.abas_visiveis_xlsb(z.read(livro), _alvos(z, livro, nomes))
        for nome, parte in abas:
            desenhos = _alvos(z, parte, nomes, "drawing", "vmlDrawing")
            if not desenhos or parte not in nomes:
                continue
            for rid in legados.desenhos_da_aba_xlsb(z.read(parte)):
                desenho = desenhos.get(rid)
                if desenho in vistos:
                    continue
                vistos.add(desenho)
                raiz = xml_ou_nada(z.read(desenho)) if desenho in nomes else None
                if raiz is None:
                    ilegiveis.append(f"aba {nome}: desenho {rid} ilegível")
                    continue
                if raiz.tag != f"{_XDR}wsDr":
                    if desenho.lower().endswith(".vml"):
                        # Notas de célula também têm uma v:textbox: não são caixas de texto do desenho.
                        if not any(
                            forma.find("{*}ClientData[@ObjectType='Note']") is None
                            and (forma.find("{*}textbox") is not None or forma.find("{*}textpath") is not None)
                            for forma in raiz.iter()
                        ):
                            continue
                    ilegiveis.append(f"aba {nome}: desenho {desenho} em formato sem suporte")
                    continue
                rels = _alvos(z, desenho, nomes, "chart")
                for forma in _objetos_visiveis_xlsb(raiz):
                    if forma.tag == f"{_XDR}graphicFrame":
                        for grafico in forma.iter(f"{{{NS['c']}}}chart"):
                            alvo = rels.get(grafico.get(f"{_REL}id"))
                            if alvo in nomes:
                                graficos[alvo] = nome
                            else:
                                ilegiveis.append(f"aba {nome}: gráfico sem parte interna legível")
                        continue
                    paragrafos = []
                    for p in forma.findall(f"{_XDR}txBody/a:p", NS):
                        trechos = []
                        for t in p.iter():
                            if t.tag == f"{{{NS['a']}}}t":
                                trechos.append(t.text or "")
                            elif t.tag == f"{{{NS['a']}}}br":
                                trechos.append("\n")
                        paragrafos.append("".join(trechos))
                    texto = "\n".join(paragrafos).strip()
                    if texto:
                        extras.append(f"**Texto de forma (aba {nome}):**\n\n{texto}")
                    elif forma.get("textlink"):
                        ilegiveis.append(f"aba {nome}: forma com texto vinculado sem cópia salva em {desenho}")
    lidos = set()
    for g in graficos_ooxml(path, graficos) if graficos else ():
        parte = g["parte"]
        lidos.add(parte)
        extras.append(f"Aba {graficos[parte]}\n\n" + grafico_md(g, saldo, ilegiveis))
        if any(not s["vals"].quantidade for s in g["series"]):
            ilegiveis.append(f"aba {graficos[parte]}: gráfico {parte} com série sem valores em cache")
    for parte in graficos.keys() - lidos:
        ilegiveis.append(f"aba {graficos[parte]}: gráfico {parte} ilegível ou com tipo sem suporte")


def _imagens_xlsx(path, ilegiveis) -> list[tuple[str, str, bytes]]:
    """(onde, MIME, bytes) das imagens das abas do .xlsx, na ordem das abas e das âncoras, lidas direto das partes de
    desenho do pacote (workbook -> aba -> drawing -> mídia), sem carregar as células: a planilha pode ter milhões.
    A âncora dá a célula do canto da imagem. Mesma mídia, uma vez; até legados.IMAGENS_MAX."""
    from openpyxl.utils import get_column_letter

    achadas, vistos = [], set()
    with zipfile.ZipFile(path) as z:
        nomes = set(z.namelist())
        livro = xml_ou_nada(z.read("xl/workbook.xml")) if "xl/workbook.xml" in nomes else None
        if livro is None:
            return []
        abas = _alvos(z, "xl/workbook.xml", nomes)
        for aba in livro.iter():
            if aba.tag.rsplit("}", 1)[-1] != "sheet" or (parte := abas.get(aba.get(f"{_REL}id"))) not in nomes:
                continue
            for desenho in (d for d in _alvos(z, parte, nomes).values() if "/drawings/" in d and d in nomes):
                raiz, midias = xml_ou_nada(z.read(desenho)), _alvos(z, desenho, nomes)
                for ancora in list(raiz) if raiz is not None else ():
                    blip = ancora.find(f".//{_A_BLIP}")
                    alvo = midias.get(blip.get(f"{_REL}embed")) if blip is not None else None
                    if alvo not in nomes or alvo in vistos:
                        continue
                    if len(vistos) >= legados.IMAGENS_MAX:
                        ilegiveis.append(f"imagens da planilha: mais de {legados.IMAGENS_MAX}; o resto ficou de fora")
                        return achadas
                    vistos.add(alvo)
                    canto = ancora.find(f"{_XDR}from")
                    try:
                        col, lin = int(canto.findtext(f"{_XDR}col")), int(canto.findtext(f"{_XDR}row"))
                        celula = f", célula {get_column_letter(col + 1)}{lin + 1}"
                    except (AttributeError, TypeError, ValueError):
                        celula = ""  # âncora absoluta: sem célula
                    achadas.append((f"aba {aba.get('name')}{celula}", _mime(alvo), z.read(alvo)))
    return achadas


PPTX = {".pptx", ".pptm", ".ppsx", ".ppsm"}
OCULTA_TEXTO_MAX = 500  # caracteres guardados de cada aba oculta, o mesmo registro do texto oculto
OCULTA_CELULAS_MAX = 20_000  # células varridas de cada aba oculta até juntar esse texto (a vazia não conta)


def _com_titulos(md, titulos):
    """Marca como título, na ordem do documento, o bloco do Markdown que tem exatamente o texto de cada título (sem a
    ênfase que o leitor do corpo pôs), com ou sem a numeração de lista antes. O título que não for achado depois do
    anterior fica como está."""
    blocos, k = md.split("\n\n"), 0
    for nivel, texto in titulos:
        for j in range(k, len(blocos)):
            if "\n" in blocos[j].strip():
                continue
            limpo = " ".join(re.sub(r"^#{1,6}\s+|[*_]", "", blocos[j]).split())
            # a numeração da lista ("1. ", "2.3 ") vem da tabela de listas, fora do parágrafo: ela fica no título
            if limpo == texto or re.fullmatch(rf"\d{{1,3}}(?:\.\d{{1,3}})*[.)]?\s+{re.escape(texto)}", limpo):
                blocos[j] = "#" * nivel + " " + limpo
                k = j + 1
                break
    return "\n\n".join(blocos)


def _tabela_no_lugar_das_celulas(md, tabela):
    """Troca a sequência de parágrafos com as células da tabela, na ordem das linhas, pela tabela Markdown. Só quando a
    sequência aparece uma vez, inteira e com o mesmo texto: senão o texto do leitor do corpo fica como está."""
    celulas = [c for linha in tabela for c in linha if c]
    blocos = md.split("\n\n")
    normal = [" ".join(re.sub(r"[*_\\]", "", b).split()) for b in blocos]
    achados = [k for k in range(len(blocos) - len(celulas) + 1) if normal[k : k + len(celulas)] == celulas]
    if len(achados) != 1:
        return md
    k = achados[0]
    linhas = [[c.replace("|", "\\|") for c in linha] for linha in tabela]
    texto = "\n".join(
        ["| " + " | ".join(linhas[0]) + " |", "|" + " --- |" * len(linhas[0])]
        + ["| " + " | ".join(ln) + " |" for ln in linhas[1:]]
    )
    return "\n\n".join(blocos[:k] + [texto] + blocos[k + len(celulas) :])


def _abas_ocultas(path, ext, ilegiveis) -> list[tuple[str, str]]:
    """(nome, começo do texto) de cada aba oculta ou muito oculta com conteúdo. O XLSX é lido em fluxo pelo openpyxl;
    o XLS, o XLSB e o ODS pelo calamine, porque o anydoc não diz quais abas são ocultas. A aba que não abre fica em
    `ilegiveis` com o nome: a conferência dela não passa em silêncio."""
    out = []
    try:
        if ext in (".xlsx", ".xlsm"):
            from openpyxl import load_workbook

            wb = load_workbook(path, read_only=True, data_only=True)
            try:
                abas = [
                    (ws.title, ws.iter_rows(values_only=True)) for ws in wb.worksheets if ws.sheet_state != "visible"
                ]
                out = [(nome, _texto_de_linhas(linhas)) for nome, linhas in abas]
            finally:
                wb.close()
        else:
            import python_calamine

            wb = python_calamine.CalamineWorkbook.from_path(path)
            try:
                visivel = python_calamine.SheetVisibleEnum.Visible
                for m in wb.sheets_metadata:
                    if m.visible != visivel and m.typ == python_calamine.SheetTypeEnum.WorkSheet:
                        out.append((m.name, _texto_de_linhas(wb.get_sheet_by_name(m.name).iter_rows())))
            finally:
                wb.close()
    except (MemoryError, KeyboardInterrupt, SystemExit):
        raise
    except BaseException as e:  # noqa: BLE001 — o calamine entra em pânico (PanicException, uma BaseException) com
        # arquivo fora da especificação: o corpo segue, e a falha fica registrada
        ilegiveis.append(f"abas ocultas: não conferidas ({type(e).__name__})")
        return []
    return [(nome, texto) for nome, texto in out if texto]


def _texto_de_linhas(linhas) -> str:
    partes, tamanho, varridas = [], 0, 0
    for linha in linhas:
        for valor in linha:
            varridas += 1
            if varridas > OCULTA_CELULAS_MAX or tamanho > OCULTA_TEXTO_MAX:
                return " ".join(partes)
            if valor is not None and (texto := " ".join(str(valor).split())):
                partes.append(texto)
                tamanho += len(texto) + 1
    return " ".join(partes)


def _sem_abas(doc, nomes):
    """O documento do anydoc sem as abas de `nomes`: cada aba do .ods é um título de nível 2 com o nome dela e os
    blocos até o próximo título desse nível."""
    from types import SimpleNamespace

    blocos, pulando = [], False
    for b in doc.blocks:
        if b.kind == "heading" and b.level == 2:
            nome = "".join(x.text or "" for x in b.content or [] if x.kind == "text").strip()
            pulando = nome in nomes
        if not pulando:
            blocos.append(b)
    return SimpleNamespace(blocks=blocos, assets=doc.assets, notes=doc.notes)


PLANILHAS = {".xlsx", ".xlsm", ".xlsb", ".xls", ".ods", ".csv", ".tsv"}  # as que ganham perfil tabular

# Planilha grande, lida em fluxo (openpyxl read_only), com os mesmos tetos do conversor que o Orbital usava
_CELULAS_ANYDOC_MAX = 200_000  # células declaradas acima das quais a planilha não passa pelo anydoc
LINHAS_POR_ABA_MAX = 5_000
CELULAS_POR_ABA_MAX = 200_000
CELULAS_TOTAL_MAX = 500_000
CARACTERES_POR_CELULA_MAX = 500
_VARREDURA_MAX = 10 * CELULAS_TOTAL_MAX  # células lidas, vazias inclusive: uma linha em XFD vale 16.384
_BYTES_POR_CELULA = 20  # aba sem <dimension>: estimativa pelo tamanho do XML descompactado
_DIMENSAO = re.compile(rb'<(?:\w+:)?dimension\s+ref="([A-Z]{1,3})(\d+)(?::([A-Z]{1,3})(\d+))?"')


def _celulas_declaradas(path) -> int:
    """Células de cada parte do pacote pelo maior entre o que a <dimension> declara e o que o tamanho da parte
    comporta, sem ler as células. Não depende de onde nem com que nome o pacote guarda as abas (o openpyxl abre a aba
    pelo nome que a relação dá, com qualquer extensão), nem da dimensão, que pode mentir para menos; exagerar só
    desvia para a leitura em fluxo, que escreve igual. Barato: os primeiros bytes de cada parte."""
    from openpyxl.utils import column_index_from_string

    total = 0
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            try:
                with z.open(info) as f:
                    m = _DIMENSAO.search(f.read(4096))
            except ERROS_DE_PARTE:  # parte que não abre (cifrada, compressão estranha) conta só pelo tamanho
                m = None
            declaradas = 0
            if m and m.group(3):
                largura = column_index_from_string(m.group(3).decode()) - column_index_from_string(m.group(1).decode())
                declaradas = (largura + 1) * (int(m.group(4)) - int(m.group(2)) + 1)
            total += max(declaradas, info.file_size // _BYTES_POR_CELULA)
    return total


def _celula(v) -> str:
    """O valor como o leitor do corpo escreve: data ISO, número inteiro sem .0, booleano TRUE/FALSE, texto aparado
    (os espaços de dentro ficam), | escapado, no máximo CARACTERES_POR_CELULA_MAX caracteres."""
    import datetime

    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, datetime.datetime) and v.time() == datetime.time(0):
        v = v.date()
    texto = re.sub(r"\r\n|[\r\n]", " ", str(v)).strip()  # como o leitor do corpo: cada quebra vira um espaço
    if len(texto) > CARACTERES_POR_CELULA_MAX:
        texto = f"{texto[:CARACTERES_POR_CELULA_MAX]}…[+{len(texto) - CARACTERES_POR_CELULA_MAX} caracteres]"
    return texto.replace("|", "\\|")


def _numero_formatado(celula, formatos_invalidos):
    from xlformat import format_value

    valor = celula.value
    if type(valor) not in (int, float) or not (mascara := celula.number_format) or mascara == "General":
        return _celula(valor)
    # O Excel admite até 255 caracteres no formato numérico. Uma máscara inválida preserva o valor original.
    if len(mascara) <= 255:
        try:
            return _celula(format_value(valor, mascara))
        except (ValueError, ArithmeticError):
            pass
    if not formatos_invalidos:
        formatos_invalidos.append(celula.coordinate)
    return _celula(valor)


def _xlsx_em_fluxo(path, ilegiveis, limites_formulas=None) -> tuple[str, list[dict]]:
    """Corpo da planilha grande, aba por aba (as ocultas e as vazias ficam de fora, como no leitor do corpo), em
    tabela Markdown, lida em fluxo. Acima dos tetos, o resto da aba (ou as abas seguintes) não entra, e o aviso fica
    no próprio texto e em `ilegiveis`: quem lê nunca toma o recorte pelo todo. Os tetos contam as células que saem
    no texto (a tabela é retangular) e as que só foram varridas: a <dimension> do arquivo não é confiável, nem para
    mais nem para menos. Devolve também o perfil de cada aba, das mesmas linhas da tabela."""
    from openpyxl import load_workbook

    partes, perfis, total, varridas, esgotado = [], [], 0, 0, None
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        visiveis = [ws for ws in wb.worksheets if ws.sheet_state == "visible"]
        for ws in visiveis:
            titulo = f"## {ws.title}\n\n" if len(visiveis) > 1 else ""  # aba única vai sem título, como no corpo
            if esgotado:
                aviso = f"aba {ws.title}: {esgotado} atingido antes dela"
                partes.append(f"{titulo}[TRUNCADO: {aviso}; ela não está nesta leitura.]")
                ilegiveis.append(aviso)
                perfis.append(perfil_tabular.Aba(ws.title).montar(cortada=True))
                continue
            ws.reset_dimensions()  # lê as linhas que existem, não as que a <dimension> declara
            linhas, largura, cortada, vazias, inicio = [], 0, False, 0, None
            formatos_invalidos, numeros = [], {}
            for n_linha, celulas in enumerate(ws.iter_rows(), 1):
                varridas += len(celulas) + 1
                if varridas > _VARREDURA_MAX:
                    cortada, esgotado = True, f"teto de {_VARREDURA_MAX} células varridas do documento"
                    break
                valores = [_numero_formatado(c, formatos_invalidos) for c in celulas]
                cheias = [i for i, v in enumerate(valores) if v]
                if not cheias:
                    if limites_formulas is not None:
                        limites_formulas[ws.title] = n_linha
                    vazias += bool(linhas)  # linha vazia entre linhas com dados entra na tabela, como no corpo
                    continue
                nova, n = max(largura, cheias[-1] + 1), len(linhas) + vazias + 1
                if nova * n > CELULAS_TOTAL_MAX - total:
                    cortada, esgotado = True, f"teto de {CELULAS_TOTAL_MAX} células do documento"
                    break
                if n > LINHAS_POR_ABA_MAX + 1 or nova * n > CELULAS_POR_ABA_MAX:
                    cortada = True
                    break
                if limites_formulas is not None:
                    limites_formulas[ws.title] = n_linha
                linhas += [[]] * vazias + [valores[: cheias[-1] + 1]]
                numeros[len(linhas) - 1] = [i for i, c in enumerate(celulas) if type(c.value) in (int, float)]
                largura, vazias = nova, 0
                inicio = cheias[0] if inicio is None else min(inicio, cheias[0])
            total += largura * len(linhas)
            if formatos_invalidos:
                ilegiveis.append(
                    f"aba {ws.title}: formato numérico ilegível a partir de {formatos_invalidos[0]}; valor bruto"
                )
            if not linhas:
                if cortada:  # o teto chegou antes da primeira linha: a aba não some sem aviso
                    aviso = f"aba {ws.title}: {esgotado} atingido antes dela"
                    partes.append(f"{titulo}[TRUNCADO: {aviso}; ela não está nesta leitura.]")
                    ilegiveis.append(aviso)
                    perfis.append(perfil_tabular.Aba(ws.title).montar(cortada=True))
                continue
            corpo = [
                (linha + [""] * (largura - len(linha)))[inicio:] for linha in linhas
            ]  # colunas à esquerda vazias saem
            tabela = ["| " + " | ".join(corpo[0]) + " |", "|" + " --- |" * (largura - inicio)]
            tabela += ["| " + " | ".join(linha) + " |" for linha in corpo[1:]]
            linhas_perfil = (
                ([c.replace("\\|", "|") for c in linha], {i - inicio for i in numeros.get(n, ()) if i >= inicio})
                for n, linha in enumerate(corpo)
            )
            perfis_aba = perfil_tabular.perfis_xlsx(ws.title, linhas_perfil, range(inicio + 1, largura + 1), cortada)
            perfis.extend(perfis_aba)
            texto = titulo + "\n".join(tabela)
            if cortada:
                com_dado = max(p["linhas_extraidas"] for p in perfis_aba)
                aviso = f"aba {ws.title} entregue com {com_dado} linhas de dados por limite de extração"
                texto += (
                    f"\n\n[TRUNCADO: {aviso}. O restante NÃO está nesta leitura; não afirme nada sobre ele. Para a"
                    " análise completa, use o arquivo original.]"
                )
                ilegiveis.append(aviso)
            partes.append(texto)
    finally:
        wb.close()
    return "\n\n".join(partes), perfis


def _xlsb_tolerante(path, ilegiveis) -> tuple[str, list[dict]]:
    """Corpo do .xlsb que o leitor do corpo recusa, escrito como o das outras planilhas: célula pelo `_celula`,
    colunas vazias à esquerda fora, linha vazia entre linhas com dado mantida, aba única sem título."""
    abas, cortado = legados.xlsb_tolerante(path)
    ilegiveis.append("corpo do .xlsb lido registro a registro: o arquivo não segue a especificação")
    if cortado:
        ilegiveis.append(f"células além de {legados._XLSB_CELULAS_MAX}: o resto ficou de fora")
    partes, perfis = [], []
    for nome, linhas in abas:
        cheias = {r: {c: t for c, v in cel.items() if (t := _celula(v))} for r, cel in linhas.items()}
        cheias = {r: cel for r, cel in cheias.items() if cel}
        if not cheias:
            continue
        inicio = min(min(cel) for cel in cheias.values())
        largura = max(max(cel) for cel in cheias.values()) + 1 - inicio
        corpo = [
            [cheias.get(r, {}).get(inicio + c, "") for c in range(largura)] for r in range(min(cheias), max(cheias) + 1)
        ]
        tabela = ["| " + " | ".join(corpo[0]) + " |", "|" + " --- |" * largura]
        tabela += ["| " + " | ".join(lin) + " |" for lin in corpo[1:]]
        partes.append((nome, "\n".join(tabela)))
        aba = perfil_tabular.Aba(nome)
        for lin in corpo:
            aba.linha([c.replace("\\|", "|") for c in lin])
        perfis.append(aba.montar(cortado))
    md = "\n\n".join((f"## {n}\n\n" if len(partes) > 1 else "") + t for n, t in partes)
    return md, perfis


def _texto_simples(blocos) -> str:
    """Texto de uma célula do documento do anydoc, sem marcação: o mesmo que a tabela do Markdown mostra (os
    parágrafos juntos por espaço, as pontas aparadas, os espaços de dentro mantidos)."""

    def inl(xs):
        out = []
        for x in xs or []:
            if x.kind == "text":
                out.append(x.text or "")
            elif x.kind == "link":
                out.append(inl(x.content))
            elif x.kind == "line_break":
                out.append(" ")
        return "".join(out)

    return "\n\n".join(t for t in (inl(b.content) for b in blocos or []) if t.strip()).replace("\n", " ").strip()


def _perfis(doc, grades=None) -> list[dict]:
    """Perfil de cada tabela da planilha lida pelo anydoc, com o nome da aba pelo título que a precede (planilha de
    aba única e CSV vêm sem título: o nome fica None)."""
    perfis, nome = [], None
    for b in doc.blocks:
        if b.kind == "heading":  # o título da aba traz o nome exato, espaços inclusive
            nome = "".join(x.text or "" for x in b.content or [] if x.kind == "text")
        elif b.kind == "table":
            linhas = (
                [_texto_simples(s.cell.blocks) if s.kind == "origin" and s.cell else "" for s in linha]
                for linha in b.table.grid
            )
            grade = grades.get(nome) if grades else None
            if nome is None and grades and len(grades) == 1:
                grade = next(iter(grades.values()))
            if grade:
                tipadas, colunas = grade.projetar(linhas)
                perfis.extend(perfil_tabular.perfis_xlsx(nome, tipadas, colunas))
            else:
                aba = perfil_tabular.Aba(nome)
                for linha in linhas:
                    aba.linha(linha)
                perfis.append(aba.montar())
            nome = None
    return perfis


_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"


def _slides_pptx(z, nomes) -> list[str]:
    """As partes dos slides na ordem da apresentação (sldIdLst), como o PowerPoint numera."""

    try:
        raiz = _xml(z.read("ppt/presentation.xml"))
    except (KeyError, XMLIlegivel):
        return []
    rels = _alvos(z, "ppt/presentation.xml", nomes)
    lista = next(raiz.iter(f"{_P}sldIdLst"), None)  # o primeiro, em qualquer profundidade, como o anydoc acha
    ids = lista.iterfind(f"{_P}sldId") if lista is not None else ()
    return [rels[s.get(f"{_REL}id")] for s in ids if s.get(f"{_REL}id") in rels]


def _com_marcas_de_slide(dados: bytes, marca: str, graficos) -> bytes:
    """Cópia do pacote com uma caixa de texto invisível ("<marca>N X", tamanho 1, no canto) no começo de cada slide:
    o leitor do corpo a escreve antes do resto do slide, e ela vira <!-- slide N -->. Slide que não abre fica sem
    marca (o texto dele segue com o slide anterior); pacote que não abre segue sem marca nenhuma. O gráfico tem
    sua própria marca na posição da forma para a tabela entrar só nele, mesmo que repita o cabeçalho de outro."""
    try:
        return _marcar(dados, marca, graficos)
    except MemoryError:
        raise
    except Exception:  # noqa: BLE001 — pacote que não dá para copiar (CRC, parte cifrada): segue sem marca
        return dados


def _marcar(dados, marca, graficos):

    with zipfile.ZipFile(io.BytesIO(dados)) as zi:
        nomes = set(zi.namelist())
        ordem = {parte: n for n, parte in enumerate(_slides_pptx(zi, nomes), 1)}
        notas = {nota for slide in ordem for nota in _alvos(zi, slide, nomes, "notesSlide").values()}
        saida = io.BytesIO()
        with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as zo:
            for info in zi.infolist():
                conteudo = zi.read(info)
                if info.filename in ordem or info.filename in notas:
                    try:
                        raiz = _xml(conteudo)
                        # O anydoc representa tanto um vínculo externo relativo quanto uma parte interna órfã como
                        # "relative". Na cópia de leitura, retire só os vínculos internos sem slide de destino.
                        rels = _alvos(zi, info.filename, nomes)
                        mudou = False
                        for link in raiz.iter(f"{{{NS['a']}}}hlinkClick"):
                            rid = link.get(f"{_REL}id")
                            if rid in rels and rels[rid] not in ordem:
                                link.attrib.pop(f"{_REL}id")
                                mudou = True
                        arvore = raiz.find(f"{_P}cSld/{_P}spTree")
                        if info.filename in ordem and arvore is not None:
                            for forma in arvore.iter(f"{_P}graphicFrame"):
                                ch = forma.find("a:graphic/a:graphicData/c:chart", NS)
                                if ch is not None and (parte := rels.get(ch.get(f"{_REL}id"))) in graficos:
                                    marcador, completo = graficos[parte]
                                    if completo:
                                        forma.getparent().replace(forma, _caixa_marca(marcador))
                                    else:  # tipo não interpretado: manter também a tabela do anydoc
                                        forma.addnext(_caixa_marca(marcador))
                            arvore.insert(min(2, len(arvore)), _caixa_marca(f"{marca}{ordem[info.filename]}X"))
                            mudou = True
                        if mudou:
                            conteudo = etree.tostring(raiz, xml_declaration=True, encoding="UTF-8", standalone=True)
                    except XMLIlegivel:
                        pass
                # writestr altera header_offset. As relações podem preceder o slide e ainda serão lidas em zi.
                zo.writestr(copy.copy(info), conteudo)
    return saida.getvalue()


def _caixa_marca(texto):
    p, a = _P[1:-1], "http://schemas.openxmlformats.org/drawingml/2006/main"
    return etree.fromstring(
        f'<p:sp xmlns:p="{p}" xmlns:a="{a}"><p:nvSpPr><p:cNvPr id="2147483646" name="lince"/><p:cNvSpPr '
        f'txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1" cy="1"/></a:xfrm>'
        f"</p:spPr><p:txBody><a:bodyPr/><a:p><a:r><a:t>{texto}</a:t></a:r></a:p></p:txBody></p:sp>"
    )


def _nao_lida(onde, mime, ilegiveis) -> str:
    ilegiveis.append(f"{onde}: imagem {mime or 'sem tipo'} não lida")
    return f"[{onde}: imagem {_FORMATOS.get((mime or '').lower(), mime or 'sem tipo')} não lida]"


_METAFILES = {"image/x-emf", "image/emf", "image/x-wmf", "image/wmf"}


def _metafile(data, mime) -> str | None:
    """O tipo do EMF ou WMF, pela assinatura (o tipo declarado pode ser genérico, como o image/png que a extensão
    desconhecida recebe) ou, sem ela, pelo tipo declarado; None se não for metafile."""
    if data[40:44] == b" EMF":
        return "image/x-emf"
    if data[:4] == b"\xd7\xcd\xc6\x9a" or (
        data[:1] in (b"\x01", b"\x02") and data[1:6] in (b"\x00\x09\x00\x00\x01", b"\x00\x09\x00\x00\x03")
    ):
        return "image/x-wmf"  # placeable ou cabeçalho padrão (memória ou disco, qualquer versão)
    return (mime or "").lower() if (mime or "").lower() in _METAFILES else None


def _desenhar_svgs(svgs, pasta, tarefas, ilegiveis, pendentes, alternativas):
    """Desenha os SVG, EMF e WMF do documento num lote só (o teto de tempo é do documento) e até DESENHOS_MAX;
    devolve a troca de cada marca provisória pela tarefa da IA ou pela marca de imagem não lida."""
    from lxml import etree

    from .desenho_svg import desenhar_lote
    from .pagina_html import _SVG_XML, DESENHOS_MAX

    lote, trocas = [], {}
    for k, (data, mime, onde) in enumerate(svgs):
        if tipo := _metafile(data, mime):
            desenho, mime = data, tipo
        else:
            mime = "image/svg+xml"
            try:
                desenho = etree.fromstring(data, _SVG_XML)
            except etree.XMLSyntaxError:
                desenho = None
            if desenho is not None and etree.QName(desenho).localname != "svg":
                desenho = None
        if desenho is None or len(lote) >= DESENHOS_MAX:
            trocas[k] = _nao_lida(onde, mime, ilegiveis)
            continue
        lote.append((k, onde, mime, f"img{len(tarefas) + len(lote) + 1:02d}", desenho))
    feitos = desenhar_lote([(desenho, f"{pasta}/ia/{tid}.png") for _, _, _, tid, desenho in lote])
    for (k, onde, mime, tid, _), feito in zip(lote, feitos, strict=True):
        if feito == "lido":
            tarefas.append({"id": tid, "modo": "imagem", "ancora": onde})
            trocas[k] = f"{{{{IA:{tid}}}}}"
        else:
            trocas[k] = _nao_lida(onde, mime, ilegiveis)
            if feito.startswith("sem "):  # falta o desenhista: dá para resolver instalando, fica pendente
                extra = ' (instale "lince[emf]")' if feito == "sem metafile-render" else ""
                pendentes.append(f"{onde}: {_FORMATOS.get(mime, mime)} não desenhado, {feito}{extra}")

    def substituir(m):
        k = int(m.group(1))
        marca = trocas[k]
        alt = alternativas.get(svgs[k][2])
        if alt and not marca.startswith("{{IA:"):
            return f"{marca} [texto alternativo: {alt}]"
        return marca

    return substituir


def _complemento(funcao, nome, ilegiveis):
    """Enriquecimento além do texto (gráficos, diagramas, imagens de planilha). Se a parte for malformada ou
    hostil, o documento segue sem ele e o que ficou de fora é registrado; o corpo lido nunca se perde por isso."""
    try:
        funcao()
    except MemoryError:  # falta de memória não é parte ruim: derruba o documento
        raise
    except Exception as e:  # noqa: BLE001
        ilegiveis.append(f"{nome}: {type(e).__name__}")


def _tam(data):
    try:
        from PIL import Image

        return Image.open(io.BytesIO(data)).size
    except Exception:  # noqa: BLE001 — formato que o Pillow não abre conta como imagem grande (não é descartada)
        return (999, 999)


# comandos e ambientes que o anydoc escreve na fórmula (shared/math/tex.rs), e o símbolo que ele escreve com uma
# palavra dentro (≝)
_TEX = re.compile(r"\\overset\{\\mathrm\{def\}\}|\\(?:begin|end)\{[^}]*\}|\\[A-Za-z]+")


def texto_lido(doc) -> str:
    """O texto que o anydoc extraiu do documento, para a rede de segurança da limpeza: o texto dos parágrafos,
    títulos, listas, tabelas, citações e notas, e o da fórmula sem os comandos do TeX. O texto alternativo das imagens
    fica de fora (a conferência não o confere: não tem como ser escondido por estilo), e o que o Lince acrescenta
    (marcas de imagem e de slide) não está no documento do anydoc."""
    out = []

    def inl(xs):
        for x in xs or []:
            if x.kind == "text":
                out.append(x.text or "")
            elif x.kind == "link":
                inl(x.content)
            elif x.kind == "math":
                out.append(f" {_TEX.sub(' ', x.text or '')} ")
            else:  # imagem, quebra, âncora, nota, caixa: separam o texto
                out.append(" ")

    def blocos(bs):
        for b in bs or []:
            if b.kind in ("heading", "paragraph"):
                inl(b.content)
            elif b.kind == "list":
                for it in b.list.items:
                    if it.marker_label:
                        out.append(f" {it.marker_label} ")
                    blocos(it.blocks)
            elif b.kind == "table":
                for linha in b.table.grid:
                    for s in linha:
                        if s.kind == "origin" and s.cell:
                            blocos(s.cell.blocks)
                            out.append("\n")
            elif b.kind == "block_quote":
                blocos(b.blocks)
            elif b.kind in ("code_block", "math"):
                out.append(_TEX.sub(" ", b.text or "") if b.kind == "math" else b.text or "")
            out.append("\n")

    blocos(doc.blocks)
    for nota in doc.notes:
        blocos(nota.blocks)
    return "".join(out)


def _link_markdown(rotulo, alvo):
    """Conserva o destino como dado, sem buscar o recurso."""
    if alvo is None or not alvo.value:
        return rotulo
    destino = alvo.value.strip()
    if alvo.kind == "anchor":
        destino = "#" + destino.lstrip("#")
    return _destino_markdown(rotulo, destino) or rotulo


def _destino_markdown(rotulo, destino):
    from urllib.parse import quote

    esquema = re.match(r"^([a-z][a-z0-9+.-]*):", destino, flags=re.I)
    if esquema and esquema[1].lower() not in ("http", "https", "mailto", "ftp", "tel"):
        return ""
    if not destino or any(ord(c) < 32 for c in destino):
        return ""
    destino = quote(destino, safe="/:?#@!$&'*+,;=%~")
    texto = (rotulo or destino).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    return f"[{texto}]({destino})"


def _letra_word(numero):
    if numero == 0:
        return "0"
    ciclos, indice = divmod(numero - 1, 26)
    letra = chr(ord("a") + indice)
    # Um start pode ser qualquer inteiro de 32 bits. A forma compacta mantém a contagem sem expandir milhões de letras.
    return letra * (ciclos + 1) if ciclos < 256 else f"{letra}×{ciclos + 1}"


def _do_contador_alfabetico(m):
    original = m[1]
    if original == "0":
        return original
    numero = 0
    for letra in original.lower():
        numero = 26 * numero + ord(letra) - ord("a") + 1
    texto = _letra_word(numero)
    return texto.upper() if original.isupper() else texto


def _rotulo_word(lista, item, numero, letras):
    """O anydoc já resolveu a máscara multinível e os reinícios. Marcadores simples trazem apenas a família."""
    if item.marker_label is not None:
        rotulo = " ".join(item.marker_label.split())
        if letras is not None:
            rotulo = letras.sub(_do_contador_alfabetico, rotulo)
        # A máscara pode conter texto arbitrário. O rótulo publicado é literal, inclusive dentro de tabelas.
        return re.sub(r"([\\\[\]*_\x60#!~])", r"\\\1", html.escape(rotulo, quote=False))
    if lista.marker.endswith("_roman"):
        valor = romano(numero)
    elif lista.marker.endswith("_alpha"):
        valor = _letra_word(numero)
    else:
        return "-" if lista.marker == "bullet" else f"{numero}."
    return (valor.upper() if lista.marker.startswith("upper") else valor) + "."


def serializar(doc, ler_imagem, *, links=False, formato=None, marca_slide=None, letras=None):
    epub = formato == "epub"
    cobertura = {"pendente": []}
    assets = {a.id: a for a in doc.assets}
    n_img = {"n": 0}
    links = links or formato in ("docx", "rtf", "pptx")
    # Só uma marca inserida pelo Lince comprova que a parte de destino foi lida. A numeração segue a apresentação.
    slides = set()
    if marca_slide:
        for b in doc.blocks:
            if b.kind == "paragraph" and len(b.content) == 1 and b.content[0].kind == "text":
                if m := re.fullmatch(rf"{marca_slide}(\d+)X", b.content[0].text or ""):
                    slides.add(f"slide-{m[1]}")

    def inl(xs):
        out = []
        for x in xs or []:
            k = x.kind
            if k == "text":
                texto = x.text or ""
                out.append(letras.sub(_do_contador_alfabetico, texto) if letras is not None else texto)
            elif k == "link":
                rotulo = inl(x.content)
                alvo = x.target
                if marca_slide and alvo and alvo.kind == "anchor" and alvo.value not in slides:
                    alvo = None
                out.append(_link_markdown(rotulo, alvo) if links else rotulo)
            elif k == "anchor" and links and x.anchor:
                if not marca_slide:  # a âncora dos slides entra dentro do próprio slide, junto da marca de posição
                    out.append(f'<a id="{html.escape(x.anchor, quote=True)}"></a>')
            elif k == "image":
                n_img["n"] += 1
                src = x.source
                onde = f"imagem {n_img['n']}"
                if src and src.kind == "asset" and src.asset_id in assets:
                    a = assets[src.asset_id]
                    if not a.media_type.startswith("image/"):
                        cobertura["pendente"].append(f"{onde}: {a.media_type}")
                        out.append(f"[{onde}: objeto {a.media_type}]")
                    elif min(_tam(a.data)) < 48 and not (epub and x.alt):
                        cobertura["pendente"].append(f"{onde}: decorativa")
                    else:
                        if epub and x.alt:
                            alt = (
                                html.escape(" ".join(x.alt.split()), quote=False)
                                .replace("{", "&#123;")
                                .replace("}", "&#125;")
                            )
                            out.append(ler_imagem(a.data, a.media_type, onde, alt))
                        else:
                            out.append(ler_imagem(a.data, a.media_type, onde))
                else:
                    out.append(f"[{onde}: {x.alt or 'sem fonte'}]")
            elif k == "line_break":
                out.append("  \n" if epub else " ")
            elif k == "math":
                out.append(f"${x.text}$")
            elif k == "note_ref":
                out.append(f"[^{x.note_id}]")
            elif k == "checkbox":
                out.append("[x]" if x.checked else "[ ]")
        return "".join(out)

    def blocos(bs, prof=0):
        out = []
        for b in bs or []:
            k = b.kind
            if k == "heading":
                out.append("#" * (b.level or 1) + " " + inl(b.content))
            elif k == "paragraph":
                txt = inl(b.content)
                out.append("\\" + txt if txt and txt[0] in ">#-+|" else txt)
            elif k == "list":
                for i, it in enumerate(b.list.items):
                    corpo = blocos(it.blocks, prof + 1).strip()
                    if not epub:
                        corpo = corpo.replace("\n", "\n  ")
                    marca = "-" if b.list.marker == "bullet" else f"{b.list.start + i}."
                    if formato == "docx":
                        marca = _rotulo_word(b.list, it, b.list.start + i, letras)
                        if b.list.marker != "bullet" and marca and corpo.startswith(marca + " "):
                            corpo = corpo[len(marca) + 1 :]
                    if epub:
                        recuo = " " * (len(marca) + 1)
                        out.append(f"{marca} " + corpo.replace("\n", "\n" + recuo))
                    else:
                        out.append("  " * prof + f"{marca} {corpo}")
            elif k == "table":
                linhas = []
                for row in b.table.grid:
                    cel = []
                    for s in row:
                        t = blocos(s.cell.blocks).replace("\n", " ").strip() if s.kind == "origin" and s.cell else ""
                        cel.append(t.replace("|", "\\|"))
                    linhas.append("| " + " | ".join(cel) + " |")
                if linhas:
                    ncol = linhas[0].count(" | ") + 1
                    linhas.insert(1, "|" + " --- |" * ncol)
                out.append("\n".join(linhas))
            elif k == "block_quote":
                out.append("> " + blocos(b.blocks).replace("\n", "\n> "))
            elif k == "code_block":
                out.append(f"```{b.lang or ''}\n{b.text}\n```")
            elif k == "rule":
                out.append("---")
            elif k == "math":
                out.append(f"$$\n{b.text}\n$$")
        return "\n\n".join(x for x in out if x.strip())

    md = blocos(doc.blocks)
    if doc.notes:
        md += "\n\n" + "\n".join(f"[^{n.id}]: {blocos(n.blocks)}" for n in doc.notes)
    return md, n_img["n"]
