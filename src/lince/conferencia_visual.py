"""Conferências conservadoras sobre transcrições, sem substituir os dados do provedor."""

import re
from decimal import Decimal
from html.parser import HTMLParser

# Uma página por vez. Um expoente sem a mesma mantissa e unidade não constitui divergência.
_TEXTO_MAX = 128_000
_EXPOENTES = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")
_POTENCIA = re.compile(r"(?:×|\\times|[xX·])\s*10\s*(\^)?\s*\{?([+\-]?\d{1,3})\}?(?!\d)")
_MANTISSA = re.compile(
    r"(?<![\d.,])([+\-]?\d{1,6}[.,]\d+(?:[ \t]\d{1,4}){0,8})(?:\(\d{1,6}\))?(?:\s*(?:…|\.\s*\.\s*\.))?\s*$"
)
_UNIDADE = re.compile(r"(?:[\s$|~]|\\[,;:! ])*(?:\\(?:mathrm|text|unit)\s*\{\s*)?([a-zA-ZµμΩΩ]+)\b")
_HACHURA = re.compile(r"hachur|shad(?:ed|ing)|hatch|fundo\s+(?:branco|cinza)|background\s+colou?r", re.I)
_EFETIVO = re.compile(r"\b(?:efetiv|realizad|observad|históric|actual|observed|historical)\w*", re.I)
_PROJECAO = re.compile(r"\b(?:proje[çtc]|projetad|previst|forecast|projected|projection)\w*", re.I)


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
        if not m or not u:
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


class _Celulas(HTMLParser):
    def __init__(self):
        super().__init__()
        self.atual = None
        self.rodape = False
        self.linha = []
        self.linhas = []

    def handle_starttag(self, tag, attrs):
        if tag == "tfoot":
            self.rodape = True
        elif tag == "tr":
            self.linha = []
        elif tag in ("td", "th") and not self.rodape:
            self.atual = []

    def handle_endtag(self, tag):
        if tag == "tfoot":
            self.rodape = False
        elif tag in ("td", "th") and self.atual is not None:
            self.linha.append((tag == "th", "".join(self.atual)))
            self.atual = None
        elif tag == "tr" and self.linha:
            self.linhas.append(self.linha)
            self.linha = []

    def handle_data(self, data):
        if self.atual is not None:
            self.atual.append(data)


def _campos_classificados(texto):
    tabela = _Celulas()
    try:
        tabela.feed(texto)
        tabela.close()
    except (ValueError, AssertionError):
        tabela.linhas.clear()
    linhas = texto.splitlines()
    for n, linha in enumerate(linhas):
        if linha.lstrip().startswith("|"):
            campos = linha.strip().strip("|").split("|")
            cabecalho = n + 1 < len(linhas) and re.fullmatch(r"[\s:|\-]+", linhas[n + 1]) is not None
            tabela.linhas.append([(cabecalho, c) for c in campos])
    for linha in tabela.linhas:
        tem_medida = any(re.search(r"\d", c) for _, c in linha)
        for cabecalho, c in linha:
            if tem_medida or cabecalho:
                yield c


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
        campos = list(_campos_classificados(texto))
        efetivo = any(_EFETIVO.search(c) and not _PROJECAO.search(c) for c in campos)
        projetado = any(_PROJECAO.search(c) and not _EFETIVO.search(c) for c in campos)
        if not (efetivo and projetado):
            avisos["condicao_visual"] = (
                "condição visual de valores efetivos e projetados sem associação explícita na tabela"
            )
    return avisos
