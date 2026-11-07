"""Metadados dos formatos binários e do RTF, que o leitor do corpo não entrega: cabeçalho e rodapé (DOC, XLS, PPT,
RTF), abas ocultas (XLS) e macros (VBA no PPT, folha de macro do Excel 4.0 no XLS).

Leitura defensiva: arquivo hostil ou fora do padrão nunca derruba a leitura do documento; quem chama trata a exceção
e segue sem esses metadados. Os laços andam sobre os bytes, sem recursão, e nunca passam do fim do stream; todo o
trabalho (bytes percorridos, copiados e decodificados, textos guardados) sai de um `Orcamento` proporcional ao
stream, então nenhuma estrutura sobreposta ou repetida multiplica o custo."""

import codecs
import itertools
import re
import struct

_BOF, _EOF, _FILEPASS, _BOUNDSHEET, _DIMENSIONS = 0x0809, 0x000A, 0x002F, 0x0085, 0x0200
_HEADER, _FOOTER, _CODEPAGE, _HEADER_FOOTER = 0x0014, 0x0015, 0x0042, 0x089C
FOLHA_DE_MACRO, MODULO_VBA = 1, 6  # BOUNDSHEET.dt: folha de macro do Excel 4.0 e módulo do Excel 5
_REGISTROS_POR_ABA = 4096  # o cabeçalho fica no bloco de configuração da página, antes das células
_CODEPAGES = {367: "ascii", 1200: "utf-16-le", 10000: "mac_roman", 32768: "mac_roman", 32769: "cp1252"}
_FATOR = 2  # arquivo legítimo percorre cada região uma vez; o dobro sobra para diretórios e cabeçalhos de registro
_FOLGA = 64 * 1024
_TEXTO_MAX = 1024  # caracteres por cabeçalho ou rodapé (o Excel limita a 255; o PowerPoint, na prática, a poucos)
_TEXTOS_MAX = 1024  # cabeçalhos e rodapés distintos por arquivo


class Orcamento:
    """Teto de trabalho de um arquivo. Estourar é estrutura hostil (regiões sobrepostas, cadeias repetidas): levanta
    ValueError, e quem chama registra os metadados como ilegíveis em vez de gastar memória e CPU sem limite."""

    def __init__(self, tamanho):
        self.bytes, self.textos = _FATOR * tamanho + _FOLGA, _TEXTOS_MAX

    def gastar(self, n):
        self.bytes -= n
        if self.bytes < 0:
            raise ValueError("estrutura sobreposta ou repetida: orçamento de leitura esgotado")

    def guardar(self, bordas: dict, texto: str):
        if texto not in bordas:
            self.textos -= 1
            if self.textos < 0:
                raise ValueError("cabeçalhos e rodapés distintos demais")
            bordas[texto] = None


def ler_stream(o, nome, teto):
    """Bytes do stream OLE. Tamanho declarado acima do próprio arquivo é estrutura hostil (o olefile só registra
    o defeito e seguiria a cadeia de setores): recusa antes de ler."""
    if o.get_size(nome) > teto:
        raise ValueError(f"stream {nome} declara mais bytes que o arquivo")
    return o.openstream(nome).read()


# ---------- Excel 97-2003 (BIFF8) e Excel 5/95 (BIFF5) ----------


def _registros(b, i, orcamento):
    """(tipo, dados) de cada registro a partir de `i`; cada registro (cabeçalho e cópia dos dados) sai do orçamento."""
    while i + 4 <= len(b):
        tipo, tam = struct.unpack_from("<HH", b, i)
        orcamento.gastar(4 + tam)
        yield tipo, b[i + 4 : i + 4 + tam]
        i += 4 + tam


def _texto_xls(d, i, codificacao, cch_bytes):
    """(string, posição seguinte) do BIFF. No BIFF8 (`codificacao` None) vem a flag: 16 bits, ou só o byte baixo
    (latin-1); no BIFF5, os bytes na página de código do registro CODEPAGE."""
    if len(d) < i + cch_bytes:
        return "", len(d)
    cch = d[i] if cch_bytes == 1 else struct.unpack_from("<H", d, i)[0]
    i += cch_bytes
    n = min(cch, _TEXTO_MAX)
    if codificacao is None:
        alto = i < len(d) and d[i] & 1
        i += 1
        if alto:
            return d[i : i + 2 * n].decode("utf-16-le", "replace"), i + 2 * cch
        return d[i : i + n].decode("latin-1"), i + cch
    return d[i : i + n].decode(codificacao, "replace"), i + cch


def _codificacao_biff5(cp):
    nome = _CODEPAGES.get(cp, f"cp{cp}")
    try:
        return codecs.lookup(nome).name
    except LookupError:
        return "cp1252"


def _header_footer_2007(d):
    """Registro HeaderFooter (MS-XLS 2.4.137), que o Excel 2007+ grava: cabeçalho e rodapé das páginas pares e da
    primeira página, depois de 38 bytes fixos (frtHeader, guidSView, flags e os quatro cch)."""
    if len(d) < 38:
        return []
    textos, i = [], 38
    for cch in struct.unpack_from("<4H", d, 30):
        if cch:
            texto, i = _texto_xls(d, i, None, 2)
            textos.append(texto)
    return textos


def xls(o, teto) -> dict:
    """Cabeçalho e rodapé de cada aba, abas ocultas e se há folha de macro (Excel 4.0) ou módulo VBA do Excel 5."""
    b = ler_stream(o, "Workbook" if o.exists("Workbook") else "Book", teto)
    if len(b) < 8 or struct.unpack_from("<H", b, 0)[0] != _BOF:
        return {}
    codificacao = None if struct.unpack_from("<H", b, 4)[0] == 0x0600 else "cp1252"  # BOF.vers: BIFF8 ou não
    orcamento = Orcamento(len(b))
    abas, bordas, saida = set(), {}, {"ocultas": [], "macro": False}
    for tipo, d in _registros(b, 0, orcamento):
        if tipo == _FILEPASS:
            return {}  # protegida por senha: o resto do stream é cifrado
        if tipo == _EOF:
            break
        if tipo == _CODEPAGE and len(d) >= 2 and codificacao:
            codificacao = _codificacao_biff5(struct.unpack_from("<H", d, 0)[0])
        if tipo == _BOUNDSHEET and len(d) >= 7:
            abas.add(struct.unpack_from("<I", d, 0)[0])
            if d[4] & 3:  # hsState: 1 oculta, 2 muito oculta (só macro reexibe)
                saida["ocultas"].append(_texto_xls(d, 6, codificacao, 1)[0])
            if d[5] in (FOLHA_DE_MACRO, MODULO_VBA):
                saida["macro"] = True
    for pos in sorted(abas):
        lidos = 0
        for tipo, d in _registros(b, pos, orcamento):
            lidos += 1
            if tipo in (_DIMENSIONS, _EOF) or lidos > _REGISTROS_POR_ABA:
                break
            if tipo in (_HEADER, _FOOTER) and d:
                orcamento.guardar(bordas, _texto_xls(d, 0, codificacao, 1 if codificacao else 2)[0])
            elif tipo == _HEADER_FOOTER:
                for texto in _header_footer_2007(d):
                    orcamento.guardar(bordas, texto)
    saida["bordas"] = list(bordas)
    return saida


# ---------- PowerPoint 97-2003 ----------

_CONTEINER, _VBA_INFO, _VBA_INFO_ATOM, _CABECALHOS_RODAPES, _CSTRING = 0xF, 0x03FF, 0x0400, 0x0FD9, 0x0FBA
_USER_EDIT, _PERSIST_DIRECTORY, _EDICOES_MAX = 0x0FF5, 0x1772, 4096


def _objetos_vivos(b, usuario, orcamento):
    """Posições dos objetos da versão atual: Current User → último UserEditAtom → diretórios de persistência, do mais
    novo ao mais antigo (MS-PPT 2.3). Assim o salvamento rápido, que deixa versões antigas no stream, não traz macro
    ou rodapé que já foram apagados. None se a cadeia não fecha: quem chama varre o stream inteiro."""
    if len(usuario) < 20:
        return None
    edicao, vistos, posicoes = struct.unpack_from("<I", usuario, 16)[0], set(), {}
    while edicao and edicao not in vistos and len(vistos) < _EDICOES_MAX:
        vistos.add(edicao)
        orcamento.gastar(36)
        if edicao + 36 > len(b) or struct.unpack_from("<H", b, edicao + 2)[0] != _USER_EDIT:
            return None
        anterior, diretorio = struct.unpack_from("<II", b, edicao + 16)
        if diretorio + 8 > len(b) or struct.unpack_from("<H", b, diretorio + 2)[0] != _PERSIST_DIRECTORY:
            return None
        fim = min(diretorio + 8 + struct.unpack_from("<I", b, diretorio + 4)[0], len(b))
        orcamento.gastar(fim - diretorio)  # edições diferentes apontando o mesmo diretório pagam cada leitura
        i = diretorio + 8
        while i + 4 <= fim:
            v = struct.unpack_from("<I", b, i)[0]
            ident, quantos = v & 0xFFFFF, v >> 20
            for k in range(min(quantos, (fim - i - 4) // 4)):
                posicoes.setdefault(ident + k, struct.unpack_from("<I", b, i + 4 + 4 * k)[0])
            i += 4 + 4 * quantos
        edicao = anterior
    validas = sorted({p for p in posicoes.values() if p + 8 <= len(b)})
    return validas or None


def ppt(o, teto) -> dict:
    """Macro (VBAInfoAtom com fHasMacros) e textos de cabeçalho, rodapé e data dos slides e das anotações."""
    b = ler_stream(o, "PowerPoint Document", teto)
    orcamento = Orcamento(len(b))
    vivos = _objetos_vivos(b, ler_stream(o, "Current User", teto), orcamento) if o.exists("Current User") else None
    if vivos is None:
        pilha = [(0, len(b), None)]
    else:  # cada objeto vivo é um registro: o tamanho dele delimita a varredura
        pilha = [(p, min(p + 8 + struct.unpack_from("<I", b, p + 4)[0], len(b)), None) for p in vivos]
    saida, bordas = {"macro": False}, {}
    while pilha:
        i, fim, pai = pilha.pop()
        while i + 8 <= fim:
            orcamento.gastar(8)  # objetos vivos sobrepostos pagam de novo cada registro que repetem
            ver, tipo, tam = struct.unpack_from("<HHI", b, i)
            corpo = i + 8
            if ver & 0xF == _CONTEINER:
                pilha.append((corpo, min(corpo + tam, fim), tipo))
            elif pai == _VBA_INFO and tipo == _VBA_INFO_ATOM and tam >= 8 and corpo + 8 <= len(b):
                saida["macro"] |= struct.unpack_from("<I", b, corpo + 4)[0] == 1
            elif pai == _CABECALHOS_RODAPES and tipo == _CSTRING:
                texto = b[corpo : min(corpo + tam, fim, corpo + 2 * _TEXTO_MAX)]
                orcamento.gastar(len(texto))
                orcamento.guardar(bordas, texto.decode("utf-16-le", "replace"))
            i = corpo + tam
    saida["bordas"] = list(bordas)
    return saida


# ---------- Word 97-2003 ----------

_ANINHAMENTO_MAX = 64  # o Word aninha poucos níveis; acima disso é arquivo hostil
_CAMPOS = {"PAGE": "[página]", "NUMPAGES": "[páginas]", "SECTIONPAGES": "[páginas]", "DATE": "[data]",
           "TIME": "[hora]", "FILENAME": "[arquivo]"}  # fmt: skip


def _sem_campos(texto: str) -> str:
    """Campo do Word (0x13 código 0x14 resultado 0x15) vira o resultado; número de página e data viram nome, como no
    cabeçalho do Excel. Campos aninhados são tratados com pilha e um contador dos que escondem o texto (código ou
    resultado pulado), para custo linear em qualquer entrada."""
    saida, pilha, escondem = [], [], 0  # pilha: [código, fase]; fase "codigo", "resultado" ou "pular"
    for c in texto:
        if c == "\x13":
            if len(pilha) >= _ANINHAMENTO_MAX:
                raise ValueError("campos aninhados demais")
            pilha.append([[], "codigo"])
            escondem += 1
        elif c == "\x14" and pilha:
            codigo, fase = pilha[-1]
            if fase != "codigo":
                continue
            nome = "".join(codigo).split()[:1]
            if nome and nome[0].upper() in _CAMPOS:
                if escondem == 1:  # só este campo esconde: o nome aparece
                    saida.append(_CAMPOS[nome[0].upper()])
                pilha[-1][1] = "pular"
            else:
                pilha[-1][1] = "resultado"
                escondem -= 1
        elif c == "\x15" and pilha:
            if pilha.pop()[1] != "resultado":
                escondem -= 1
        elif not escondem:
            saida.append(c)
        elif pilha[-1][1] == "codigo" and len(pilha[-1][0]) < 32:  # só a primeira palavra do código importa
            pilha[-1][0].append(c)
    return "".join(saida)


def _texto_por_cp(wd, tabela, fib, ini, fim) -> str:
    """Texto entre as posições de caractere [ini, fim) pela piece table (CLX) do Word 97."""
    fcclx, lcbclx = struct.unpack_from("<II", wd, fib + 33 * 8)
    clx = tabela[fcclx : fcclx + lcbclx]
    i = 0
    while i + 3 <= len(clx) and clx[i] == 0x01:  # Prc: formatação, pulada
        i += 3 + struct.unpack_from("<H", clx, i + 1)[0]
    if i + 5 > len(clx) or clx[i] != 0x02:
        return ""
    lcb = struct.unpack_from("<I", clx, i + 1)[0]
    plc = clx[i + 5 : i + 5 + lcb]
    n = (len(plc) - 4) // 12
    if n <= 0:
        return ""
    cps = struct.unpack_from(f"<{n + 1}I", plc, 0)
    if any(x >= y for x, y in itertools.pairwise(cps)):
        return ""  # a especificação exige posições crescentes: fora disso, peças repetidas multiplicariam o texto
    partes, restante = [], min(fim - ini, len(wd))  # o cabeçalho nunca é maior que o próprio stream
    for k in range(n):
        a, z = max(cps[k], ini), min(cps[k + 1], fim, max(cps[k], ini) + restante)
        if a >= z:
            continue
        restante -= z - a
        fc = struct.unpack_from("<I", plc, 4 * (n + 1) + 8 * k + 2)[0]
        if fc & 0x40000000:  # comprimido: um byte por caractere, cp1252
            o = (fc & 0x3FFFFFFF) // 2 + (a - cps[k])
            partes.append(wd[o : o + (z - a)].decode("cp1252", "replace"))
        else:
            o = fc + 2 * (a - cps[k])
            partes.append(wd[o : o + 2 * (z - a)].decode("utf-16-le", "replace"))
        if restante <= 0:
            break
    return "".join(partes)


def doc(o, teto) -> list[str]:
    """Parágrafos da história de cabeçalho e rodapé do Word 97-2003 (vem depois do corpo e das notas de rodapé)."""
    wd = ler_stream(o, "WordDocument", teto)
    if len(wd) < 64 or struct.unpack_from("<H", wd, 0)[0] != 0xA5EC:
        return []
    flags = struct.unpack_from("<H", wd, 0x0A)[0]
    if flags & 0x0100:  # cifrado
        return []
    nome_tabela = "1Table" if flags & 0x0200 else "0Table"
    if not o.exists(nome_tabela):
        return []
    csw = struct.unpack_from("<H", wd, 32)[0]
    lw = 34 + 2 * csw
    cslw = struct.unpack_from("<H", wd, lw)[0]
    fib = lw + 2 + 4 * cslw + 2  # início do fibRgFcLcb
    if cslw < 6 or fib + 34 * 8 > len(wd):
        return []
    ccp_texto, ccp_notas, ccp_cabecalho = struct.unpack_from("<3i", wd, lw + 2 + 12)
    if ccp_cabecalho <= 0 or min(ccp_texto, ccp_notas) < 0:
        return []
    ini = ccp_texto + ccp_notas
    texto = _sem_campos(_texto_por_cp(wd, ler_stream(o, nome_tabela, teto), fib, ini, ini + ccp_cabecalho))
    texto = texto.replace("\x07", " ").replace("\x0b", " ").replace("\x1e", "-")  # 0x1E: hífen inseparável
    texto = re.sub(r"[\x00-\x08\x0e-\x1f]", "", texto)
    return [p.replace("\t", " ") for p in texto.split("\r")]


# ---------- RTF ----------

_GRUPO_BORDA = re.compile(rb"\{\\(?:header|footer)[lrf]?(?![a-z])")
_RELEITURA_MAX = 4  # bytes relidos por byte do arquivo; arquivo real relê bem menos (cabeçalhos curtos e repetidos)
_GRUPOS_MAX = 64  # distintos; o Word grava até seis por seção, quase sempre repetidos de uma seção para outra


def _fim_do_grupo(s: bytes, i: int) -> int:
    """Posição logo depois do `}` que fecha o grupo aberto antes de `i` (\\{ e \\} escapados não contam), ou -1 se
    ele nunca fecha."""
    prof = 1
    while i < len(s) and prof:
        c = s[i]
        if c == 0x5C:  # barra invertida: o caractere seguinte é escapado ou começa palavra de controle
            i += 2
            continue
        prof += (c == 0x7B) - (c == 0x7D)
        i += 1
    return -1 if prof else i


def rtf(dados: bytes, ler_rtf) -> tuple[list[str], bool]:
    """Texto dos grupos \\header e \\footer (e variantes l, r, f). Cada grupo vira um RTF mínimo com o prólogo e a
    tabela de fontes do original e é lido pelo mesmo leitor do corpo (`ler_rtf`), que já resolve página de código
    por fonte, \\u e campos. Devolve também se todos os grupos foram lidos (False: algum teto cortou a leitura)."""
    cab = re.match(rb"\{\\rtf1(?:\\[a-z]+-?\d* ?)*", dados)
    prologo = cab.group(0) if cab else b"{\\rtf1"
    if (m := re.search(rb"\{\\fonttbl", dados)) and (fim := _fim_do_grupo(dados, m.end())) > 0:
        prologo += dados[m.start() : fim]
    vistos, saida, lidos, percorridos = set(), [], 0, 0
    for m in _GRUPO_BORDA.finditer(dados):
        fim = _fim_do_grupo(dados, m.end())
        if fim < 0:
            return saida, False  # grupo sem fim: o resto do arquivo não é cabeçalho
        percorridos += fim - m.end()  # antes do teste de repetição: grupo repetido também custa a busca do fim
        if percorridos > _RELEITURA_MAX * len(dados):
            return saida, False
        corpo = dados[m.end() : fim - 1]
        if corpo in vistos:
            continue  # mala direta repete o mesmo cabeçalho em cada seção: não conta para os tetos
        vistos.add(corpo)
        lidos += len(prologo) + len(corpo)  # o prólogo (tabela de fontes) é relido a cada grupo distinto
        if len(vistos) > _GRUPOS_MAX or lidos > _RELEITURA_MAX * len(dados):
            return saida, False  # grupos aninhados ou prólogo enorme de propósito: custo proporcional ao arquivo
        saida += ler_rtf(prologo + corpo + b"}")
    return saida, True
