"""Página em duas colunas que o extrator leu como tabela: refaz a ordem de leitura pela posição dos itens.

O pdf_inspector lê a maioria das páginas em colunas na ordem certa. Quando há um bloco de largura total no topo, uma
figura ou uma tabela dentro de uma coluna, ele pode casar as linhas das duas colunas pela altura e entregar uma
"tabela" de prosa ("| linha da coluna 1 | linha da coluna 2 |"), com os títulos das duas colunas fundidos. Aí, e só
aí, a página é remontada pelos itens posicionados: o que atravessa a calha entre as colunas é bloco de largura total
e, entre dois blocos assim, lê-se a coluna da esquerda inteira e depois a da direita (o corte XY clássico, com a
calha já conhecida). O texto é o dos itens, que já passaram pela conferência de texto oculto."""

import re
from collections import Counter

PALAVRAS_DE_PROSA = 5  # célula com isto ou mais de palavras é linha de parágrafo, não valor de tabela
PALAVRAS_DE_LINHA_FUNDIDA = 4  # a linha da coluna lateral estreita tem umas quatro palavras ("aumentou em 1.316 mil
# pessoas"); a emenda das duas linhas, ao menos duas vezes no texto do extrator, é que prova a fusão
LINHAS_DE_PROSA_MIN = 3  # linhas da "tabela" com prosa nos dois lados para ela ser duas colunas de texto
ITENS_POR_LADO_MIN = 6
ITENS_MAX = 1500  # a remontagem compara cada item com as linhas de largura total
TITULO_PRINCIPAL = 1.5  # o título com este tanto do corpo do texto é o da página (#); abaixo disso, de seção (##)
_ITEM_DE_LISTA = re.compile(r"(\d{1,3}(\.\d{1,3}){0,3}[.)]|[a-z][.)]|[-•–▪◦])\s")  # "3.2.", "a)", "•": item novo
CALHA_MIN = 5  # pontos: espaço em branco entre as colunas
CALHA_LIMITE = 0.2  # fração da largura: a calha fica entre isso e 1 - isso (a coluna lateral tem uns 25% da página)


def _prosa(texto) -> int:
    return len(re.findall(r"[^\W\d_]{2,}", texto))


def confundidas(tabelas) -> bool:
    """A página tem colunas de texto lidas como tabela: linhas com prosa em duas células."""
    n = 0
    for tb in tabelas:
        for linha in tb["md"].splitlines():
            celulas = [c for c in linha.strip().strip("|").split("|") if c.strip()]
            n += sum(_prosa(c) >= PALAVRAS_DE_PROSA for c in celulas) >= 2
    return n >= LINHAS_DE_PROSA_MIN


def fundidas(md, itens, W, filetes=()) -> bool:
    """O extrator emendou linhas das duas colunas: o texto traz, em sequência, uma linha de parágrafo da coluna da
    esquerda e a linha da direita que está na mesma altura, ao menos duas vezes."""
    itens = [x for x in itens if x.text.strip() and not x.text.startswith("[Image:") and not x.rotation and x.width > 0]
    if not 2 * ITENS_POR_LADO_MIN <= len(itens) <= ITENS_MAX:
        return False
    if _vizinhas_fundidas(md, itens, filetes):
        return True
    if (calha := _calha(itens, W)) is None:
        return False
    a, b = calha
    plano = " ".join(" ".join(ln for ln in md.splitlines() if not ln.lstrip().startswith("|")).split())
    esquerda = {}
    for x in itens:
        if x.x + x.width <= a + 1:
            esquerda[round(x.y)] = x
    n = 0
    for x in itens:
        if x.x >= b - 1:
            par = next((esquerda[y] for y in (round(x.y), round(x.y) - 1, round(x.y) + 1) if y in esquerda), None)
            if par is None or min(_prosa(par.text), _prosa(x.text)) < PALAVRAS_DE_LINHA_FUNDIDA:
                continue  # dois valores curtos na mesma altura são células de uma tabela, não linhas de parágrafo
            if _entre_celulas(par, x, filetes):
                continue
            if f"{' '.join(par.text.split())} {' '.join(x.text.split())}" in plano:
                n += 1
                if n >= 2:
                    return True
    return False


def _normal(texto):
    return " ".join(re.findall(r"\w+", texto.lower()))


def _entre_celulas(p, q, filetes) -> bool:
    """Um traço vertical entre os dois itens, na altura deles: são células de uma tabela com grade, não colunas."""
    meio_y = p.y + p.height / 2
    return any(p.x + p.width <= f[0] and f[2] <= q.x and f[1] <= meio_y <= f[3] for f in filetes)


def _vizinhas_fundidas(md, itens, filetes=()) -> bool:
    """O extrator emendou, na mesma linha do texto, linhas de parágrafo de duas colunas vizinhas quaisquer (a faixa de
    avisos lado a lado, com quatro colunas): ao menos duas vezes, a linha de uma coluna seguida da linha da coluna ao
    lado, na mesma altura e separadas por uma calha. Comparado sem a marcação (o negrito do começo do aviso). Duas
    células de uma tabela com grade, separadas por um traço vertical (`filetes`), não contam."""
    plano = _normal(" ".join(ln for ln in md.splitlines() if not ln.lstrip().startswith("|")))  # a tabela é do extrator
    n = 0
    for ln in _linhas(itens):
        for p, q in zip(ln["itens"], ln["itens"][1:], strict=False):
            if q.x - (p.x + p.width) < 2 * CALHA_MIN:
                continue
            if min(_prosa(p.text), _prosa(q.text)) < PALAVRAS_DE_LINHA_FUNDIDA:
                continue
            if _entre_celulas(p, q, filetes):
                continue
            if f"{_normal(p.text)} {_normal(q.text)}" in plano:
                n += 1
                if n >= 2:
                    return True
    return False


def _calha(itens, W):
    """A faixa vertical em branco entre as duas colunas: (esquerda, direita) ou None. Procura o intervalo sem nenhum
    item estreito de texto (os de largura total, que atravessam o meio, não contam; nem os números e os rótulos girados
    de um gráfico, que podem encostar na calha). A calha pode estar fora do meio: a coluna lateral estreita, ao lado
    do corpo, também é coluna."""
    estreitos = [x for x in itens if x.width < 0.55 * W and not x.rotation and _prosa(x.text) >= 2]
    bordas = sorted((x.x, x.x + x.width) for x in estreitos)
    melhor, fim = None, None
    for l, r in bordas:
        if fim is not None and l - fim >= CALHA_MIN and CALHA_LIMITE * W <= (fim + l) / 2 <= (1 - CALHA_LIMITE) * W:
            if melhor is None or l - fim > melhor[1] - melhor[0]:
                melhor = (fim, l)
        fim = r if fim is None else max(fim, r)
    if melhor is None:
        return None
    a, b = melhor
    lados = (sum(x.x + x.width <= a + 1 for x in estreitos), sum(x.x >= b - 1 for x in estreitos))
    return melhor if min(lados) >= ITENS_POR_LADO_MIN else None


def _linhas(itens):
    """Itens na mesma altura formam uma linha (o índice e o expoente, de corpo menor, entram na linha da base)."""
    linhas = []
    for x in sorted(itens, key=lambda x: (-(x.y + x.height / 2), x.x)):
        meio = x.y + x.height / 2
        for ln in reversed(linhas[-3:]):
            if ln["b"] <= meio <= ln["t"]:
                ln["itens"].append(x)
                break
        else:
            linhas.append({"b": x.y - 0.15 * x.height, "t": x.y + x.height, "itens": [x]})
    for ln in linhas:
        ln["itens"].sort(key=lambda x: x.x)
    return linhas


def _texto(itens):
    misto = not all(x.is_bold for x in itens)  # o negrito de um trecho da linha fica; o da linha toda é título
    partes = []
    for k, x in enumerate(itens):
        t = " ".join(x.text.split())
        if not t:
            continue
        if misto and x.is_bold:
            t = f"**{t}**"
        if (
            k
            and partes
            and x.x - (itens[k - 1].x + itens[k - 1].width) < 0.15 * x.font_size
            and x.font_size < itens[k - 1].font_size
        ):
            partes[-1] += t  # índice ou expoente colado na base
        else:
            partes.append(t)
    return " ".join(partes).replace("** **", " ")


def _celulas(ln, corpo):
    """Os grupos de itens de uma linha separados por um espaço largo: as células, se a linha for de tabela."""
    grupos = [[ln["itens"][0]]]
    for x in ln["itens"][1:]:
        ant = grupos[-1][-1]
        (grupos.append([x]) if x.x - (ant.x + ant.width) > 1.2 * corpo else grupos[-1].append(x))
    return grupos


def blocos_de_texto(itens, corpo):
    """Markdown de uma coluna (ou de uma faixa de largura total): títulos, parágrafos e linhas de tabela."""
    out, paragrafo, tabela, titulo, anterior = [], [], [], [], None

    def fecha():
        if paragrafo:
            out.append(" ".join(paragrafo))
            paragrafo.clear()
        if len(tabela) >= 2:
            out.append("\n".join(["| " + " | ".join(tabela[0]) + " |", "|" + " --- |" * len(tabela[0])]
                                 + ["| " + " | ".join(r) + " |" for r in tabela[1:]]))  # fmt: skip
        else:
            out.extend(" ".join(r) for r in tabela)
        tabela.clear()

    for ln in _linhas(itens):
        texto = _texto(ln["itens"])
        if not texto:
            continue
        tamanho = max(x.font_size for x in ln["itens"])
        negrito = all(x.is_bold for x in ln["itens"])
        celulas = _celulas(ln, corpo)
        distante = anterior is not None and anterior["b"] - ln["t"] > 0.9 * corpo
        # linhas mais juntas que o corpo da letra são partes de uma fórmula empilhada (numerador, barra, denominador)
        base = min(x.y for x in ln["itens"])
        empilhada = anterior is not None and anterior["base"] - base < 0.95 * max(tamanho, anterior["tam"])
        if empilhada and len(tabela) == 1:
            paragrafo.append(" ".join(tabela.pop()))
        if empilhada and paragrafo:
            paragrafo.append(texto)
        elif titulo and negrito and abs(tamanho - anterior["tam"]) <= 0.6 and anterior["base"] - base <= 1.6 * tamanho:
            out.append(out.pop() + " " + texto)  # o título que quebrou em duas linhas
        elif len(celulas) >= 3 and all(len(_texto(g)) <= 40 for g in celulas):
            titulo.clear()
            if paragrafo or (tabela and len(tabela[0]) != len(celulas)):
                fecha()
            tabela.append([_texto(g).replace("|", "\\|") for g in celulas])
        elif negrito and tamanho >= 1.15 * corpo and len(texto) <= 120:
            fecha()
            out.append(("# " if tamanho >= TITULO_PRINCIPAL * corpo else "## ") + texto)
            titulo[:] = [texto]
        else:
            titulo.clear()
            item = _ITEM_DE_LISTA.match(texto) and paragrafo and paragrafo[-1].rstrip().endswith((".", ";", ":"))
            if tabela or distante or item or (anterior is not None and abs(tamanho - anterior["tam"]) > 0.6):
                fecha()
            paragrafo.append(texto)
        anterior = {"b": ln["b"], "t": ln["t"], "tam": tamanho, "base": base}
    fecha()
    return out


def _corrido(linhas) -> bool:
    """Texto corrido: a maioria das linhas termina no meio da frase. As células de uma tabela de perguntas e respostas
    são unidades fechadas ("?" e "."), mesmo quando quebram em várias linhas e não há espaço entre uma e outra."""
    textos = [_texto(ln["itens"]) for ln in linhas]
    textos = [t for t in textos if _prosa(t) >= 3]
    abertas = sum(not t.rstrip("*").endswith((".", "!", "?", ":", ";")) for t in textos)
    return len(textos) >= 4 and abertas >= 0.6 * len(textos)


def _inicios(linhas, corpo):
    """A altura de cada começo de bloco: a primeira linha e as que vêm depois de um espaço maior que o da entrelinha."""
    return [ln["t"] for k, ln in enumerate(linhas) if not k or linhas[k - 1]["b"] - ln["t"] > 0.5 * corpo]


def _linhas_de_tabela(esquerda, direita, corpo) -> bool:
    """Os blocos dos dois lados começam na mesma altura: são as linhas de uma tabela de duas colunas de texto ("Risco |
    Mitigação"), e ler um lado inteiro antes do outro separaria cada linha do seu par."""
    a, b = _inicios(esquerda, corpo), _inicios(direita, corpo)
    if min(len(a), len(b)) < 3:
        return False
    pares = sum(any(abs(y - v) <= 2 for v in b) for y in a)
    return pares >= 0.7 * max(len(a), len(b))


def refazer(md, itens, W, tique):
    """Markdown da página na ordem de leitura das duas colunas, ou None se a geometria não confirma duas colunas de
    texto (aí a saída do extrator fica como está). Nada some: se alguma letra do texto do extrator não estiver na
    página remontada (texto girado na margem, item sem largura, o endereço de um link), a remontagem é recusada."""
    # o item girado (o rótulo do eixo de um gráfico) entra pela posição, como os outros: fora, faltaria na remontagem
    itens = [x for x in itens if x.text.strip() and not x.text.startswith("[Image:") and x.width > 0]
    if not 2 * ITENS_POR_LADO_MIN <= len(itens) <= ITENS_MAX or any(x.render_mode != 0 for x in itens):
        return None
    if (calha := _calha(itens, W)) is None:
        return None
    a, b = calha
    pesos = Counter()
    for x in itens:
        pesos[round(x.font_size * 2) / 2] += len(x.text)
    corpo = pesos.most_common(1)[0][0]
    esquerda = _linhas([x for x in itens if x.x + x.width <= a + 1])
    direita = _linhas([x for x in itens if x.x >= b - 1])
    if not (_corrido(esquerda) and _corrido(direita)) or _linhas_de_tabela(esquerda, direita, corpo):
        return None
    inteiros = sorted((x for x in itens if x.x < a - 1 and x.x + x.width > b + 1), key=lambda x: -x.y)
    novo = _intercalar(itens, inteiros, a, b, corpo, tique)
    # letra por letra, e não palavra por palavra: o extrator separa o índice da base ("C" e "0") e a remontagem junta
    if novo is None or _letras(md) - _letras(novo):
        return None
    return novo


def _letras(md) -> Counter:
    """As letras e os algarismos do texto, sem a marcação do Markdown e do HTML embutido: o que duas montagens da
    mesma página precisam ter em comum, qualquer que seja a ordem e o corte das palavras."""
    return Counter(ch for ch in re.sub(r"</?(su[bp]|u|br)\s*/?>", " ", md).lower() if ch.isalnum())


def _intercalar(itens, inteiros, a, b, corpo, tique):
    """Percorre a página de cima para baixo: cada grupo de linhas de largura total é um bloco; entre dois grupos, as
    duas colunas, uma depois da outra."""
    meio = (a + b) / 2
    ids = {id(x) for x in inteiros}
    resto = [x for x in itens if id(x) not in ids]
    # também é de largura total o item estreito na altura de uma linha assim ("Resumo." em negrito no começo dela) e
    # a continuação curta dela: a última linha do título ou do parágrafo, logo abaixo, no mesmo corpo e na mesma margem
    # de cima para baixo, numa passada: a continuação está sempre abaixo da linha que ela continua
    cheios = list(inteiros)
    for x in sorted(resto, key=lambda x: -x.y):
        tique()
        meio_y = x.y + x.height / 2
        for v in cheios:
            na_linha = v.y - 0.2 * v.height <= meio_y <= v.y + v.height
            continua = (
                abs(v.font_size - x.font_size) <= 0.6 and 0 < v.y - x.y <= 1.6 * x.font_size and abs(v.x - x.x) <= 2
            )
            if na_linha or continua:
                cheios.append(x)
                break
    ids = {id(x) for x in cheios}
    resto = [x for x in resto if id(x) not in ids]
    cheios.sort(key=lambda x: -x.y)
    grupos = []  # linhas de largura total seguidas, sem nada de coluna entre elas
    for x in cheios:
        if grupos and not any(x.y + x.height <= v.y + v.height / 2 <= grupos[-1][-1].y for v in resto):
            grupos[-1].append(x)
        else:
            grupos.append([x])
    out, teto, usados = [], float("inf"), sum(map(len, grupos))
    for grupo in [*grupos, None]:
        piso = max(x.y + x.height for x in grupo) if grupo else float("-inf")
        faixa = [x for x in resto if piso <= x.y + x.height / 2 < teto]
        usados += len(faixa)
        out += blocos_de_texto([x for x in faixa if x.x + x.width / 2 < meio], corpo)
        out += blocos_de_texto([x for x in faixa if x.x + x.width / 2 >= meio], corpo)
        if grupo:
            out += blocos_de_texto(grupo, corpo)
            teto = min(x.y for x in grupo)
    # todo item entra uma vez: se a conta não fecha, a geometria enganou e a saída do extrator fica como está
    return "\n\n".join(out) if usados == len(itens) else None


XY_PROFUNDIDADE = 8  # cortes um dentro do outro: faixas da página, colunas da faixa, faixas da coluna
ALINHADAS_DE_TABELA = 0.7  # fração das linhas de cada coluna na altura de uma linha da vizinha numa tabela
FAIXA_VAO = 1.5  # corpos de letra: espaço entre duas linhas que separa duas faixas da página


def _vaos(intervalos, minimo):
    """Os espaços em branco entre os intervalos (esquerda, direita) juntos, com ao menos `minimo` de largura."""
    vaos, fim = [], None
    for l, r in sorted(intervalos):
        if fim is not None and l - fim >= minimo:
            vaos.append((fim, l))
        fim = r if fim is None else max(fim, r)
    return vaos


def _em_colunas(itens, corpo, tique, prof, cortes_aceitos):
    """Os blocos de um trecho da página na ordem de leitura, por cortes recursivos (o corte XY): as calhas do trecho
    são os vãos verticais entre os itens estreitos de texto; o que atravessa uma calha é bloco de largura total e,
    entre dois deles, lê-se cada coluna inteira, da esquerda para a direita, com o mesmo corte dentro dela. A coluna
    só é aceita se for texto corrido; senão (células de tabela, rótulos) o trecho fica na ordem das linhas."""
    tique()
    l0, r0 = min(x.x for x in itens), max(x.x + x.width for x in itens)
    estreitos = [x for x in itens if x.width < 0.55 * (r0 - l0) and not x.rotation and _prosa(x.text) >= 2]
    if prof >= XY_PROFUNDIDADE:
        return blocos_de_texto(itens, corpo)
    vaos = _vaos([(x.x, x.x + x.width) for x in estreitos], CALHA_MIN)
    if not vaos:  # só uma coluna de texto: a outra pode ser só de números (os rótulos de um gráfico ao lado)
        largos = [x for x in itens if x.width < 0.55 * (r0 - l0) and not x.rotation]
        vaos = _vaos([(x.x, x.x + x.width) for x in largos], CALHA_MIN)
    # as faixas separadas por um espaço maior que uma linha (a faixa de avisos embaixo do mapa); o corte é o do vão
    # maior, como no corte XY clássico: a faixa larga corta antes da calha estreita que só existe numa parte dela
    linhas = _linhas(itens)
    entre = [(anterior["b"] - ln["t"], k) for k, (anterior, ln) in enumerate(zip(linhas, linhas[1:], strict=False), 1)]
    horizontais = [k for vao, k in entre if vao > FAIXA_VAO * corpo]
    maior_h = max((vao for vao, _ in entre), default=0)
    if horizontais and (not vaos or maior_h > max(b - a for a, b in vaos)):
        faixas = [
            [x for ln in linhas[a:b] for x in ln["itens"]]
            for a, b in zip([0, *horizontais], [*horizontais, len(linhas)], strict=True)
        ]
        return [bloco for faixa in faixas for bloco in _em_colunas(faixa, corpo, tique, prof + 1, cortes_aceitos)]
    if not vaos:
        return blocos_de_texto(itens, corpo)
    cheios = [x for x in itens if any(x.x < a - 1 and x.x + x.width > b + 1 for a, b in vaos)]
    for x in itens:  # o item na altura de uma linha que atravessa a calha é dessa linha
        meio_y = x.y + x.height / 2
        if x not in cheios and any(v.y - 0.2 * v.height <= meio_y <= v.y + v.height for v in cheios):
            cheios.append(x)
    ids = {id(x) for x in cheios}
    resto = [x for x in itens if id(x) not in ids]
    cortes = [(a + b) / 2 for a, b in vaos]
    colunas_por_faixa, grupos = [], []
    for x in sorted(cheios, key=lambda x: -x.y):
        if grupos and not any(x.y + x.height <= v.y + v.height / 2 <= grupos[-1][-1].y for v in resto):
            grupos[-1].append(x)
        else:
            grupos.append([x])
    teto = float("inf")
    for grupo in [*grupos, None]:
        piso = max(x.y + x.height for x in grupo) if grupo else float("-inf")
        faixa = [x for x in resto if piso <= x.y + x.height / 2 < teto]
        colunas = [[] for _ in range(len(cortes) + 1)]
        for x in faixa:
            colunas[sum(x.x + x.width / 2 >= c for c in cortes)].append(x)
        colunas = [c for c in colunas if c]
        colunas_por_faixa.append((colunas, grupo))
        if grupo:
            teto = min(x.y for x in grupo)
    # cada coluna é texto corrido, e duas vizinhas não começam os blocos na mesma altura (as linhas de uma tabela de
    # texto, "Risco | Mitigação", que ler coluna por coluna separaria do par)
    if not all(
        len(colunas) < 2
        or (
            all(_corrido(_linhas(c)) or _linhas_de_prosa(c) <= 3 for c in colunas)
            and not any(_tabela_entre(e, d, corpo) for e, d in zip(colunas, colunas[1:], strict=False))
        )
        for colunas, _ in colunas_por_faixa
    ):
        return blocos_de_texto(itens, corpo)
    out = []
    for colunas, grupo in colunas_por_faixa:
        if len(colunas) > 1:
            cortes_aceitos.append(prof)
        for c in colunas:
            out += (
                _em_colunas(c, corpo, tique, prof + 1, cortes_aceitos)
                if len(colunas) > 1
                else blocos_de_texto(c, corpo)
            )
        if grupo:
            out += blocos_de_texto(grupo, corpo)
    return out


def _tabela_entre(esquerda, direita, corpo) -> bool:
    """Duas colunas vizinhas que são colunas de uma tabela: os blocos de prosa começam na mesma altura dos dois lados
    ("Risco | Mitigação"), ou uma delas não é prosa (valores) e as linhas dela estão na altura das linhas da outra. A
    coluna de números fora do alinhamento é o eixo de um gráfico ao lado do texto, e se separa."""
    a, b = _linhas(esquerda), _linhas(direita)
    if _linhas_de_tabela(a, b, corpo):
        return True
    if all(2 * _linhas_de_prosa(c) >= len(ln) for c, ln in ((esquerda, a), (direita, b))):
        return False
    # as linhas de cada lado na altura de uma linha do outro (as duas contas: rótulos densos de um gráfico encostam
    # em quase toda linha de uma caixa de texto ao lado, mas a maioria deles fica sem par)
    return all(
        sum(any(abs(ln["t"] - v["t"]) <= 2 for v in outro) for ln in uma) >= ALINHADAS_DE_TABELA * len(uma)
        for uma, outro in ((a, b), (b, a))
    )


def _linhas_de_prosa(itens) -> int:
    """Linhas com prosa (três palavras ou mais): a coluna com tão poucas não dá para julgar se é texto corrido."""
    return sum(_prosa(_texto(ln["itens"])) >= 3 for ln in _linhas(itens))


def refazer_em_colunas(md, itens, W, tique):
    """O mesmo que `refazer`, para mais de duas colunas (a faixa de avisos lado a lado, a coluna lateral com caixas
    ao lado do corpo): o corte XY recursivo. Nada some: se alguma letra do extrator faltar, None."""
    itens = [x for x in itens if x.text.strip() and not x.text.startswith("[Image:") and x.width > 0]
    if not 2 * ITENS_POR_LADO_MIN <= len(itens) <= ITENS_MAX or any(x.render_mode != 0 for x in itens):
        return None
    pesos = Counter()
    for x in itens:
        pesos[round(x.font_size * 2) / 2] += len(x.text)
    cortes_aceitos = []
    novo = "\n\n".join(_em_colunas(itens, pesos.most_common(1)[0][0], tique, 0, cortes_aceitos))
    # sem nenhuma coluna aceita, não há ordem a corrigir: a saída do extrator fica (com as tabelas que ele achou)
    if not cortes_aceitos or _letras(md) - _letras(novo):
        return None
    return novo
