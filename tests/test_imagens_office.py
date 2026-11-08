"""Imagem do Office que não abre nunca derruba o documento; SVG embutido é desenhado e lido."""

import io
import re
import zipfile

from docx import Document
from docx.shared import Inches
from PIL import Image

import lince


def _png(cor="#2561C1", tamanho=(200, 100)):
    b = io.BytesIO()
    Image.new("RGB", tamanho, cor).save(b, "PNG")
    return b.getvalue()


def _docx_com(tmp_path, ext, mime, dados):
    """Word com uma imagem entre dois parágrafos, trocada pelo conteúdo e pelo tipo dados."""
    base = tmp_path / "base.docx"
    d = Document()
    d.add_paragraph("Antes.")
    d.add_picture(io.BytesIO(_png()), width=Inches(3))
    d.add_paragraph("Depois.")
    d.save(base)
    destino = tmp_path / f"com_{ext}.docx"
    with zipfile.ZipFile(base) as zi, zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as zo:
        for n in zi.namelist():
            conteudo = zi.read(n)
            if n.startswith("word/media/"):
                zo.writestr(re.sub(r"\.\w+$", f".{ext}", n), dados)
                continue
            if n == "word/_rels/document.xml.rels":
                conteudo = re.sub(rb"(media/image\d+)\.\w+", rb"\1." + ext.encode(), conteudo)
            if n == "[Content_Types].xml":
                conteudo = conteudo.replace(
                    b"<Default ", f'<Default Extension="{ext}" ContentType="{mime}"/><Default '.encode(), 1
                )
            zo.writestr(n, conteudo)
    return destino


def _ler(p):
    vistas = []
    r = lince.Leitor(
        ia=lince.FuncaoLeitor(lambda img, instr: vistas.append(img) or "Lido pela IA.", "m"), agrupar=False
    )
    return r.ler(p), vistas


def test_imagem_emf_ou_quebrada_nao_derruba_o_documento(tmp_path):
    for ext, mime, dados, rotulo in (
        ("emf", "image/x-emf", b"\x01\x00\x00\x00" + b"\x00" * 36 + b" EMF" + b"\x00" * 60, "EMF"),
        ("wmf", "image/x-wmf", b"\xd7\xcd\xc6\x9a" + b"\x00" * 40, "WMF"),
        ("png", "image/png", b"\x89PNG\r\n\x1a\n quebrado", "image/png"),
    ):
        r, vistas = _ler(_docx_com(tmp_path, ext, mime, dados))
        md = r.markdown
        assert md.index("Antes.") < md.index("não lida]") < md.index("Depois."), ext  # no lugar da imagem
        assert not vistas and any("não lida" in x for x in r.metadados["partes_ilegiveis"]), ext
        assert rotulo in md or rotulo in " ".join(r.metadados["partes_ilegiveis"]), ext


def test_svg_dentro_do_word_e_desenhado_e_lido(tmp_path):
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="150"><rect width="300" height="150"'
        b' fill="#2561C1"/><image href="/etc/passwd" width="10" height="10"/></svg>'
    )
    r, vistas = _ler(_docx_com(tmp_path, "svg", "image/svg+xml", svg))
    assert len(vistas) == 1 and "Lido pela IA." in r.markdown
    cores = {c for _, c in Image.open(io.BytesIO(vistas[0])).convert("RGB").getcolors(1 << 24)}
    assert (37, 97, 193) in cores
    assert r.markdown.index("Antes.") < r.markdown.index("Lido pela IA.") < r.markdown.index("Depois.")
