"""Reassocia blocos Markdown de tabelas quando os filetes e a camada de texto confirmam a leitura."""

import re
from bisect import bisect
from collections import Counter

from .pdf_tabelas import _caixa, _celulas, _linha

_TABELA = re.compile(r"\{\{TABELA:(\d{1,6})\}\}")
_TITULO = re.compile(r"(?:Tabela|Table)\s+\d+[.\d]*\s*\W", re.I)
_GRAFICO = re.compile(r"\|\s*(?:Gráfico|Figure)\s+\d+", re.I)
_NUMERO = re.compile(r"(?:[+−-]?\d+(?:[.,]\d+)*\s*%?|[-–—])")
_ANO = re.compile(r"(?:19|20)\d{2}")


def _conteudo(texto):
    texto = re.sub(r"\[([^\]\n]+)\]\([^\n)]*\)", r"\1", texto)
    texto = re.sub(r"</?(?:u|su[bp])>", "", texto)
    return Counter(re.sub(r"[\s*_|]", "", texto).casefold())


def _texto(itens):
    return " ".join(x.text.strip() for x in itens)


def _linhas(tb):
    linhas = tb["md"].splitlines()
    return linhas[:1] + linhas[2:]


def texto_de_rotulos(tb):
    return "\n\n".join(" ".join(c for c in _celulas(ln) if c) for ln in _linhas(tb))


def _fisicas(itens, tolerancia=1.25):
    linhas = []
    for x in sorted(itens, key=lambda x: -x.y):
        if not linhas or linhas[-1][0].y - x.y > tolerancia:
            linhas.append([])
        linhas[-1].append(x)
    return [sorted(xs, key=lambda x: x.x) for xs in linhas]


def _cortes(valores, tolerancia=1.25):
    cortes = []
    for v in sorted(valores):
        if not cortes or v - cortes[-1] > tolerancia:
            cortes.append(v)
    return cortes


def _grupos_horizontais(marcas):
    grupos = []
    for l, b, r, t in marcas:
        if not (r - l >= 60 and 0 <= t - b <= 2):
            continue
        for l0, r0, ys in grupos:
            if abs(l - l0) < 2 and abs(r - r0) < 2:
                ys.append((b + t) / 2)
                break
        else:
            grupos.append((l, r, [(b + t) / 2]))
    return [(l, r, _cortes(ys)) for l, r, ys in grupos if len(ys) >= 3]


def _grupos_verticais(filetes):
    colunas = []
    for l, b, r, t in sorted(filetes):
        if t - b <= 5:
            continue
        x = (l + r) / 2
        if not colunas or abs(x - colunas[-1][0]) > 1:
            colunas.append((x, []))
        colunas[-1][1].append((b, t))
    for x, ys in colunas:
        unidos = []
        for b, t in sorted(ys):
            if unidos and b <= unidos[-1][1] + 1:
                unidos[-1][1] = max(unidos[-1][1], t)
            else:
                unidos.append([b, t])
        for b, t in unidos:
            yield x, b, t


def _na_faixa(itens, l, b, r, t):
    return [x for x in itens if l - 2 <= x.x <= x.x + x.width <= r + 2 and b <= x.y + x.height / 2 <= t]


def _confirmar(tb, md, caixa, *, estrutura=True):
    tb.update(
        md=md,
        caixa=caixa,
        confirmada=True,
        estrutura_confirmada=estrutura,
        complexa=True,
        palavras=set(re.findall(r"\w+", md.lower())),
    )


def _markdown(rows):
    return "\n".join((_linha(rows[0]), _linha(["---"] * len(rows[0])), *map(_linha, rows[1:])))


def _grade_repetida(blocos, k, tb, itens, horizontais, verticais, ler_celulas):
    linhas = _linhas(tb)
    if k == 0 or len(linhas) < 8 or len(linhas) > 128:
        return
    cab = _conteudo(linhas[0])
    if sum(_conteudo(ln) == cab for ln in linhas) != 2:
        return
    cabecalhos = [xs for xs in _fisicas(itens) if len(xs) >= 4 and _conteudo(_texto(xs)) == cab]
    if len(cabecalhos) != 2 or _TABELA.fullmatch(blocos[k - 1]):
        return
    a, b = [xs[0] for xs in cabecalhos]
    candidatos = []
    for l, r, ys in horizontais:
        if not (l <= a.x <= l + 2 * a.height and l <= b.x <= l + 2 * b.height):
            continue
        superiores = [min((y for y in ys if x.y + x.height / 2 < y), default=float("inf")) for x in (a, b)]
        if any(y - x.y > 4 * x.height for x, y in zip((a, b), superiores, strict=True)):
            continue
        topo_a, topo_b = superiores
        fundos = [max((y for y in ys if limite < y < x.y - x.height), default=0) for x, limite in ((a, topo_b), (b, 0))]
        if all(x.y - y > 8 * x.height and y > 0 for x, y in zip((a, b), fundos, strict=True)):
            candidatos.append((l, r, superiores, fundos))
    if len(candidatos) != 1:
        return
    l, r, topos, fundos = candidatos[0]
    tabelas = []
    usados = []
    for cab_itens, topo, fundo in zip(cabecalhos, topos, fundos, strict=True):
        altura = cab_itens[0].height
        titulos = _na_faixa(itens, l, topo, r, topo + 4 * altura)
        if not titulos or (not tabelas and _conteudo(blocos[k - 1]) != _conteudo(_texto(titulos))):
            return
        corpo = _na_faixa(itens, l, fundo, r, topo)
        fisicas = _fisicas(corpo)
        if not fisicas or fisicas[0] != cab_itens:
            return
        cortes = _cortes(x for x, base, alto in verticais if l < x < r and base < cab_itens[0].y + altura / 2 < alto)
        if not len(cab_itens) - 1 <= len(cortes) <= 31:
            return
        limites = [l, *cortes, r]
        caixa_cab = _caixa(cab_itens)
        caixas = [(a, caixa_cab[1], b, caixa_cab[3]) for a, b in zip(limites, limites[1:], strict=False)]
        cabecalho = ler_celulas(caixas)
        if len(cabecalho) != len(caixas) or any(not c for c in cabecalho) or _conteudo(" ".join(cabecalho)) != cab:
            return
        linhas_grade = [cabecalho]
        for xs in fisicas[1:]:
            celulas = [""] * (len(cortes) + 1)
            for x in xs:
                j = bisect(cortes, x.x)
                if j != bisect(cortes, x.x + x.width) or celulas[j]:
                    return
                celulas[j] = x.text.strip()
            if not celulas[0] or sum(bool(v) for v in celulas) < len(celulas) * 0.65:
                return
            linhas_grade.append(celulas)
        tb_grade = {}
        md = _texto(titulos) + "\n\n" + _markdown(linhas_grade)
        caixa = [l, fundo, r, _caixa(titulos)[3]]
        _confirmar(tb_grade, md, caixa)
        tabelas.append(tb_grade)
        usados.extend(titulos + corpo)
    if _conteudo(blocos[k - 1] + " ".join(linhas)) == _conteudo(_texto(usados)):
        return tabelas


def _cabecalho_numerico(cab, centros, tracos):
    """Uma régua parcial separa o título comum dos subtítulos físicos de cada coluna."""
    saida = [[] for _ in cab]
    for j, itens in enumerate(cab):
        for titulo in itens:
            grupos = set()
            for l, b, r, t in tracos:
                if not (
                    0 <= t - b <= 2
                    and 0 <= titulo.y - t <= titulo.height
                    and l <= titulo.x <= titulo.x + titulo.width <= r
                ):
                    continue
                colunas = tuple(i for i, x in enumerate(centros) if l < x < r)
                if (
                    len(colunas) > 1
                    and j in colunas
                    and all(any(x.y + x.height / 2 < b for x in cab[i]) for i in colunas)
                ):
                    grupos.add(colunas)
            colunas = next(iter(grupos)) if len(grupos) == 1 else (j,)
            for i in colunas:
                saida[i].append((titulo.y, titulo.text.strip()))
    return [" / ".join(t for _, t in sorted(rotulos, key=lambda x: -x[0])) for rotulos in saida]


def _cabecalho_de_anos(cab, centros, tracos, calendario):
    """Cada ano inicia na mesma posição da primeira subcoluna de seu grupo."""
    anos, meses = calendario
    if cab[0] or len(cab) != len(meses) + 1 or any(not 1 <= len(xs) <= 2 for xs in cab[1:]):
        return
    folhas = [xs[-1] for xs in cab[1:]]
    grupos = [(i, xs[0]) for i, xs in enumerate(cab[1:]) if len(xs) == 2]
    if not grupos or grupos[0][0] != 0 or tuple(x.text.strip() for _, x in grupos) != anos:
        return
    if tuple(x.text.strip() for x in folhas) != meses:
        return
    if any(abs(x.x - folhas[i].x) > 1 or x.y <= folhas[i].y + folhas[i].height for i, x in grupos):
        return
    if not any(
        0 <= t - b <= 2
        and centros[0] < l <= folhas[0].x
        and folhas[0].x - l <= folhas[0].height
        and r >= max(folhas[-1].x + folhas[-1].width, centros[-1])
        and max(x.y + x.height for x in folhas) < b <= t <= min(x.y for _, x in grupos)
        for l, b, r, t in tracos
    ):
        return
    rotulos = [""]
    limites = [i for i, _ in grupos[1:]] + [len(meses)]
    for (i, ano), fim in zip(grupos, limites, strict=True):
        rotulos.extend(f"{ano.text.strip()} / {mes}" for mes in meses[i:fim])
    return rotulos


def _tabela_numerica(corpo, topo, fundo, divisor, tracos, calendario):
    """Reordena células apenas quando o corpo tem uma grade estável de valores alinhados."""
    linhas = _fisicas(corpo)
    indice = next((i for i, xs in enumerate(linhas) if xs[0].y + xs[0].height / 2 < divisor), len(linhas))
    dados = linhas[indice:]
    if len(dados) < 3 or len(dados[0]) < 3:
        return
    n = len(dados[0])
    if any(len(xs) != n or any(not _NUMERO.fullmatch(x.text.strip()) for x in xs[1:]) for xs in dados):
        return
    limites = []
    for a, b in zip(range(n - 1), range(1, n), strict=True):
        direita = max(xs[a].x + xs[a].width for xs in dados)
        esquerda = min(xs[b].x for xs in dados)
        if direita >= esquerda:
            return
        limites.append((direita + esquerda) / 2)
    cab = [[] for _ in range(n)]
    for x in (x for xs in linhas[:indice] for x in xs):
        tolerancia = min(0.5, x.width / 4) if tracos else 0
        j = bisect(limites, x.x + tolerancia)
        if j != bisect(limites, x.x + x.width - tolerancia):
            return
        cab[j].append(x)
    if sum(bool(c) for c in cab) < len(cab) - 1:
        return
    centros = [x.x + x.width / 2 for x in dados[0]]
    if calendario:
        cabecalho = _cabecalho_de_anos(cab, centros, tracos, calendario)
        if not cabecalho:
            return
    else:
        cabecalho = _cabecalho_numerico(cab, centros, tracos) if tracos else [_texto(c) for c in cab]
    md = _markdown([cabecalho, *[[x.text.strip() for x in xs] for xs in dados]])
    return "\n\n".join(s for s in (_texto(topo), md, _texto(fundo)) if s)


def _tabela_com_titulo(blocos, k, tb, itens, horizontais, tracos):
    if tb.get("caixa") or not tb["md"]:
        return
    linhas = _linhas(tb)
    valores = [c for c in _celulas(linhas[0]) if c]
    interno = len(valores) == 1 and _TITULO.match(valores[0].lstrip("*# "))
    if interno:
        prefixo, nome = [], valores[0].strip("*# ")
    else:
        partes = blocos[max(k - 2, 0) : k]
        a = next((i for i, s in enumerate(partes) if _TITULO.match(s.lstrip("*# "))), None)
        if a is None:
            return
        prefixo = partes[a:]
        nome = prefixo[0].strip("*# ")
    calendario = reserva = None
    if not tb["complexa"]:
        meses = tuple(c for c in _celulas(linhas[1]) if c) if len(linhas) >= 5 else ()
        if len(valores) < 2 or not all(_ANO.fullmatch(ano) for ano in valores) or len(meses) <= len(valores):
            return
        espera_anos = f"<u>{' '.join(valores)}</u>"
        if not any(espera_anos in s for s in prefixo):
            return
        prefixo = [s.replace(espera_anos, "", 1) for s in prefixo]
        calendario = tuple(valores), meses
        reserva = "[Associação entre anos e colunas a conferir.]\n\n" + texto_de_rotulos(tb)
    titulos = [x for x in itens if _TITULO.match(x.text.strip()) and nome.startswith(x.text.strip())]
    if len(titulos) != 1:
        return (0, reserva, None, "", False) if reserva else None
    titulo = titulos[0]
    corte = next((i for i, ln in enumerate(linhas) if _GRAFICO.match(ln)), len(linhas))
    restante = "\n\n".join(" ".join(c for c in _celulas(ln) if c) for ln in linhas[corte:])
    linhas = linhas[:corte]
    esperado = _conteudo(" ".join(prefixo + linhas))
    for l, r, ys in horizontais:
        if not (l - 2 <= titulo.x <= titulo.x + titulo.width <= r + 2):
            continue
        abaixo = [y for y in ys if y < titulo.y]
        if len(abaixo) < 3 or titulo.y - abaixo[-1] > 5 * titulo.height:
            continue
        topo = abaixo[-1]
        cab = _na_faixa(itens, l, topo, r, titulo.y + titulo.height)
        esperado_sem_repeticao = None
        if prefixo and len(valores) == 1 and any(_conteudo(x.text) == _conteudo(valores[0]) for x in cab):
            if _conteudo(valores[0]) <= _conteudo(" ".join(prefixo)):
                esperado_sem_repeticao = _conteudo(" ".join(prefixo + linhas[1:]))
        for fundo in abaixo[:-2]:
            corpo = _na_faixa(itens, l, fundo, r, topo)
            notas = _fisicas(_na_faixa(itens, l, fundo - 4 * titulo.height, r, fundo))
            anexas = []
            for proxima in [[], *notas]:
                anexas.extend(proxima)
                reunidos = cab + corpo + anexas
                pertence = _conteudo(_texto(reunidos))
                if pertence not in (esperado, esperado_sem_repeticao):
                    continue
                if not corpo or not cab:
                    continue
                saida = _tabela_numerica(
                    corpo, cab, anexas, abaixo[-2], tracos if interno or calendario else (), calendario
                )
                estrutura = bool(saida)
                if not saida:
                    if corte < len(_linhas(tb)):
                        return
                    saida = "\n\n".join([*prefixo, reserva or tb["md"]])
                caixa = [l, min(fundo, _caixa(reunidos)[1]), r, _caixa(reunidos)[3]]
                return len(prefixo), saida, caixa, restante, estrutura
    if reserva:
        return 0, reserva, None, "", False


def _juntar_em_moldura(blocos, k, tb, proxima, itens, verticais):
    linhas, dados = _linhas(tb), _linhas(proxima)
    if len(linhas) < 2 or len(dados) < 2 or len(linhas) > 6 or not _TITULO.match(_celulas(linhas[0])[0]):
        return
    primeira = [c for c in _celulas(linhas[0]) if c]
    cabecalho = _celulas(linhas[-1])
    if len(primeira) != 1 or len(cabecalho) < 3 or not all(cabecalho):
        return
    if any(len(_celulas(d)) != len(cabecalho) or not all(_celulas(d)) for d in dados):
        return
    titulos = [x for x in itens if x.text.strip() == primeira[0]]
    if len(titulos) != 1:
        return
    titulo = titulos[0]
    notas = blocos[k + 2 : k + 3]
    if not notas or not re.match(r"(?:Nota|Note|Fonte|Source)s?\s*:", notas[0], re.I):
        return
    notas.extend(blocos[k + 3 : k + 7])
    nativo = _conteudo(" ".join(linhas + dados))
    for i, (l, b, t) in enumerate(verticais):
        if not (l < titulo.x and titulo.y + titulo.height <= t <= titulo.y + 3 * titulo.height):
            continue
        for r, base, alto in verticais[i + 1 :]:
            if abs(b - base) > 1 or abs(t - alto) > 1 or titulo.x + titulo.width >= r:
                continue
            inscritos = _na_faixa(itens, l, b, r, t)
            corpo = [[x.text.strip() for x in xs] for xs in _fisicas(inscritos) if len(xs) == len(cabecalho)]
            esperadas = [cabecalho, *map(_celulas, dados)]
            if corpo[: len(esperadas)] != esperadas:
                continue
            moldura = _conteudo(_texto(inscritos))
            esperado = nativo.copy()
            for n, nota in enumerate(notas, 1):
                if _TABELA.fullmatch(nota):
                    break
                esperado.update(_conteudo(nota))
                if esperado == moldura:
                    legenda = "\n\n".join(" ".join(c for c in _celulas(ln) if c) for ln in linhas[:-1])
                    tabela = _markdown([cabecalho, *map(_celulas, dados)])
                    return n, "\n\n".join([legenda, tabela, *notas[:n]]), [l, b, r, t]
                if not esperado <= moldura:
                    break


def _indice_em_paineis(bloco, tb, itens, verticais, W, H):
    if not bloco or not tb["complexa"] or len(itens) < 150 or W <= H:
        return
    for xs in _fisicas(itens):
        if len(xs) < 8 or len(xs) % 2:
            continue
        esquerda, direita = xs[::2], xs[1::2]
        if len({x.text.strip() for x in esquerda}) != 1 or len({x.text.strip() for x in direita}) != 1:
            continue
        inicio = xs[0]
        divisorias = _cortes(x for x, b, t in verticais if t - b > H / 2 and abs(t - inicio.y) <= inicio.height)
        if len(divisorias) != len(esquerda) - 1 or not all(
            a.x + a.width < x < b.x for a, x, b in zip(direita[:-1], divisorias, esquerda[1:], strict=True)
        ):
            continue
        acima = [x for x in itens if inicio.y < x.y < inicio.y + 3 * inicio.height and abs(x.x - inicio.x) < 4]
        if len(acima) != 1 or not bloco.lstrip("*# ").startswith(acima[0].text.strip()):
            continue
        b = min(b for x, b, t in verticais if x in divisorias and t - b > H / 2)
        largura = max(x.x + x.width for x in direita) - inicio.x
        usados = _na_faixa(itens, inicio.x - 6, b, inicio.x + largura + 6, acima[0].y + acima[0].height)
        if len(usados) < 0.9 * len(itens):
            continue
        return _caixa(usados)


def conferir_blocos(md, tabelas, itens, filetes, tracos, W, H, tique, ler_celulas):
    if (
        not tabelas
        or len(itens) > 2048
        or len(tracos) > 2048
        or len(md) + sum(len(tb["md"]) for tb in tabelas) > 128_000
    ):
        return md
    blocos = md.split("\n\n")
    ocorrencias = Counter(s.strip() for s in blocos if _TABELA.fullmatch(s.strip()))
    visiveis = [x for x in itens if not x.rotation and x.render_mode == 0 and x.width > 0 and x.height > 0]
    horizontais = _grupos_horizontais(tracos)
    verticais = list(_grupos_verticais(filetes))
    for k, bloco in enumerate(blocos):
        if ocorrencias[bloco.strip()] != 1 or not (m := _TABELA.fullmatch(bloco.strip())) or int(m[1]) >= len(tabelas):
            continue
        tique()
        tb = tabelas[int(m[1])]
        if tb.get("confirmada"):
            continue
        if k and verticais:
            nova_marca = "{{TABELA:" + str(len(tabelas)) + "}}"
            par = (
                None
                if ocorrencias[nova_marca]
                else _grade_repetida(blocos, k, tb, visiveis, horizontais, verticais, ler_celulas)
            )
            if par:
                tabelas[int(m[1])] = par[0]
                blocos[k - 1] = bloco
                blocos[k] = nova_marca
                tabelas.append(par[1])
                continue
            if caixa := _indice_em_paineis(blocos[k - 1], tb, visiveis, verticais, W, H):
                _confirmar(tb, blocos[k - 1] + "\n\n" + tb["md"], caixa, estrutura=False)
                blocos[k - 1] = ""
                continue
        if k + 1 < len(blocos) and ocorrencias[blocos[k + 1]] == 1 and (seguinte := _TABELA.fullmatch(blocos[k + 1])):
            j = int(seguinte[1])
            if j < len(tabelas) and (junta := _juntar_em_moldura(blocos, k, tb, tabelas[j], visiveis, verticais)):
                n, texto, caixa = junta
                _confirmar(tb, texto, caixa)
                tabelas[j] = {"md": "", "complexa": False, "palavras": set()}
                blocos[k + 1 : k + n + 2] = [""] * (n + 1)
                continue
        if ajustada := _tabela_com_titulo(blocos, k, tb, visiveis, horizontais, tracos):
            n, texto, caixa, resto, estrutura = ajustada
            precisa_visao = tb["complexa"] or not estrutura
            if caixa:
                _confirmar(tb, texto, caixa, estrutura=estrutura)
            else:
                tb["md"] = texto
            tb["complexa"] = precisa_visao
            blocos[k - n : k] = [""] * n
            if resto:
                blocos[k] = bloco + "\n\n" + resto
    return "\n\n".join(blocos)
