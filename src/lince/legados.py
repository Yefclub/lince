"""Metadados dos formatos binários e do RTF, que o leitor do corpo não entrega: cabeçalho e rodapé (DOC, XLS, PPT,
RTF), abas ocultas (XLS) e macros (VBA no PPT, folha de macro do Excel 4.0 no XLS).

Leitura defensiva: arquivo hostil ou fora do padrão nunca derruba a leitura do documento; quem chama trata a exceção
e segue sem esses metadados. Os laços andam sobre os bytes, sem recursão, e nunca passam do fim do stream."""

import re
import struct

_BOF, _EOF, _FILEPASS, _BOUNDSHEET, _DIMENSIONS = 0x0809, 0x000A, 0x002F, 0x0085, 0x0200
_HEADER, _FOOTER = 0x0014, 0x0015
FOLHA_DE_MACRO, MODULO_VBA = 1, 6  # BOUNDSHEET.dt: folha de macro do Excel 4.0 e módulo do Excel 5
_REGISTROS_POR_ABA = 4096  # o cabeçalho fica no bloco de configuração da página, antes das células


# ---------- Excel 97-2003 (BIFF8) e Excel 5/95 (BIFF5) ----------


def _registros(b, i, limite=None):
    """(tipo, dados, posição) de cada registro a partir de `i`."""
    n = 0
    while i + 4 <= len(b) and (limite is None or n < limite):
        tipo, tam = struct.unpack_from("<HH", b, i)
        yield tipo, b[i + 4 : i + 4 + tam], i
        i += 4 + tam
        n += 1


def _texto_xls(d, i, biff8, cch_bytes):
    """String do BIFF: no BIFF8 vem a flag de 16 bits; no BIFF5, só bytes (página de código do Windows)."""
    if len(d) < i + cch_bytes:
        return ""
    cch = d[i] if cch_bytes == 1 else struct.unpack_from("<H", d, i)[0]
    i += cch_bytes
    if biff8:
        alto = i < len(d) and d[i] & 1
        i += 1
        if alto:
            return d[i : i + 2 * cch].decode("utf-16-le", "replace")
    return d[i : i + cch].decode("cp1252", "replace")


def xls(o) -> dict:
    """Cabeçalho e rodapé de cada aba, abas ocultas e se há folha de macro (Excel 4.0) ou módulo VBA do Excel 5."""
    nome = "Workbook" if o.exists("Workbook") else "Book"
    b = o.openstream(nome).read()
    if len(b) < 8 or struct.unpack_from("<H", b, 0)[0] != _BOF:
        return {}
    biff8 = struct.unpack_from("<H", b, 4)[0] == 0x0600  # BOF.vers
    abas, saida = [], {"bordas": [], "ocultas": [], "macro": False}
    for tipo, d, _ in _registros(b, 0):
        if tipo == _FILEPASS:
            return {}  # protegida por senha: o resto do stream é cifrado
        if tipo == _EOF:
            break
        if tipo == _BOUNDSHEET and len(d) >= 7:
            pos, estado, dt = struct.unpack_from("<I", d, 0)[0], d[4] & 3, d[5]
            nome_aba = _texto_xls(d, 6, biff8, 1)
            abas.append(pos)
            if estado:
                saida["ocultas"].append(nome_aba)
            if dt in (FOLHA_DE_MACRO, MODULO_VBA):
                saida["macro"] = True
    for pos in abas:
        for tipo, d, _ in _registros(b, pos, _REGISTROS_POR_ABA):
            if tipo in (_DIMENSIONS, _EOF):
                break
            if tipo in (_HEADER, _FOOTER) and d:
                saida["bordas"].append(_texto_xls(d, 0, biff8, 2 if biff8 else 1))
    return saida


# ---------- PowerPoint 97-2003 ----------

_CONTEINER, _VBA_INFO, _VBA_INFO_ATOM, _CABECALHOS_RODAPES, _CSTRING = 0xF, 0x03FF, 0x0400, 0x0FD9, 0x0FBA


def ppt(o) -> dict:
    """Macro (VBAInfoAtom com fHasMacros) e textos de cabeçalho, rodapé e data dos slides e das anotações."""
    b = o.openstream("PowerPoint Document").read()
    saida = {"bordas": [], "macro": False}
    pilha = [(0, len(b), None)]  # (início, fim, tipo do contêiner pai)
    while pilha:
        i, fim, pai = pilha.pop()
        while i + 8 <= fim:
            ver, tipo, tam = struct.unpack_from("<HHI", b, i)
            corpo = i + 8
            if ver & 0xF == _CONTEINER:
                pilha.append((corpo, min(corpo + tam, fim), tipo))
            elif pai == _VBA_INFO and tipo == _VBA_INFO_ATOM and tam >= 8:
                saida["macro"] |= struct.unpack_from("<I", b, corpo + 4)[0] == 1
            elif pai == _CABECALHOS_RODAPES and tipo == _CSTRING:
                saida["bordas"].append(b[corpo : min(corpo + tam, fim)].decode("utf-16-le", "replace"))
            i = corpo + tam
    return saida


# ---------- Word 97-2003 ----------

_CAMPOS = {"PAGE": "[página]", "NUMPAGES": "[páginas]", "SECTIONPAGES": "[páginas]", "DATE": "[data]",
           "TIME": "[hora]", "FILENAME": "[arquivo]"}  # fmt: skip


def _sem_campos(texto: str) -> str:
    """Campo do Word (0x13 código 0x14 resultado 0x15) vira o resultado; número de página e data viram nome, como no
    cabeçalho do Excel. Campos aninhados são tratados com pilha."""
    saida, pilha = [], []  # pilha: [código, fase] de cada campo aberto; fase "codigo", "resultado" ou "pular"
    for c in texto:
        if c == "\x13":
            pilha.append([[], "codigo"])
        elif c == "\x14" and pilha:
            codigo, _ = pilha[-1]
            nome = "".join(codigo).split()[:1]
            if nome and nome[0].upper() in _CAMPOS:
                if all(f == "resultado" for _, f in pilha[:-1]):
                    saida.append(_CAMPOS[nome[0].upper()])
                pilha[-1][1] = "pular"
            else:
                pilha[-1][1] = "resultado"
        elif c == "\x15" and pilha:
            pilha.pop()
        elif not pilha or all(f == "resultado" for _, f in pilha):
            saida.append(c)
        elif pilha[-1][1] == "codigo":
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
    partes = []
    for k in range(n):
        a, z = max(cps[k], ini), min(cps[k + 1], fim)
        if a >= z:
            continue
        fc = struct.unpack_from("<I", plc, 4 * (n + 1) + 8 * k + 2)[0]
        if fc & 0x40000000:  # comprimido: um byte por caractere, cp1252
            o = (fc & 0x3FFFFFFF) // 2 + (a - cps[k])
            partes.append(wd[o : o + (z - a)].decode("cp1252", "replace"))
        else:
            o = fc + 2 * (a - cps[k])
            partes.append(wd[o : o + 2 * (z - a)].decode("utf-16-le", "replace"))
    return "".join(partes)


def doc(o) -> list[str]:
    """Parágrafos da história de cabeçalho e rodapé do Word 97-2003 (vem depois do corpo e das notas de rodapé)."""
    wd = o.openstream("WordDocument").read()
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
    texto = _sem_campos(_texto_por_cp(wd, o.openstream(nome_tabela).read(), fib, ini, ini + ccp_cabecalho))
    texto = re.sub(r"[\x00-\x08\x0e-\x1f]", "", texto.replace("\x07", " ").replace("\x0b", " "))
    return [p.replace("\t", " ") for p in texto.split("\r")]


# ---------- RTF ----------

_GRUPO_BORDA = re.compile(rb"\{\\(?:header|footer)[lrf]?(?![a-z])")
_GRUPOS_MAX = 64  # Word grava até seis por seção (comum, esquerda, direita, primeira; cabeçalho e rodapé)


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


def rtf(dados: bytes, ler_rtf) -> list[str]:
    """Texto dos grupos \\header e \\footer (e variantes l, r, f). Cada grupo vira um RTF mínimo com o prólogo e a
    tabela de fontes do original e é lido pelo mesmo leitor do corpo (`ler_rtf`), que já resolve página de código
    por fonte, \\u e campos."""
    cab = re.match(rb"\{\\rtf1(?:\\[a-z]+-?\d* ?)*", dados)
    prologo = cab.group(0) if cab else b"{\\rtf1"
    if (m := re.search(rb"\{\\fonttbl", dados)) and (fim := _fim_do_grupo(dados, m.end())) > 0:
        prologo += dados[m.start() : fim]
    vistos, saida, lidos = set(), [], 0
    for n, m in enumerate(_GRUPO_BORDA.finditer(dados)):
        if n >= _GRUPOS_MAX:
            break
        fim = _fim_do_grupo(dados, m.end())
        if fim < 0:
            break  # grupo sem fim: o resto do arquivo não é cabeçalho
        corpo = dados[m.end() : fim - 1]
        lidos += len(corpo)
        if lidos > len(dados):
            break  # grupos aninhados de propósito: nunca relê mais que o próprio arquivo
        if corpo in vistos:
            continue
        vistos.add(corpo)
        saida += ler_rtf(prologo + corpo + b"}")
    return saida
