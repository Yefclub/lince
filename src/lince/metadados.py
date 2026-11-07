"""Metadados que a empresa precisa: propriedades do arquivo, comentários, alterações rastreadas, abas ocultas."""

import hashlib  # noqa: F401
import io  # noqa: F401
import json  # noqa: F401
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
        return _metadados_odf(path, meta)
    if ext not in OOXML:
        return meta
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as z:
        nomes = set(z.namelist())
        ilegiveis = []

        def arvore(parte):
            raiz = xml_ou_nada(z.read(parte))
            if raiz is None:
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
                bordas += ["".join(t.text or "" for t in par.iter(f"{W}t")) for par in raiz.iter(f"{W}p")]
            elif re.fullmatch(r"xl/worksheets/sheet\d+\.xml", parte) and (raiz := arvore(parte)) is not None:
                for el in raiz.iter():
                    if el.tag.split("}")[-1] in CABECALHOS_XLSX and el.text:
                        bordas.append(_sem_codigos_excel(el.text))
        _guardar_bordas(meta, bordas)
        if any(n.lower().endswith("vbaproject.bin") for n in nomes):
            meta["macros"] = True  # nunca executadas; ficam no arquivo e o aviso vai junto
        if ilegiveis:
            meta["partes_ilegiveis"] = ilegiveis  # malformadas ou hostis: ficaram de fora dos metadados
    return meta


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
    texto = re.sub(r"&\d+", "", texto)  # tamanho
    texto = re.sub(r"&([LCR])", " | ", texto)
    texto = re.sub(r"&([PNDTFA])", lambda m: nomes[m.group(1)], texto)
    texto = re.sub(r"&[BIUSEXYKGZ]", "", texto).replace("&&", "&")
    return " ".join(p.strip() for p in texto.split("|") if p.strip())


def _guardar_bordas(meta: dict, textos):
    """Cabeçalho e rodapé saem do corpo e ficam aqui: nada some do documento."""
    # campo que o LibreOffice não resolveu fica em cache como "???": sem conteúdo, não é guardado
    vistos = sorted({" ".join(t.split()) for t in textos if t and re.search(r"\w", re.sub(r"\?{3}", "", t))})
    if vistos:
        meta["cabeçalho/rodapé"] = vistos


def _texto_odf(el) -> str:
    return "".join(el.itertext())


def _metadados_odf(path, meta):
    """ODT, ODS e ODP: propriedades (meta.xml) e cabeçalho/rodapé das páginas mestras (styles.xml), que o leitor
    do corpo não vê; no ODP, também as declarações de rodapé e cabeçalho dos slides (content.xml)."""
    ns = {k: "{" + v + "}" for k, v in ODF_NS.items()}
    with zipfile.ZipFile(path) as z:
        nomes = set(z.namelist())
        ilegiveis = []

        def arvore(parte):
            if parte not in nomes:
                return None
            raiz = xml_ou_nada(z.read(parte))
            if raiz is None:
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
            for el in raiz.iter():
                nome = el.tag.split("}")[-1]
                if nome in campos and el.text and el.text.strip():
                    props.setdefault(campos[nome], el.text.strip())
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
        if (raiz := arvore("content.xml")) is not None:
            for el in raiz.iter():
                if el.tag in (ns["presentation"] + "footer-decl", ns["presentation"] + "header-decl"):
                    bordas.append(_texto_odf(el))
        _guardar_bordas(meta, bordas)
        if any(n.startswith("Basic/") or n.startswith("Scripts/") for n in nomes):
            meta["macros"] = True
        if ilegiveis:
            meta["partes_ilegiveis"] = ilegiveis
    return meta
