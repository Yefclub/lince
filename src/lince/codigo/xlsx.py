"""Código de um XLSX: abas e células com ids naturais (`Aba!B2`), e edição cirúrgica por operações nomeadas.

A escrita não passa pelo openpyxl (que perde imagens, gráficos e partes que não conhece ao salvar): só o XML das
partes tocadas muda. Fórmula alterada faz o Excel recalcular tudo ao abrir (`fullCalcOnLoad`), e a cadeia de cálculo
antiga (`calcChain.xml`), que ficaria inconsistente, sai do pacote; o Excel a refaz.
"""

import math
import re
from datetime import datetime, timedelta

from lxml import etree
from openpyxl.formula.tokenizer import Tokenizer

from .pacote import (
    CRESCIMENTO_MAX,
    NS_CT,
    OperacaoInvalida,
    Pacote,
    campo_texto,
    sha,
)
from .pacote import integridade as _integridade

NS = {
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
}
S = f"{{{NS['s']}}}"
R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
TIPO_DOCUMENTO = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
CELULAS_MAX = 50_000  # células no código de um documento (o resto da aba fica marcado como truncado)
INTERVALO_MAX = 100_000  # células escritas somando todas as operações de um pedido
_REF = re.compile(r"\$?([A-Z]{1,3})\$?([1-9]\d{0,6})")
_NOME_PROIBIDO = re.compile(r"[\[\]:*?/\\]")
_FORMATOS_DATA = set(range(14, 23)) | {45, 46, 47}
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


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
        rels = pk.arvore("_rels/.rels")
        self.parte = next(
            r.get("Target").lstrip("/") for r in rels.findall("rel:Relationship", NS) if r.get("Type") == TIPO_DOCUMENTO
        )
        self.raiz = pk.arvore(self.parte)
        self.rels = pk.rels(self.parte)
        alvo = next((a for t, a in self.rels.values() if t == "sharedStrings"), None)
        self.compartilhadas = []
        if alvo in pk.partes:
            for si in pk.arvore(alvo).findall("s:si", NS):
                self.compartilhadas.append("".join(t.text or "" for t in si.iter(S + "t") if _fora_de_fonetica(t)))
        self.datas = self._estilos_de_data()

    def abas(self):
        """(elemento sheet, nome, parte) na ordem do livro."""
        for sh in self.raiz.findall("s:sheets/s:sheet", NS):
            tipo, parte = self.rels.get(sh.get(R_ID), (None, None))
            if tipo == "worksheet":
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
            codigo = re.sub(r'"[^"]*"|\[[^\]]*\]', "", proprios.get(fid, "")).lower()
            if fid in _FORMATOS_DATA or (fid in proprios and re.search(r"[dy]|m{3,}", codigo)):
                datas.add(k)
        return datas

    def valor(self, c):
        """Valor exibível da célula: número, texto, booleano, erro (#N/A) ou None."""
        t = c.get("t", "n")
        if t == "inlineStr":
            return "".join(x.text or "" for x in c.iter(S + "t") if _fora_de_fonetica(x))
        v = c.find("s:v", NS)
        if v is None or v.text is None:
            return None
        if t == "s":
            k = int(v.text)
            return self.compartilhadas[k] if 0 <= k < len(self.compartilhadas) else None
        if t == "b":
            return v.text == "1"
        if t in ("e", "str"):
            return v.text
        numero = float(v.text)
        return int(numero) if numero.is_integer() and abs(numero) < 2**53 else numero


def _fora_de_fonetica(t) -> bool:
    return not any(etree.QName(a).localname == "rPh" for a in t.iterancestors())


def _data(numero) -> str | None:
    if not isinstance(numero, (int, float)) or isinstance(numero, bool) or not 0 <= numero < 2_958_466:
        return None
    base = datetime(1899, 12, 30) + timedelta(days=float(numero))
    return base.date().isoformat() if float(numero).is_integer() else base.isoformat(timespec="seconds")


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
                cel["formula"] = "=" + (f.text or "") if f.text else None
                if f.get("t") == "shared":
                    cel["formula_compartilhada"] = f.get("si")
            if c.get("s", "").isdigit() and int(c.get("s")) in livro.datas and (d := _data(valor)):
                cel["data"] = d
            celulas.append(cel)
            total += 1
        dim = ws.find("s:dimension", NS)
        abas.append(
            {
                "id": nome,
                "ordem": len(abas) + 1,
                "estado": sh.get("state", "visible"),
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
                **({"aba": int(d.get("localSheetId"))} if d.get("localSheetId") else {}),
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
            out.append({"nome": t.get("displayName") or t.get("name"), "ref": t.get("ref"), "parte": "/" + alvo})
    return out


# ------------------------------------------------------------------ edição
class _Editor:
    def __init__(self, dados: bytes):
        self.pk = Pacote(dados)
        self.livro = _Livro(self.pk)
        self.escritas = 0
        self.crescimento = 0
        self.recalcular = False
        self.avisos: list[str] = []

    def aba(self, nome) -> tuple[object, str]:
        for sh, n, parte in self.livro.abas():
            if n == nome:
                return sh, parte
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
        _, parte = self.aba(nome)
        return parte, col, lin


def _linha(ws, lin: int):
    dados = ws.find("s:sheetData", NS)
    anterior = None
    for row in dados.findall("s:row", NS):
        r = int(row.get("r", "0"))
        if r == lin:
            return row
        if r > lin:
            break
        anterior = row
    nova = etree.Element(S + "row", r=str(lin))
    if anterior is not None:
        anterior.addnext(nova)
    else:
        dados.insert(0, nova)
    return nova


def _celula(ws, col: int, lin: int):
    row = _linha(ws, lin)
    if row.find("s:c", NS) is not None and row.find("s:c", NS).get("r") is None:
        raise OperacaoInvalida("operacao-invalida", f"linha {lin} usa células sem referência (r); não dá para editar")
    anterior = None
    for c in row.findall("s:c", NS):
        cc, _ = _ref(c.get("r"))
        if cc == col:
            return c
        if cc > col:
            break
        anterior = c
    nova = etree.Element(S + "c", r=f"{_letras(col)}{lin}")
    if anterior is not None:
        anterior.addnext(nova)
    else:
        row.insert(0, nova)
    if row.get("spans"):  # dica de leitura do Excel; com célula fora dela, sai (é opcional)
        del row.attrib["spans"]
    _ampliar_dimensao(ws, col, lin)
    return nova


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


def _conferir_area(ed: _Editor, parte: str, ws, col: int, lin: int, alvo: str):
    """Células que não podem ser escritas uma a uma sem quebrar o arquivo ou o que ele calcula."""
    for m in ws.findall("s:mergeCells/s:mergeCell", NS):
        area = _intervalo(m.get("ref"))
        if _dentro(col, lin, area) and (col, lin) != (area[0], area[1]):
            raise OperacaoInvalida("operacao-invalida", f"{alvo} fica dentro da mesclagem {m.get('ref')}", alvo=alvo)
    for f in ws.iterfind("s:sheetData/s:row/s:c/s:f", NS):
        if f.get("t") == "array" and f.get("ref") and _dentro(col, lin, _intervalo(f.get("ref"))):
            raise OperacaoInvalida(
                "operacao-invalida", f"{alvo} faz parte da fórmula matricial {f.get('ref')}", alvo=alvo
            )
        if f.get("t") == "shared" and f.get("ref") and _ref(f.getparent().get("r")) == (col, lin):
            raise OperacaoInvalida(
                "operacao-invalida", f"{alvo} é a origem de uma fórmula compartilhada ({f.get('ref')})", alvo=alvo
            )
    for t in _tabelas(ed.pk, parte):
        area = _intervalo(t["ref"])
        tabela = ed.pk.arvore(t["parte"].lstrip("/"))
        cabecalho = int(tabela.get("headerRowCount", "1"))
        if cabecalho and _dentro(col, lin, (area[0], area[1], area[2], area[1] + cabecalho - 1)):
            raise OperacaoInvalida(
                "operacao-invalida",
                f"{alvo} é cabeçalho da tabela {t['nome']}: o nome da coluna mora na tabela",
                alvo=alvo,
            )


def _escrever(ed: _Editor, parte: str, col: int, lin: int, alvo: str, valor=None, formula=None) -> dict:
    ws = ed.pk.arvore(parte)
    _conferir_area(ed, parte, ws, col, lin, alvo)
    c = _celula(ws, col, lin)
    antes = {"valor": ed.livro.valor(c)}
    f_antes = c.find("s:f", NS)
    if f_antes is not None:
        antes["formula"] = "=" + (f_antes.text or "")
        ed.recalcular = True  # quem dependia da fórmula antiga precisa recalcular
    for x in list(c):
        c.remove(x)
    if "t" in c.attrib:
        del c.attrib["t"]
    if formula is not None:
        f = etree.SubElement(c, S + "f")
        f.text = formula.removeprefix("=")
        ed.recalcular = True
        ed.crescimento += len(formula)
    elif isinstance(valor, bool):
        c.set("t", "b")
        etree.SubElement(c, S + "v").text = "1" if valor else "0"
    elif isinstance(valor, (int, float)):
        if not math.isfinite(valor):
            raise OperacaoInvalida("operacao-invalida", "número precisa ser finito", alvo=alvo)
        etree.SubElement(c, S + "v").text = repr(valor) if isinstance(valor, float) else str(valor)
        ed.recalcular = True  # fórmulas que leem a célula têm resultado guardado antigo
    elif isinstance(valor, str):
        c.set("t", "inlineStr")  # não mexe na tabela de textos compartilhados, que outras células usam
        t = etree.SubElement(etree.SubElement(c, S + "is"), S + "t")
        t.text = valor
        if valor != valor.strip():
            t.set(XML_SPACE, "preserve")
        ed.crescimento += len(valor)
        ed.recalcular = True
    elif valor is None:
        ed.recalcular = True
    else:
        raise OperacaoInvalida("operacao-invalida", "valor é número, texto, booleano ou null", alvo=alvo)
    if ed.crescimento > CRESCIMENTO_MAX:
        raise OperacaoInvalida("texto-grande", "o texto resultante passa do limite de tamanho do documento")
    ed.escritas += 1
    if ed.escritas > INTERVALO_MAX:
        raise OperacaoInvalida("operacao-invalida", f"no máximo {INTERVALO_MAX} células por pedido")
    ed.pk.tocar(parte)
    return antes


def _valor_da_op(op, alvo):
    if "formula" in op:
        f = campo_texto(op, "formula")
        if not f.startswith("=") or len(f) < 2:
            raise OperacaoInvalida(
                "operacao-invalida", "formula começa com = (ex.: =SOMA(A1:A3) em inglês: =SUM)", alvo=alvo
            )
        if len(f) > 8192:
            raise OperacaoInvalida(
                "operacao-invalida", "fórmula passa de 8.192 caracteres (limite do Excel)", alvo=alvo
            )
        return None, f
    if "valor" not in op:
        raise OperacaoInvalida("operacao-invalida", "informe valor ou formula", alvo=alvo)
    valor = op["valor"]
    if isinstance(valor, str):
        valor = campo_texto(op, "valor")
        if len(valor) > 32_767:
            raise OperacaoInvalida("operacao-invalida", "texto passa de 32.767 caracteres (limite do Excel)", alvo=alvo)
    return valor, None


def _op_celula(ed: _Editor, op, rel):
    alvo = op.get("alvo")
    parte, col, lin = ed.celula_alvo(alvo)
    valor, formula = _valor_da_op(op, alvo)
    if "se_valor" in op:
        atual = ed.livro.valor(_celula_existente(ed.pk.arvore(parte), col, lin))
        if atual != op["se_valor"]:
            raise OperacaoInvalida("conflito", f"o valor atual de {alvo} não é o esperado: {atual!r}", alvo=alvo)
    antes = _escrever(ed, parte, col, lin, alvo, valor, formula)
    rel.update(antes=antes, depois={"formula": formula} if formula else {"valor": valor}, partes=["/" + parte])


def _celula_existente(ws, col, lin):
    for c in ws.iterfind("s:sheetData/s:row/s:c", NS):
        if c.get("r") and _ref(c.get("r")) == (col, lin):
            return c
    return etree.Element(S + "c")


def _op_intervalo(ed: _Editor, op, rel):
    alvo = op.get("alvo")
    parte, col, lin = ed.celula_alvo(alvo)
    valores = op.get("valores")
    if not isinstance(valores, list) or not valores or not all(isinstance(x, list) for x in valores):
        raise OperacaoInvalida("operacao-invalida", "valores é uma matriz: lista de linhas", alvo=alvo)
    if sum(len(x) for x in valores) > INTERVALO_MAX:
        raise OperacaoInvalida("operacao-invalida", f"no máximo {INTERVALO_MAX} células por pedido", alvo=alvo)
    largura = max(len(x) for x in valores)
    if col + largura - 1 > 16_384 or lin + len(valores) - 1 > 1_048_576:
        raise OperacaoInvalida("operacao-invalida", "o intervalo passa do limite da planilha", alvo=alvo)
    for i, linha in enumerate(valores):
        for j, valor in enumerate(linha):
            ref = f"{_letras(col + j)}{lin + i}"
            if isinstance(valor, str) and len(valor) > 32_767:
                raise OperacaoInvalida("operacao-invalida", f"{ref}: texto passa de 32.767 caracteres", alvo=alvo)
            if isinstance(valor, str) and valor.startswith("=") and len(valor) > 1:
                _escrever(ed, parte, col + j, lin + i, ref, formula=valor)
            else:
                _escrever(ed, parte, col + j, lin + i, ref, valor=valor)
    fim = f"{_letras(col + largura - 1)}{lin + len(valores) - 1}"
    rel.update(intervalo=f"{alvo.rpartition('!')[2]}:{fim}", celulas=sum(len(x) for x in valores), partes=["/" + parte])


def _citar(nome: str) -> str:
    return (
        nome
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", nome) and not _REF.fullmatch(nome.upper())
        else ("'" + nome.replace("'", "''") + "'")
    )


def _renomear_em_formula(texto: str, antigo: str, novo: str) -> str:
    """Troca a aba citada nos operandos de intervalo da fórmula (texto entre aspas e nomes não mudam)."""
    if not texto:
        return texto
    com_igual = texto.startswith("=")
    try:
        tok = Tokenizer(texto if com_igual else "=" + texto)
    except Exception:  # noqa: BLE001 — fórmula que o tokenizador não entende fica como está
        return texto
    prefixos = {f"{antigo}!", f"'{antigo.replace(chr(39), chr(39) * 2)}'!"}
    saida = []
    for item in tok.items:
        v = item.value
        if item.type == "OPERAND" and item.subtype == "RANGE":  # "[1]Aba!A1" é outro arquivo e não casa
            for p in prefixos:
                if v.startswith(p):
                    v = _citar(novo) + "!" + v[len(p) :]
                    break
        saida.append(v)
    resultado = "=" + "".join(saida) if com_igual else "".join(saida)
    return resultado if tok.render() == ("=" + texto if not com_igual else texto) else texto


def _op_renomear_aba(ed: _Editor, op, rel):
    antigo, novo = op.get("aba"), op.get("nome")
    sh, _ = ed.aba(antigo)
    if (
        not isinstance(novo, str)
        or not 0 < len(novo) <= 31
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
    if any(n.lower() == novo.lower() for _, n, _ in ed.livro.abas() if n != antigo):
        raise OperacaoInvalida("conflito", f"já existe uma aba chamada {novo!r}", alvo=antigo)
    for pivo in [
        p for p in ed.pk.partes if p.startswith("xl/pivotCache/") and p.endswith(".xml") and "/_rels/" not in p
    ]:
        for src in ed.pk.arvore(pivo).iter(S + "worksheetSource"):
            if src.get("sheet") == antigo:
                src.set("sheet", novo)
                ed.pk.tocar(pivo)
    sh.set("name", novo)
    ed.pk.tocar(ed.livro.parte)
    trocas = 0
    for d in ed.livro.raiz.findall("s:definedNames/s:definedName", NS):
        novo_texto = _renomear_em_formula(d.text, antigo, novo)
        if novo_texto != d.text:
            d.text, trocas = novo_texto, trocas + 1
    for _, _, parte in ed.livro.abas():
        ws = ed.pk.arvore(parte)
        for el in ws.iter(S + "f", S + "formula", S + "formula1", S + "formula2"):
            novo_texto = _renomear_em_formula(el.text, antigo, novo)
            if novo_texto != el.text:
                el.text, trocas = novo_texto, trocas + 1
                ed.pk.tocar(parte)
    for grafico in [p for p in ed.pk.partes if p.startswith("xl/charts/chart") and p.endswith(".xml")]:
        for el in ed.pk.arvore(grafico).iter(f"{{{NS['c']}}}f"):
            novo_texto = _renomear_em_formula(el.text, antigo, novo)
            if novo_texto != el.text:
                el.text, trocas = novo_texto, trocas + 1
                ed.pk.tocar(grafico)
    rel.update(antes=antigo, depois=novo, referencias=trocas, partes=sorted("/" + p for p in ed.pk.tocadas))


def _recalcular_ao_abrir(ed: _Editor):
    """O Excel recalcula tudo ao abrir, e a cadeia de cálculo antiga sai (ele refaz)."""
    raiz = ed.livro.raiz
    calc = raiz.find("s:calcPr", NS)
    if calc is None:
        calc = etree.Element(S + "calcPr")
        depois_de = None
        for nome in ("definedNames", "externalReferences", "functionGroups", "sheets"):
            depois_de = raiz.find(f"s:{nome}", NS)
            if depois_de is not None:
                break
        depois_de.addnext(calc)
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


def aplicar(dados: bytes, operacoes: list[dict]) -> tuple[bytes, list[dict], list[str]]:
    """Tudo ou nada: aplica as operações em ordem; a primeira que falha cancela todas."""
    ed = _Editor(dados)
    relatorio = []
    for k, op in enumerate(operacoes):
        if not isinstance(op, dict) or op.get("op") not in OPERACOES:
            raise OperacaoInvalida(
                "operacao-invalida",
                f"op desconhecida: {op.get('op') if isinstance(op, dict) else op!r}; use uma de {', '.join(OPERACOES)}",
                indice=k,
            )
        rel = {"op": op["op"], **({"alvo": op["alvo"]} if "alvo" in op else {})}
        try:
            OPERACOES[op["op"]](ed, op, rel)
        except OperacaoInvalida as e:
            e.indice = k
            raise
        except (KeyError, IndexError, ValueError, TypeError, AttributeError) as e:
            raise OperacaoInvalida(
                "operacao-invalida", f"não foi possível aplicar ({type(e).__name__})", indice=k, alvo=op.get("alvo")
            ) from None
        relatorio.append(rel)
    if ed.recalcular:
        _recalcular_ao_abrir(ed)
        ed.avisos.append("o Excel recalcula as fórmulas ao abrir; os valores calculados no código são os antigos")
    return ed.pk.salvar(), relatorio, ed.avisos


def integridade(dados: bytes) -> list[str]:
    return _integridade(dados, "xl/")
