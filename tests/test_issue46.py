"""PDF com imagem fora da área visível e pacote Office com `\\` nos nomes internos (issue #46)."""

import io
import zipfile
import zlib

import pytest

import lince
from lince.limites import DocumentoRecusado

TEXTO = "BT /F1 12 Tf 72 700 Td (Texto nativo visivel: codigo ABC-123 e valor 4.567) Tj ET "


def _pdf(p, mediabox, conteudo, cropbox=None, img_w=200, img_h=150):
    """PDF de uma página com uma imagem RGB (o roteiro da issue)."""
    raw = bytearray()
    for y in range(img_h):
        raw += b"\x00"
        for x in range(img_w):
            raw += bytes(((x * 7) % 256, (y * 5) % 256, ((x + y) * 3) % 256))
    img = zlib.compress(bytes(raw))
    cb = (b" /CropBox [" + b" ".join(str(v).encode() for v in cropbox) + b"]") if cropbox else b""
    c = conteudo.encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [" + b" ".join(str(v).encode() for v in mediabox) + b"]" + cb
        + b" /Resources << /Font << /F1 5 0 R >> /XObject << /Im1 6 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(c)).encode() + b" >>\nstream\n" + c + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB /BitsPerComponent 8"
        b" /Filter /FlateDecode /DecodeParms << /Predictor 15 /Colors 3 /Columns %d >> /Length %d >>\nstream\n"
        % (img_w, img_h, img_w, len(img)) + img + b"\nendstream",
    ]  # fmt: skip
    b = bytearray(b"%PDF-1.7\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(b))
        b += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(b)
    b += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        b += b"%010d 00000 n \n" % o
    b += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    p.write_bytes(bytes(b))
    return p


def _tarefas(r):
    return [p for p in r.cobertura.pendentes if p.endswith("sem modelo de visão configurado")]


@pytest.mark.parametrize(
    ("mediabox", "conteudo", "texto"),
    [
        ([0, 0, 612, 792], TEXTO + "q 200 0 0 150 700 300 cm /Im1 Do Q", "ABC-123"),  # toda à direita
        ([0, 0, 612, 792], TEXTO + "q 200 0 0 150 100 -400 cm /Im1 Do Q", "ABC-123"),  # toda abaixo
    ],
    ids=["fora-a-direita", "fora-abaixo"],
)
def test_imagem_toda_fora_da_pagina_nao_vira_tarefa_nem_derruba(tmp_path, mediabox, conteudo, texto):
    r = lince.Leitor().ler(_pdf(tmp_path / "f.pdf", mediabox, conteudo))
    assert texto in r.markdown and not _tarefas(r)  # o texto sai e a imagem que não aparece não vai para a IA


def test_mediabox_com_origem_negativa_le_a_imagem_no_lugar_certo(tmp_path):
    conteudo = "BT /F1 12 Tf -540 -92 Td (Texto nativo visivel) Tj ET q 200 0 0 150 -500 -500 cm /Im1 Do Q"
    r = lince.Leitor().ler(_pdf(tmp_path / "f.pdf", [-612, -792, 0, 0], conteudo))
    assert "Texto nativo visivel" in r.markdown and len(_tarefas(r)) == 1  # a imagem, dentro da página, é lida


def test_imagem_dentro_e_cortada_pela_borda_e_cropbox_deslocado(tmp_path):
    dentro = lince.Leitor().ler(
        _pdf(tmp_path / "d.pdf", [0, 0, 612, 792], TEXTO + "q 200 0 0 150 100 300 cm /Im1 Do Q")
    )
    assert "ABC-123" in dentro.markdown and len(_tarefas(dentro)) == 1
    from PIL import Image

    def largura(arquivo, x):  # a largura do recorte da imagem posta em x
        pasta = tmp_path / arquivo
        (t,) = lince.Leitor().planejar(
            _pdf(tmp_path / f"{arquivo}.pdf", [0, 0, 612, 792], TEXTO + f"q 200 0 0 150 {x} 300 cm /Im1 Do Q"), pasta
        )["tarefas"]
        with Image.open(pasta / "ia" / f"{t['id']}.png") as im:
            return im.width

    assert largura("cortada", 512) < 0.7 * largura("inteira", 100)  # metade passa da borda: só a parte visível
    deslocado = _pdf(tmp_path / "x.pdf", [0, 0, 1224, 792],
                     "BT /F1 12 Tf 700 700 Td (Texto nativo visivel) Tj ET q 200 0 0 150 100 300 cm /Im1 Do Q",
                     cropbox=[612, 0, 1224, 792])  # fmt: skip
    r = lince.Leitor().ler(deslocado)
    assert "Texto nativo visivel" in r.markdown and not _tarefas(r)  # a imagem está na parte cortada


def test_erro_numa_pagina_vira_pendencia_e_as_outras_seguem(tmp_path, monkeypatch):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    from lince import pdf as pdf_mod

    p = tmp_path / "duas.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    for k in (1, 2):
        c.drawString(60, 780, f"Texto da página {k}.")
        c.rect(60, 400, 300, 200)
        c.showPage()
    c.save()
    original = pdf_mod._pagina

    def falha_na_primeira(doc, i, *a, **k):
        if i == 0:
            raise ValueError("Coordinate 'right' is less than 'left'")
        return original(doc, i, *a, **k)

    monkeypatch.setattr(pdf_mod, "_pagina", falha_na_primeira)
    r = lince.Leitor().ler(p)
    assert "Texto da página 1." in r.markdown and "Texto da página 2." in r.markdown
    assert any(x.startswith("página 1: só o texto") and "ValueError" in x for x in r.cobertura.pendentes)
    assert not any(x.startswith("página 2") for x in r.cobertura.pendentes if "só o texto" in x)


def _com_barras(origem: bytes, destino):
    zin = zipfile.ZipFile(io.BytesIO(origem))
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as zo:
        for it in zin.infolist():
            zo.writestr(it.filename.replace("/", "\\"), zin.read(it.filename))
    return destino


def _xlsx():
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Cadastro"
    ws.append(["Codigo", "Descricao", "Valor"])
    for i in range(1, 21):
        ws.append([f"X-{i:03d}", f"Item {i}", i * 10.5])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _docx():
    from docx import Document

    d = Document()
    d.add_paragraph("Contrato de fornecimento: cláusula de entrega.")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _pptx():
    from pptx import Presentation
    from pptx.util import Inches

    p = Presentation()
    s = p.slides.add_slide(p.slide_layouts[6])
    s.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(1)).text_frame.text = "Moagem por unidade em 2026"
    buf = io.BytesIO()
    p.save(buf)
    return buf.getvalue()


@pytest.mark.parametrize(
    ("ext", "gerar", "texto"),
    [(".xlsx", _xlsx, "X-020"), (".docx", _docx, "cláusula de entrega"), (".pptx", _pptx, "Moagem por unidade")],
)
def test_pacote_com_barra_invertida_nos_nomes_le_igual_ao_normal(tmp_path, ext, gerar, texto):
    dados = gerar()
    normal = tmp_path / f"normal{ext}"
    normal.write_bytes(dados)
    invertido = _com_barras(dados, tmp_path / f"invertido{ext}")
    assert any("\\" in n for n in zipfile.ZipFile(invertido).namelist())
    a, b = lince.ler(normal), lince.ler(invertido)
    assert texto in b.markdown and b.markdown == a.markdown


def test_barra_invertida_nao_abre_caminho_fora_nem_nome_repetido(tmp_path):
    fora = tmp_path / "fora.xlsx"
    with zipfile.ZipFile(fora, "w") as z:
        z.writestr("xl\\..\\..\\segredo.xml", "<a/>")
    with pytest.raises(DocumentoRecusado, match="fora do pacote"):
        lince.ler(fora)
    absoluto = tmp_path / "absoluto.xlsx"
    with zipfile.ZipFile(absoluto, "w") as z:
        z.writestr("\\xl\\workbook.xml", "<a/>")
    with pytest.raises(DocumentoRecusado, match="fora do pacote"):
        lince.ler(absoluto)
    repetido = tmp_path / "repetido.xlsx"
    dados = _xlsx()
    with zipfile.ZipFile(io.BytesIO(dados)) as zin, zipfile.ZipFile(repetido, "w") as zo:
        for it in zin.infolist():
            zo.writestr(it.filename.replace("/", "\\"), zin.read(it.filename))
        zo.writestr("xl/workbook.xml", "<a/>")  # o mesmo nome, com / e com \
    with pytest.raises(DocumentoRecusado, match="repetida"):
        lince.ler(repetido)
