"""Documento hostil é recusado antes de ser descompactado; documento real passa."""

import zipfile

import pytest
from docx import Document

import lince
from lince.limites import DocumentoRecusado, conferir_zip


def _docx_real(tmp_path):
    d = Document()
    d.add_paragraph("Relatório trimestral de produção.")
    p = tmp_path / "real.docx"
    d.save(p)
    return p


def _docx_com_parte(tmp_path, nome, conteudo, compressao=zipfile.ZIP_DEFLATED):
    p = tmp_path / "hostil.docx"
    with zipfile.ZipFile(_docx_real(tmp_path)) as origem, zipfile.ZipFile(p, "w", compressao) as z:
        for i in origem.infolist():
            if i.filename != nome:
                z.writestr(i, origem.read(i))
        z.writestr(nome, conteudo)
    return p


def test_documento_real_passa(tmp_path):
    conferir_zip(_docx_real(tmp_path))
    assert "Relatório trimestral" in lince.ler(_docx_real(tmp_path)).markdown


def test_zip_bomb_e_recusado_antes_de_descompactar(tmp_path):
    bomba = _docx_com_parte(tmp_path, "word/media/bomba.bin", b"\0" * (64 * 1024 * 1024))
    with pytest.raises(DocumentoRecusado, match="zip bomb"):
        lince.ler(bomba)


def test_caminho_fora_do_pacote_e_recusado(tmp_path):
    with pytest.raises(DocumentoRecusado, match="fora do pacote"):
        conferir_zip(_docx_com_parte(tmp_path, "../../etc/cron.d/x", b"x"))


def test_partes_demais_sao_recusadas(tmp_path, monkeypatch):
    import lince.limites as limites

    monkeypatch.setattr(limites, "ITENS_MAX", 5)
    with pytest.raises(DocumentoRecusado, match="partes no pacote"):
        conferir_zip(_docx_real(tmp_path))


def test_xml_com_expansao_de_entidades_nao_explode(tmp_path):
    """'Billion laughs' numa parte XML: o expat recusa a expansão em vez de alocar gigabytes."""
    risada = (
        '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaaaaaaaa">'
        + "".join(f'<!ENTITY {chr(98 + k)} "{("&" + chr(97 + k) + ";") * 10}">' for k in range(9))
        + ']><r xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">&j;</r>'
    )
    hostil = _docx_com_parte(tmp_path, "docProps/app.xml", risada.encode())
    from xml.etree import ElementTree as ET

    with zipfile.ZipFile(hostil) as z, pytest.raises(ET.ParseError):
        ET.fromstring(z.read("docProps/app.xml"))
    # e a leitura do documento segue: o corpo sai, a parte hostil só não vira metadado
    r = lince.ler(hostil)
    assert "Relatório trimestral" in r.markdown
    assert r.metadados["partes_ilegiveis"] == ["docProps/app.xml"]  # ficou de fora, mas registrado


def test_parte_grande_demais_e_recusada(tmp_path, monkeypatch):
    import lince.limites as limites

    monkeypatch.setattr(limites, "PARTE_MAX", 1024)
    with pytest.raises(DocumentoRecusado, match="descompacta"):
        conferir_zip(_docx_com_parte(tmp_path, "word/media/grande.bin", b"x" * 2048, zipfile.ZIP_STORED))


def test_pacote_grande_demais_e_recusado(tmp_path, monkeypatch):
    import lince.limites as limites

    monkeypatch.setattr(limites, "DESCOMPACTADO_MAX", 4096)
    with pytest.raises(DocumentoRecusado, match="pacote descompacta"):
        conferir_zip(_docx_com_parte(tmp_path, "word/media/grande.bin", b"x" * 8192, zipfile.ZIP_STORED))


def test_muitas_partes_pequenas_comprimidas_demais_sao_recusadas(tmp_path):
    p = tmp_path / "muitas.docx"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        for k in range(8):  # cada parte abaixo do limite individual, o conjunto passa da razão
            z.writestr(f"word/media/m{k}.bin", b"\0" * (1024 * 1024 - 1))
    with pytest.raises(DocumentoRecusado, match="no conjunto"):
        conferir_zip(p)


def test_grafico_hostil_nao_derruba_a_leitura_do_pptx(tmp_path):
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches

    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "Produção anual"
    dados = CategoryChartData()
    dados.categories = ["2025", "2026"]
    dados.add_series("t", (1, 2))
    s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(2), Inches(6), Inches(4), dados)
    original = tmp_path / "g.pptx"
    prs.save(original)
    hostil = tmp_path / "hostil.pptx"
    with zipfile.ZipFile(original) as o, zipfile.ZipFile(hostil, "w", zipfile.ZIP_DEFLATED) as z:
        for i in o.infolist():
            dados_parte = o.read(i)
            if i.filename.startswith("ppt/charts/chart"):
                dados_parte = b'<?xml version="1.0"?><!DOCTYPE c [<!ENTITY a "x">]><c>&a;<'  # malformado e com DTD
            z.writestr(i, dados_parte)
    assert "Produção anual" in lince.ler(hostil).markdown


def test_corpo_que_nao_abre_nunca_passa_como_sem_texto_oculto(tmp_path):
    from lince.seguranca import XMLIlegivel, texto_oculto_docx

    hostil = _docx_com_parte(tmp_path, "word/document.xml", b"<w:document><nao-fecha>")
    with pytest.raises(XMLIlegivel):  # quem chama trata como suspeita (alerta), não como "nada oculto"
        texto_oculto_docx(hostil)


def test_pagina_gigante_nao_vira_imagem_gigante():
    from lince.limites import LADO_MAX_PX, escala

    assert escala(595, 842, 2.0) == 2.0  # A4 segue igual
    assert 14400 * escala(14400, 14400, 2.0) <= LADO_MAX_PX
