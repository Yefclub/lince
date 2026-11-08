"""Estrutura do documento para quem navega ou indexa: páginas, slides, abas e seções do Markdown.

Os números vêm do próprio arquivo, não do Markdown: slide sem título e aba vazia também contam. Estrutura é auxílio:
arquivo fora do padrão fica sem ela, nunca derruba a leitura. Posições (`inicio`) são em pontos de código Unicode,
como `len()` do Python; em JavaScript, conte por `Array.from(texto)` (um emoji ocupa duas unidades UTF-16)."""

import re
import struct
import zipfile

from defusedxml.ElementTree import iterparse

from .limites import xml_ou_nada

SECOES_MAX = 2000  # documento com mais títulos que isso é índice de livro: as primeiras bastam para navegar
_TITULO = re.compile(r"(#{1,6})[ \t]+(\S.*?)[ \t]*\r?")  # título ATX; linha quebrada só em \n, como no Markdown
_CERCA = re.compile(r" {0,3}(`{3,}|~{3,})(.*?)\r?")
_PAGINA = re.compile(r"<!-- página ([1-9]\d*) · \w+ -->\r?")  # exatamente o que a montagem do PDF escreve
_PPTX = {".pptx", ".pptm", ".ppsx", ".ppsm"}
_XLSX = {".xlsx", ".xlsm"}
_ODF_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_ODF_STYLE = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"


def secoes(md: str, pdf: bool = False) -> dict:
    """Títulos do Markdown fora de bloco de código (cercas pela regra do CommonMark), com nível e posição. No PDF,
    também onde começa cada página: o marcador é do próprio Lince e fecha qualquer cerca deixada aberta (uma resposta
    da IA cortada no teto não apaga as âncoras das páginas seguintes)."""
    titulos, paginas, pos, cerca = [], [], 0, None  # cerca aberta: (caractere, tamanho)
    cortadas = False
    for linha in md.split("\n"):
        if pdf and (m := _PAGINA.fullmatch(linha)):
            paginas.append({"pagina": int(m.group(1)), "inicio": pos})
            cerca = None
        elif m := _CERCA.fullmatch(linha):
            marca, resto = m.group(1), m.group(2)
            if cerca is None:
                if not (marca[0] == "`" and "`" in resto):  # ```x``` na linha é código inline, não cerca
                    cerca = (marca[0], len(marca))
            elif marca[0] == cerca[0] and len(marca) >= cerca[1] and not resto.strip():
                cerca = None  # fecha só com o mesmo caractere, pelo menos o mesmo tamanho e sem texto depois
        elif cerca is None and (m := _TITULO.fullmatch(linha)):
            if len(titulos) < SECOES_MAX:
                titulos.append({"titulo": m.group(2), "nivel": len(m.group(1)), "inicio": pos})
            else:
                cortadas = True
        pos += len(linha) + 1
    saida = {"secoes": titulos}
    if cortadas:
        saida["secoes_cortadas"] = True
    if paginas:
        saida["inicio_das_paginas"] = paginas
    return saida


def estrutura_arquivo(path, ext) -> dict:
    """Slides e abas dos formatos em pacote (OOXML e ODF). Os binários antigos vêm de `legados`."""
    try:
        with zipfile.ZipFile(path) as z:
            if ext in _PPTX:
                raiz = xml_ou_nada(z.read("ppt/presentation.xml"))
                if raiz is None:
                    return {}
                return {"slides": sum(1 for el in raiz.iter() if el.tag.endswith("}sldId"))}  # a lista que vale
            if ext in _XLSX:
                return _abas_xlsx(z.read("xl/workbook.xml"))
            if ext == ".xlsb":
                return _abas_xlsb(z.read("xl/workbook.bin"))
            if ext in (".ods", ".odp"):
                with z.open("content.xml") as f:
                    return _odf(f, ext)
    except MemoryError:
        raise
    except Exception:  # noqa: BLE001 — sem estrutura, a leitura segue igual
        return {}
    return {}


def _abas_xlsx(dados) -> dict:
    raiz = xml_ou_nada(dados)
    if raiz is None:
        return {}
    abas = [  # pelo nome local: vale também no OOXML Strict, que usa outro namespace
        {"nome": s.get("name", ""), "oculta": s.get("state") in ("hidden", "veryHidden")}
        for s in raiz.iter()
        if s.tag.endswith("}sheet")
    ]
    return {"abas": abas} if abas else {}


def _abas_xlsb(b: bytes) -> dict:
    """BrtBundleSh (0x9C) do workbook.bin: hsState, iTabID, strRelID e strName (MS-XLSB 2.4.304)."""
    abas, i = [], 0
    while i < len(b):
        tipo = b[i]
        i += 1
        if tipo & 0x80:
            tipo = (tipo & 0x7F) | (b[i] << 7)
            i += 1
        tam, desloc = 0, 0
        for _ in range(4):
            c = b[i]
            i += 1
            tam |= (c & 0x7F) << desloc
            desloc += 7
            if not c & 0x80:
                break
        d = b[i : i + tam]
        i += tam
        if tipo == 0x9C and len(d) >= 16:
            estado = struct.unpack_from("<I", d, 0)[0]
            j = 8
            n_rel = struct.unpack_from("<I", d, j)[0]
            j += 4 + (0 if n_rel == 0xFFFFFFFF else 2 * n_rel)
            if j + 4 <= len(d):
                n = struct.unpack_from("<I", d, j)[0]
                nome = d[j + 4 : j + 4 + 2 * n].decode("utf-16-le", "replace")
                abas.append({"nome": nome, "oculta": bool(estado & 3)})
    return {"abas": abas} if abas else {}


def _odf(arquivo, ext) -> dict:
    """content.xml em leitura incremental (expat: linear, sem expansão de entidades), limpando cada elemento: a
    planilha pode ser enorme e só os nomes das tabelas e os estilos que as escondem interessam."""
    ocultos, abas, slides, estilo = set(), [], 0, None
    for evento, el in iterparse(arquivo, events=("start", "end")):
        if evento == "start":
            if el.tag == f"{_ODF_STYLE}style" and el.get(f"{_ODF_STYLE}family") == "table":
                estilo = el.get(f"{_ODF_STYLE}name")
            elif el.tag == f"{_ODF_STYLE}table-properties" and estilo and el.get(f"{_ODF_TABLE}display") == "false":
                ocultos.add(estilo)
            elif el.tag == f"{_ODF_TABLE}table" and ext == ".ods":
                nome = el.get(f"{_ODF_TABLE}name")
                if nome is not None:
                    abas.append({"nome": nome, "oculta": el.get(f"{_ODF_TABLE}style-name") in ocultos})
            elif el.tag == "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}page":
                slides += 1
            continue
        if el.tag == f"{_ODF_STYLE}style":
            estilo = None
        el.clear()
    if ext == ".odp":
        return {"slides": slides}
    return {"abas": abas} if abas else {}
