"""Metadados que a empresa precisa: propriedades do arquivo, comentários, alterações rastreadas, abas ocultas."""

import hashlib  # noqa: F401
import html
import io  # noqa: F401
import json  # noqa: F401
import mmap
import os  # noqa: F401
import re  # noqa: F401
import zipfile  # noqa: F401
from xml.etree import ElementTree as ET  # noqa: F401

from .limites import xml_ou_nada


def metadados_arquivo(path, ext=None):
    """Metadados que a empresa precisa e que não estão no corpo: propriedades do arquivo,
    comentários e alterações rastreadas (DOCX), abas ocultas (XLSX), info do PDF. `ext` é o formato já decidido."""
    meta = {}
    ext = ext or os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        import pypdfium2 as pdfium

        try:
            info = pdfium.PdfDocument(path).get_metadata_dict()
            meta["propriedades"] = {k: v for k, v in info.items() if v}
        except Exception:  # noqa: BLE001, S110 — PDF sem propriedades legíveis: segue sem elas
            pass
        return meta
    if ext in ODF:
        return _metadados_odf(path, meta, ext)
    if ext in BINARIOS_OLE:
        # Word e Excel binários guardam o VBA no armazenamento _VBA_PROJECT (o PowerPoint, dentro de um objeto)
        if os.path.getsize(path):
            with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
                if m.find("_VBA_PROJECT".encode("utf-16-le")) >= 0:
                    meta["macros"] = True
        return meta
    if ext not in OOXML:
        return meta
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as z:
        nomes = set(z.namelist())
        ilegiveis = []

        def ler(parte):
            return _ler_parte(z, parte, ilegiveis)

        def arvore(parte):
            dados = ler(parte)
            raiz = xml_ou_nada(dados) if dados is not None else None
            if raiz is None and parte not in ilegiveis:
                ilegiveis.append(parte)
            return raiz

        props = {}
        for parte in ("docProps/core.xml", "docProps/app.xml"):
            if parte in nomes and (raiz := arvore(parte)) is not None:
                for el in raiz.iter():
                    tag = el.tag.split("}")[1]
                    if (
                        el.text
                        and el.text.strip()
                        and tag
                        in (
                            "title",
                            "subject",
                            "creator",
                            "lastModifiedBy",
                            "created",
                            "modified",
                            "revision",
                            "keywords",
                            "description",
                            "category",
                            "Company",
                            "Manager",
                            "contentStatus",
                            "version",
                        )
                    ):
                        props[tag] = el.text.strip()
        if props:
            meta["propriedades"] = props
        if "word/comments.xml" in nomes and (raiz := arvore("word/comments.xml")) is not None:
            com = []
            for c in raiz.iter(f"{W}comment"):
                txt = "".join(t.text or "" for t in c.iter(f"{W}t")).strip()
                com.append({"autor": c.get(f"{W}author"), "data": c.get(f"{W}date"), "texto": txt})
            if com:
                meta["comentarios"] = com
        if "word/document.xml" in nomes:
            doc = z.read("word/document.xml")
            ins, dele = doc.count(b"<w:ins "), doc.count(b"<w:del ")
            if ins or dele:
                meta["alteracoes_rastreadas"] = {"insercoes": ins, "exclusoes": dele}
        if "xl/workbook.xml" in nomes and (raiz := arvore("xl/workbook.xml")) is not None:
            ocultas = [
                sh.get("name")
                for sh in raiz.iter()
                if sh.tag.endswith("}sheet") and sh.get("state") in ("hidden", "veryHidden")
            ]
            if ocultas:
                meta["abas_ocultas"] = ocultas
        bordas = []
        for parte in sorted(nomes):
            if re.fullmatch(r"word/(header|footer)\d*\.xml", parte) and (raiz := arvore(parte)) is not None:
                bordas += [_texto_word(par, W) for par in raiz.iter(f"{W}p")]
            elif re.fullmatch(r"xl/worksheets/sheet\d+\.xml", parte) and (dados := ler(parte)) is not None:
                # a aba pode ser enorme: só o trecho <headerFooter> (fica no fim) é lido, sem montar a árvore
                for _, texto in _CABECALHO_XLSX.findall(dados):
                    bordas.append(_sem_codigos_excel(html.unescape(texto.decode("utf-8", "replace"))))
        _guardar_bordas(meta, bordas)
        if any(n.lower().endswith("vbaproject.bin") or n.startswith("xl/macrosheets/") for n in nomes):
            meta["macros"] = True  # VBA ou macro do Excel 4.0: nunca executadas; ficam no arquivo e o aviso vai junto
        if ilegiveis:
            meta["partes_ilegiveis"] = ilegiveis  # malformadas ou hostis: ficaram de fora dos metadados
    return meta


_CABECALHO_XLSX = re.compile(
    rb"<(?:\w+:)?(oddHeader|oddFooter|evenHeader|evenFooter|firstHeader|firstFooter)\b[^>]*>(.*?)</", re.S
)
BINARIOS_OLE = {".doc", ".xls", ".ppt", ".pps", ".pot"}


def _ler_parte(z, parte, ilegiveis):
    """Bytes da parte, ou None (registrada) se ela não puder ser lida: CRC errado, cifrada, compressão estranha."""
    try:
        return z.read(parte)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, OSError, KeyError, ValueError):
        ilegiveis.append(parte)
        return None


def _texto_word(par, W) -> str:
    """Texto do parágrafo do Word com tab e quebra viradas espaço (o cabeçalho costuma separar com tab)."""
    partes = []
    for el in par.iter():
        if el.tag == f"{W}t":
            partes.append(el.text or "")
        elif el.tag in (f"{W}tab", f"{W}br", f"{W}cr"):
            partes.append(" ")
    return "".join(partes)


OOXML = {".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pptm", ".ppsx", ".ppsm"}
ODF = {".odt", ".ods", ".odp"}
CABECALHOS_XLSX = {"oddHeader", "oddFooter", "evenHeader", "evenFooter", "firstHeader", "firstFooter"}
ODF_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
    "dc": "http://purl.org/dc/elements/1.1/",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
}


def _sem_codigos_excel(texto: str) -> str:
    """Cabeçalho do Excel sem os códigos de formatação: &L/&C/&R viram separador, &P/&N/&D/&T/&F/&A viram nome."""
    nomes = {"P": "[página]", "N": "[páginas]", "D": "[data]", "T": "[hora]", "F": "[arquivo]", "A": "[aba]"}
    texto = re.sub(r'&"[^"]*"', "", texto)  # fonte
    texto = re.sub(r"&K(?:[0-9A-Fa-f]{6}|\d{2}[+-]\d{3})", "", texto)  # cor (RGB ou tema)
    texto = re.sub(r"&\d+", "", texto)  # tamanho
    texto = re.sub(r"&([LCR])", " | ", texto)
    texto = re.sub(r"&([PNDTFA])", lambda m: nomes[m.group(1)], texto)
    texto = re.sub(r"&[BIUSEXYKGZOH]", "", texto).replace("&&", "&")
    return " ".join(p.strip() for p in texto.split("|") if p.strip())


def _guardar_bordas(meta: dict, textos):
    """Cabeçalho e rodapé saem do corpo e ficam aqui: nada some do documento."""
    # campo que o LibreOffice não resolveu fica em cache como "???": sem conteúdo, não é guardado
    vistos = sorted({" ".join(t.split()) for t in textos if t and re.search(r"\w", re.sub(r"\?{3}", "", t))})
    if vistos:
        meta["cabeçalho/rodapé"] = vistos


def _texto_odf(el) -> str:
    """Texto ODF com tab, quebra de linha e espaços (text:s) virados espaço."""
    partes = [el.text or ""]
    for filho in el:
        nome = filho.tag.split("}")[-1]
        partes.append(" " if nome in ("tab", "line-break", "s") else _texto_odf(filho))
        partes.append(filho.tail or "")
    return "".join(partes)


def _metadados_odf(path, meta, ext):
    """ODT, ODS e ODP: propriedades (meta.xml) e cabeçalho/rodapé das páginas mestras (styles.xml), que o leitor
    do corpo não vê; no ODP, também as declarações de rodapé e cabeçalho dos slides (content.xml)."""
    ns = {k: "{" + v + "}" for k, v in ODF_NS.items()}
    with zipfile.ZipFile(path) as z:
        nomes = set(z.namelist())
        ilegiveis = []

        def arvore(parte):
            if parte not in nomes:
                return None
            dados = _ler_parte(z, parte, ilegiveis)
            raiz = xml_ou_nada(dados) if dados is not None else None
            if raiz is None and parte not in ilegiveis:
                ilegiveis.append(parte)
            return raiz

        if (raiz := arvore("meta.xml")) is not None:
            campos = {
                "title": "title",
                "subject": "subject",
                "creator": "lastModifiedBy",
                "initial-creator": "creator",
                "creation-date": "created",
                "date": "modified",
                "keyword": "keywords",
                "description": "description",
            }
            props = {}
            chaves = []
            for el in raiz.iter():
                nome = el.tag.split("}")[-1]
                if nome == "keyword" and el.text and el.text.strip():
                    chaves.append(el.text.strip())  # cada palavra-chave vem num elemento próprio
                elif nome in campos and el.text and el.text.strip():
                    props.setdefault(campos[nome], el.text.strip())
            if chaves:
                props["keywords"] = ", ".join(chaves)
            if props:
                meta["propriedades"] = props
        bordas = []
        if (raiz := arvore("styles.xml")) is not None:
            for mestra in raiz.iter(ns["style"] + "master-page"):
                for el in mestra:
                    nome = el.tag.split("}")[-1]
                    if nome in ("header", "footer", "header-left", "footer-left", "header-first", "footer-first"):
                        bordas += [_texto_odf(p) for p in el.iter() if p.tag in (ns["text"] + "p", ns["text"] + "h")]
                for quadro in mestra.iter(ns["draw"] + "frame"):
                    if quadro.get(ns["presentation"] + "class") in ("footer", "header"):
                        bordas += [_texto_odf(p) for p in quadro.iter(ns["text"] + "p")]
        if ext == ".odp" and (raiz := arvore("content.xml")) is not None:  # declarações só no ODP
            for el in raiz.iter():
                if el.tag in (ns["presentation"] + "footer-decl", ns["presentation"] + "header-decl"):
                    bordas.append(_texto_odf(el))
        _guardar_bordas(meta, bordas)
        if any(n.startswith("Basic/") or n.startswith("Scripts/") for n in nomes):
            meta["macros"] = True
        if ilegiveis:
            meta["partes_ilegiveis"] = ilegiveis
    return meta
