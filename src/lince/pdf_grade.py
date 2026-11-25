"""Tabela com grade desenhada: as linhas da própria tabela dão as células, sem IA.

O extrator de texto monta a tabela pela posição das palavras: junta o quadro vizinho na primeira linha, a nota na
última e o eixo de um gráfico ao lado numa coluna a mais. Quando a tabela tem as linhas de grade no PDF, as células
são as que o documento desenhou. A detecção da grade (arestas → cruzamentos → células → tabelas) é a do pdfplumber,
alimentada com os traços que a PDFium já entregou; o texto de cada célula vem dos itens da página, que já passaram
pela conferência de texto oculto."""

import html
import re
from collections import Counter

from pdfplumber.table import cells_to_tables, edges_to_intersections, intersections_to_cells, merge_edges

from .pdf_colunas import PALAVRAS_DE_PROSA
from .pdf_colunas import blocos_de_texto as blocos

ESPESSURA_MAX = 2.5  # pontos: traço mais grosso que isso é preenchimento, não linha de grade
COMPRIMENTO_MIN = 6
TOLERANCIA = 3  # pontos: folga para encostar arestas e achar cruzamentos, a mesma do pdfplumber
CRUZAMENTOS_MAX = 3000  # traços horizontais × verticais: uma tabela de 120 linhas e 24 colunas ainda cabe
TRACOS_MAX = 2048  # grade maior que isso é planta ou malha de desenho: fica com o leitor geral
CONTEUDO_MIN = 0.6  # fração das palavras da tabela do extrator que precisa estar nas grades para elas a substituírem
COBERTURA_MIN = 0.9  # fração das palavras da grade que a tabela do extrator precisa ter para ser a mesma tabela


def _arestas(tracos):
    out = []
    for l, b, r, t in tracos:
        w, h = r - l, t - b
        if h <= ESPESSURA_MAX and w >= COMPRIMENTO_MIN:
            y = -(b + t) / 2  # o pdfplumber mede de cima para baixo
            out.append({"x0": l, "x1": r, "top": y, "bottom": y, "width": w, "height": 0, "orientation": "h"})
        elif w <= ESPESSURA_MAX and h >= COMPRIMENTO_MIN:
            x = (l + r) / 2
            out.append({"x0": x, "x1": x, "top": -t, "bottom": -b, "width": 0, "height": h, "orientation": "v"})
    return out


def _eixos(valores):
    """Coordenadas distintas, com as que diferem menos que a tolerância contadas como uma."""
    eixos = []
    for v in sorted(valores):
        if not eixos or v - eixos[-1] > TOLERANCIA:
            eixos.append(v)
    return eixos


def _indice(eixos, v):
    return min(range(len(eixos)), key=lambda k: abs(eixos[k] - v))


def _palavras(texto):
    return re.findall(r"\w+", texto.lower())


def grades(tracos, itens, tique):
    """As tabelas com grade da página: {"caixa": [l, b, r, t], "celulas": [(linha, coluna, nlinhas, ncolunas, texto)],
    "linhas": n, "colunas": n, "itens": ids dos itens dentro dela}. Só a grade com ao menos 2 linhas, 2 colunas e
    texto em 4 células é tabela; um quadro em volta de um parágrafo não é."""
    if not 4 <= len(tracos) <= TRACOS_MAX:
        return []
    arestas = _arestas(tracos)
    deitadas = sum(e["orientation"] == "h" for e in arestas)
    # o custo da detecção cresce com os cruzamentos possíveis: a malha densa de um desenho técnico ou de um arquivo
    # hostil não é tabela, e fica com o leitor geral
    if deitadas * (len(arestas) - deitadas) > CRUZAMENTOS_MAX:
        return []
    arestas = merge_edges(arestas, TOLERANCIA, TOLERANCIA, TOLERANCIA, TOLERANCIA)
    if len(arestas) < 4:
        return []
    tique()
    celulas = intersections_to_cells(edges_to_intersections(arestas, TOLERANCIA, TOLERANCIA))
    out = []
    for tabela in cells_to_tables(celulas):
        tique()
        xs = _eixos([v for x0, _, x1, _ in tabela for v in (x0, x1)])
        ys = _eixos([v for _, y0, _, y1 in tabela for v in (y0, y1)])
        if len(xs) < 3 or len(ys) < 3:
            continue
        caixa = [xs[0], -ys[-1], xs[-1], -ys[0]]
        dentro = [
            x
            for x in itens
            if x.text.strip()
            and caixa[0] - 1 <= x.x + x.width / 2 <= caixa[2] + 1
            and caixa[1] - 1 <= x.y + x.height / 2 <= caixa[3] + 1
        ]
        prontas, bases = [], {}
        for x0, y0, x1, y1 in tabela:
            tique()
            i, j = _indice(ys, y0), _indice(xs, x0)
            ni, nj = _indice(ys, y1) - i, _indice(xs, x1) - j
            if ni < 1 or nj < 1:
                continue
            seus = [x for x in dentro if x0 <= x.x + x.width / 2 <= x1 and y0 <= -(x.y + x.height / 2) <= y1]
            seus.sort(key=lambda x: (-round(x.y / 2), x.x))  # linhas de cima para baixo, da esquerda para a direita
            prontas.append((i, j, ni, nj, " ".join(x.text.strip() for x in seus)))
            bases.setdefault(i, []).append({round(x.y / 2) for x in seus})
        # várias linhas de texto alinhadas em duas células da mesma linha da grade: são linhas da tabela sem traço
        # entre elas (a grade só separa o cabeçalho ou as colunas), e a estrutura não está toda desenhada
        if any(len(a & b) >= 3 for linha in bases.values() for k, a in enumerate(linha) for b in linha[k + 1 :]):
            continue
        if len({(i, j) for i, j, *_ in prontas}) != len(prontas):
            continue  # duas células no mesmo canto: a grade não fechou direito
        if sum(1 for *_, texto in prontas if texto) < 4:
            continue
        out.append(
            {
                "caixa": caixa,
                "celulas": sorted(prontas),
                "linhas": len(ys) - 1,
                "colunas": len(xs) - 1,
                "itens": {id(x) for x in dentro},
            }
        )
    return out


def markdown(grade):
    """Tabela Markdown quando toda célula ocupa um lugar só; HTML com rowspan/colspan quando há célula mesclada."""
    mescla = any(ni > 1 or nj > 1 for _, _, ni, nj, _ in grade["celulas"])
    if not mescla and len(grade["celulas"]) == grade["linhas"] * grade["colunas"]:
        linhas = [[""] * grade["colunas"] for _ in range(grade["linhas"])]
        for i, j, _, _, texto in grade["celulas"]:
            linhas[i][j] = texto.replace("|", "\\|")
        out = ["| " + " | ".join(linhas[0]) + " |", "|" + " --- |" * grade["colunas"]]
        return "\n".join(out + ["| " + " | ".join(ln) + " |" for ln in linhas[1:]])
    # o cabeçalho é a primeira linha e as que uma célula mesclada dela alcança
    cabecalho = max([ni for i, _, ni, _, _ in grade["celulas"] if i == 0] or [1])
    out = ["<table>"]
    for i in range(grade["linhas"]):
        out.append("<tr>")
        for li, _, ni, nj, texto in [c for c in grade["celulas"] if c[0] == i]:
            tag = "th" if li < cabecalho else "td"
            span = (f' rowspan="{ni}"' if ni > 1 else "") + (f' colspan="{nj}"' if nj > 1 else "")
            out.append(f"<{tag}{span}>{html.escape(texto, quote=False)}</{tag}>")
        out.append("</tr>")
    return "\n".join(out + ["</table>"])


def aplicar(tabelas, tracos, itens, tique):
    """Troca a estrutura da tabela do extrator pela da grade quando as duas são a mesma tabela (as palavras da grade
    estão na tabela do extrator). O que o extrator juntou à tabela e está fora da grade (a nota, o quadro vizinho, o
    eixo de um gráfico ao lado) sai como texto, em parágrafos, na posição em que está na página: nada some.
    Devolve as caixas das grades da página."""
    if not tabelas:
        return []
    itens = [x for x in itens if not x.rotation and x.render_mode == 0 and not x.text.startswith("[Image:")]
    achadas = grades(tracos, itens, tique)
    if not achadas:
        return []
    pesos = Counter()
    for x in itens:
        pesos[round(x.font_size * 2) / 2] += len(x.text)
    corpo = pesos.most_common(1)[0][0]
    livres = list(achadas)
    for tb in tabelas:
        tique()
        suas = []
        for g in livres:
            palavras = [w for *_, texto in g["celulas"] for w in _palavras(texto)]
            if palavras and sum(w in tb["palavras"] for w in palavras) / len(palavras) >= COBERTURA_MIN:
                suas.append(g)
        celulas_tb = [
            c.strip() for ln in tb["md"].splitlines() for c in ln.strip().strip("|").split("|") if c.strip(" :-")
        ]
        resto = Counter(w for c in celulas_tb for w in _palavras(c))
        total = sum(resto.values())
        na_grade = sum(len(_palavras(texto)) for g in suas for *_, texto in g["celulas"])
        # a grade substitui a tabela do extrator quando é quase toda ela, ou quando o resto é prosa: o extrator fez da
        # página uma "tabela" de parágrafos e a tabela de verdade é só a parte com grade
        em_prosa = sum(len(_palavras(c)) for c in celulas_tb if len(_palavras(c)) >= PALAVRAS_DE_PROSA)
        if not suas or (na_grade < CONTEUDO_MIN * total and em_prosa < CONTEUDO_MIN * (total - na_grade)):
            continue
        for g in suas:
            resto.subtract(w for *_, texto in g["celulas"] for w in _palavras(texto))
            livres.remove(g)
        dentro = set().union(*(g["itens"] for g in achadas))
        # o que sobrou da tabela do extrator: os itens fora de qualquer grade cujas palavras ainda não saíram
        suas.sort(key=lambda g: -g["caixa"][3])
        zonas = [[] for _ in range(len(suas) + 1)]
        for x in sorted(itens, key=lambda x: (-x.y, x.x)):
            w = _palavras(x.text)
            if id(x) in dentro or not w or any(resto[p] <= 0 for p in set(w)):
                continue
            resto.subtract(w)
            zonas[sum(g["caixa"][3] > x.y + x.height / 2 for g in suas)].append(x)  # depois das grades acima dele
        pedacos = blocos(zonas[0], corpo)
        for g, zona in zip(suas, zonas[1:], strict=True):
            pedacos += [markdown(g), *blocos(zona, corpo)]
        novo = "\n\n".join(pedacos)
        # nada some: toda palavra da tabela do extrator tem de estar na saída; se a conta não fecha (a grade casou com
        # a tabela errada, um item ficou sem lugar), a tabela do extrator fica como estava
        if Counter(w for c in celulas_tb for w in _palavras(c)) - Counter(_palavras(novo)):
            livres.extend(suas)
            continue
        tb["md"] = novo
        tb["palavras"] = {w for g in suas for *_, texto in g["celulas"] for w in _palavras(texto)}
        tb["complexa"] = False
        tb["confirmada"] = tb["grade"] = True
    return [g["caixa"] for g in achadas]
