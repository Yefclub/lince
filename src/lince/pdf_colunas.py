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
LINHAS_DE_PROSA_MIN = 3  # linhas da "tabela" com prosa nos dois lados para ela ser duas colunas de texto
ITENS_POR_LADO_MIN = 6
ITENS_MAX = 1500  # a remontagem compara cada item com as linhas de largura total
TITULO_PRINCIPAL = 1.5  # o título com este tanto do corpo do texto é o da página (#); abaixo disso, de seção (##)
_ITEM_DE_LISTA = re.compile(r"(\d{1,3}(\.\d{1,3}){0,3}[.)]|[a-z][.)]|[-•–▪◦])\s")  # "3.2.", "a)", "•": item novo
CALHA_MIN = 5  # pontos: espaço em branco entre as colunas


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


def fundidas(md, itens, W) -> bool:
    """O extrator emendou linhas das duas colunas: o texto traz, em sequência, uma linha de parágrafo da coluna da
    esquerda e a linha da direita que está na mesma altura, ao menos duas vezes."""
    itens = [x for x in itens if x.text.strip() and not x.text.startswith("[Image:") and not x.rotation and x.width > 0]
    if not 2 * ITENS_POR_LADO_MIN <= len(itens) <= ITENS_MAX or (calha := _calha(itens, W)) is None:
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
            if par is None or min(_prosa(par.text), _prosa(x.text)) < PALAVRAS_DE_PROSA:
                continue  # dois valores curtos na mesma altura são células de uma tabela, não linhas de parágrafo
            if f"{' '.join(par.text.split())} {' '.join(x.text.split())}" in plano:
                n += 1
                if n >= 2:
                    return True
    return False


def _calha(itens, W):
    """A faixa vertical em branco no meio da página, entre as duas colunas: (esquerda, direita) ou None. Procura o
    intervalo sem nenhum item estreito (os de largura total, que atravessam o meio, não contam)."""
    estreitos = [x for x in itens if x.width < 0.55 * W]
    bordas = sorted((x.x, x.x + x.width) for x in estreitos)
    melhor, fim = None, None
    for l, r in bordas:
        if fim is not None and l - fim >= CALHA_MIN and 0.3 * W <= (fim + l) / 2 <= 0.7 * W:
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
    itens = [x for x in itens if x.text.strip() and not x.text.startswith("[Image:") and not x.rotation and x.width > 0]
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
    if _linhas_de_tabela(esquerda, direita, corpo):
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
