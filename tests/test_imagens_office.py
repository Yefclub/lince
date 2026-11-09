"""Imagem do Office que não abre nunca derruba o documento; SVG embutido é desenhado e lido."""

import io
import re
import sys
import time
import zipfile
from pathlib import Path

import pytest
from docx import Document
from docx.shared import Inches
from PIL import Image
from pptx import Presentation
from pptx.util import Inches as PI

import lince
from lince import desenho_svg
from lince.pagina_html import DESENHOS_MAX


def _png(cor="#2561C1", tamanho=(200, 100)):
    b = io.BytesIO()
    Image.new("RGB", tamanho, cor).save(b, "PNG")
    return b.getvalue()


def _trocar_midia(base, destino, ext, mime, dados):
    """Copia o pacote trocando toda imagem pelo conteúdo e pelo tipo dados."""
    with zipfile.ZipFile(base) as zi, zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as zo:
        for n in zi.namelist():
            conteudo = zi.read(n)
            if "/media/" in n:
                zo.writestr(re.sub(r"\.\w+$", f".{ext}", n), dados)
                continue
            if n.endswith(".rels"):
                conteudo = re.sub(rb"(media/image\d+)\.\w+", rb"\1." + ext.encode(), conteudo)
            if n == "[Content_Types].xml":
                tipo = f'<Default Extension="{ext}" ContentType="{mime}"/><Default '.encode()
                conteudo = conteudo.replace(b"<Default ", tipo, 1)
            zo.writestr(n, conteudo)
    return destino


def _docx_com(tmp_path, ext, mime, dados, imagens=1):
    """Word com imagens entre dois parágrafos, trocadas pelo conteúdo e pelo tipo dados."""
    base = tmp_path / "base.docx"
    d = Document()
    d.add_paragraph("Antes.")
    for _ in range(imagens):
        d.add_picture(io.BytesIO(_png()), width=Inches(3))
    d.add_paragraph("Depois.")
    d.save(base)
    return _trocar_midia(base, tmp_path / f"com_{ext}.docx", ext, mime, dados)


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


def _emf_real():
    """O EMF do Word 97 de teste do Apache POI (um desenho vetorial de verdade)."""
    import olefile

    from lince import legados

    p = Path(__file__).parent / "fixtures" / "reais" / "poi_vector_image.doc"
    with olefile.OleFileIO(p) as o:
        return next(d for m, d in legados.imagens(o, p.stat().st_size)[0] if m == "image/x-emf")


@pytest.mark.skipif(
    sys.version_info >= (3, 15), reason="o metafile-render ainda não instala no 3.15 (o fallback tem teste)"
)
def test_emf_dentro_do_word_e_desenhado_e_lido_no_lugar(tmp_path):
    r, vistas = _ler(_docx_com(tmp_path, "emf", "image/x-emf", _emf_real()))
    assert len(vistas) == 1 and Image.open(io.BytesIO(vistas[0])).size[0] > 0
    assert r.markdown.index("Antes.") < r.markdown.index("Lido pela IA.") < r.markdown.index("Depois.")
    assert not r.metadados.get("partes_ilegiveis")


@pytest.mark.skipif(
    sys.version_info >= (3, 15), reason="o metafile-render ainda não instala no 3.15 (o fallback tem teste)"
)
def test_emf_com_tipo_declarado_errado_e_reconhecido_pela_assinatura(tmp_path):
    r, vistas = _ler(_docx_com(tmp_path, "png", "image/png", _emf_real()))  # o pacote diz PNG; o conteúdo é EMF
    assert len(vistas) == 1 and r.markdown.index("Antes.") < r.markdown.index("Lido pela IA.")


@pytest.mark.parametrize("ext,mime", [("emf", "image/x-emf"), ("png", "image/png")])
def test_emf_no_word_sem_o_desenhista_fica_marcado_como_emf(tmp_path, monkeypatch, ext, mime):
    monkeypatch.setitem(sys.modules, "metafile_render", None)
    r, vistas = _ler(_docx_com(tmp_path, ext, mime, _emf_real()))
    assert not vistas and "[imagem 1: imagem EMF não lida]" in r.markdown
    assert r.cobertura.pendentes == ['imagem 1: EMF não desenhado, sem metafile-render (instale "lince[emf]")']


def test_assinaturas_de_wmf_e_emf():
    from lince.office import _metafile

    assert _metafile(b"\x00" * 40 + b" EMF" + b"\x00" * 20, "image/png") == "image/x-emf"
    assert _metafile(b"\xd7\xcd\xc6\x9a" + b"\x00" * 20, None) == "image/x-wmf"  # placeable
    for cabecalho in (b"\x01\x00\x09\x00\x00\x03", b"\x02\x00\x09\x00\x00\x01"):  # padrão, versões 3 e 1
        assert _metafile(cabecalho + b"\x00" * 20, None) == "image/x-wmf"
    assert _metafile(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50, "image/png") is None


SVG_AZUL = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="150">'
    b'<rect width="300" height="150" fill="#2561C1"/></svg>'
)


def test_svg_no_word_sem_resvg_fica_marcado(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "resvg_py", None)  # o pacote não está instalado
    r, vistas = _ler(_docx_com(tmp_path, "svg", "image/svg+xml", SVG_AZUL))
    assert not vistas and "[imagem 1: imagem SVG não lida]" in r.markdown
    assert any("svg" in x for x in r.metadados["partes_ilegiveis"])


def test_emf_no_powerpoint_nao_derruba_a_apresentacao(tmp_path):
    base = tmp_path / "base.pptx"
    p = Presentation()
    s = p.slides.add_slide(p.slide_layouts[5])
    s.shapes.title.text = "Fluxo da moagem"
    s.shapes.add_picture(io.BytesIO(_png()), PI(1), PI(2))
    p.save(base)
    emf = b"\x01\x00\x00\x00" + b"\x00" * 36 + b" EMF" + b"\x00" * 60
    r, vistas = _ler(_trocar_midia(base, tmp_path / "emf.pptx", "emf", "image/x-emf", emf))
    assert "Fluxo da moagem" in r.markdown and "EMF não lida]" in r.markdown and not vistas


def test_svgs_do_documento_tem_teto_de_tempo(tmp_path, monkeypatch):
    monkeypatch.setattr(desenho_svg, "SEGUNDOS_MAX", 2)
    hostil = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="150"><filter id="t"><feTurbulence'
        b' baseFrequency="0.05" numOctaves="100000"/></filter><rect width="300" height="150" filter="url(#t)"/></svg>'
    )
    inicio = time.monotonic()
    r, _ = _ler(_docx_com(tmp_path, "svg", "image/svg+xml", hostil, imagens=4))
    assert time.monotonic() - inicio < 6  # um teto para o documento, não um por imagem
    assert r.markdown.count("SVG não lida]") == 4


def test_svgs_do_documento_tem_teto_de_quantidade(tmp_path):
    r, vistas = _ler(_docx_com(tmp_path, "svg", "image/svg+xml", SVG_AZUL, imagens=DESENHOS_MAX + 3))
    assert r.markdown.count("SVG não lida]") == 3
    assert len(vistas) == 1  # o mesmo desenho em todos os lugares: a IA lê uma vez, e a leitura entra uma vez
    assert r.markdown.count("Lido pela IA.") == 1 and r.markdown.count("mesma imagem lida acima") == DESENHOS_MAX - 1
