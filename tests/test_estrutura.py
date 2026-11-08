"""Estrutura do documento: abas, slides e seções certas em qualquer produtor e sem custo extra com entrada hostil."""

import io
import struct
import time
import zipfile

from lince.estrutura import SECOES_MAX, _abas_xlsb, _abas_xlsx, _odf, estrutura_arquivo, secoes

_ODS = (
    '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"><office:automatic-styles>'
    '<style:style style:family="table" style:name="ta2"><style:table-properties table:display="false"/>'
    "</style:style>"
    '<style:style style:name="ta1" style:family="table"/>'  # autofechado: não pode "herdar" o estilo seguinte
    "%s</office:automatic-styles><office:body><office:spreadsheet>"
    "<table:table table:style-name='ta1' table:name='P&amp;L &#8211; 2026'/>"  # aspas simples e entidades
    '<table:table table:name="Escondida" table:style-name="ta2"/>'
    "</office:spreadsheet></office:body></office:document-content>"
)


def test_abas_do_ods_pelo_xml_com_entidades_e_qualquer_ordem_de_atributos():
    abas = _odf(io.BytesIO((_ODS % "").encode()), ".ods")["abas"]
    assert abas == [{"nome": "P&L – 2026", "oculta": False}, {"nome": "Escondida", "oculta": True}]


def test_ods_hostil_nao_prende_a_leitura():
    hostil = (_ODS % ('<style:style style:name="x" style:family="table"/>' * 200_000)).encode()
    inicio = time.monotonic()
    assert len(_odf(io.BytesIO(hostil), ".ods")["abas"]) == 2
    assert time.monotonic() - inicio < 3  # linear: o regex anterior era quadrático nesse arquivo


def test_abas_do_xlsx_no_ooxml_strict_e_ocultas():
    strict = (
        b'<workbook xmlns="http://purl.oclc.org/ooxml/spreadsheetml/main"><sheets>'
        b'<sheet name="Resumo" sheetId="1"/><sheet name="Base" sheetId="2" state="veryHidden"/></sheets></workbook>'
    )
    assert _abas_xlsx(strict) == {"abas": [{"nome": "Resumo", "oculta": False}, {"nome": "Base", "oculta": True}]}
    assert _abas_xlsx(b'<workbook xmlns="x"><sheets/></workbook>') == {}  # sem abas: sem campo, não lista vazia


def _bundle(estado, nome):
    rel = "rId1".encode("utf-16-le")
    d = struct.pack("<II", estado, 1) + struct.pack("<I", 4) + rel + struct.pack("<I", len(nome))
    d += nome.encode("utf-16-le")
    return bytes([0x9C, 0x01, len(d)]) + d


def test_abas_do_xlsb_ocultas_e_arquivo_truncado(tmp_path):
    livro = _bundle(0, "Vendas") + _bundle(1, "Apoio")
    assert _abas_xlsb(livro)["abas"] == [{"nome": "Vendas", "oculta": False}, {"nome": "Apoio", "oculta": True}]
    with zipfile.ZipFile(tmp_path / "t.xlsb", "w") as z:
        z.writestr("xl/workbook.bin", livro + b"\x9c\x01\x7f\x00")  # registro que diz 127 bytes e acaba antes
    assert estrutura_arquivo(tmp_path / "t.xlsb", ".xlsb")["abas"][0]["nome"] == "Vendas"
    with zipfile.ZipFile(tmp_path / "u.xlsb", "w") as z:
        z.writestr("xl/workbook.bin", b"\x9c")  # cortado no meio do tipo: sem estrutura, sem erro
    assert estrutura_arquivo(tmp_path / "u.xlsb", ".xlsb") == {}


def test_slides_do_pptx_pela_lista_da_apresentacao(tmp_path):
    with zipfile.ZipFile(tmp_path / "a.pptx", "w") as z:
        z.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:p14="http://schemas.microsoft.com/office/powerpoint/2010/main"><p:sldIdLst>'
            '<p:sldId id="256"/><p:sldId id="257"/></p:sldIdLst><p:extLst><p:ext><p14:sectionLst>'
            '<p14:section name="Abertura"><p14:sldIdLst><p14:sldId id="256"/><p14:sldId id="257"/></p14:sldIdLst>'
            "</p14:section></p14:sectionLst></p:ext></p:extLst></p:presentation>",  # seções repetem os slides
        )
        for n in (1, 2, 3):  # a parte slide3 é órfã: não está na lista e não conta
            z.writestr(f"ppt/slides/slide{n}.xml", "<x/>")
    assert estrutura_arquivo(tmp_path / "a.pptx", ".pptx") == {"slides": 2}


def test_secoes_seguem_as_cercas_do_commonmark():
    md = (
        "# Um\n\n````md\n```\n# dentro da cerca de 4\n```python\n# ainda dentro\n````\n\n"
        "```x``` inline no começo da linha\n\n## Dois\n\n~~~\n# código\n~~~\n### Três\n"
    )
    assert [s["titulo"] for s in secoes(md)["secoes"]] == ["Um", "Dois", "Três"]


def test_marcador_de_pagina_so_no_pdf_e_resiste_a_cerca_aberta():
    md = "<!-- página 1 · nativa -->\nTexto.\n```\ncódigo cortado no teto\n<!-- página 2 · pagina -->\n# Seção 2\n"
    s = secoes(md, pdf=True)
    assert [p["pagina"] for p in s["inicio_das_paginas"]] == [1, 2]  # a cerca aberta não apagou a página 2
    assert [x["titulo"] for x in s["secoes"]] == ["Seção 2"]
    assert "inicio_das_paginas" not in secoes(md + "<!-- página 0 · x -->\n")  # fora do PDF, não há página
    assert "inicio_das_paginas" not in secoes("<!-- página 0 · nativa -->\n", pdf=True)  # página 0 não existe


def test_posicoes_em_pontos_de_codigo_com_crlf_emoji_e_form_feed():
    md = "Relatório 📈\r\n\r\n# Receita\r\n\x0c# não é linha nova no Markdown\r\n## Custo 🚚\r\n"
    s = secoes(md)["secoes"]
    assert [x["titulo"] for x in s] == ["Receita", "Custo 🚚"]
    for x in s:
        assert md[x["inicio"] :].startswith("#" * x["nivel"] + " " + x["titulo"])


def test_secoes_alem_do_teto_sao_marcadas_como_cortadas():
    s = secoes("".join(f"# T{n}\n" for n in range(SECOES_MAX + 5)))
    assert len(s["secoes"]) == SECOES_MAX and s["secoes_cortadas"] is True
    assert "secoes_cortadas" not in secoes("# Só uma\n")


def test_titulo_com_muitos_espacos_nao_prende_a_leitura():
    inicio = time.monotonic()
    s = secoes("# x" + " " * 200_000 + "y\n# Fim   \r\n")["secoes"]
    assert time.monotonic() - inicio < 2  # o regex anterior era quadrático nessa linha
    assert s[0]["titulo"].startswith("x ") and s[0]["titulo"].endswith("y") and s[1]["titulo"] == "Fim"
