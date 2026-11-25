"""Conferências locais da tabela Markdown contra os itens posicionados e os filetes do próprio PDF."""

import re
from bisect import bisect
from collections import Counter, defaultdict


def _celulas(linha):
    return [c.strip() for c in linha.strip("|").split("|")]


def _linha(celulas):
    return "| " + " | ".join(celulas) + " |"


def _tokens(texto):
    return re.findall(r"\w+|[^\w\s]", texto)


def _cabecalho(linhas, itens, filetes):
    """Identifica a linha de subtítulos e a primeira de dados pelo texto. Um filete que termina
    antes do título de grupo prova a união das colunas. Exige todos os rótulos do Markdown."""
    if len(linhas) < 4:
        return
    topo, sub, dados = map(_celulas, (linhas[0], linhas[2], linhas[3]))
    if "" not in topo or "" not in sub or not all(dados) or not 3 <= len(dados) <= 32:
        return
    rotulos = [s for s in sub if s]
    if len(rotulos) < 2:
        return

    def encontrar(textos):
        candidatos = []
        for i, x in enumerate(itens):
            if x.text.strip() != textos[0]:
                continue
            faixa = itens[i : i + len(textos)]
            if [v.text.strip() for v in faixa] == textos and all(abs(v.y - x.y) < 1 for v in faixa):
                candidatos.append(i)
        return candidatos[0] if len(candidatos) == 1 else None

    a, b = encontrar(rotulos), encontrar(dados)
    if a is None or b != a + len(rotulos):
        return
    folhas, valores = itens[a:b], itens[b : b + len(dados)]
    if folhas[0].y <= valores[0].y or any(x.x + x.width >= y.x for x, y in zip(valores, valores[1:], strict=False)):
        return
    # As palavras dos títulos podem ocupar várias linhas na mesma célula.
    precisa = Counter(_tokens(" ".join(topo)))
    acima = []
    for k in range(a - 1, max(-1, a - 65), -1):
        x = itens[k]
        palavras = Counter(_tokens(x.text))
        if not palavras <= precisa or x.y <= folhas[0].y:
            break
        acima.append(x)
        precisa.subtract(palavras)
        if not +precisa:
            break
    if +precisa or not acima:
        return

    def divisoes(y):
        return sorted({round((l + r) / 2, 1) for l, base, r, t in filetes if base < y < t})

    cortes = divisoes(folhas[0].y + folhas[0].height / 2)
    limites = []
    for x, y in zip(valores, valores[1:], strict=False):
        entre = [c for c in cortes if x.x + x.width < c < y.x]
        if len(entre) != 1:
            return
        limites.extend(entre)
    grupos = defaultdict(list)
    for x in reversed(acima):
        ativos = set(divisoes(x.y + x.height / 2)) & set(limites)
        if any(x.x < c < x.x + x.width for c in ativos):
            return
        esq = max((j + 1 for j, c in enumerate(limites) if c in ativos and c <= x.x), default=0)
        fim = min((j + 1 for j, c in enumerate(limites) if c in ativos and c >= x.x + x.width), default=len(dados))
        grupos[esq, fim].append(x)
    pais = [""] * len(dados)
    for (esq, fim), xs in grupos.items():
        if any(pais[esq:fim]):
            return
        pais[esq:fim] = [" ".join(x.text.strip() for x in sorted(xs, key=lambda x: (-x.y, x.x)))] * (fim - esq)
    if not all(pais) or len(grupos) == len(pais):
        return
    filhos = [""] * len(pais)
    for x in folhas:
        j = bisect(limites, x.x)
        if j != bisect(limites, x.x + x.width) or filhos[j]:
            return
        filhos[j] = x.text.strip()
    if any(bool(filhos[j]) != (fim - esq > 1) for esq, fim in grupos for j in range(esq, fim)):
        return
    linhas[0] = _linha([f"{p} / {s}" if s else p for p, s in zip(pais, filhos, strict=True)])
    del linhas[2]


def _simbolos(linhas, itens, por_texto, tique):
    """Só troca a ordem base/expoente quando ela está invertida literalmente na célula e a
    geometria a comprova. A letra ASCII de fonte simbólica tem codificação ambígua."""
    duvida = False
    for i in range(2, len(linhas)):
        tique()
        cells = _celulas(linhas[i])
        ancoras = por_texto.get(cells[0], ())
        if len(ancoras) != 1:
            continue
        a = ancoras[0]
        ref = itens[a]
        # Somente as células de uma linha curta cuja primeira coluna tem identificação única.
        grupo = []
        for x in itens[a + 1 : a + 33]:
            if abs(ref.y - x.y) > ref.height * 0.55 or x.x < ref.x + ref.width:
                break
            grupo.append(x)
        if len(cells) < 3 or not grupo or grupo[0].text.strip() != cells[1]:
            continue
        if grupo[0].font.split("+")[-1] in {"Symbol", "ZapfDingbats"} and re.fullmatch(r"[!-~]", cells[1]):
            cells[1] += " [símbolo a conferir]"
            duvida = True
        for inicio, (base, exp) in enumerate(zip(grupo, grupo[1:], strict=False)):
            e, v = exp.text.strip(), base.text.strip()
            if not (re.fullmatch(r"[+−–-]?\d+", e) and re.fullmatch(r"\w+", v)):
                continue
            if not (
                0 < exp.height < 0.85 * base.height
                and 0.2 * base.height < exp.y - base.y < base.height
                and abs(exp.x - base.x - base.width) < base.height / 3
            ):
                continue
            # Os outros itens da célula precisam corresponder também, sem cruzar outras colunas.
            resto = " ".join(x.text.strip() for x in grupo[inicio + 2 :])
            invertida = f"{e} {v} {resto}".strip()
            if cells[-1] == invertida:
                cells[-1] = f"{v}<sup>{e}</sup> {resto}".strip()
        linhas[i] = _linha(cells)
    return duvida


_FRAGMENTO = re.compile(r"(?:<sup>[+−–-]?\d+</sup>|\([\w]+\))(?:\s+(?:<sup>[+−–-]?\d+</sup>|\([\w]+\)))*")


def _itens_da_tabela(linhas, fragmentos, itens, por_texto, *, por_linha=False):
    primeira = _celulas(linhas[0])
    pos = por_texto.get(primeira[0], ())
    if len(pos) != 1:
        return []
    grupos = [" ".join(_celulas(ln)) for j, ln in enumerate(linhas) if j != 1]
    if por_linha:
        # O rótulo de abertura precisa ser distinto de uma unidade, ano ou número repetido nos gráficos.
        if any(primeira[1:]) or len(set(re.findall(r"[^\W\d_]{2,}", primeira[0].casefold()))) < 2:
            return []
    else:
        # Ao reconstruir expoentes, os fragmentos soltos podem pertencer a qualquer linha.
        grupos = [" ".join(grupos + fragmentos)]
    i = pos[0]
    for texto in grupos:
        falta = Counter(_tokens(re.sub(r"</?su[bp]>", " ", texto)))
        restantes = falta.total()
        while restantes:
            if i == len(itens):
                return []
            tokens = Counter(_tokens(itens[i].text))
            if not tokens or any(n > falta[t] for t, n in tokens.items()):
                return []
            falta.subtract(tokens)
            restantes -= tokens.total()
            i += 1
    return itens[pos[0] : i]


def _caixa(itens):
    return [
        min(v.x for v in itens),
        min(v.y for v in itens),
        max(v.x + v.width for v in itens),
        max(v.y + v.height for v in itens),
    ]


def _caixa_do_bloco(usados, itens, marcas):
    """Só delimita um bloco contínuo, com fim visível. Um cabeçalho reconhecido como tabela
    separada não basta se as linhas de dados ou a nota continuam logo abaixo dele."""
    if not usados or any(x.rotation or x.render_mode != 0 for x in usados):
        return None
    ordenados = sorted(usados, key=lambda x: x.y)
    anterior = ordenados[0]
    topo = anterior.y + anterior.height
    for x in ordenados[1:]:
        if x.y - topo > 2 * max(anterior.height, x.height):
            return None
        if x.y + x.height > topo:
            anterior, topo = x, x.y + x.height
    caixa = _caixa(usados)
    esquerda, baixo, direita, alto = caixa
    # A faixa textual pode cortar uma marca gráfica da própria linha, por exemplo um quadrado de cor.
    # Retém o objeto inteiro. Se ele parece pertencer a outro quadro, a localização fica em aberto.
    altura_linha = max(x.height for x in usados)
    alcance = 4 * altura_linha
    for x0, y0, x1, y1 in marcas:
        if y1 < baixo - altura_linha or y0 > alto + altura_linha:
            continue
        if x0 > direita + alcance or x1 < esquerda - alcance or y0 < baixo - alcance or y1 > alto + alcance:
            return None
        caixa[0] = min(caixa[0], x0)
        caixa[1] = min(caixa[1], y0)
        caixa[2] = max(caixa[2], x1)
        caixa[3] = max(caixa[3], y1)
    for x in itens:
        folga = baixo - (x.y + x.height)
        if x.y < baixo and folga <= 2 * ordenados[0].height and x.x < caixa[2] and x.x + x.width > caixa[0]:
            return None
    return caixa


def _reunir_linhas(itens, primeira):
    """Continuação sem cabeçalho: a primeira linha dá as colunas. Um sobrescrito deve
    tocar uma única base, e cada nova célula deve começar no alinhamento da sua coluna."""
    altura = itens[0].height
    bases = [x for x in itens if abs(x.height - altura) < 0.1 * altura]
    sobrescritos = {}
    for x in itens:
        if abs(x.height - altura) < 0.1 * altura:
            continue
        if not (0.4 * altura < x.height < 0.85 * altura and re.fullmatch(r"(?:[−–+-]?\d+|\(\w+\))", x.text.strip())):
            return
        pares = [
            v for v in bases if 0.2 * altura < x.y - v.y < 0.75 * altura and abs(x.x - v.x - v.width) < 0.4 * altura
        ]
        if len(pares) != 1 or id(pares[0]) in sobrescritos:
            return
        sobrescritos[id(pares[0])] = "<sup>" + x.text.strip() + "</sup>"
    if not sobrescritos:
        return
    fisicas = []
    for x in sorted(bases, key=lambda x: (-x.y, x.x)):
        if not fisicas or fisicas[-1][0].y - x.y > altura * 0.15:
            fisicas.append([])
        fisicas[-1].append(x)
    saida, colunas = [], []
    for itens_linha in fisicas:
        grupos = []
        for x in sorted(itens_linha, key=lambda x: x.x):
            inicia_coluna = any(abs(v - x.x) < altura / 2 for v in colunas)
            if not grupos or inicia_coluna or x.x - (grupos[-1][-1].x + grupos[-1][-1].width) > altura * 1.5:
                grupos.append([])
            grupos[-1].append(x)
        if not colunas:
            if [" ".join(x.text.strip() for x in g) for g in grupos] != primeira:
                return
            colunas = [g[0].x for g in grupos]
        celulas = [""] * len(colunas)
        for g in grupos:
            js = [j for j, x in enumerate(colunas) if abs(x - g[0].x) < altura / 2]
            if len(js) != 1 or celulas[js[0]]:
                return
            celulas[js[0]] = " ".join(x.text.strip() + sobrescritos.get(id(x), "") for x in g)
        if len(grupos) == 1:
            j = js[0]
            if not saida or not saida[-1][j].endswith(","):
                return
            saida[-1][j] += " " + celulas[j]
        else:
            saida.append(celulas)
    return [_linha(saida[0]), _linha(["---"] * len(colunas)), *map(_linha, saida[1:])]


def _expoentes_soltos(blocos, k, linhas, itens, por_texto, tb):
    a = b = k
    while a > 0 and blocos[a - 1] and _FRAGMENTO.fullmatch(blocos[a - 1].strip()):
        a -= 1
    while b + 1 < len(blocos) and blocos[b + 1] and _FRAGMENTO.fullmatch(blocos[b + 1].strip()):
        b += 1
    fragmentos = blocos[a:k] + blocos[k + 1 : b + 1]
    if not any("<sup>" in s for s in fragmentos):
        return
    tb["complexa"] = True
    usados = _itens_da_tabela(linhas, fragmentos, itens, por_texto)
    if not usados:
        tb["md"] += "\n\n[Correspondência entre linhas e expoentes a conferir.]"
        return
    for j in range(a, b + 1):
        if j != k:
            blocos[j] = None
    novas = _reunir_linhas(usados, _celulas(linhas[0])) if len(usados) <= 256 else None
    if novas:
        tb["md"] = "\n".join(novas)
        tb["complexa"] = False
    else:
        tb["caixa"] = _caixa(usados)
        tb["md"] += "\n\n" + "\n\n".join(fragmentos) + "\n\n[Correspondência entre linhas e expoentes a conferir.]"


def conferir_tabelas(md, tabelas, itens, filetes, tique, marcas=()):
    # A conferência geométrica é opcional, restrita a páginas de tamanho comum (poucos milhares de
    # itens/filetes). O leitor geral e seus limites continuam valendo para plantas e grades enormes.
    if not tabelas or len(itens) > 2048 or len(filetes) > 2048:
        return md
    if sum(len(x.text) for x in itens) > 128_000:
        return md
    conferir_conteudo = all(not x.rotation and x.render_mode == 0 for x in itens)
    por_texto = defaultdict(list)
    for i, x in enumerate(itens):
        por_texto[x.text.strip()].append(i)
    blocos = md.split("\n\n")
    posicoes = defaultdict(list)
    for k, bloco in enumerate(blocos):
        posicoes[bloco.strip()].append(k)
    for n, tb in enumerate(tabelas):
        tique()
        if len(tb["md"]) > 128_000:
            continue
        linhas = tb["md"].splitlines()
        if not linhas or len(linhas) > 256:
            continue
        # Localizar a tabela só exige geometria confiável dos seus próprios itens. Uma anotação de link
        # em outra parte da página pode não ter modo de renderização.
        if tb["complexa"] and len(marcas) <= 2048:
            usados = _itens_da_tabela(linhas, [], itens, por_texto, por_linha=True)
            if caixa := _caixa_do_bloco(usados, itens, marcas):
                tb["caixa"] = caixa
        if not conferir_conteudo:
            continue
        _cabecalho(linhas, itens, filetes)
        originais = linhas.copy()
        if _simbolos(linhas, itens, por_texto, tique):
            if usados := _itens_da_tabela(originais, [], itens, por_texto):
                tb["caixa"] = _caixa(usados)
            tb["complexa"] = True
        tb["md"] = "\n".join(linhas)
        marcas = posicoes.get("{{TABELA:" + str(n) + "}}", ())
        if len(marcas) == 1:
            _expoentes_soltos(blocos, marcas[0], linhas, itens, por_texto, tb)
    return "\n\n".join(b for b in blocos if b is not None)
