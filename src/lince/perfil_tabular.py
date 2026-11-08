"""Perfil de cada aba de planilha (e do CSV): cabeçalho, colunas com o tipo dominante, amostra de linhas e quantas
linhas entraram no texto. É o que a IA precisa para decidir como consultar uma planilha grande sem ler tudo.

Montado das mesmas linhas que viram a tabela do Markdown, na mesma passada: o perfil nunca diz mais do que o texto
tem. Determinístico (sem IA), com tetos declarados: coluna além do teto marca `colunas_cortadas`, e aba cortada no
texto marca `extracao_cortada`."""

import re

AMOSTRA_LINHAS = 5
COLUNAS_MAX = 64  # planilha larga: o perfil lista as primeiras e declara o corte
LINHAS_OBSERVADAS_MAX = 5_000  # janela de inferência de tipo por aba
CARACTERES_MAX = 120  # por valor no cabeçalho e na amostra

_NUMERO = re.compile(r"[+-]?(?:\d+(?:[.,]\d+)*|[.,]\d+)(?:[eE][+-]?\d+)?%?")
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
    """Acumula o perfil de uma aba linha a linha: a primeira linha com valor é o cabeçalho; as seguintes com valor
    contam em `linhas_extraidas` (as linhas com dado que estão no texto)."""

    def __init__(self, nome):
        self.nome, self.cabecalho, self.tipos, self.amostra = nome, None, [], []
        self.linhas, self.colunas_cortadas = 0, False

    def linha(self, celulas: list[str]) -> None:
        fim = len(celulas)
        while fim and not celulas[fim - 1].strip():
            fim -= 1  # colunas vazias à direita não são colunas
        if not fim:
            return  # linha vazia não é dado nem cabeçalho
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
            for contagem, c in zip(self.tipos, celulas, strict=False):
                t = tipo(c)
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
