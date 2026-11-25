"""Informação de células fora da tabela de valores no SpreadsheetML. Os links e as fórmulas são dados."""

import math
import re
import zipfile
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from functools import cached_property
from itertools import count
from typing import NamedTuple

from lxml import etree
from openpyxl.utils import coordinate_to_tuple, get_column_letter, range_boundaries

from .limites import xml_ou_nada

ANOTACOES_MAX = 20_000  # o anexo estatístico do ONS contém quase 2.000 hiperlinks
CARACTERES_MAX = 2_000_000  # total de texto suplementar, além dos limites da tabela
TEXTO_MAX = 8_192  # uma fórmula do Excel aceita até 8.192 caracteres
_CELULAS_REGRA_MAX = 500_000  # trabalho total das expressões, inclusive intervalos sobrepostos
_COMPARTILHADAS_MAX = 500_000  # entradas esparsas que têm recuo ou são vazias
_NUMEROS_MAX = 500_000  # valores salvos consultados por perfis e condições, inclusive os ocultos pela máscara
_REF = r"([A-Z]{1,3})([1-9]\d{0,6})"
_RAZAO = re.compile(rf"ABS\({_REF}/{_REF}\)>([0-9]+(?:\.[0-9]+)?)", re.I)
_LOG = re.compile(rf"ABS\(LN\({_REF}\)/\({_REF}/{_REF}\)\)>([0-9]+(?:\.[0-9]+)?)", re.I)


def _literal(texto):
    texto = " ".join(texto[: TEXTO_MAX + 1].splitlines())
    marca = "`" * (max((len(m) for m in re.findall(r"`+", texto)), default=0) + 1)
    return f"{marca} {texto} {marca}"


class _Saida:
    def __init__(self, extras, pendentes):
        self.extras, self.pendentes = extras, pendentes
        self.caracteres = self.itens = self.calculos = self.numeros = 0
        self.avisos = set()

    def adicionar(self, onde, descricao):
        if len(descricao) > TEXTO_MAX:
            descricao = _literal(descricao[:TEXTO_MAX]) + "…"
            self.avisar("texto de anotação cortado", onde)
        linha = f"- {_literal(onde)} — {descricao}"
        self.caracteres += len(linha) + 2
        self.itens += 1
        if self.itens > ANOTACOES_MAX or self.caracteres > CARACTERES_MAX:
            raise _Limite
        self.extras.append(linha)

    def avisar(self, motivo, onde):
        if motivo not in self.avisos:
            self.avisos.add(motivo)
            self.pendentes.append(f"Anotações da planilha: {motivo} ({onde[:120]}).")


class _Limite(Exception):
    pass


def _elementos(z, parte, tags):
    """Descarta cada unidade já processada, incluindo linhas sem valores e partes extensas."""
    with z.open(parte) as arq:
        for _, el in etree.iterparse(
            arq, tag=tuple("{*}" + t for t in tags), resolve_entities=False, load_dtd=False, no_network=True
        ):
            yield el
            el.clear()
            while el.getprevious() is not None:
                del el.getparent()[0]


def _texto(el):
    return "".join(
        t.text or ""
        for t in el.iterfind(".//{*}t")
        if not any(isinstance(p.tag, str) and p.tag.endswith("}rPh") for p in t.iterancestors())
    )


def _recuo(texto):
    espacos = len(texto) - len(texto.lstrip(" "))
    return (espacos, texto.strip()[:120]) if texto.strip() else (-1, "")


def _compartilhadas(z, parte):
    especiais = {}
    if parte:
        for i, el in enumerate(_elementos(z, parte, ["si"])):
            espacos, rotulo = _recuo(_texto(el))
            if espacos:
                especiais[str(i)] = (espacos, rotulo)
                if len(especiais) > _COMPARTILHADAS_MAX:
                    raise _Limite
    return especiais


@dataclass
class Grade:
    """Origem no arquivo e números da grade, para reconhecer inclusive zeros exibidos como traço."""

    primeira_linha: int = 1_048_577
    primeira_coluna: int = 16_385
    ultima_coluna: int = 0
    numeros: dict = field(default_factory=dict)
    colunas_ocultas: set = field(default_factory=set)
    linhas_ocultas: set = field(default_factory=set)

    def ler_colunas_ocultas(self, el):
        # Ranges sobrepostos não multiplicam o trabalho pelo número de colunas de cada um.
        intervalos = sorted(
            (max(1, int(c.get("min"))), min(16_384, int(c.get("max"))))
            for c in el.iterfind("{*}col")
            if c.get("hidden") in ("1", "true")
        )
        anterior = 0
        for inicio, fim in intervalos:
            self.colunas_ocultas.update(range(max(inicio, anterior + 1), fim + 1))
            anterior = max(fim, anterior)

    def visivel(self, ref):
        if not self.colunas_ocultas and not self.linhas_ocultas:
            return True
        for faixa in ref.split():
            try:
                c1, l1, c2, l2 = range_boundaries(faixa)
            except ValueError:
                continue
            # Célula ou intervalo integralmente oculto não aparece no corpo do anydoc.
            cols, linhas = self.ocultas_ordenadas
            if self._alguma_visivel(c1 or 1, c2 or 16384, cols) and self._alguma_visivel(
                l1 or 1, l2 or 1048576, linhas
            ):
                return True
        return False

    @cached_property
    def ocultas_ordenadas(self):
        # Hiperlinks, regras e notas são lidos depois da grade e consultam intervalos sem expandi-los.
        return sorted(self.colunas_ocultas), sorted(self.linhas_ocultas)

    @staticmethod
    def _alguma_visivel(inicio, fim, ocultas):
        return fim - inicio + 1 > bisect_right(ocultas, fim) - bisect_left(ocultas, inicio)

    def projetar(self, linhas):
        colunas = [i for i in range(self.primeira_coluna, self.ultima_coluna + 1) if i not in self.colunas_ocultas]
        indice = {c: j for j, c in enumerate(colunas)}
        visiveis = (n for n in count(self.primeira_linha) if n not in self.linhas_ocultas)
        tipadas = (
            (linha, {indice[c] for c in self.numeros.get(n, {})}) for linha, n in zip(linhas, visiveis, strict=False)
        )
        return tipadas, colunas

    def linha(self, el, n_linha, compartilhadas, nome, saida, estilos, ler_numeros):
        coluna = 0
        for c in el.iterfind("{*}c"):
            ref = c.get("r")
            if ref:
                linha, coluna = coordinate_to_tuple(ref)
            else:
                linha, coluna = n_linha, coluna + 1
                ref = f"{get_column_letter(coluna)}{linha}"
            if coluna in self.colunas_ocultas:
                continue
            bruto = c.findtext("{*}v")
            tipo = c.get("t", "n")
            recuo, rotulo = 0, ""
            if tipo == "inlineStr":
                recuo, rotulo = _recuo(_texto(c))
            elif not bruto:
                continue
            elif tipo == "s":
                recuo, rotulo = compartilhadas.get(bruto, (0, ""))
            elif tipo == "str":
                recuo, rotulo = _recuo(bruto)
            if recuo < 0:
                continue
            self.primeira_linha = min(self.primeira_linha, linha)
            self.primeira_coluna = min(self.primeira_coluna, coluna)
            self.ultima_coluna = max(self.ultima_coluna, coluna)
            if recuo > 0:
                saida.adicionar(f"{nome}!{ref}", f"recuo do texto: {recuo} espaços em {_literal(rotulo)}.")
            if (nivel := estilos.get(c.get("s"))) and tipo in ("s", "str", "inlineStr"):
                saida.adicionar(f"{nome}!{ref}", f"recuo do alinhamento: nível {nivel}.")
            if tipo == "n" and ler_numeros:
                try:
                    numero = float(bruto)
                except (ValueError, OverflowError):
                    continue
                if math.isfinite(numero):
                    saida.numeros += 1
                    if saida.numeros > _NUMEROS_MAX:
                        raise _Limite
                    self.numeros.setdefault(linha, {})[coluna] = numero


def _estilos(z, parte):
    if not parte:
        return {}, set()
    raiz = xml_ou_nada(z.read(parte))
    if raiz is None:
        raise ValueError("estilos ilegíveis")
    recuos = {}
    for i, xf in enumerate(raiz.findall("{*}cellXfs/{*}xf")):
        a = xf.find("{*}alignment")
        if a is not None and a.get("indent", "0") != "0":
            recuos[str(i)] = a.get("indent")
    negritos = set()
    for i, estilo in enumerate(raiz.findall("{*}dxfs/{*}dxf")):
        b = estilo.find("{*}font/{*}b")
        if b is not None and b.get("val", "1") not in ("0", "false", "off"):
            negritos.add(str(i))
    return recuos, negritos


def _satisfaz(formula, faixa, grade, saida):
    """Subconjunto aritmético explícito. Só referências relativas na própria linha, sem executar fórmulas."""
    razao = _RAZAO.fullmatch(formula)
    m = razao or _LOG.fullmatch(formula)
    if not m or not re.fullmatch(r"[A-Z]{1,3}[1-9]\d{0,6}(?::[A-Z]{1,3}[1-9]\d{0,6})?", faixa):
        return None
    c1, l1, c2, l2 = range_boundaries(faixa)
    args = m.groups()
    if l2 < l1 or c2 != c1 or any(int(l) != l1 for l in args[1:-1:2]):
        return None
    quantidade = l2 - l1 + 1
    if saida.calculos + quantidade > _CELULAS_REGRA_MAX:
        saida.avisar("limite de avaliação de regras atingido", faixa)
        return None
    saida.calculos += quantidade
    letras = get_column_letter(c1)
    colunas = [coordinate_to_tuple(c + "1")[1] for c in args[:-1:2]]
    limiar = float(args[-1])
    positivas = []
    for linha in range(l1, l2 + 1):
        numeros = grade.numeros.get(linha, {})
        valores = [numeros.get(c) for c in colunas]
        if None in valores:
            continue
        try:
            valor = valores[0] / valores[1] if razao else math.log(valores[0]) / (valores[1] / valores[2])
        except (ValueError, ZeroDivisionError, OverflowError):
            continue
        if abs(valor) > limiar:
            positivas.append(f"{letras}{linha}")
    return ", ".join(positivas) or "nenhuma célula numérica"


def _regra(el, faixa, grade, nome, negritos, saida):
    for regra in el.iterfind("{*}cfRule"):
        if regra.get("dxfId") not in negritos:
            continue
        formulas = [f.text or "" for f in regra.iterfind("{*}formula")]
        tipo = regra.get("type", "")
        satisfeita = (
            _satisfaz(formulas[0], faixa, grade, saida) if tipo == "expression" and len(formulas) == 1 else None
        )
        resultado = "aplicação não avaliada" if satisfeita is None else "regra satisfeita em " + satisfeita
        condicao = " ".join([tipo, regra.get("operator", ""), *(_literal(f) for f in formulas)]).strip()
        saida.adicionar(
            f"{nome}!{faixa}", f"negrito condicional ({condicao}), nos valores numéricos salvos: {resultado}."
        )


def _validacao(el):
    campos = [el.get("type", "none"), el.get("operator", "")]
    for chave in ("formula1", "formula2"):
        if valor := el.findtext("{*}" + chave):
            campos.append(_literal(valor))
    for chave in (
        "allowBlank",
        "errorStyle",
        "showErrorMessage",
        "showInputMessage",
        "errorTitle",
        "error",
        "promptTitle",
        "prompt",
    ):
        if el.get(chave) is not None:
            campos.append(f"{chave}={_literal(el.get(chave))}")
    return "validação: " + " ".join(c for c in campos if c)


def extrair(path, limites, cortadas, extras, pendentes):
    from .office import _VARREDURA_MAX, _alvos, _destino_markdown

    saida = _Saida(extras, pendentes)
    grades = {}
    varridas = 0
    with zipfile.ZipFile(path) as z:
        nomes = set(z.namelist())
        livro = next(iter(_alvos(z, "", nomes, "officeDocument").values()), "xl/workbook.xml")
        if livro not in nomes:
            return grades
        raiz = xml_ou_nada(z.read(livro))
        if raiz is None:
            raise ValueError("pasta de trabalho ilegível")
        abas = _alvos(z, livro, nomes, "worksheet")
        try:
            especiais = _compartilhadas(z, next(iter(_alvos(z, livro, nomes, "sharedStrings").values()), None))
            recuos, negritos = _estilos(z, next(iter(_alvos(z, livro, nomes, "styles").values()), None))
            for aba in raiz.findall("{*}sheets/{*}sheet"):
                nome = aba.get("name")
                parte = abas.get(next((v for k, v in aba.attrib.items() if k.endswith("}id")), None))
                if aba.get("state", "visible") != "visible" or parte not in nomes:
                    continue
                if nome in cortadas and (limites is None or nome not in limites):
                    continue
                grade, n_linha = Grade(), 0
                teto = limites.get(nome, 0) if nome in cortadas else None
                if limites is None:
                    grades[nome] = grade
                destinos = _alvos(z, parte, nomes, "hyperlink", externos=True)
                tags = ["cols", "row", "hyperlink", "dataValidation", "conditionalFormatting"]
                for el in _elementos(z, parte, tags):
                    tag = etree.QName(el).localname
                    if tag == "cols":
                        if limites is None:  # a tabela do anydoc compacta as linhas e colunas ocultas
                            grade.ler_colunas_ocultas(el)
                        continue
                    if tag == "row":
                        n_linha = int(el.get("r", n_linha + 1))
                        varridas += 1 + len(el)
                        if varridas > _VARREDURA_MAX:
                            raise _Limite
                        if limites is None and el.get("hidden") in ("1", "true"):
                            grade.linhas_ocultas.add(n_linha)
                            continue
                        if teto is None or n_linha <= teto:
                            grade.linha(el, n_linha, especiais, nome, saida, recuos, limites is None or bool(negritos))
                        continue
                    ref = el.get("ref") or el.get("sqref", "")
                    if teto is not None:
                        ref = _recortar(ref, teto)
                    if not ref or not grade.visivel(ref):
                        continue
                    onde = f"{nome}!{ref}"
                    if tag == "hyperlink":
                        rid = next((v for k, v in el.attrib.items() if k.endswith("}id")), None)
                        destino = destinos.get(rid, "")
                        local = el.get("location")
                        if local:
                            destino += "#" + local
                        if destino:
                            rotulo = el.get("display") or ref
                            saida.adicionar(
                                onde,
                                "hiperlink: "
                                + (_destino_markdown(rotulo, destino) or f"{_literal(rotulo)} → {_literal(destino)}"),
                            )
                        else:
                            saida.avisar("hiperlink sem destino legível", onde)
                    elif tag == "dataValidation":
                        saida.adicionar(onde, _validacao(el))
                    elif tag == "conditionalFormatting":
                        _regra(el, ref, grade, nome, negritos, saida)
                for comentarios in _alvos(z, parte, nomes, "comments").values():
                    for nota in _elementos(z, comentarios, ["comment"]):
                        ref = nota.get("ref", "")
                        if grade.visivel(ref) and (teto is None or _recortar(ref, teto)):
                            saida.adicionar(f"{nome}!{ref}", "nota: " + _literal(_texto(nota)))
                if limites is not None:
                    grade.numeros.clear()
        except _Limite:
            saida.avisar("limite de extração atingido", "anotações restantes omitidas")
    return grades


def _recortar(ref, teto):
    """Interseção com as linhas entregues, sem expandir a lista de células."""
    lidos = []
    for parte in ref.split():
        try:
            c1, l1, c2, l2 = range_boundaries(parte)
        except ValueError:
            continue
        if (l1 or 1) > teto:
            continue
        if l2 is not None and l2 <= teto:
            lidos.append(parte)
        elif c1 and c2:
            lidos.append(f"{get_column_letter(c1)}{l1 or 1}:{get_column_letter(c2)}{teto}")
    return " ".join(lidos)


OCULTA_TEXTO_MAX = 500  # caracteres guardados de cada aba oculta, o mesmo registro do texto oculto
OCULTA_LINHAS_MAX = 20_000  # linhas varridas de cada aba oculta até juntar esse texto (as vazias também contam)
OCULTA_CELULAS_MAX = 20_000  # células varridas de cada aba oculta


class _Oculta:
    """O começo do texto de uma aba oculta, com os tetos: o registro guarda só isso, e a varredura para nele."""

    def __init__(self, nome, posicao=0):
        self.nome, self.posicao, self.partes, self.tamanho, self.linhas, self.celulas = nome, posicao, [], 0, 0, 0

    def cheia(self):
        return self.tamanho > OCULTA_TEXTO_MAX or self.linhas > OCULTA_LINHAS_MAX or self.celulas > OCULTA_CELULAS_MAX

    def linha(self, n=1):
        self.linhas += n

    def celula(self, valor, nota=""):
        self.celulas += 1
        for texto, prefixo in ((valor, ""), (nota, "nota: ")):
            if not self.cheia() and texto is not None and (t := " ".join(str(texto).split())):
                self.partes.append(prefixo + t)
                self.tamanho += len(t) + 1

    def texto(self):
        return " ".join(self.partes)[:OCULTA_TEXTO_MAX]


def planilha_xls(path, extras, pendentes) -> list[tuple[str, str]]:
    """Notas de célula das abas visíveis do .xls, no formato das do .xlsx, e (nome, 0, começo do texto, com as notas)
    de cada aba oculta, pelo xlrd. Uma aba por vez é carregada e descarregada; o .xls tem no máximo 65.536 linhas por
    aba, e o leitor do corpo já recusou a grade grande demais. A aba que o xlrd não lê fica registrada pelo número
    (o nome vem com ela); o leitor do corpo do .xls não põe a aba oculta no corpo."""
    import xlrd

    saida, ocultas, notas = _Saida(extras, pendentes), [], True
    livro = xlrd.open_workbook(path, on_demand=True)
    try:
        for k in range(livro.nsheets):
            try:
                aba = livro.sheet_by_index(k)
            except Exception as e:  # noqa: BLE001 — registro fora do padrão numa aba: as outras seguem
                pendentes.append(
                    f"aba {k + 1}: não lida pelo leitor das abas ocultas e das notas de célula ({type(e).__name__})"
                )
                continue
            try:
                if aba.visibility:  # 1 oculta, 2 muito oculta
                    oculta = _Oculta(aba.name)
                    notas = aba.cell_note_map
                    for r in range(aba.nrows):
                        oculta.linha()
                        for c, valor in enumerate(aba.row_values(r)):
                            nota = notas.get((r, c))
                            oculta.celula(valor if valor != "" else None, nota.text if nota else "")
                            if oculta.cheia():
                                break
                        if oculta.cheia():
                            break
                    ocultas.append((aba.name, 0, oculta.texto()))
                    continue
                for (linha, coluna), nota in sorted(aba.cell_note_map.items()) if notas else ():
                    if texto := (nota.text or "").strip():
                        onde = f"{aba.name}!{get_column_letter(coluna + 1)}{linha + 1}"
                        try:
                            saida.adicionar(onde, "nota: " + _literal(texto))
                        except _Limite:  # o teto para as notas, não a busca das abas ocultas
                            saida.avisar("limite de extração atingido", "anotações restantes omitidas")
                            notas = False
                            break
            finally:
                livro.unload_sheet(k)
    finally:
        livro.release_resources()
    return ocultas


_ODS_TB = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_ODS_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
_ODS_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_ODS_STYLE = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"
_ODS_REPETICAO_MAX = 1_048_576  # linhas e colunas que uma repetição pode pular (o tamanho máximo de uma aba)


class Ods(NamedTuple):
    """O que a passada pelo content.xml do .ods diz das abas ocultas: (nome, posição entre as abas, começo do texto)
    de cada uma, os objetos embutidos (gráficos, "Object 3") que as abas visíveis mostram e o .ods sem as ocultas
    (None se não há), que é o que o leitor do corpo e o das imagens recebem."""

    ocultas: list
    objetos: set
    sem_ocultas: bytes | None


_DRAW = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}"
_LIMPAR = {
    f"{_ODS_TB}{t}" for t in ("table-cell", "covered-table-cell", "table-row", "table-column", "table", "table-rows")
}
_XLINK = "{http://www.w3.org/1999/xlink}href"


def planilha_ods(path, extras, pendentes) -> Ods:
    """O mesmo que `planilha_xls`, para o .ods, numa passada em fluxo pelo content.xml: a aba é oculta quando o estilo
    dela tem table:display="false"; a nota de célula é o office:annotation, sem o autor e a data. A posição conta as
    repetições de linha e de coluna. Célula, linha, coluna e aba são limpas ao fechar: a planilha pode ser enorme.
    Com `extras` None, a nota das abas visíveis não é guardada; o teto das notas para só a coleta delas.

    A aba oculta sai do arquivo, pela posição entre as abas, antes de qualquer leitura do corpo: nada depende do nome
    dela nem de como o leitor do corpo a escreve. Falha fechada: se o parser recusa o XML (DTD com entidade, prefixo
    sem namespace) e o XML fala em display, a aba oculta não pode ser separada e o documento é recusado."""
    from defusedxml.ElementTree import iterparse

    from .limites import DocumentoRecusado

    saida = _Saida(extras if extras is not None else [], pendentes)
    escondidas, ocultas, objetos, oculta, notas = set(), [], set(), None, extras is not None
    posicoes, abas, dentro = set(), 0, 0  # dentro: tabelas abertas (a de uma célula não é aba)
    aba, linha, coluna = "", 0, 0
    with zipfile.ZipFile(path) as z:
        if "content.xml" not in z.namelist():
            return Ods([], set(), None)
        # DTD no XML que fala em display: o DTD muda o XML (o namespace padrão de um atributo, a entidade) para o
        # parser e não para o leitor do corpo, e a aba oculta não pode ser separada com certeza
        if menciona(xml := z.read("content.xml"), "<!DOCTYPE") and menciona(xml, "display"):
            raise DocumentoRecusado("planilha com DTD no XML: a aba oculta não pôde ser separada do corpo")
        del xml
        try:
            with z.open("content.xml") as arquivo:
                for evento, el in iterparse(arquivo, events=("start", "end")):
                    if evento == "start":
                        if el.tag == f"{_ODS_TB}table":
                            dentro += 1
                            if dentro > 1:
                                continue
                            aba, linha = el.get(f"{_ODS_TB}name") or "", 0
                            abas += 1
                            if el.get(f"{_ODS_TB}style-name") in escondidas:
                                oculta = _Oculta(aba, abas - 1)
                                posicoes.add(abas - 1)
                            else:
                                oculta = None
                        elif el.tag == f"{_ODS_TB}table-row" and dentro == 1:
                            coluna = 0
                        elif el.tag == f"{_DRAW}object" and oculta is None:
                            objetos.add((el.get(_XLINK) or "").removeprefix("./").strip("/"))
                        continue
                    if el.tag == f"{_ODS_STYLE}style":
                        # mais de um table-properties: basta um esconder (o LibreOffice usa o último)
                        if any(
                            props.get(f"{_ODS_TB}display") == "false"
                            for props in el.findall(f"{_ODS_STYLE}table-properties")
                        ):
                            escondidas.add(el.get(f"{_ODS_STYLE}name"))
                        continue
                    if el.tag == f"{_ODS_TB}table":
                        dentro -= 1
                        if not dentro and oculta is not None:
                            ocultas.append((oculta.nome, oculta.posicao, oculta.texto()))
                            oculta = None
                    elif dentro > 1:
                        pass  # célula de uma tabela dentro da célula: o texto vai com a célula de fora
                    elif el.tag in (f"{_ODS_TB}table-cell", f"{_ODS_TB}covered-table-cell"):
                        nota = el.find(f"{_ODS_OFFICE}annotation")
                        texto_nota = (
                            "\n".join("".join(p.itertext()) for p in nota.iter(f"{_ODS_TEXT}p")).strip()
                            if nota is not None
                            else ""
                        )
                        if oculta is not None:
                            if not oculta.cheia():
                                valor = " ".join("".join(p.itertext()) for p in el.findall(f"{_ODS_TEXT}p")).strip()
                                oculta.celula(valor or None, texto_nota)
                        elif texto_nota and notas:
                            onde = f"{aba}!{get_column_letter(min(coluna, 16_383) + 1)}{linha + 1}"
                            try:
                                saida.adicionar(onde, "nota: " + _literal(texto_nota))
                            except _Limite:  # o teto para as notas, não a busca das abas ocultas
                                saida.avisar("limite de extração atingido", "anotações restantes omitidas")
                                notas = False
                        coluna += _repeticao(el.get(f"{_ODS_TB}number-columns-repeated"))
                    elif el.tag == f"{_ODS_TB}table-row":
                        repeticao = _repeticao(el.get(f"{_ODS_TB}number-rows-repeated"))
                        linha += repeticao
                        if oculta is not None:
                            oculta.linha(repeticao)
                    if el.tag in _LIMPAR and dentro <= 1:  # o parágrafo é lido quando a célula fecha: não é limpo antes
                        el.clear()
        except MemoryError:
            raise
        except Exception:
            if menciona(z.read("content.xml"), "display"):
                raise DocumentoRecusado(
                    "planilha com XML fora do padrão que pode esconder aba: a aba oculta não pôde ser separada do corpo"
                ) from None
            raise
        if not posicoes:
            return Ods(ocultas, objetos, None)
        try:
            return Ods(ocultas, objetos, _sem_abas_ods(z, posicoes, abas))
        except (DocumentoRecusado, MemoryError):
            raise
        except Exception as e:  # noqa: BLE001 — parte que não sai (CRC, cifra): o original levaria a aba ao corpo
            raise DocumentoRecusado(
                f"planilha com aba oculta e pacote que não pôde ser copiado sem ela ({type(e).__name__})"
            ) from None


def menciona(xml: bytes, palavra: str) -> bool:
    """`palavra` no XML em qualquer codificação que o expat lê sem declaração externa (UTF-8, Latin-1, UTF-16): a
    busca nos bytes de uma só codificação deixa passar o arquivo gravado em outra."""
    return any(palavra.encode(c) in xml for c in ("utf-8", "utf-16-le", "utf-16-be"))


def _sem_abas_ods(z, posicoes, abas) -> bytes:
    """O .ods com o content.xml sem as abas nas `posicoes`, contadas como em `planilha_ods`: o expat dá onde cada aba
    começa e termina nos bytes, e só esses trechos saem; o resto do XML e as outras partes ficam como estão. Os trechos
    são achados pelos bytes, então o XML tem de ser de um byte por caractere ASCII (UTF-8 ou Latin-1). Se as duas
    passadas não contam as mesmas `abas`, a aba oculta não pode ser separada com certeza: o documento é recusado."""
    import io
    from xml.parsers import expat

    from .limites import DocumentoRecusado

    xml = z.read("content.xml")
    if xml[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in xml[:4]:
        raise DocumentoRecusado("planilha com aba oculta em XML UTF-16: a aba oculta não foi separada do corpo")
    tabela = f"{_ODS_TB[1:-1]} table"
    estado = {"abas": 0, "dentro": 0, "inicio": None}
    cortes = []

    def abre(nome, _atributos):
        if nome == tabela:
            estado["dentro"] += 1
            if estado["dentro"] == 1:
                estado["abas"] += 1
                if estado["abas"] - 1 in posicoes:
                    inicio = parser.CurrentByteIndex
                    fim = _fim_da_tag(xml, inicio)
                    if xml[fim - 2 : fim] == b"/>":  # aba vazia: o trecho é a própria tag
                        cortes.append((inicio, fim))
                    else:
                        estado["inicio"] = inicio

    def fecha(nome):
        if nome != tabela:
            return
        estado["dentro"] -= 1
        if not estado["dentro"] and estado["inicio"] is not None:
            onde = parser.CurrentByteIndex  # o "</" da marca de fim, que não tem atributo
            if not xml.startswith(b"</", onde):
                raise DocumentoRecusado("planilha com XML fora do padrão: a aba oculta não foi separada do corpo")
            cortes.append((estado["inicio"], _fim_da_tag(xml, onde)))
            estado["inicio"] = None

    parser = expat.ParserCreate(namespace_separator=" ")
    parser.StartElementHandler, parser.EndElementHandler = abre, fecha
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)  # o XML com DTD já foi recusado antes
    parser.Parse(xml, True)
    if estado["abas"] != abas:
        raise DocumentoRecusado("planilha com abas que as leituras contam diferente: a aba oculta não foi separada")
    partes, pos = [], 0
    for inicio, fim in cortes:
        partes.append(xml[pos:inicio])
        pos = fim
    conteudo = b"".join(partes) + xml[pos:]
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as nova:
        for parte in z.infolist():
            if parte.filename == "content.xml":  # a cópia só vive na memória, até o leitor do corpo: compressão leve
                nova.writestr(parte, conteudo, compresslevel=1)
            else:
                nova.writestr(parte, z.read(parte))
    return saida.getvalue()


def _fim_da_tag(xml: bytes, inicio: int) -> int:
    """Posição logo depois do ">" que fecha a tag que começa em `inicio`, pulando o que está entre aspas."""
    from .limites import DocumentoRecusado

    aspas, k = None, inicio
    while k < len(xml):
        c = xml[k : k + 1]
        if aspas:
            if c == aspas:
                aspas = None
        elif c in (b'"', b"'"):
            aspas = c
        elif c == b">":
            return k + 1
        k += 1
    raise DocumentoRecusado("planilha com XML cortado: a aba oculta não foi separada do corpo")


def _repeticao(valor):
    try:
        return min(max(int(valor or 1), 1), _ODS_REPETICAO_MAX)
    except ValueError:
        return 1
