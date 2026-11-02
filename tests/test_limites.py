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
