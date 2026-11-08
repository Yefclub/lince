"""Metadados que a empresa precisa: propriedades do arquivo, comentários, alterações rastreadas, abas ocultas."""

import hashlib  # noqa: F401
import html
import io  # noqa: F401
import json  # noqa: F401
import lzma
import mmap
import os  # noqa: F401
import re  # noqa: F401
import zipfile  # noqa: F401
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET  # noqa: F401

from . import legados
from .estrutura import estrutura_arquivo
from .limites import xml_ou_nada
from .seguranca import bordas_sem_oculto


def metadados_arquivo(path, ext=None):
    """Metadados que a empresa precisa e que não estão no corpo: propriedades do arquivo,
    comentários e alterações rastreadas (DOCX), abas ocultas (XLSX), info do PDF. `ext` é o formato já decidido."""
    meta = {}
    ext = ext or os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        import pypdfium2 as pdfium

        from ._trava import PDFIUM

        try:
            with PDFIUM:
                doc = pdfium.PdfDocument(path)
                try:
                    info = doc.get_metadata_dict()
                finally:
                    doc.close()
            meta["propriedades"] = {k: v for k, v in info.items() if v}
        except Exception:  # noqa: BLE001, S110 — PDF sem propriedades legíveis: segue sem elas
            pass
        return meta
    if ext in ODF:
        meta = _metadados_odf(path, meta, ext)
        if estrutura := estrutura_arquivo(path, ext):
            meta["estrutura"] = estrutura
        return meta
    if ext == ".rtf":
        try:
            bordas, completo = legados.rtf(Path(path).read_bytes(), _linhas_rtf)
        except MemoryError:
            raise  # falta de memória não é parte ruim: derruba o documento, como no resto da leitura
        except Exception:  # noqa: BLE001 — RTF fora do padrão: o corpo continua lido, sem cabeçalho e rodapé
            meta["partes_ilegiveis"] = ["cabeçalho e rodapé"]
        else:
            _guardar_bordas(meta, bordas)
            if not completo:
                meta["partes_ilegiveis"] = ["cabeçalho e rodapé (parcial)"]
        return meta
    if ext in BINARIOS_OLE:
        # Word e Excel binários guardam o VBA no armazenamento _VBA_PROJECT (o PowerPoint, dentro de um objeto)
        if os.path.getsize(path):
            with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
                if m.find("_VBA_PROJECT".encode("utf-16-le")) >= 0:
                    meta["macros"] = True
        return _metadados_ole(path, meta, ext)
    if ext not in OOXML:
        return meta
    if estrutura := estrutura_arquivo(path, ext):
        meta["estrutura"] = estrutura
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as z:
        nomes = set(z.namelist())
        ilegiveis = []
        # o texto oculto do cabeçalho, do rodapé e dos comentários sai daqui como sai do corpo
        sem_oculto = bordas_sem_oculto(z) if ext in (".docx", ".docm") else {}

        def ler(parte):
            return sem_oculto[parte] if parte in sem_oculto else _ler_parte(z, parte, ilegiveis)

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
                bordas += _paragrafos_word(raiz, W)
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


def _metadados_ole(path, meta, ext):
    """DOC, XLS e PPT: cabeçalho e rodapé, abas ocultas e macro que o _VBA_PROJECT não denuncia (VBA do PowerPoint
    e folha de macro do Excel 4.0). Excel 2.x a 4.x não são OLE: ficam sem esses metadados."""
    import olefile

    if not olefile.isOleFile(path):
        return meta
    teto = os.path.getsize(path)
    try:
        with olefile.OleFileIO(path) as o:
            if ext == ".doc":
                achado = {"bordas": legados.doc(o, teto)}
            elif ext == ".xls":
                achado = legados.xls(o, teto)
            else:
                achado = legados.ppt(o, teto)
    except MemoryError:
        raise
    except Exception:  # noqa: BLE001 — OLE malformado ou hostil: o corpo continua lido, sem estes metadados
        meta["partes_ilegiveis"] = ["cabeçalho, rodapé e macros"]
        return meta
    bordas = achado.get("bordas", [])
    _guardar_bordas(meta, [_sem_codigos_excel(t) for t in bordas] if ext == ".xls" else bordas)
    if achado.get("ocultas"):
        meta["abas_ocultas"] = achado["ocultas"]
    if achado.get("macro"):
        meta["macros"] = True
    estrutura = {k: achado[k] for k in ("abas", "slides") if k in achado}
    if estrutura:
        meta["estrutura"] = estrutura
    return meta


def _linhas_rtf(dados: bytes) -> list[str]:
    """Linhas de texto de um RTF pelo mesmo leitor do corpo, sem a marcação do Markdown: tabela vira as células
    separadas por espaço; título e item de lista perdem a marca."""
    import anydoc

    from .office import serializar

    try:
        md, _ = serializar(anydoc.to_document(dados, format="rtf"), lambda *_: "")
    except anydoc.ConvertError:
        return []
    linhas = []
    for linha in md.splitlines():
        if re.fullmatch(r"\|(?: --- \|)+", linha):
            continue
        if linha.startswith("|"):
            linha = " ".join(c.strip() for c in linha.strip("|").split(" | ") if c.strip())
        linha = re.sub(r"^\s*(?:#{1,6}|[-*+]|\d+[.)])\s+", "", linha)
        linhas.append(linha.removeprefix("\\").replace("\\|", "|"))
    return linhas


_CABECALHO_XLSX = re.compile(  # (?<!/) : <oddHeader/> vazio não engole o elemento seguinte
    rb"<(?:\w+:)?(oddHeader|oddFooter|evenHeader|evenFooter|firstHeader|firstFooter)\b[^>]*(?<!/)>(.*?)</", re.S
)
BINARIOS_OLE = {".doc", ".xls", ".ppt", ".pps", ".pot"}


# o que o zipfile levanta numa parte que não lê: CRC errado, cifrada, compressão estranha
ERROS_DE_PARTE = (
    zipfile.BadZipFile,
    RuntimeError,
    NotImplementedError,
    EOFError,
    OSError,
    KeyError,
    ValueError,
    zlib.error,
    lzma.LZMAError,
)


def _ler_parte(z, parte, ilegiveis):
    """Bytes da parte, ou None (registrada) se ela não puder ser lida."""
    try:
        return z.read(parte)
    except ERROS_DE_PARTE:
        ilegiveis.append(parte)
        return None


_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"


def _paragrafos_word(raiz, W) -> list[str]:
    """Texto de cada parágrafo, sem recursão (XML hostil profundo não estoura a pilha). Caixa de texto
    (w:txbxContent) vira entrada própria em vez de colar no parágrafo de fora; mc:Fallback, que repete o
    mc:Choice, é ignorado. Tab e quebra viram espaço (o cabeçalho costuma separar com tab)."""
    saida, pilha = [], [(raiz, None)]  # (elemento, partes do parágrafo aberto em que ele está)
    while pilha:
        el, partes = pilha.pop()
        if el.tag == _MC_FALLBACK:
            continue
        if el.tag == f"{W}txbxContent":
            partes = None  # o que tem dentro não é do parágrafo de fora
        if el.tag == f"{W}p":
            partes = []
            saida.append(partes)
        elif partes is not None:
            if el.tag == f"{W}t":
                partes.append(el.text or "")
            elif el.tag in (f"{W}tab", f"{W}br", f"{W}cr"):
                partes.append(" ")
        pilha.extend((filho, partes) for filho in reversed(list(el)))
    return ["".join(p) for p in saida]


OOXML = {".docx", ".docm", ".xlsx", ".xlsm", ".xlsb", ".pptx", ".pptm", ".ppsx", ".ppsm"}
ODF = {".odt", ".ods", ".odp"}
ODF_NS = {
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
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
    """Texto ODF sem recursão (XML hostil profundo não estoura a pilha): tab, quebra e text:s viram espaço; quadro
    (draw:frame, caixa de texto) não entra, porque os parágrafos dele são lidos como entradas próprias."""
    partes, pilha = [], [("el", el)]
    while pilha:
        tipo, x = pilha.pop()
        if tipo == "texto":
            partes.append(x)
            continue
        if x is not el:
            nome = x.tag.split("}")[-1] if isinstance(x.tag, str) else ""
            if nome in ("tab", "line-break", "s"):
                partes.append(" ")
                pilha.append(("texto", x.tail or ""))
                continue
            if nome == "frame":
                pilha.append(("texto", x.tail or ""))
                continue
            pilha.append(("texto", x.tail or ""))  # o tail vem depois dos filhos (pilha: empilhado antes)
        filhos = list(x)
        for f in reversed(filhos):
            pilha.append(("el", f))
        partes.append(x.text or "")
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
