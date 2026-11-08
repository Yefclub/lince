"""Estrutura do documento para quem navega ou indexa: páginas, slides, abas e seções do Markdown.

Os números vêm do próprio arquivo, não do Markdown: slide sem título e aba vazia também contam. Estrutura é auxílio:
arquivo fora do padrão fica sem ela, nunca derruba a leitura."""

import re
import struct
import zipfile

from .limites import xml_ou_nada

SECOES_MAX = 2000  # documento com mais títulos que isso é índice de livro: as primeiras bastam para navegar
_TITULO = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_CERCA = re.compile(r"^(```|~~~)")
_PAGINA = re.compile(r"^<!-- página (\d+) ")
_PPTX = {".pptx", ".pptm", ".ppsx", ".ppsm"}
_XLSX = {".xlsx", ".xlsm"}
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def secoes(md: str) -> dict:
    """Títulos do Markdown (fora de bloco de código) com nível e posição, e onde começa cada página do PDF."""
    titulos, paginas, pos, cerca = [], [], 0, None
    for linha in md.splitlines(keepends=True):
        if m := _CERCA.match(linha):
            cerca = None if cerca == m.group(1) else (cerca or m.group(1))
        elif cerca is None:
            if (m := _TITULO.match(linha)) and len(titulos) < SECOES_MAX:
                titulos.append({"titulo": m.group(2), "nivel": len(m.group(1)), "inicio": pos})
            elif m := _PAGINA.match(linha):
                paginas.append({"pagina": int(m.group(1)), "inicio": pos})
        pos += len(linha)
    saida = {"secoes": titulos}
    if paginas:
        saida["inicio_das_paginas"] = paginas
    return saida


def estrutura_arquivo(path, ext) -> dict:
    """Slides e abas dos formatos em pacote (OOXML e ODF). Os binários antigos vêm de `legados`."""
    try:
        with zipfile.ZipFile(path) as z:
            if ext in _PPTX:
                return {"slides": sum(1 for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))}
            if ext in _XLSX:
                return _abas_xlsx(z)
            if ext == ".xlsb":
                return _abas_xlsb(z.read("xl/workbook.bin"))
            if ext == ".ods":
                return _abas_ods(z.read("content.xml"))
            if ext == ".odp":
                return {"slides": z.read("content.xml").count(b"<draw:page ")}
    except MemoryError:
        raise
    except Exception:  # noqa: BLE001 — sem estrutura, a leitura segue igual
        return {}
    return {}


def _abas_xlsx(z) -> dict:
    raiz = xml_ou_nada(z.read("xl/workbook.xml"))
    if raiz is None:
        return {}
    abas = [
        {"nome": s.get("name", ""), "oculta": s.get("state") in ("hidden", "veryHidden")}
        for s in raiz.iter(f"{_S}sheet")
    ]
    return {"abas": abas}


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


def _abas_ods(c: bytes) -> dict:
    """Nomes das tabelas do content.xml; oculta quando o estilo dela tem table:display="false". Por regex nos bytes:
    a planilha pode ser enorme e só os atributos interessam."""
    ocultos = {
        m.group(1)
        for m in re.finditer(
            rb'<style:style style:name="([^"]+)" style:family="table"[^>]*>(.*?)</style:style>', c, re.S
        )
        if b'table:display="false"' in m.group(2)
    }
    abas = []
    for m in re.finditer(rb"<table:table ([^>]*)>", c):
        attrs = dict(re.findall(rb'([\w:-]+)="([^"]*)"', m.group(1)))
        if b"table:name" in attrs:
            abas.append(
                {
                    "nome": attrs[b"table:name"].decode("utf-8", "replace"),
                    "oculta": attrs.get(b"table:style-name") in ocultos,
                }
            )
    return {"abas": abas} if abas else {}
