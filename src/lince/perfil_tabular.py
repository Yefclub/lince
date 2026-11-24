"""Perfil de cada aba de planilha (e do CSV): cabeçalho, colunas com o tipo dominante, amostra de linhas e quantas
linhas entraram no texto. É o que a IA precisa para decidir como consultar uma planilha grande sem ler tudo.

Montado das mesmas linhas que viram a tabela do Markdown, na mesma passada: o perfil nunca diz mais do que o texto
tem. Determinístico (sem IA), com tetos declarados: coluna além do teto marca `colunas_cortadas`, e aba cortada no
texto marca `extracao_cortada`."""

import re
from itertools import chain, groupby, islice

AMOSTRA_LINHAS = 5
COLUNAS_MAX = 64  # planilha larga: o perfil lista as primeiras e declara o corte
LINHAS_OBSERVADAS_MAX = 5_000  # janela de inferência de tipo por aba
CARACTERES_MAX = 120  # por valor no cabeçalho e na amostra

# milhar em grupos de 3 com um separador só e decimal com o outro (1.234.567,89 ou 1,234.5); IP e versão são texto
_NUMERO = re.compile(
    r"[+-]?(?:\d{1,3}([., \u00a0\u202f])\d{3}(?:\1\d{3})*(?:(?!\1)[.,]\d+)?|\d+(?:[.,]\d+)?|[.,]\d+)(?:[eE][+-]?\d+)?%?"
)
_DATA = re.compile(  # ISO (como o leitor escreve), dd/mm/aaaa e hora
    r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?|\d{1,2}/\d{1,2}/\d{2,4}|\d{2}:\d{2}(?::\d{2})?"
)
_BOOLEANO = {"TRUE", "FALSE", "VERDADEIRO", "FALSO"}


def tipo(texto: str) -> str:
    """Tipo do valor pelo texto da célula: o leitor escreve data em ISO e booleano em TRUE/FALSE."""
    texto = texto.strip()
    if not texto:
        return "vazio"
    if texto.upper() in _BOOLEANO:
        return "booleano"
    if _NUMERO.fullmatch(texto):
        return "numero"
    if _DATA.fullmatch(texto):
        return "data"
    return "texto"


def _curto(texto: str) -> str:
    return texto if len(texto) <= CARACTERES_MAX else texto[:CARACTERES_MAX] + "…"


class Aba:
    """Acumula a tabela linha a linha. Uma coluna de notas à esquerda pode conter o título e o rodapé."""

    def __init__(self, nome):
        self.nome, self.cabecalho, self.tipos, self.amostra = nome, None, [], []
        self.linhas, self.colunas_cortadas = 0, False
        self.inicio, self.separada = 0, False

    def linha(self, celulas: list[str], numeros=()) -> None:
        fim = len(celulas)
        while fim and not celulas[fim - 1].strip():
            fim -= 1  # colunas vazias à direita não são colunas
        if not fim:
            self.separada = True
            return  # linha vazia não é dado nem cabeçalho
        if self.separada and self.linhas == 0 and self.cabecalho and sum(bool(c.strip()) for c in self.cabecalho) == 1:
            rotulos = [(i, c.strip()) for i, c in enumerate(celulas[:fim]) if c.strip()]
            if (
                len(self.cabecalho) <= rotulos[0][0]
                and len({c for _, c in rotulos}) >= 2
                and all(tipo(c) == "texto" for _, c in rotulos)
            ):
                # Título isolado, depois rótulos distintos em outras colunas. As notas ficam fora da tabela.
                self.inicio, self.cabecalho = rotulos[0][0], None
        if fim <= self.inicio:
            return  # linha só na margem do título; continua integral no Markdown
        self.separada = False
        celulas = celulas[self.inicio : fim]
        fim = len(celulas)
        if fim > COLUNAS_MAX:
            self.colunas_cortadas = True
        celulas = celulas[: min(fim, COLUNAS_MAX)]
        if self.cabecalho is None:
            self.cabecalho = [_curto(c) for c in celulas]
            return
        self.linhas += 1
        if self.linhas <= AMOSTRA_LINHAS:
            self.amostra.append([_curto(c) for c in celulas])
        if self.linhas <= LINHAS_OBSERVADAS_MAX:
            self.tipos += [{} for _ in range(len(celulas) - len(self.tipos))]
            for i, (contagem, c) in enumerate(zip(self.tipos, celulas, strict=False), self.inicio):
                t = tipo(c)
                if i in numeros and t not in ("vazio", "data"):
                    t = "numero"  # uma moeda, ou zero exibido como traço, conserva o tipo de origem
                contagem[t] = contagem.get(t, 0) + 1

    def montar(self, cortada=False) -> dict:
        cabecalho = self.cabecalho or []
        colunas = []
        for i in range(min(max(len(cabecalho), len(self.tipos)), COLUNAS_MAX)):
            cheios = {t: n for t, n in (self.tipos[i] if i < len(self.tipos) else {}).items() if t != "vazio"}
            nome = cabecalho[i].strip() if i < len(cabecalho) else ""
            dominante = max(cheios, key=cheios.get) if cheios else "vazio"
            colunas.append({"nome": nome or f"coluna {i + 1}", "tipo": dominante})
        perfil = {
            "aba": self.nome,
            "linhas_extraidas": self.linhas,
            "cabecalho": cabecalho,
            "colunas": colunas,
            "amostra": self.amostra,
            "linhas_observadas": min(self.linhas, LINHAS_OBSERVADAS_MAX),
        }
        if self.colunas_cortadas:
            perfil["colunas_cortadas"] = True
        if cortada:
            perfil["extracao_cortada"] = True
        return perfil


def _blocos(previa):
    """Tabelas paralelas: títulos próprios acima de duas ou mais sequências iguais de rótulos."""
    for n, (linha, _) in enumerate(previa[1:], 1):
        grupos = [list(g) for cheio, g in groupby(range(len(linha)), key=lambda i: bool(linha[i].strip())) if cheio]
        if len(grupos) < 2 or len(grupos[0]) < 2:
            continue
        rotulos = [linha[i] for i in grupos[0]]
        if len(set(rotulos)) < 2 or not all(tipo(c) == "texto" for c in rotulos):
            continue
        if any([linha[i] for i in g] != rotulos for g in grupos[1:]):
            continue
        blocos = []
        anterior = 0
        for g in grupos:
            # O título deve vir de uma coluna própria do bloco; links à direita ficam fora.
            candidatos = [
                i for i in range(anterior, g[0] + 1) if any(i < len(l) and l[i].strip() for l, _ in previa[:n])
            ]
            if not candidatos:
                break
            inicio = candidatos[0]
            titulo = " ".join(l[inicio].strip() for l, _ in previa[:n] if inicio < len(l) and l[inicio].strip())
            blocos.append((inicio, g[-1] + 1, titulo[:512]))
            anterior = g[-1] + 1
        if len(blocos) != len(grupos):
            continue
        # Uma tabela com um único rótulo de linha pode repetir cabeçalhos em grupos de medidas.
        # Só separam blocos os títulos alinhados entre si e com a mesma margem para o cabeçalho.
        if len({g[0] - b[0] for g, b in zip(grupos, blocos, strict=True)}) != 1:
            continue
        if not any(all(i < len(l) and l[i].strip() for i, _, _ in blocos) for l, _ in previa[:n]):
            continue
        fim = n + 1
        usados = {i for g in grupos for i in g}
        while fim < len(previa):
            prox = previa[fim][0]
            if {i for i, c in enumerate(prox) if c.strip()} != usados or not all(
                tipo(prox[i]) == "texto" for i in usados
            ):
                break
            fim += 1
        return n, fim, blocos
    return None


def perfis_xlsx(nome, linhas, coluna_inicial, cortada=False):
    """Cada linha traz os valores exibidos e as posições numéricas. A sondagem de cabeçalho é limitada."""
    linhas = iter(linhas)
    previa = list(islice(linhas, 24))
    # A janela de largura também limita o custo da heurística em uma aba muito larga.
    blocos = _blocos(previa) if all(len(l) <= 2 * COLUNAS_MAX for l, _ in previa) else None
    if not blocos:
        aba = Aba(nome)
        for linha, numeros in chain(previa, linhas):
            aba.linha(linha, numeros)
        return [aba.montar(cortada)]
    n, fim, faixas = blocos
    abas = []
    for inicio, limite, _ in faixas:
        aba = Aba(nome)
        aba.cabecalho = [
            _curto(" ".join(l[i] for l, _ in previa[n:fim] if i < len(l) and l[i])) for i in range(inicio, limite)
        ]
        abas.append(aba)
    for linha, numeros in chain(previa[fim:], linhas):
        for aba, (inicio, limite, _) in zip(abas, faixas, strict=True):
            aba.linha(linha[inicio:limite], {i - inicio for i in numeros if inicio <= i < limite})
    return [
        dict(aba.montar(cortada), titulo=titulo, coluna_inicial=coluna_inicial + inicio)
        for aba, (inicio, _, titulo) in zip(abas, faixas, strict=True)
    ]
