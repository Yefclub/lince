"""Metadados que a empresa precisa: propriedades do arquivo, comentários, alterações rastreadas, abas ocultas."""

import hashlib  # noqa: F401
import io  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import zipfile  # noqa: F401
from xml.etree import ElementTree as ET  # noqa: F401

from .limites import xml_ou_nada


def metadados_arquivo(path):
    """Metadados que a empresa precisa e que não estão no corpo: propriedades do arquivo,
    comentários e alterações rastreadas (DOCX), abas ocultas (XLSX), info do PDF."""
    meta = {}
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        import pypdfium2 as pdfium

        try:
            info = pdfium.PdfDocument(path).get_metadata_dict()
            meta["propriedades"] = {k: v for k, v in info.items() if v}
        except Exception:  # noqa: BLE001, S110 — PDF sem propriedades legíveis: segue sem elas
            pass
        return meta
    if ext not in (".docx", ".pptx", ".xlsx"):
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
        if ilegiveis:
            meta["partes_ilegiveis"] = ilegiveis  # malformadas ou hostis: ficaram de fora dos metadados
    return meta
