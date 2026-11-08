"""Imagem do cabeçalho e do rodapé (logotipo, carimbo) é lida, no Word, no ODF e no RTF, sem repetir a do corpo."""

import io
import re
import time
import zipfile
from pathlib import Path

from docx import Document
from docx.shared import Inches
from PIL import Image

import lince
from lince import legados

REAIS = Path(__file__).parent / "fixtures" / "reais"


def _png(cor, lado=120):
    b = io.BytesIO()
    Image.new("RGB", (lado, lado), cor).save(b, "PNG")
    return b.getvalue()


def _ler(p):
    vistas = []
    r = lince.Leitor(
        ia=lince.FuncaoLeitor(lambda img, instr: vistas.append(img) or "Logotipo lido.", "m"), agrupar=False
    )
    return r.ler(p), vistas


def _cor(img):
    return Image.open(io.BytesIO(img)).convert("RGB").getpixel((5, 5))


def test_logotipo_do_cabecalho_do_word_e_lido(tmp_path):
    d = Document()
    d.sections[0].header.paragraphs[0].add_run().add_picture(io.BytesIO(_png("#2561C1")), width=Inches(1))
    d.sections[0].footer.paragraphs[0].add_run().add_picture(io.BytesIO(_png("#28B877")), width=Inches(1))
    d.add_paragraph("Corpo do relatório.")
    p = tmp_path / "timbrado.docx"
    d.save(p)
    r, vistas = _ler(p)
    assert sorted(_cor(v) for v in vistas) == [(37, 97, 193), (40, 184, 119)]
    assert r.markdown.index("Corpo do relatório.") < r.markdown.index("Imagens do cabeçalho e do rodapé")


def test_imagem_igual_no_corpo_e_no_cabecalho_e_lida_uma_vez(tmp_path):
    d = Document()
    d.sections[0].header.paragraphs[0].add_run().add_picture(io.BytesIO(_png("#2561C1")), width=Inches(1))
    d.add_picture(io.BytesIO(_png("#2561C1")), width=Inches(1))
    p = tmp_path / "repetida.docx"
    d.save(p)
    r, vistas = _ler(p)
    assert len(vistas) == 1 and "Imagens do cabeçalho" not in r.markdown


def test_logotipo_do_cabecalho_do_odf_e_lido():
    r, vistas = _ler(REAIS / "lince_imagem_no_cabecalho.odt")
    assert len(vistas) == 1 and "Imagens do cabeçalho e do rodapé" in r.markdown


def test_logotipo_do_cabecalho_do_rtf_e_lido(tmp_path):
    hexa = _png("#2561C1").hex()
    p = tmp_path / "timbrado.rtf"
    p.write_bytes(
        b"{\\rtf1\\ansi{\\header {\\pict{\\*\\blipuid 0123}\\pngblip\\picw120\\pich120 "
        + hexa.encode()
        + b"}\\par}Corpo do relat\\'f3rio.\\par}"
    )
    r, vistas = _ler(p)
    assert len(vistas) == 1 and _cor(vistas[0]) == (37, 97, 193) and "Corpo do relatório." in r.markdown


def test_rtf_com_pict_quebrado_no_cabecalho_nao_derruba():
    achadas, completo = legados.imagens_rtf(b"{\\rtf1{\\header {\\pict\\pngblip ZZ12}}{\\footer {\\pict\\pngblip 1}")
    assert achadas == [] and completo is False  # rodapé sem fim: leitura parcial, sem erro


def test_logotipo_repetido_na_primeira_pagina_e_lido_uma_vez(tmp_path):
    d = Document()
    secao = d.sections[0]
    secao.different_first_page_header_footer = True
    for cabecalho in (secao.header, secao.first_page_header):
        cabecalho.paragraphs[0].add_run().add_picture(io.BytesIO(_png("#2561C1")), width=Inches(1))
    d.add_paragraph("Corpo.")
    p = tmp_path / "primeira.docx"
    d.save(p)
    with zipfile.ZipFile(p) as z:
        assert len([n for n in z.namelist() if re.fullmatch(r"word/_rels/header\d+\.xml\.rels", n)]) == 2
    r, vistas = _ler(p)
    assert len(vistas) == 1 and r.markdown.count("Logotipo lido.") == 1


def test_logotipo_svg_do_word_vale_pelo_png_de_reserva(tmp_path):
    base = tmp_path / "base.docx"
    d = Document()
    d.sections[0].header.paragraphs[0].add_run().add_picture(io.BytesIO(_png("#2561C1")), width=Inches(1))
    d.save(base)
    p = tmp_path / "svg.docx"
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120"><rect width="120" height="120"/></svg>'
    with zipfile.ZipFile(base) as zi, zipfile.ZipFile(p, "w") as zo:
        for n in zi.namelist():
            conteudo = zi.read(n)
            if n == "word/_rels/header1.xml.rels":
                extra = b'<Relationship Id="rId99" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                extra += b'relationships/image" Target="media/logo.svg"/></Relationships>'
                conteudo = conteudo.replace(b"</Relationships>", extra)
            zo.writestr(n, conteudo)
        zo.writestr("word/media/logo.svg", svg)
    _, vistas = _ler(p)
    assert len(vistas) == 1 and _cor(vistas[0]) == (37, 97, 193)


def test_rtf_no_formato_do_word_le_o_png_e_pula_a_reserva(tmp_path):
    hexa = _png("#2561C1").hex().encode()
    p = tmp_path / "word.rtf"
    p.write_bytes(
        b"{\\rtf1\\ansi{\\header {\\*\\shppict{\\pict{\\*\\picprop\\shplid1025{\\sp{\\sn shapeType}{\\sv 75}}}"
        b"\\picw120\\pich120\\pngblip{\\*\\blipuid 0123abcd}" + hexa + b"}}{\\nonshppict{\\pict\\wmetafile8"
        b"\\picw120\\pich120 010009000003}}\\par}Corpo.\\par}"
    )
    r, vistas = _ler(p)
    assert len(vistas) == 1 and _cor(vistas[0]) == (37, 97, 193)
    assert "WMF" not in r.markdown and not r.metadados.get("partes_ilegiveis")


def test_pict_aninhado_sem_fim_nao_prende_a_leitura():
    hostil = b"{\\rtf1{\\header " + b"{\\pict " * 100_000 + b"}" * 100_000 + b"}}"
    inicio = time.monotonic()
    legados.imagens_rtf(hostil)
    assert time.monotonic() - inicio < 5
