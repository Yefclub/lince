"""Conferências conservadoras sobre transcrições, sem substituir os dados do provedor."""

import re
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import groupby

from lxml import etree

# Uma página por vez. Um expoente sem a mesma mantissa e unidade não constitui divergência.
_TEXTO_MAX = 128_000
_EXPOENTES = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")
_POTENCIA = re.compile(r"(?:×|\\times|[xX·])\s*10\s*(\^)?\s*\{?([+\-]?\d{1,3})\}?(?!\d)")
_MANTISSA = re.compile(
    r"(?<![\d.,])([+\-]?\d{1,6}[.,]\d+(?:[ \t]\d{1,4}){0,8})(?:\(\d{1,6}\))?(?:\s*(?:…|\.\s*\.\s*\.))?\s*$"
)
_UNIDADE = re.compile(r"(?:[\s$|~]|\\[,;:! ])*(?:\\(?:mathrm|text|unit)\s*\{\s*)?([a-zA-ZµμΩΩ]+)\b")
# Uma unidade composta só seria comparável se todos os fatores e potências coincidissem.
_CONTINUACAO_UNIDADE = re.compile(
    r"(?:[}\t $~]|\\[,;:! ])*(?:[/·⋅*^]|\\(?:cdot|times|per)\b|(?:\\(?:mathrm|text|unit)\{)?[^\W\d_])"
)
_HACHURA = re.compile(r"hachur|shad(?:ed|ing)|hatch|fundo\s+(?:branco|cinza)|background\s+colou?r", re.I)
_EFETIVO = re.compile(r"\b(?:(?:efetiv|realizad|observad|hist[óo]ric)[oa]s?|actuals?|observed|historical)\b", re.I)
_PROJECAO = re.compile(
    r"\b(?:proje[çc](?:[aã]o|[oõ]es)|(?:projetad|previst)[oa]s?|forecasts?|projected|projections?)\b", re.I
)


def potencias(texto, *, camada_pdf=False):
    """Associa notações científicas pela mantissa normalizada e pela unidade seguinte."""
    if len(texto) > _TEXTO_MAX:
        return {}
    texto = re.sub(r"([⁺⁻]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+)", lambda m: "^" + m[1].translate(_EXPOENTES), texto)
    texto = re.sub(r"<sup>([+−\-]?\d{1,3})</sup>", r"^\1", texto, flags=re.I)
    texto = re.sub(r"</?(?:td|th)(?:\s[^<>]{0,256})?>", " ", texto, flags=re.I).replace("−", "-")
    achadas = {}
    for ponto in _POTENCIA.finditer(texto):
        if not camada_pdf and not ponto[1] and not ponto[2].startswith(("+", "-")):
            continue
        m = _MANTISSA.search(texto[max(0, ponto.start() - 100) : ponto.start()])
        u = _UNIDADE.match(texto, ponto.end())
        if not m or not u or _CONTINUACAO_UNIDADE.match(texto, u.end()):
            continue
        v = Decimal(m[1].replace(" ", "").replace("\t", "").replace(",", ".")).normalize()
        if len(v.as_tuple().digits) < 6:
            continue
        mantissa = v.scaleb(-v.adjusted())
        chave = f"{mantissa} {u[1]}"
        achadas.setdefault(chave, set()).add(int(ponto[2]) + v.adjusted())
        if len(achadas) > 512:
            return {}
    return {k: sorted(exps) for k, exps in achadas.items()}


# Colunas de identificação da linha em tabelas de texto (sem th scope=row).
# Uma primeira medida, mesmo textual, exige classificação como as seguintes.
_IDENTIFICACAO = re.compile(
    r"^(?:per[ií]odo|trimestre|ano|data|item|indicador|[ií]ndice|categoria|grupo|regi[aã]o|descri[çc][aã]o|"
    r"period|quarter|year|date|indicator|index|category|group|region|description)\b",
    re.I,
)
_ESTADO = re.compile(r"^(?:condição|condicao|situação|status|condition|classification|classificação)$", re.I)
_COR_LEGENDA = re.compile(
    r"\b(?:fundos?|branc[oa]s?|cinzas?|hachurad[oa]s?|hachuras?|background|white|gr[ae]y|shaded|hatched)\b", re.I
)
_COLUNAS_MAX = 128
_SLOTS_MAX = 65_536  # soma das posições lógicas visitadas, inclusive as cobertas por rowspan


@dataclass(eq=False, slots=True)
class _Celula:
    texto: str
    titulo: bool = False
    largura: int = 1
    altura: int = 1
    condicao: int = field(init=False)
    sem_valor: bool = field(init=False)

    def __post_init__(self):
        # Cada texto só é pesquisado uma vez, mesmo numa grande célula mesclada.
        self.condicao = int(bool(_EFETIVO.search(self.texto))) | (2 if _PROJECAO.search(self.texto) else 0)
        valor = self.texto
        if self.condicao:
            valor = _COR_LEGENDA.sub("", _PROJECAO.sub("", _EFETIVO.sub("", valor)))
        self.sem_valor = self.texto in ("", "-", "–", "—") or (bool(self.condicao) and not re.search(r"\w", valor))


def _extensao(c, nome):
    valor = c.get(nome, "1").strip()
    if not re.fullmatch(r"[1-9][0-9]{0,2}", valor):
        raise ValueError("extensão da célula não conferida")
    return int(valor)


def _tabelas(texto):
    if re.search(r"<table\b", texto, re.I):
        parser = etree.HTMLParser(no_network=True)
        raiz = etree.HTML(texto, parser=parser)
        if raiz is None or parser.error_log.filter_from_errors():
            raise ValueError("estrutura HTML não conferida")
        for quebra in raiz.iter("br"):
            quebra.tail = " " + (quebra.tail or "")
        for tabela in raiz.iter("table"):
            if tabela.find(".//table") is not None:
                raise ValueError("tabela aninhada")
            linhas = []
            corpo = False
            for grupo, trs in groupby(
                tabela.xpath("./tr | ./thead/tr | ./tbody/tr | ./tfoot/tr"), key=lambda tr: tr.getparent()
            ):
                for i, tr in enumerate(trs):
                    campos = [
                        _Celula(
                            "".join(c.itertext()).strip(),
                            c.tag == "th",
                            _extensao(c, "colspan"),
                            _extensao(c, "rowspan"),
                        )
                        for c in tr
                        if c.tag in ("th", "td")
                    ]
                    if grupo.tag == "tfoot":
                        # Legendas esclarecem a cor. Valores e totais do rodapé também precisam de condição.
                        if not any(re.match(r"^[\[(+−-]*\d", c.texto) for c in campos) and not (
                            campos and re.match(r"^(?:sub)?tot(?:al|ais)\b", campos[0].texto, re.I)
                        ):
                            continue
                        cab = False
                    else:
                        cab = grupo.tag == "thead" or (not corpo and bool(campos) and all(c.titulo for c in campos))
                    corpo |= not cab
                    linhas.append((i == 0, cab, campos))
            yield linhas
    # Somente grade Markdown retangular com separador de cabeçalho. Estrutura
    # sem fronteira confiável não serve para dispensar o alerta.
    for eh_tabela, grupo in groupby(texto.splitlines(), key=lambda s: s.lstrip().startswith("|")):
        if not eh_tabela:
            continue
        grade = [re.split(r"(?<!\\)\|", ln.strip().removeprefix("|").removesuffix("|")) for ln in grupo]
        if len(grade) < 3 or not all(re.fullmatch(r"\s*:?-{2,}:?\s*", s) for s in grade[1]):
            raise ValueError("cabeçalho Markdown não identificado")
        if any(len(linha) != len(grade[0]) for linha in grade):
            raise ValueError("largura Markdown variável")
        yield [(i == 0, i == 0, [_Celula(s.strip()) for s in linha]) for i, linha in enumerate(grade) if i != 1]


def _posicoes(linhas):
    """Colunas lógicas de cada linha. No HTML o rowspan acaba no seu grupo de linhas."""
    ocupadas = {}
    for novo_grupo, cab, campos in linhas:
        if novo_grupo:
            ocupadas.clear()
        linha = {j: c for j, (c, _) in ocupadas.items()}
        ocupadas = {j: (c, n - 1) for j, (c, n) in ocupadas.items() if n > 1}
        j = 0
        for c in campos:
            while j in linha:
                j += 1
            if j + c.largura > _COLUNAS_MAX:
                raise ValueError("tabela larga demais para a conferência")
            for k in range(j, j + c.largura):
                if k in linha:
                    raise ValueError("células sobrepostas")
                linha[k] = c
                if c.altura > 1:
                    ocupadas[k] = c, c.altura - 1
            j += c.largura
        yield cab, linha


def _classificacao_completa(texto):
    """Cada medida recebe uma condição pela célula, pelo rótulo da linha ou pelo cabeçalho
    da coluna. Os bits 1 e 2 representam efetivo e projeção; zero ou ambos deixam dúvida."""
    total = classes = 0
    try:
        for linhas in _tabelas(texto):
            cabecalhos = {}
            identificadores = set()
            estados_mesclados = {}
            for cab, linha in _posicoes(linhas):
                total += len(linha)
                if total > _SLOTS_MAX:
                    return False
                if cab:
                    for j, c in linha.items():
                        if c.condicao in (1, 2):
                            cabecalhos[j] = cabecalhos.get(j, 0) | c.condicao
                        if (
                            not c.condicao
                            and c.largura == 1
                            and (j == 0 and _IDENTIFICACAO.match(c.texto) or _ESTADO.fullmatch(c.texto))
                        ):
                            identificadores.add(j)
                    continue
                rotulos = {j for j, c in linha.items() if c.titulo or j in identificadores}
                da_linha = 0
                for j in rotulos:
                    da_linha |= linha[j].condicao
                for j, c in linha.items():
                    if j in rotulos or c.sem_valor:
                        continue
                    estado = c.condicao | da_linha | cabecalhos.get(j, 0)
                    if estado not in (1, 2):
                        return False
                    if (c.altura > 1 or c.largura > 1) and estados_mesclados.setdefault(c, estado) != estado:
                        return False
                    classes |= estado
    except (ValueError, etree.ParserError):
        return False
    return classes == 3


def avisos_transcricao(texto, ancora, referencias):
    if not texto or len(texto) > _TEXTO_MAX:
        return {}
    avisos = {}
    for chave, achados in (potencias(texto) if referencias else {}).items():
        nativos = referencias.get(chave, [])
        if len(nativos) == 1 and (diferentes := [e for e in achados if e != nativos[0]]):
            mantissa, unidade = chave.split()
            avisos["expoente_divergente"] = (
                f"possível divergência de expoente em {unidade}: "
                f"transcrição {mantissa} × 10^{diferentes[0]}, camada digital {mantissa} × 10^{nativos[0]}"
            )
            break
    contexto = ancora[:6000] + "\n" + texto
    if _HACHURA.search(contexto) and _EFETIVO.search(contexto) and _PROJECAO.search(contexto):
        if not _classificacao_completa(texto):
            avisos["condicao_visual"] = (
                "condição visual de valores efetivos e projetados sem associação explícita na tabela"
            )
    return avisos
