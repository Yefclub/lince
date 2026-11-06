"""Código de um XLSX: abas e células com ids naturais (`Aba!B2`), e edição cirúrgica por operações nomeadas.

A escrita não passa pelo openpyxl (que perde imagens, gráficos e partes que não conhece ao salvar): só o XML das
partes tocadas muda. Fórmula ou valor alterado faz o Excel recalcular tudo ao abrir (`fullCalcOnLoad`), e a cadeia de
cálculo antiga (`calcChain.xml`), que ficaria inconsistente, sai do pacote; o Excel a refaz.
"""

import bisect
import math
import re
import time
from datetime import datetime, timedelta

from lxml import etree
from openpyxl.formula.tokenizer import Tokenizer
from openpyxl.utils.formulas import FORMULAE

from ..limites import DocumentoRecusado
from .pacote import CRESCIMENTO_MAX, NS_CT, OperacaoInvalida, Pacote, campo_texto, sha
from .pacote import integridade as _integridade

NS = {
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
S = f"{{{NS['s']}}}"
R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
TIPO_DOCUMENTO = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
CELULAS_MAX = 50_000  # células no código de um documento (o resto da aba fica marcado como truncado)
ESCRITAS_MAX = 100_000  # células escritas somando todas as operações de um pedido (cada escrita é O(log n))
FORMULA_MAX = 8_192  # caracteres de uma fórmula (limite do Excel; acima disso ele repara o arquivo)
TEXTO_CELULA_MAX = 32_767  # caracteres num texto de célula (limite do Excel)
_REF = re.compile(r"\$?([A-Z]{1,3})\$?([1-9]\d{0,6})")
_NOME_PROIBIDO = re.compile(r"[\[\]:*?/\\]")
_FORMATOS_DATA = {14, 15, 16, 17, 22}  # 18–21 são hora e 45–47 são duração: não viram data
_NOME_SIMPLES = re.compile(r"[A-Za-z_][A-Za-z_]*")
EDICAO_PRAZO = 60  # segundos por pedido de edição: passou, nada é aplicado (o servidor não fica preso)
FUNCOES_ANINHADAS_MAX = 64  # níveis de função (limite do Excel)
ARGUMENTOS_MAX = 255  # argumentos por função (limite do Excel)
BLOCO = 1024  # linhas por balde no índice das áreas proibidas
# Funções do Excel 2010 em diante: no arquivo, levam o prefixo _xlfn. (sem ele, o Excel mostra #NAME?)
_XLFN = set(
    """AGGREGATE BETA.DIST BETA.INV BINOM.DIST BINOM.DIST.RANGE BINOM.INV BITAND BITLSHIFT BITOR BITRSHIFT BITXOR
    CEILING.MATH CEILING.PRECISE CHISQ.DIST CHISQ.DIST.RT CHISQ.INV CHISQ.INV.RT CHISQ.TEST COMBINA CONCAT
    CONFIDENCE.NORM CONFIDENCE.T COT COTH COVARIANCE.P COVARIANCE.S CSC CSCH DAYS DECIMAL ERF.PRECISE ERFC.PRECISE
    EXPON.DIST F.DIST F.DIST.RT F.INV F.INV.RT F.TEST FLOOR.MATH FLOOR.PRECISE FORECAST.LINEAR FORMULATEXT GAMMA
    GAMMA.DIST GAMMA.INV GAMMALN.PRECISE GAUSS HYPGEOM.DIST IFNA IFS ISFORMULA ISOWEEKNUM LOGNORM.DIST LOGNORM.INV
    MAXIFS MINIFS MODE.MULT MODE.SNGL NEGBINOM.DIST NORM.DIST NORM.INV NORM.S.DIST NORM.S.INV NUMBERVALUE PDURATION
    PERCENTILE.EXC PERCENTILE.INC PERCENTRANK.EXC PERCENTRANK.INC PERMUTATIONA PHI POISSON.DIST QUARTILE.EXC
    QUARTILE.INC RANK.AVG RANK.EQ RRI SEC SECH SHEET SHEETS SKEW.P STDEV.P STDEV.S SWITCH T.DIST T.DIST.2T T.DIST.RT
    T.INV T.INV.2T T.TEST TEXTJOIN UNICHAR UNICODE VAR.P VAR.S WEIBULL.DIST XLOOKUP XMATCH XOR NETWORKDAYS.INTL
    WORKDAY.INTL ISO.CEILING ACOT ACOTH ARABIC BASE Z.TEST IMCOSH IMCOT IMCSC IMCSCH IMSEC IMSECH IMSINH IMTAN MUNIT
    ENCODEURL FILTERXML WEBSERVICE FORECAST.ETS FORECAST.ETS.CONFINT FORECAST.ETS.SEASONALITY
    FORECAST.ETS.STAT TEXTBEFORE TEXTAFTER VALUETOTEXT ARRAYTOTEXT REGEXTEST REGEXEXTRACT REGEXREPLACE IMAGE
    ECMA.CEILING""".split()
)
# Devolvem matriz que se espalha pelas células vizinhas: exigem metadados de matriz dinâmica que não escrevemos
_MATRIZ_DINAMICA = set(
    """FILTER UNIQUE SORT SORTBY SEQUENCE RANDARRAY LET LAMBDA TEXTSPLIT VSTACK HSTACK TOCOL TOROW WRAPROWS WRAPCOLS
    TAKE DROP CHOOSECOLS CHOOSEROWS EXPAND MAKEARRAY MAP REDUCE SCAN BYROW BYCOL""".split()
)


def _coluna(letras: str) -> int:
    n = 0
    for ch in letras:
        n = n * 26 + ord(ch) - 64
    return n


def _letras(n: int) -> str:
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def _ref(texto: str) -> tuple[int, int]:
    """(coluna, linha) de uma referência A1, dentro dos limites do Excel."""
    m = _REF.fullmatch(texto or "")
    if not m:
        raise ValueError(texto)
    col, lin = _coluna(m.group(1)), int(m.group(2))
    if col > 16_384 or lin > 1_048_576:
        raise ValueError(texto)
    return col, lin


def _utf16(texto: str) -> int:
    """Tamanho como o Excel conta (unidades UTF-16: emoji vale 2)."""
    return len(texto.encode("utf-16-le")) // 2


def _intervalo(texto: str) -> tuple[int, int, int, int]:
    a, _, b = texto.partition(":")
    c1, l1 = _ref(a)
    c2, l2 = _ref(b or a)
    return min(c1, c2), min(l1, l2), max(c1, c2), max(l1, l2)


def _dentro(col, lin, area) -> bool:
    return area[0] <= col <= area[2] and area[1] <= lin <= area[3]


# ------------------------------------------------------------------ leitura
class _Livro:
    def __init__(self, pk: Pacote):
        self.pk = pk
        self.parte = None
        if "_rels/.rels" in pk.partes:
            for r in pk.arvore("_rels/.rels").findall("rel:Relationship", NS):
                alvo = (r.get("Target") or "").lstrip("/")
                if r.get("Type") == TIPO_DOCUMENTO and alvo in pk.partes:
                    self.parte = alvo
        if self.parte is None:
            raise OperacaoInvalida(
                "operacao-invalida", "pacote sem pasta de trabalho no padrão transicional (Strict não é editável)"
            )
        self.raiz = pk.arvore(self.parte)
        if self.raiz.tag != S + "workbook":
            raise OperacaoInvalida("operacao-invalida", "pasta de trabalho fora do padrão transicional")
        self.rels = pk.rels(self.parte)
        alvo = next((a for t, a in self.rels.values() if t == "sharedStrings"), None)
        self.compartilhadas = []
        if alvo in pk.partes:
            for si in pk.arvore(alvo).findall("s:si", NS):
                self.compartilhadas.append(
                    _decodificar("".join(t.text or "" for t in si.iter(S + "t") if _fora_de_fonetica(t)))
                )
        pr = self.raiz.find("s:workbookPr", NS)
        self.data1904 = pr is not None and pr.get("date1904") in ("1", "true")
        self.datas = self._estilos_de_data()

    def todas(self):
        """Todos os `sheet` do livro (planilhas, gráficos, macros), na ordem."""
        return self.raiz.findall("s:sheets/s:sheet", NS)

    def abas(self):
        """(elemento sheet, nome, parte) das planilhas, na ordem do livro."""
        for sh in self.todas():
            tipo, parte = self.rels.get(sh.get(R_ID), (None, None))
            if tipo == "worksheet" and parte in self.pk.partes:
                yield sh, sh.get("name"), parte

    def _estilos_de_data(self) -> set[int]:
        alvo = next((a for t, a in self.rels.values() if t == "styles"), None)
        if alvo not in self.pk.partes:
            return set()
        estilos = self.pk.arvore(alvo)
        proprios = {
            int(n.get("numFmtId")): n.get("formatCode", "")
            for n in estilos.findall("s:numFmts/s:numFmt", NS)
            if (n.get("numFmtId") or "").isdigit()
        }
        datas = set()
        for k, xf in enumerate(estilos.findall("s:cellXfs/s:xf", NS)):
            fid = int(xf.get("numFmtId", "0")) if (xf.get("numFmtId") or "0").isdigit() else 0
            codigo = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", proprios.get(fid, "")).lower()
            if fid in _FORMATOS_DATA or (fid in proprios and re.search(r"[dy]|m{3,}", codigo)):
                datas.add(k)
        return datas

    def valor(self, c):
        """Valor exibível da célula: número, texto, booleano, erro (#N/A), data ISO (t="d") ou None."""
        t = c.get("t", "n")
        if t == "inlineStr":
            return _decodificar("".join(x.text or "" for x in c.iter(S + "t") if _fora_de_fonetica(x)))
        v = c.find("s:v", NS)
        if v is None or v.text is None:
            return None
        if t == "s":
            k = int(v.text) if v.text.isdigit() else -1
            return self.compartilhadas[k] if 0 <= k < len(self.compartilhadas) else None
        if t == "b":
            return v.text == "1"
        if t in ("e", "str", "d"):
            return v.text
        try:
            numero = float(v.text)
        except ValueError:
            return v.text
        if not math.isfinite(numero):  # NaN e infinito não existem no Excel nem cabem em JSON
            return v.text
        return int(numero) if numero.is_integer() and abs(numero) < 2**53 else numero

    def data(self, c, valor) -> str | None:
        if c.get("t") == "d":
            return valor
        if not (c.get("s", "").isdigit() and int(c.get("s")) in self.datas):
            return None
        if not isinstance(valor, (int, float)) or isinstance(valor, bool) or not 0 <= valor < 2_958_466:
            return None
        if self.data1904:
            base = datetime(1904, 1, 1)
        elif valor < 60:  # o Excel conta 29/02/1900, que não existe: antes dele, a base é um dia depois
            base = datetime(1899, 12, 31)
        elif valor < 61:
            return None
        else:
            base = datetime(1899, 12, 30)
        momento = base + timedelta(days=float(valor))
        return momento.date().isoformat() if float(valor).is_integer() else momento.isoformat(timespec="seconds")


_ESCAPE = re.compile(r"_x([0-9A-Fa-f]{4})_")


def _decodificar(texto: str) -> str:
    """O Excel guarda caractere especial como _xHHHH_ (ex.: _x000D_ é o retorno de carro): o código mostra o texto.
    Emoji gravado em duas metades (_xD83D__xDE00_) vira um caractere só; metade solta vira U+FFFD."""
    bruto = _ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), texto)
    return bruto.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


_FORA_DO_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def _codificar(texto: str) -> str:
    """O inverso: todo _ que começaria um _xHHHH_ vira _x005F_, e o caractere que o XML não aceita (controle) vira
    _xHHHH_, então o Excel mostra exatamente o texto pedido e o que foi lido pode ser gravado de volta."""
    texto = re.sub(r"_(?=x[0-9A-Fa-f]{4}_)", "_x005F_", texto)
    return _FORA_DO_XML.sub(lambda m: f"_x{ord(m.group(0)):04X}_", texto)


def _fora_de_fonetica(t) -> bool:
    return not any(etree.QName(a).localname == "rPh" for a in t.iterancestors())


def extrair(dados: bytes) -> dict:
    pk = Pacote(dados)
    livro = _Livro(pk)
    abas, total = [], 0
    for sh, nome, parte in livro.abas():
        ws = pk.arvore(parte)
        celulas, truncada = [], False
        for c in ws.iterfind("s:sheetData/s:row/s:c", NS):
            if total >= CELULAS_MAX:
                truncada = True
                break
            f = c.find("s:f", NS)
            valor = livro.valor(c)
            if valor is None and f is None:
                continue
            cel = {"id": f"{nome}!{c.get('r')}", "valor": valor}
            if f is not None:
                cel["formula"] = "=" + f.text if f.text else None
                if f.get("t") == "shared":
                    cel["formula_compartilhada"] = f.get("si")
            if d := livro.data(c, valor):
                cel["data"] = d
            celulas.append(cel)
            total += 1
        dim = ws.find("s:dimension", NS)
        protecao = ws.find("s:sheetProtection", NS)
        abas.append(
            {
                "id": nome,
                "ordem": len(abas) + 1,
                "estado": sh.get("state", "visible"),
                "protegida": protecao is not None and protecao.get("sheet") in ("1", "true"),
                "dimensao": dim.get("ref") if dim is not None else None,
                "mescladas": [m.get("ref") for m in ws.findall("s:mergeCells/s:mergeCell", NS)],
                "tabelas": _tabelas(pk, parte),
                "celulas": celulas,
                "truncada": truncada,
            }
        )
    externos = []
    for n in pk.partes:
        if n.endswith(".rels"):
            dono = n.replace("_rels/", "").removesuffix(".rels")
            externos += [{"parte": "/" + dono, "url": t} for k, t in pk.rels(dono).values() if k.startswith("externo")]
    return {
        "formato": "xlsx",
        "esquema": 1,
        "sha256": sha(dados),
        "abas": abas,
        "nomes": [
            {
                "nome": d.get("name"),
                "ref": d.text,
                **({"aba": int(d.get("localSheetId"))} if (d.get("localSheetId") or "").isdigit() else {}),
            }
            for d in livro.raiz.findall("s:definedNames/s:definedName", NS)
        ],
        "links_externos": externos,
        "macros": any(n.lower().endswith("vbaproject.bin") for n in pk.partes),
        "partes": len(pk.partes),
    }


def _tabelas(pk: Pacote, parte: str) -> list[dict]:
    out = []
    for tipo, alvo in pk.rels(parte).values():
        if tipo == "table" and alvo in pk.partes:
            t = pk.arvore(alvo)
            out.append(
                {
                    "nome": t.get("displayName") or t.get("name"),
                    "ref": t.get("ref"),
                    "parte": "/" + alvo,
                    "cabecalho": int(t.get("headerRowCount", "1")) if t.get("headerRowCount", "1").isdigit() else 1,
                    "totais": int(t.get("totalsRowCount", "0")) if t.get("totalsRowCount", "0").isdigit() else 0,
                }
            )
    return out


# ------------------------------------------------------------------ edição
class _Aba:
    """Índice de uma aba para escrever em O(log n): linhas e células por número, e as áreas que não aceitam escrita
    célula a célula (calculadas uma vez: escrever não cria mesclagem, matriz nem tabela)."""

    def __init__(self, ed, nome: str, parte: str):
        self.nome, self.parte = nome, parte
        self.ws = ed.pk.arvore(parte)
        self.dados = self.ws.find("s:sheetData", NS)
        if self.dados is None:
            raise OperacaoInvalida("operacao-invalida", f"aba {nome!r} sem sheetData")
        protecao = self.ws.find("s:sheetProtection", NS)
        self.protegida = protecao is not None and protecao.get("sheet") in ("1", "true")
        self.linhas: dict[int, object] = {}
        self.numeros: list[int] = []
        self.celulas: dict[int, dict[int, object]] = {}
        self.colunas: dict[int, list[int]] = {}
        sem_r = False
        for row in self.dados.findall("s:row", NS):
            if not (row.get("r") or "").isdigit():
                sem_r = True
                continue
            lin = int(row.get("r"))
            self.linhas[lin] = row
            cels = {}
            for c in row.findall("s:c", NS):
                try:
                    cels[_ref(c.get("r") or "")[0]] = c
                except ValueError:
                    sem_r = True
            self.celulas[lin] = cels
            self.colunas[lin] = sorted(cels)
        self.numeros = sorted(self.linhas)
        self.sem_r = sem_r  # linha ou célula sem referência: posição implícita, não dá para inserir com segurança
        self.proibidas: list[tuple[tuple, str]] = []  # vira índice no fim do __init__ (_indexar)
        for m in self.ws.findall("s:mergeCells/s:mergeCell", NS):
            area = _intervalo(m.get("ref"))
            self.proibidas.append(
                ((area[0] + 1, area[1], area[2], area[3]), f"fica dentro da mesclagem {m.get('ref')}")
            )
            self.proibidas.append(
                ((area[0], area[1] + 1, area[0], area[3]), f"fica dentro da mesclagem {m.get('ref')}")
            )
        for f in self.ws.iterfind("s:sheetData/s:row/s:c/s:f", NS):
            tipo, ref = f.get("t"), f.get("ref")
            if tipo in ("array", "dataTable") and ref:
                rotulo = "fórmula matricial" if tipo == "array" else "tabela de dados (teste de hipóteses)"
                self.proibidas.append((_intervalo(ref), f"faz parte da {rotulo} {ref}"))
            elif tipo == "shared" and ref:
                col, lin = _ref(f.getparent().get("r"))
                self.proibidas.append(((col, lin, col, lin), f"é a origem de uma fórmula compartilhada ({ref})"))
        self.tabelas = _tabelas(ed.pk, parte)
        for t in self.tabelas:
            a = _intervalo(t["ref"])
            if t["cabecalho"]:
                self.proibidas.append(
                    ((a[0], a[1], a[2], a[1] + t["cabecalho"] - 1), f"é cabeçalho da tabela {t['nome']}")
                )
            if t["totais"]:
                self.proibidas.append(
                    ((a[0], a[3] - t["totais"] + 1, a[2], a[3]), f"é a linha de totais da tabela {t['nome']}")
                )
        for tipo, alvo in ed.pk.rels(parte).values():  # tabela dinâmica: a próxima atualização apaga o que entrar ali
            if tipo == "pivotTable" and alvo in ed.pk.partes:
                local = ed.pk.arvore(alvo).find("s:location", NS)
                if local is not None and local.get("ref"):
                    self.proibidas.append(
                        (_intervalo(local.get("ref")), f"fica dentro da tabela dinâmica {local.get('ref')}")
                    )
        self._indexar(ed)
        self.estilos_coluna = [
            (int(c.get("min")), int(c.get("max")), c.get("style"))
            for c in self.ws.findall("s:cols/s:col", NS)
            if c.get("style") and (c.get("min") or "").isdigit() and (c.get("max") or "").isdigit()
        ]

    def _indexar(self, ed):
        """Áreas proibidas por célula única (set) e por balde de BLOCO linhas: conferir uma escrita custa só as
        áreas daquele balde, não todas as da aba."""
        self.unicas: dict[tuple[int, int], str] = {}
        self.baldes: dict[int, list] = {}
        for k, (area, motivo) in enumerate(self.proibidas):
            if k % 4096 == 0:
                ed.no_prazo()
            if area[0] > area[2] or area[1] > area[3]:
                continue  # mesclagem de uma coluna ou de uma linha deixa uma das metades vazia
            if area[0] == area[2] and area[1] == area[3]:
                self.unicas.setdefault((area[0], area[1]), motivo)
                continue
            for b in range(area[1] // BLOCO, area[3] // BLOCO + 1):
                self.baldes.setdefault(b, []).append((area, motivo))
        del self.proibidas

    def conferir(self, col: int, lin: int, alvo: str):
        if self.protegida:
            raise OperacaoInvalida("operacao-invalida", f"a aba {self.nome!r} está protegida", alvo=alvo)
        if self.sem_r:
            raise OperacaoInvalida("operacao-invalida", f"a aba {self.nome!r} tem linhas sem referência", alvo=alvo)
        motivo = self.unicas.get((col, lin))
        motivo = motivo or next((m for area, m in self.baldes.get(lin // BLOCO, ()) if _dentro(col, lin, area)), None)
        if motivo:
            raise OperacaoInvalida("operacao-invalida", f"{alvo} {motivo}", alvo=alvo)

    def celula(self, col: int, lin: int, criar: bool):
        c = self.celulas.get(lin, {}).get(col)
        if c is not None or not criar:
            return c
        row = self.linhas.get(lin)
        if row is None:
            row = etree.Element(S + "row", r=str(lin))
            k = bisect.bisect_left(self.numeros, lin)
            if k > 0:
                self.linhas[self.numeros[k - 1]].addnext(row)
            else:
                self.dados.insert(0, row)
            self.numeros.insert(k, lin)
            self.linhas[lin], self.celulas[lin], self.colunas[lin] = row, {}, []
        c = etree.Element(S + "c", r=f"{_letras(col)}{lin}")
        estilo = row.get("s") if row.get("customFormat") in ("1", "true") else None
        estilo = estilo or next((s for a, b, s in self.estilos_coluna if a <= col <= b), None)
        if estilo:
            c.set("s", estilo)  # o formato que o Excel daria à célula vazia (moeda, data…) continua valendo
        cols = self.colunas[lin]
        k = bisect.bisect_left(cols, col)
        if k > 0:
            self.celulas[lin][cols[k - 1]].addnext(c)
        else:
            row.insert(0, c)
        cols.insert(k, col)
        self.celulas[lin][col] = c
        if row.get("spans"):  # dica opcional de leitura do Excel; com célula fora dela, sai
            del row.attrib["spans"]
        _ampliar_dimensao(self.ws, col, lin)
        return c


class _Editor:
    def __init__(self, dados: bytes):
        self.pk = Pacote(dados)
        self.livro = _Livro(self.pk)
        self.cache: dict[str, _Aba] = {}
        self.escritas = 0
        self.crescimento = 0
        self.recalcular = False
        self.avisos: list[str] = []
        self.prazo_final = time.monotonic() + EDICAO_PRAZO

    def no_prazo(self):
        if time.monotonic() > self.prazo_final:
            raise OperacaoInvalida(
                "operacao-invalida", f"a edição passou de {EDICAO_PRAZO} s: divida em pedidos menores"
            )

    def aba(self, nome) -> _Aba:
        for _, n, parte in self.livro.abas():
            if n == nome:
                if parte not in self.cache:
                    self.cache[parte] = _Aba(self, n, parte)
                self.cache[parte].nome = n
                return self.cache[parte]
        raise OperacaoInvalida("alvo-inexistente", f"aba {nome!r} não existe", alvo=str(nome))

    def celula_alvo(self, alvo):
        if not isinstance(alvo, str) or "!" not in alvo:
            raise OperacaoInvalida("alvo-invalido", f"alvo de célula é Aba!A1: {alvo!r}", alvo=str(alvo))
        nome, _, ref = alvo.rpartition("!")
        nome = nome[1:-1].replace("''", "'") if len(nome) > 1 and nome[0] == nome[-1] == "'" else nome
        try:
            col, lin = _ref(ref.upper())
        except ValueError:
            raise OperacaoInvalida("alvo-invalido", f"referência inválida: {ref!r}", alvo=alvo) from None
        return self.aba(nome), col, lin


def _ampliar_dimensao(ws, col, lin):
    dim = ws.find("s:dimension", NS)
    if dim is None:
        return
    try:
        c1, l1, c2, l2 = _intervalo(dim.get("ref", "A1"))
    except ValueError:
        return
    c1, l1, c2, l2 = min(c1, col), min(l1, lin), max(c2, col), max(l2, lin)
    dim.set("ref", f"{_letras(c1)}{l1}" + ("" if (c1, l1) == (c2, l2) else f":{_letras(c2)}{l2}"))


class _Contexto:
    """O que uma fórmula pode citar: abas e nomes definidos da pasta (em minúsculas)."""

    def __init__(self, ed):
        self.abas = {x.get("name", "").casefold() for x in ed.livro.todas()}
        self.nomes = {
            (d.get("name") or "").casefold() for d in ed.livro.raiz.findall("s:definedNames/s:definedName", NS)
        }


_PREFIXO = re.compile(r"(?:'((?:[^']|'')+)'|([^\s!:'\"\[\]()]+(?::[^\s!:'\"\[\]()]+)?))!")
_CEL = r"\$?[A-Za-z]{1,3}\$?[0-9]{1,7}"
_AREA = re.compile(rf"{_CEL}(?::{_CEL})?|\$?[A-Za-z]{{1,3}}:\$?[A-Za-z]{{1,3}}|\$?[0-9]{{1,7}}:\$?[0-9]{{1,7}}")
_PONTA = re.compile(rf"{_CEL}|\$?[A-Za-z]{{1,3}}|\$?[0-9]{{1,7}}")
_NOME_DEFINIDO = re.compile(r"[^\W\d][\w.\\?]*|\\[\w.\\?]*")  # letras com acento valem (Preço, Março)
_CELULA = re.compile(_CEL)
_NUMERO = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")  # só o que o Excel lê como número


def _dentro_dos_limites(ref: str) -> bool:
    for parte in ref.replace("$", "").split(":"):
        m = re.fullmatch(r"([A-Za-z]{0,3})([0-9]{0,7})", parte)
        if not m or (m.group(1) and _coluna(m.group(1).upper()) > 16_384):
            return False
        if m.group(2) and not 1 <= int(m.group(2)) <= 1_048_576:
            return False
    return True


def _referencia(v: str, ctx: _Contexto, erro):
    """Lista branca de operando de referência: [aba!]A1, A1:B2, A:A, 1:1, Aba!A1:Aba!B2 ou nome definido existente.
    Qualquer outra coisa, inclusive referência estruturada de tabela, é recusada (o Excel repararia o arquivo)."""
    m = _PREFIXO.match(v)
    resto = v
    if m:
        nomes = (m.group(1).replace("''", "'") if m.group(1) else m.group(2)).split(":")
        for nome in nomes:
            if nome.casefold() not in ctx.abas:
                raise erro(f"a aba {nome!r} não existe")
        resto = v[m.end() :]
    elif v.startswith("["):
        raise erro("referência a outro arquivo não é escrita pela edição")
    if _AREA.fullmatch(resto) and _dentro_dos_limites(resto):
        return
    if ":" in resto and "!" in resto:  # Aba!A1:Aba!B2
        esquerda, direita = resto.split(":", 1)
        if _PONTA.fullmatch(esquerda) and _dentro_dos_limites(esquerda):
            m2 = _PREFIXO.match(direita)
            if m2 and _PONTA.fullmatch(direita[m2.end() :]) and _dentro_dos_limites(direita[m2.end() :]):
                return _referencia(direita, ctx, erro)
    if "[" in resto:
        # Tabela[Col], Tabela[@Col]: a forma gravada no arquivo e as colunas da tabela precisariam de conferência
        # própria; sem ela o Excel repara. A edição não escreve: use o intervalo de células (B2:B10).
        raise erro("referência estruturada de tabela não é escrita pela edição; use o intervalo de células")
    if _NOME_DEFINIDO.fullmatch(resto) and not _CELULA.fullmatch(resto):
        if resto.casefold() in ctx.nomes:
            return
        raise erro(f"o nome {resto!r} não existe na pasta")
    raise erro(f"referência inválida {v!r}")


def _formula(texto: str, alvo: str, ctx: _Contexto) -> str:
    """Fórmula pronta para o arquivo, ou recusa com o motivo. Lista branca: operando e operador alternados, parênteses
    e chaves com par e não vazios, separadores no lugar, referências válidas e existentes, constantes de matriz só com
    valores, texto até 255 caracteres, limites do Excel, e funções conhecidas (em inglês; as do Excel 2010+ ganham o
    prefixo _xlfn.)."""
    if not texto.startswith("=") or len(texto) < 2:
        raise OperacaoInvalida("operacao-invalida", "fórmula começa com = (ex.: =SUM(A1:A3), em inglês)", alvo=alvo)
    if _utf16(texto) > FORMULA_MAX:  # antes de tokenizar: o tokenizador não é linear em texto hostil
        raise OperacaoInvalida("operacao-invalida", f"fórmula passa de {FORMULA_MAX} caracteres", alvo=alvo)
    try:
        tok = Tokenizer(texto)
    except Exception:  # noqa: BLE001 — o tokenizador do openpyxl recusa sintaxe quebrada com erros variados
        raise OperacaoInvalida(
            "operacao-invalida", f"fórmula com sintaxe inválida: {texto[:80]!r}", alvo=alvo
        ) from None

    def erro(motivo):
        return OperacaoInvalida("operacao-invalida", f"fórmula inválida ({motivo}): {texto[:80]!r}", alvo=alvo)

    itens = list(tok.items)
    pilha, espera_valor, prefixo, uniao = [], True, False, False  # pilha: [tipo, argumentos, vazio, colunas]
    for k, item in enumerate(itens):
        tipo, sub, v = item.type, item.subtype, item.value
        em_matriz = bool(pilha) and pilha[-1][0] == "ARRAY"
        if tipo == "WHITE-SPACE":
            prox = next((x for x in itens[k + 1 :] if x.type != "WHITE-SPACE"), None)
            if not espera_valor and prox is not None and (prox.type == "OPERAND" or prox.subtype == "OPEN"):
                antes = next((x for x in reversed(itens[:k]) if x.type != "WHITE-SPACE"), None)
                referencia_antes = antes is not None and (antes.subtype in ("RANGE", "CLOSE"))
                referencia_depois = prox.subtype in ("RANGE", "OPEN")
                if em_matriz or not (referencia_antes and referencia_depois):
                    raise erro("espaço entre valores: a interseção só vale entre intervalos")
                espera_valor = True  # espaço entre dois intervalos é o operador de interseção
            continue
        if pilha and sub != "CLOSE":
            pilha[-1][2] = False
        anterior = next((x for x in reversed(itens[:k]) if x.type != "WHITE-SPACE"), None)
        if tipo == "OPERAND" and sub == "RANGE" and v.startswith(":") and not espera_valor and not em_matriz:
            if anterior is None or anterior.subtype != "CLOSE" or anterior.type not in ("FUNC", "PAREN"):
                raise erro("intervalo com : só depois de referência")
            _referencia(v[1:], ctx, erro)  # INDEX(...):A10
            prefixo = False
            continue
        if tipo == "OPERAND":
            if not espera_valor:
                raise erro("falta operador entre dois valores")
            if em_matriz and sub not in ("NUMBER", "TEXT", "LOGICAL", "ERROR"):
                raise erro("matriz constante só aceita números, textos, VERDADEIRO/FALSO e erros")
            if sub == "NUMBER" and not _NUMERO.fullmatch(v):
                raise erro(f"número {v!r} fora do formato do Excel")
            if uniao and sub != "RANGE":
                raise erro("vírgula entre valores: use ponto como separador decimal (1.5)")
            if sub == "TEXT" and _utf16(v[1:-1].replace('""', '"')) > 255:
                raise erro("texto entre aspas passa de 255 caracteres")
            if sub == "RANGE":
                _referencia(v, ctx, erro)
            espera_valor, prefixo, uniao = False, False, False
        elif sub == "OPEN":
            if not espera_valor:
                raise erro("falta operador antes do parêntese")
            if em_matriz:
                raise erro("matriz constante só aceita valores")
            if tipo == "FUNC":
                nome = v[:-1]
                if ":" in nome:  # A1:INDEX(...)
                    ponta, nome = nome.rsplit(":", 1)
                    _referencia(ponta, ctx, erro)
                    item.value = ponta + ":" + _nome_funcao(nome, erro) + "("
                else:
                    item.value = _nome_funcao(nome, erro) + "("
                if sum(1 for x in pilha if x[0] == "FUNC") >= FUNCOES_ANINHADAS_MAX:
                    raise erro(f"mais de {FUNCOES_ANINHADAS_MAX} funções aninhadas")
            pilha.append([tipo, 0, True, None])
            espera_valor, prefixo = True, False
        elif sub == "CLOSE":
            if prefixo:
                raise erro("operador sem valor depois")
            if not pilha or pilha[-1][0] != tipo:
                raise erro("parêntese ou chave sem par")
            aberto = pilha.pop()
            if espera_valor and not (tipo == "FUNC" and (aberto[2] or aberto[1])):
                raise erro("parêntese vazio ou valor faltando antes de fechar")
            if tipo == "ARRAY" and aberto[3] is not None and aberto[3] != aberto[1]:
                raise erro("linhas da matriz com tamanhos diferentes")
            espera_valor = False
        elif tipo == "SEP":
            if prefixo:
                raise erro("operador sem valor depois")
            if not pilha:
                raise erro("separador fora de função")
            if sub == "ROW" and pilha[-1][0] != "ARRAY":
                raise erro("separe os argumentos com vírgula, não ponto e vírgula")
            if espera_valor and pilha[-1][0] != "FUNC":  # argumento vazio só vale em função: IF(A1,,1)
                raise erro("valor faltando antes do separador")
            if sub == "ROW":  # matriz: toda linha tem o mesmo número de colunas
                topo = pilha[-1]
                if topo[3] is None:
                    topo[3] = topo[1]
                elif topo[3] != topo[1]:
                    raise erro("linhas da matriz com tamanhos diferentes")
                topo[1] = 0
            else:
                pilha[-1][1] += 1
                if pilha[-1][0] == "FUNC" and pilha[-1][1] >= ARGUMENTOS_MAX:
                    raise erro(f"mais de {ARGUMENTOS_MAX} argumentos")
            espera_valor = True
        elif tipo == "OPERATOR-PREFIX":
            if not espera_valor:
                raise erro("operador fora do lugar")
            prefixo = True
        elif tipo == "OPERATOR-INFIX":
            if espera_valor or em_matriz:
                raise erro("operador sem valor antes")
            if v == "," and not (
                pilha
                and pilha[-1][0] == "PAREN"
                and anterior is not None
                and anterior.subtype in ("RANGE", "CLOSE")
                and anterior.type != "ARRAY"  # constante de matriz nunca é referência
            ):
                raise erro("vírgula fora de função: use ponto como separador decimal (1.5)")
            uniao = v == ","  # união (A1,B1): o operando seguinte também precisa ser referência
            espera_valor = True
        elif tipo == "OPERATOR-POSTFIX":
            if espera_valor:
                raise erro("operador sem valor antes")
        else:
            raise erro(f"elemento inesperado {v!r}")
    if pilha:
        raise erro("parêntese ou chave sem par")
    if espera_valor:
        raise erro("termina sem valor")
    final = tok.render()
    if _utf16(final) > FORMULA_MAX:
        raise OperacaoInvalida("operacao-invalida", f"fórmula passa de {FORMULA_MAX} caracteres", alvo=alvo)
    return final


def _nome_funcao(bruto: str, erro) -> str:
    """Nome da função como o arquivo guarda: maiúsculas, prefixo _xlfn. só onde o Excel exige."""
    nome = bruto.upper()
    while nome.startswith(("_XLFN.", "_XLWS.")):  # quem já mandou com prefixo passa pela mesma conferência
        nome = nome.split(".", 1)[1]
    if nome.startswith("_XLUDF."):
        raise erro("função de suplemento ou macro não é escrita pela edição")
    if nome in _MATRIZ_DINAMICA:
        raise erro(f"{nome} devolve matriz dinâmica, que a edição não escreve")
    if nome in _XLFN:
        return "_xlfn." + nome
    if nome in FORMULAE:
        return nome
    raise erro(f"função desconhecida {nome}; use o nome em inglês, como SUM e IF")


def _valor(valor, alvo: str):
    """Confere um valor de célula (número, texto, booleano ou null)."""
    if isinstance(valor, bool) or valor is None:
        return valor
    if isinstance(valor, int):
        if abs(valor) > 2**53:
            raise OperacaoInvalida(
                "operacao-invalida", "número inteiro grande demais (o Excel guarda 15 dígitos)", alvo=alvo
            )
        return valor
    if isinstance(valor, float):
        if not math.isfinite(valor):
            raise OperacaoInvalida("operacao-invalida", "número precisa ser finito", alvo=alvo)
        return valor
    if isinstance(valor, str):
        if _utf16(valor) > TEXTO_CELULA_MAX:
            raise OperacaoInvalida("operacao-invalida", f"texto passa de {TEXTO_CELULA_MAX} caracteres", alvo=alvo)
        return valor
    raise OperacaoInvalida("operacao-invalida", "valor é número, texto, booleano ou null", alvo=alvo)


def _escrever(ed: _Editor, aba: _Aba, col: int, lin: int, alvo: str, valor=None, formula=None) -> dict:
    aba.conferir(col, lin, alvo)
    _avisar_tabela(ed, aba, col, lin, alvo)
    c = aba.celula(col, lin, criar=True)
    antes = {"valor": ed.livro.valor(c)}
    f_antes = c.find("s:f", NS)
    if f_antes is not None:
        antes["formula"] = "=" + (f_antes.text or "")
    for x in list(c):
        c.remove(x)
    for atributo in ("t", "vm", "cm"):  # vm/cm apontam para metadados (imagem na célula, matriz) do valor antigo
        if atributo in c.attrib:
            del c.attrib[atributo]
    if formula is not None:
        etree.SubElement(c, S + "f").text = formula.removeprefix("=")
        ed.crescimento += len(formula)
    elif isinstance(valor, bool):
        c.set("t", "b")
        etree.SubElement(c, S + "v").text = "1" if valor else "0"
    elif isinstance(valor, (int, float)):
        etree.SubElement(c, S + "v").text = repr(valor) if isinstance(valor, float) else str(valor)
    elif isinstance(valor, str):
        c.set("t", "inlineStr")  # não mexe na tabela de textos compartilhados, que outras células usam
        t = etree.SubElement(etree.SubElement(c, S + "is"), S + "t")
        t.text = _codificar(valor)  # _xHHHH_ é escape no Excel: o literal pedido continua literal
        if valor != valor.strip():
            t.set(XML_SPACE, "preserve")
        ed.crescimento += len(valor)
    ed.recalcular = True  # fórmulas que leem a célula têm o resultado antigo guardado
    if ed.crescimento > CRESCIMENTO_MAX:
        raise OperacaoInvalida("texto-grande", "o texto resultante passa do limite de tamanho do documento")
    ed.escritas += 1
    if ed.escritas % 1024 == 0:
        ed.no_prazo()
    if ed.escritas > ESCRITAS_MAX:
        raise OperacaoInvalida("operacao-invalida", f"no máximo {ESCRITAS_MAX} células por pedido")
    ed.pk.tocar(aba.parte)
    return antes


def _avisar_tabela(ed: _Editor, aba: _Aba, col: int, lin: int, alvo: str):
    for t in aba.tabelas:
        a = _intervalo(t["ref"])
        if lin == a[3] + 1 and a[0] <= col <= a[2]:
            aviso = f"{aba.nome}: a linha {lin} fica logo abaixo da tabela {t['nome']}, que não cresce sozinha"
            if aviso not in ed.avisos:
                ed.avisos.append(aviso)


def _valor_da_op(ed: _Editor, op, alvo):
    if "formula" in op:
        return None, _formula(campo_texto(op, "formula"), alvo, _Contexto(ed))
    if "valor" not in op:
        raise OperacaoInvalida("operacao-invalida", "informe valor ou formula", alvo=alvo)
    return _valor(op["valor"], alvo), None


def _op_celula(ed: _Editor, op, rel):
    alvo = op.get("alvo")
    aba, col, lin = ed.celula_alvo(alvo)
    valor, formula = _valor_da_op(ed, op, alvo)
    if "se_valor" in op:
        c = aba.celula(col, lin, criar=False)
        atual = ed.livro.valor(c) if c is not None else None
        if atual != op["se_valor"]:
            raise OperacaoInvalida("conflito", f"o valor atual de {alvo} não é o esperado: {atual!r}", alvo=alvo)
    antes = _escrever(ed, aba, col, lin, alvo, valor, formula)
    rel.update(antes=antes, depois={"formula": formula} if formula else {"valor": valor}, partes=["/" + aba.parte])


def _op_intervalo(ed: _Editor, op, rel):
    alvo = op.get("alvo")
    aba, col, lin = ed.celula_alvo(alvo)
    valores = op.get("valores")
    if not isinstance(valores, list) or not valores or not all(isinstance(x, list) and x for x in valores):
        raise OperacaoInvalida("operacao-invalida", "valores é uma matriz: lista de linhas não vazias", alvo=alvo)
    ctx = _Contexto(ed)
    total = sum(len(x) for x in valores)
    if ed.escritas + total > ESCRITAS_MAX:
        raise OperacaoInvalida("operacao-invalida", f"no máximo {ESCRITAS_MAX} células por pedido", alvo=alvo)
    largura = max(len(x) for x in valores)
    if col + largura - 1 > 16_384 or lin + len(valores) - 1 > 1_048_576:
        raise OperacaoInvalida("operacao-invalida", "o intervalo passa do limite da planilha", alvo=alvo)
    for i, linha in enumerate(valores):
        for j, valor in enumerate(linha):
            ref = f"{aba.nome}!{_letras(col + j)}{lin + i}"
            if isinstance(valor, str) and valor.startswith("=") and len(valor) > 1:
                _escrever(ed, aba, col + j, lin + i, ref, formula=_formula(valor, ref, ctx))
            else:
                _escrever(ed, aba, col + j, lin + i, ref, valor=_valor(valor, ref))
    fim = f"{_letras(col + largura - 1)}{lin + len(valores) - 1}"
    rel.update(intervalo=f"{alvo.rpartition('!')[2]}:{fim}", celulas=total, partes=["/" + aba.parte])


# ------------------------------------------------------------------ renomear aba
def _citar(nome: str) -> str:
    """Nome de aba como aparece numa fórmula. Sem aspas só o que é só letras e não lembra referência (A1, R1C1)."""
    if _NOME_SIMPLES.fullmatch(nome) and not re.fullmatch(r"[A-Za-z]{1,3}|[RrCc]{1,2}", nome):
        return nome
    return "'" + nome.replace("'", "''") + "'"


def _trocar_aba(texto: str, antigo: str, novo: str) -> tuple[str, bool]:
    """Troca a aba `antigo` por `novo` nas referências de uma fórmula, preservando todo o resto (espaços, quebras).
    Entende nome com e sem aspas, intervalo 3D (Aba1:Aba3!A1), texto entre aspas duplas e referência a outro arquivo
    ([1]Aba!A1, que não é desta pasta). Devolve (texto novo, se mudou)."""
    out, i, n, mudou, externo = [], 0, len(texto), False, False
    while i < n:
        ch = texto[i]
        if ch == '"':  # texto literal: copia até a aspa que fecha ("" é aspa dentro do texto)
            j = i + 1
            while j < n and not (texto[j] == '"' and (j + 1 >= n or texto[j + 1] != '"')):
                j += 2 if texto[j] == '"' else 1
            out.append(texto[i : j + 1])
            i = j + 1
            continue
        if ch == "[":  # [1] de outro arquivo ou referência estruturada Tab[Col]: copia com os colchetes aninhados
            externo = i == 0 or not (texto[i - 1].isalnum() or texto[i - 1] in "_.")
            nivel, j = 0, i
            while j < n:
                nivel += {"[": 1, "]": -1}.get(texto[j], 0)
                j += 1
                if nivel == 0:
                    break
            out.append(texto[i:j])
            i = j
            continue
        if ch == "'":  # nome de aba entre aspas: '...''...'!
            j = i + 1
            while j < n and not (texto[j] == "'" and (j + 1 >= n or texto[j + 1] != "'")):
                j += 2 if texto[j] == "'" else 1
            bruto = texto[i : j + 1]
            if j + 1 < n and texto[j + 1] == "!" and not externo:
                nomes = bruto[1:-1].replace("''", "'").split(":")
                if any(x.casefold() == antigo.casefold() for x in nomes):
                    bruto = "'" + ":".join(_trocar(nomes, antigo, novo)).replace("'", "''") + "'"
                    mudou = True
            out.append(bruto)
            externo = False
            i = j + 1
            continue
        if ch == "#":  # código de erro (#REF!, #DIV/0!, #N/A): não é nome de aba
            j = i + 1
            while j < n and (texto[j].isalnum() or texto[j] in "/_"):
                j += 1
            j += 1 if j < n and texto[j] in "!?" else 0
            out.append(texto[i:j])
            externo, i = False, j
            continue
        if (ch.isalnum() or ch in "_.") and (i == 0 or not (texto[i - 1].isalnum() or texto[i - 1] in "_.$")):
            j = i
            while j < n and (texto[j].isalnum() or texto[j] in "_.:"):
                j += 1
            palavra = texto[i:j]
            if j < n and texto[j] == "!" and not externo:
                inicio_ref, nomes = "", palavra.split(":")
                if len(nomes) > 1 and i > 0 and texto[i - 1] == "!" and _PARTE_REF.fullmatch(nomes[0]):
                    # Aba!A1:Aba!B2: o A1 depois do ! é célula; sem ! antes, Jan:Dez! é intervalo 3D de abas
                    inicio_ref, nomes = nomes[0] + ":", nomes[1:]
                if any(x.casefold() == antigo.casefold() for x in nomes):
                    palavra = inicio_ref + _citar(":".join(_trocar(nomes, antigo, novo)))
                    mudou = True
            out.append(palavra)
            externo = False
            i = j
            continue
        out.append(ch)
        if ch not in "]":
            externo = False
        i += 1
    return "".join(out), mudou


def _trocar(nomes: list[str], antigo: str, novo: str) -> list[str]:
    return [novo if x.casefold() == antigo.casefold() else x for x in nomes]


_PARTE_REF = re.compile(r"\$?[A-Za-z]{1,3}\$?\d+|\$?[A-Za-z]{1,3}|\$?\d+")
_FORMULAS = {"f", "formula", "formula1", "formula2", "calculatedColumnFormula", "totalsRowFormula"}


def _renomear_em(ed: _Editor, parte: str, antigo: str, novo: str) -> int:
    """Troca a aba em toda fórmula da parte (em qualquer namespace: x14:formula1/xm:f, c:f, cx:f, c15:f…) e no
    destino interno dos hiperlinks."""
    trocas = 0
    for k, el in enumerate(ed.pk.arvore(parte).iter()):
        if k % 8192 == 0:
            ed.no_prazo()
        if not isinstance(el.tag, str):
            continue
        nome = etree.QName(el).localname
        if nome in _FORMULAS and el.text:
            novo_texto, mudou = _trocar_aba(el.text, antigo, novo)
        elif nome == "hyperlink" and el.get("location"):
            novo_local, mudou = _trocar_aba(el.get("location"), antigo, novo)
            if mudou:
                el.set("location", novo_local)
                trocas += 1
                ed.pk.tocar(parte)
            continue
        else:
            continue
        if mudou:
            el.text = novo_texto
            trocas += 1
            ed.pk.tocar(parte)
    return trocas


def _op_renomear_aba(ed: _Editor, op, rel):
    antigo, novo = op.get("aba"), op.get("nome")
    protecao = ed.livro.raiz.find("s:workbookProtection", NS)
    if protecao is not None and protecao.get("lockStructure") in ("1", "true"):
        raise OperacaoInvalida("operacao-invalida", "a estrutura da pasta de trabalho está protegida", alvo=str(antigo))
    sh = next((s for s in ed.livro.todas() if s.get("name") == antigo), None)
    if sh is None:
        raise OperacaoInvalida("alvo-inexistente", f"aba {antigo!r} não existe", alvo=str(antigo))
    if (
        not isinstance(novo, str)
        or not 0 < _utf16(novo) <= 31
        or _NOME_PROIBIDO.search(novo)
        or novo.startswith("'")
        or novo.endswith("'")
        or novo.lower() == "history"
    ):
        raise OperacaoInvalida(
            "operacao-invalida",
            "nome de aba tem de 1 a 31 caracteres, sem [ ] : * ? / \\ nem aspas nas pontas",
            alvo=str(antigo),
        )
    if any(s.get("name").lower() == novo.lower() for s in ed.livro.todas() if s is not sh):
        raise OperacaoInvalida("conflito", f"já existe uma aba chamada {novo!r}", alvo=antigo)
    sh.set("name", novo)
    ed.pk.tocar(ed.livro.parte)
    mexidas, trocas = {ed.livro.parte}, 0
    for d in ed.livro.raiz.findall("s:definedNames/s:definedName", NS):
        if d.text:
            texto, mudou = _trocar_aba(d.text, antigo, novo)
            if mudou:
                d.text, trocas = texto, trocas + 1
    for parte in list(ed.pk.partes):
        if parte in ed.pk.removidas or not parte.endswith(".xml") or "/_rels/" in parte or parte == ed.livro.parte:
            continue
        if parte.startswith(("xl/worksheets/", "xl/tables/", "xl/charts/", "xl/chartsheets/")):
            n = _renomear_em(ed, parte, antigo, novo)
        elif parte.startswith("xl/pivotCache/"):
            n = 0
            for src in ed.pk.arvore(parte).iter(S + "worksheetSource"):
                if src.get("sheet") == antigo:
                    src.set("sheet", novo)
                    n += 1
        elif parte == "docProps/app.xml":  # lista de abas que o Explorer e o Finder mostram
            n = 0
            for el in ed.pk.arvore(parte).iter():
                if isinstance(el.tag, str) and etree.QName(el).localname == "lpstr" and el.text:
                    if el.text == antigo:  # nome da aba
                        el.text, n = novo, n + 1
                    elif "!" in el.text:  # Aba!Print_Area e outros nomes com escopo de aba
                        texto, mudou = _trocar_aba(el.text, antigo, novo)
                        if mudou:
                            el.text, n = texto, n + 1
        else:
            continue
        if n:
            ed.pk.tocar(parte)
            mexidas.add(parte)
            trocas += n
    restos = [] if antigo.casefold() == novo.casefold() else _restos(ed, antigo)  # só a caixa: referência vale
    if restos:  # falha fechada: referência que não sabemos atualizar ficaria quebrada
        raise OperacaoInvalida(
            "operacao-invalida",
            f"a aba {antigo!r} é citada em {', '.join('/' + p for p in restos[:3])}, onde a edição não atualiza "
            "referências: renomeie no Excel",
            alvo=antigo,
        )
    rel.update(antes=antigo, depois=novo, referencias=trocas, partes=sorted("/" + p for p in mexidas))


_PARSER_TOLERANTE = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, recover=True)


_TEXTO_EXIBIDO = {"t", "v", "text"}  # texto de célula, valor em cache e texto de comentário: não são referência
_ATRIBUTOS_EXIBIDOS = {"prompt", "error", "promptTitle", "errorTitle", "display", "tooltip"}


def _restos(ed: _Editor, antigo: str) -> list[str]:
    """Partes do pacote (XML, VML e relações) que ainda citam a aba `antigo` como referência, fora de texto exibido.
    Varre tudo, inclusive o que o renomear não conhece. Parte que não dá para ler entra como resto (falha fechada)."""
    alvo, achadas = antigo.casefold(), []
    escapavel = bool(re.search(r"['&<>\"]", antigo)) or not antigo.isascii()
    for parte in ed.pk.partes:
        if parte in ed.pk.removidas or not parte.endswith((".xml", ".vml", ".rels")) or "sharedStrings" in parte:
            continue
        ed.no_prazo()
        if parte in ed.pk.arvores:
            raiz = ed.pk.arvores[parte]
        else:
            bruto = ed.pk.partes[parte]
            texto = bruto.decode("utf-8", "ignore")
            if (
                not escapavel
                and "&#" not in texto
                and not bruto.startswith((b"\xff\xfe", b"\xfe\xff"))
                and (alvo not in texto.casefold())
            ):
                continue  # atalho: o nome nem aparece (só vale sem escape possível)
            if b"<!DOCTYPE" in bruto or b"<!ENTITY" in bruto:
                achadas.append(parte)
                continue
            try:
                raiz = etree.fromstring(bruto, _PARSER_TOLERANTE)
            except etree.XMLSyntaxError:
                raiz = None
            if raiz is None:
                achadas.append(parte)
                continue
        for k, el in enumerate(raiz.iter()):
            if k % 8192 == 0:
                ed.no_prazo()
            if not isinstance(el.tag, str):
                continue
            valores = [] if etree.QName(el).localname in _TEXTO_EXIBIDO else [el.text or ""]
            for chave, valor in el.attrib.items():
                nome = etree.QName(chave).localname
                if nome not in _ATRIBUTOS_EXIBIDOS:
                    valores.append(valor[1:] if nome == "Target" and valor.startswith("#") else valor)
            if any("!" in v and _trocar_aba(v, antigo, "\x00")[1] for v in valores):
                achadas.append(parte)
                break
    return achadas


def _recalcular_ao_abrir(ed: _Editor):
    """O Excel recalcula tudo ao abrir, e a cadeia de cálculo antiga sai (ele refaz)."""
    raiz = ed.livro.raiz
    calc = raiz.find("s:calcPr", NS)
    if calc is None:  # na sequência do esquema, calcPr vem logo depois do último destes que existir
        calc = etree.Element(S + "calcPr")
        for nome in ("definedNames", "externalReferences", "functionGroups", "sheets"):
            depois_de = raiz.find(f"s:{nome}", NS)
            if depois_de is not None:
                depois_de.addnext(calc)
                break
    calc.set("fullCalcOnLoad", "1")
    ed.pk.tocar(ed.livro.parte)
    for rid, (tipo, alvo) in list(ed.livro.rels.items()):
        if tipo == "calcChain" and alvo in ed.pk.partes:
            rels = ed.pk.arvore(ed.pk.caminho_rels(ed.livro.parte))
            for r in rels.findall("rel:Relationship", NS):
                if r.get("Id") == rid:
                    rels.remove(r)
            ed.pk.tocar(ed.pk.caminho_rels(ed.livro.parte))
            ed.pk.removidas.add(alvo)
            ct = ed.pk.arvore("[Content_Types].xml")
            for o in ct.findall("ct:Override", NS_CT):
                if o.get("PartName", "").lstrip("/") == alvo:
                    ct.remove(o)
            ed.pk.tocar("[Content_Types].xml")


OPERACOES = {
    "celula": _op_celula,
    "intervalo": _op_intervalo,
    "renomear_aba": _op_renomear_aba,
}
_INESPERADOS = (KeyError, IndexError, ValueError, TypeError, AttributeError, StopIteration, OverflowError)


def aplicar(dados: bytes, operacoes: list[dict]) -> tuple[bytes, list[dict], list[str]]:
    """Tudo ou nada: aplica as operações em ordem; a primeira que falha cancela todas."""
    try:
        ed = _Editor(dados)
    except _INESPERADOS as e:
        if isinstance(e, (OperacaoInvalida, DocumentoRecusado)):
            raise
        raise OperacaoInvalida("operacao-invalida", f"XLSX fora do esperado ({type(e).__name__})") from None
    relatorio = []
    for k, op in enumerate(operacoes):
        if not isinstance(op, dict) or not isinstance(op.get("op"), str) or op["op"] not in OPERACOES:
            raise OperacaoInvalida(
                "operacao-invalida",
                f"op desconhecida: {op.get('op') if isinstance(op, dict) else op!r}; use uma de {', '.join(OPERACOES)}",
                indice=k,
            )
        rel = {"op": op["op"], **({"alvo": op["alvo"]} if "alvo" in op else {})}
        try:
            ed.no_prazo()
            OPERACOES[op["op"]](ed, op, rel)
        except OperacaoInvalida as e:
            e.indice = k
            raise
        except _INESPERADOS as e:
            raise OperacaoInvalida(
                "operacao-invalida", f"não foi possível aplicar ({type(e).__name__})", indice=k, alvo=op.get("alvo")
            ) from None
        relatorio.append(rel)
    if ed.recalcular:
        try:
            _recalcular_ao_abrir(ed)
        except _INESPERADOS as e:
            raise OperacaoInvalida("operacao-invalida", f"XLSX fora do esperado ({type(e).__name__})") from None
        ed.avisos.append("o Excel recalcula as fórmulas ao abrir; os valores calculados no código são os antigos")
    return ed.pk.salvar(), relatorio, ed.avisos


def integridade(dados: bytes) -> list[str]:
    return _integridade(dados, "xl/")
