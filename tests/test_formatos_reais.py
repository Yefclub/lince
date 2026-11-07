"""Todo formato aceito, com arquivo gravado por programa de verdade (Word, Excel, PowerPoint, LibreOffice…).

Origem e licença de cada arquivo em tests/fixtures/reais/ORIGEM.md.
"""

import io
import struct
import time
import zipfile
from pathlib import Path

import openpyxl
import pytest
from docx import Document
from pptx import Presentation

import lince
from lince import codigo, legados
from lince.leitor import FORMATOS

REAIS = Path(__file__).parent / "fixtures" / "reais"

# arquivo -> trecho que precisa estar no Markdown (None: imagem, que sem IA fica pendente)
ESPERADO = {
    "testPDF.pdf": "Apache Tika is",
    "testWORD.docx": "Sample Word Document Title",
    "poi_SampleDoc.docx": "I am a test document",
    "testWORD_macros.docm": "The quick brown fox",
    "poi_SimpleMacro.docm": "This is a macro word processing document",
    "testWORD.doc": "Sample Word Document Title",
    "poi_SimpleMacro.doc": "This is a macro word processing document",
    "poi_DiffFirstPageHeadFoot.doc": "HEADING TEXT",
    "poi_HeaderFooterUnicode.doc": "Molière",
    "testOpenOffice2.odt": "sample Open Office document",
    "testFooter.odt": "Here is some text on page 2",
    "testRTF.rtf": "Test d’indexation Word",
    "testRTFVarious.rtf": "Here is a text box",
    "testEPUB.epub": "This is the text for chapter One",
    "testPPT.pptx": "Attachment Test",
    "poi_SampleShow.pptx": "Title of the first slide",
    "testPPT.pptm": "Attachment Test",
    "poi_SimpleMacro.pptm": "This is a macro slideshow",
    "testPPT.ppsx": "Attachment Test",
    "testPPT.ppsm": "Attachment Test",
    "testPPT.ppt": "Sample Powerpoint Slide",
    "poi_SampleShow.ppt": "Title of the first slide",
    "poi_SimpleMacro.ppt": "This is a macro slideshow",
    "poi_headers_footers.ppt": None,  # só tem cabeçalho e rodapé: vão para os metadados
    "testPPT.pps": "Sample Powerpoint Slide",
    "testPPT.pot": "Sample Powerpoint Slide",
    "testMasterFooter.odp": None,  # só tem texto no rodapé da página mestra: vai para os metadados
    "testEXCEL.xlsx": "Sample Excel Worksheet",
    "poi_SampleSS.xlsx": "Test spreadsheet",
    "testEXCEL_macro.xlsm": "However, a macro is",
    "poi_SimpleMacro.xlsm": "This is a macro workbook",
    "poi_sample.xlsb": "| Lorem | 111 |",
    "poi_testVarious.xlsb": "This is a string",
    "testEXCEL.xls": "Sample Excel Worksheet",
    "poi_SimpleMacro.xls": "This is a macro workbook",
    "poi_EmbeddedChartHeaderTest.xls": "| Data 1 | Data 2 | Data 3 |",
    "poi_47251_1.xls": "AAAAAAAAAA AAAAAAA AAAAA AAAAAAAA",  # o POI já publica o conteúdo trocado por "A"
    "poi_TwoSheetsOneHidden.xls": "Sheet2A1",
    "poi_45761.xls": "| Visible |",
    "poi_60405.xls": "NullPointerException during build",
    "testFooter.ods": "| Data 3 | Data 4 |",
    "testCSV.csv": "Numbers and their Squares",
    "texto.tsv": "| soja | 3500 |",
    "texto.txt": "Relatório em texto puro.",
    "texto.md": "Parágrafo em Markdown.",
    "texto.json": '"toneladas": 3500',
    "texto.yaml": "produto: soja",
    "texto.yml": "produto: soja",
    "texto.xml": "<produto>soja</produto>",
    "texto.tex": "\\section{Resumo}",
    "testPNG.png": None,
    "testJPEG.jpg": None,
    "testJPEG.jpeg": None,
    "testWEBP.webp": None,
    "testTIFF.tif": None,
    "testTIFF.tiff": None,
    "testBMP.bmp": None,
    "testGIF.gif": None,
}
IMAGENS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif"}


def test_todo_formato_aceito_tem_arquivo_real():
    cobertos = {Path(n).suffix.lower() for n in ESPERADO}
    assert cobertos == set(FORMATOS)  # formato novo na lista exige arquivo novo aqui


@pytest.mark.parametrize("nome", sorted(ESPERADO))
def test_leitura_de_arquivo_real(nome):
    r = lince.ler(REAIS / nome)
    trecho = ESPERADO[nome]
    if trecho is not None:
        assert trecho in r.markdown
    elif Path(nome).suffix.lower() in IMAGENS:
        assert r.cobertura.pendentes  # sem modelo de visão, a imagem fica pendente e registrada, nunca some
    assert "\n\\\n" not in "\n" + r.markdown  # parágrafo vazio não vira "\" solto


@pytest.mark.parametrize(
    ("nome", "texto"),
    [
        ("testFooter.odt", "Here is footer text"),
        ("testFooter.ods", "Here is a footer in the center area"),
        ("testMasterFooter.odp", "Master footer is here"),
    ],
)
def test_cabecalho_e_rodape_do_odf_vao_para_os_metadados(nome, texto):
    assert texto in lince.ler(REAIS / nome).metadados["cabeçalho/rodapé"]


def test_cabecalho_e_rodape_do_word_e_do_excel_vao_para_os_metadados(tmp_path):
    d = Document()
    d.sections[0].header.paragraphs[0].text = "Confidencial"
    d.sections[0].footer.paragraphs[0].text = "Versão 3"
    d.add_paragraph("Corpo do texto.")
    d.save(tmp_path / "c.docx")
    r = lince.ler(tmp_path / "c.docx")
    assert r.metadados["cabeçalho/rodapé"] == ["Confidencial", "Versão 3"] and "Confidencial" not in r.markdown
    wb = openpyxl.Workbook()
    wb.active["A1"] = "dado"
    wb.active.oddFooter.center.text = "Página &P de &N"
    wb.save(tmp_path / "p.xlsx")
    assert lince.ler(tmp_path / "p.xlsx").metadados["cabeçalho/rodapé"] == ["Página [página] de [páginas]"]


@pytest.mark.parametrize(
    ("nome", "texto"),
    [
        ("testWORD.doc", "This is the header for our document"),
        ("poi_DiffFirstPageHeadFoot.doc", "I am the header on the first page, and I’m nice and simple"),
        ("poi_HeaderFooterUnicode.doc", "This is a simple header, with a € euro symbol in it."),
        ("poi_EmbeddedChartHeaderTest.xls", "Top Left Top Center Top Right"),
        ("poi_47251_1.xls", "[página] of [páginas]"),
        ("poi_headers_footers.ppt", "Global Slide Footer"),
        ("testRTFVarious.rtf", "This is the header text."),
    ],
)
def test_cabecalho_e_rodape_dos_binarios_e_do_rtf_vao_para_os_metadados(nome, texto):
    r = lince.ler(REAIS / nome)
    assert texto in r.metadados["cabeçalho/rodapé"] and texto not in r.markdown


def test_aba_oculta_e_muito_oculta_do_xls_sao_sinalizadas():
    assert lince.ler(REAIS / "poi_TwoSheetsOneHidden.xls").metadados["abas_ocultas"] == ["Sheet1"]
    # "muito oculta" (hsState 2): a interface do Excel não reexibe, só macro
    assert lince.ler(REAIS / "poi_45761.xls").metadados["abas_ocultas"] == ["HiddenSheet", "VeryHiddenSheet"]


@pytest.mark.parametrize(
    "nome",
    [
        "testWORD_macros.docm",
        "poi_SimpleMacro.xlsm",
        "poi_SimpleMacro.pptm",
        "poi_SimpleMacro.doc",
        "poi_SimpleMacro.xls",
        "poi_SimpleMacro.ppt",  # VBA dentro de um objeto comprimido: só o VBAInfoAtom denuncia
        "poi_60405.xls",  # folha de macro do Excel 4.0, sem VBA nenhum
    ],
)
def test_macro_e_sinalizada_na_leitura(nome):
    assert lince.ler(REAIS / nome).metadados["macros"] is True


@pytest.mark.parametrize(
    "nome",
    # o testPPT.ppt tem o registro de VBA com "sem macros": a presença do registro não basta
    ["testWORD.docx", "testWORD.doc", "testEXCEL.xls", "testOpenOffice2.odt", "testPPT.ppt", "poi_SampleShow.ppt"],
)
def test_arquivo_sem_macro_nao_e_sinalizado(nome):
    assert "macros" not in lince.ler(REAIS / nome).metadados


@pytest.mark.parametrize("nome", ["poi_60405.xls", "poi_SimpleMacro.ppt"])
def test_macro_sem_vba_project_visivel_vem_do_caminho_novo(nome):
    # nem folha de macro do Excel 4.0 nem o VBA comprimido do PowerPoint deixam _VBA_PROJECT à vista
    assert "_VBA_PROJECT".encode("utf-16-le") not in (REAIS / nome).read_bytes()


def test_campo_do_word_vira_o_resultado_e_pagina_vira_nome():
    from lince.legados import _sem_campos

    assert _sem_campos("Página \x13 PAGE \x14 3\x15 de \x13 NUMPAGES \x145\x15") == "Página [página] de [páginas]"
    assert _sem_campos('\x13 HYPERLINK "https://exemplo.com" \x14exemplo.com\x15') == "exemplo.com"
    aninhado = '\x13 IF \x13 PAGE \x141\x15 = 1 "a" \x14primeira\x15 fim'
    assert _sem_campos(aninhado) == "primeira fim"


def test_rtf_usa_a_pagina_de_codigo_da_fonte_e_ignora_grupo_sem_fim(tmp_path):
    # fonte 1 em Shift-JIS (\fcharset128): \'82\'a0 é "あ"; o cabeçalho é lido como o corpo, pela fonte
    rtf = (
        rb"{\rtf1\ansi\deff0{\fonttbl{\f0\fcharset0 Arial;}{\f1\fcharset128 MS Gothic;}}"
        rb"{\header\pard\f1 \'82\'a0\par}{\footerr\pard Rodap\'e9\par}\pard Corpo\par}"
    )
    (tmp_path / "jp.rtf").write_bytes(rtf)
    r = lince.ler(tmp_path / "jp.rtf")
    assert r.metadados["cabeçalho/rodapé"] == ["Rodapé", "あ"] and "Corpo" in r.markdown
    from lince.metadados import metadados_arquivo

    (tmp_path / "aberto.rtf").write_bytes(rb"{\rtf1{\header abc")  # grupo que nunca fecha
    assert metadados_arquivo(tmp_path / "aberto.rtf") == {}


def test_ole_malformado_nao_derruba_os_metadados(tmp_path):
    from lince.metadados import metadados_arquivo

    cortado = tmp_path / "cortado.doc"
    cortado.write_bytes((REAIS / "testWORD.doc").read_bytes()[:4096])  # cabeçalho OLE válido, setores faltando
    assert metadados_arquivo(cortado).get("partes_ilegiveis") == ["cabeçalho, rodapé e macros"]


def test_macro_do_odf_e_do_excel_4_sao_sinalizadas(tmp_path):
    for origem, parte in (
        ("testOpenOffice2.odt", "Basic/Standard/Module1.xml"),
        ("testEXCEL.xlsx", "xl/macrosheets/sheet1.xml"),
    ):
        destino = tmp_path / origem
        with zipfile.ZipFile(REAIS / origem) as o, zipfile.ZipFile(destino, "w") as z:
            for i in o.infolist():
                z.writestr(i, o.read(i))
            z.writestr(parte, "<x/>")
        assert lince.ler(destino).metadados["macros"] is True


def test_cabecalho_do_excel_sem_codigos_e_word_com_tab(tmp_path):
    from lince.metadados import _sem_codigos_excel

    assert _sem_codigos_excel('&L&"Arial,Bold"&KFF0000Confidencial&C&K01+000&OPágina &P&R&H&D') == (
        "Confidencial Página [página] [data]"
    )
    d = Document()
    d.sections[0].header.paragraphs[0].add_run("Confidencial")
    d.sections[0].header.paragraphs[0].add_run().add_tab()
    d.sections[0].header.paragraphs[0].add_run("Versão 3")
    d.add_paragraph("Corpo")
    d.save(tmp_path / "t.docx")
    assert lince.ler(tmp_path / "t.docx").metadados["cabeçalho/rodapé"] == ["Confidencial Versão 3"]


def test_xlsb_que_o_leitor_nao_entende_falha_com_erro_claro():
    import anydoc

    with pytest.raises(anydoc.ConvertError):  # erro do documento, nunca resultado vazio ou parcial calado
        lince.ler(REAIS / "testEXCEL.xlsb")


def test_acabamento_limpa_o_texto_e_preserva_bloco_de_codigo():
    from lince.montagem import acabamento

    codigo_py = "```python\ndef a():\n    pass\n\n\n\\\ndef b():\n    pass\n```"
    saida = acabamento("Texto--solto\n\n\\\n\n\n\nFim\n\n" + codigo_py + "\n")
    assert saida.startswith("Texto—solto\n\nFim\n\n")  # fora do bloco: limpo
    assert codigo_py in saida  # dentro: intacto (duas linhas em branco e a barra continuam)


EDITAVEIS = [n for n in ESPERADO if Path(n).suffix.lower() in codigo.EDITAVEIS]


@pytest.mark.parametrize("nome", EDITAVEIS)
def test_codigo_e_ida_e_volta_de_arquivo_do_office(nome):
    dados = (REAIS / nome).read_bytes()
    c = codigo.extrair(dados, nome)
    assert c["sha256"]
    modulo = codigo.EDITAVEIS[Path(nome).suffix.lower()]
    novo, _, _ = modulo.aplicar(dados, [])
    assert codigo.comparar(dados, novo)["partes_alteradas"] == []


def _vba(dados: bytes) -> bytes | None:
    with zipfile.ZipFile(io.BytesIO(dados)) as z:
        nome = next((n for n in z.namelist() if n.lower().endswith("vbaproject.bin")), None)
        return z.read(nome) if nome else None


def test_edicao_no_word_real_preserva_macro_e_reabre():
    for nome in ("poi_SampleDoc.docx", "testWORD_macros.docm"):
        dados = (REAIS / nome).read_bytes()
        c = codigo.extrair(dados, nome)
        alvo = next(b for b in c["blocos"] if b["tipo"] == "paragrafo" and b["texto"].strip())
        palavra = alvo["texto"].split()[0]
        novo, rel = codigo.editar(
            dados, nome, [{"op": "substituir", "alvo": alvo["id"], "buscar": palavra, "trocar": palavra.upper()}]
        )
        if nome.endswith(".docx"):  # o python-docx recusa .docm pelo content type de macro
            Document(io.BytesIO(novo))
        depois = next(b for b in codigo.extrair(novo, nome)["blocos"] if b.get("id") == alvo["id"])
        assert rel["aplicadas"][0]["trocas"] >= 1 and depois["texto"].startswith(palavra.upper())
        assert _vba(novo) == _vba(dados)  # a macro sai idêntica (ou continua ausente)
        assert codigo.docx.integridade(novo) == []


def test_edicao_no_excel_real_preserva_macro_e_reabre():
    for nome in ("poi_SampleSS.xlsx", "testEXCEL.xlsx", "poi_SimpleMacro.xlsm"):
        dados = (REAIS / nome).read_bytes()
        aba = codigo.extrair(dados, nome)["abas"][0]["id"]
        novo, rel = codigo.editar(
            dados,
            nome,
            [
                {"op": "celula", "alvo": f"{aba}!Z50", "valor": 42},
                {"op": "celula", "alvo": f"{aba}!Z51", "formula": "=Z50*2"},
                {"op": "renomear_aba", "aba": aba, "nome": "Renomeada"},
            ],
        )
        wb = openpyxl.load_workbook(io.BytesIO(novo))
        assert wb["Renomeada"]["Z50"].value == 42 and wb["Renomeada"]["Z51"].value == "=Z50*2"
        assert _vba(novo) == _vba(dados)
        assert codigo.xlsx.integridade(novo) == []


def test_edicao_no_powerpoint_real_preserva_macro_e_reabre():
    for nome in ("poi_SampleShow.pptx", "testPPT.pptx", "poi_SimpleMacro.pptm"):
        dados = (REAIS / nome).read_bytes()
        c = codigo.extrair(dados, nome)
        forma = next(f for s in c["slides"] for f in s["formas"] if f.get("paragrafos"))
        novo, rel = codigo.editar(dados, nome, [{"op": "texto", "alvo": forma["id"], "texto": "Título novo"}])
        Presentation(io.BytesIO(novo))
        depois = next(f for s in rel["codigo"]["slides"] for f in s["formas"] if f["id"] == forma["id"])
        assert [p["texto"] for p in depois["paragrafos"]] == ["Título novo"]
        assert _vba(novo) == _vba(dados)
        assert codigo.pptx.integridade(novo) == []


def _com_parte(origem: Path, destino: Path, parte: str, conteudo: bytes, trocar: bool = False):
    with zipfile.ZipFile(origem) as o, zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        for i in o.infolist():
            if not (trocar and i.filename == parte):
                z.writestr(i, o.read(i))
        z.writestr(parte, conteudo)
    return destino


def test_cabecalho_vazio_do_excel_nao_engole_o_rodape(tmp_path):
    with zipfile.ZipFile(REAIS / "testEXCEL.xlsx") as z:
        xml = z.read("xl/worksheets/sheet1.xml")
    xml = xml.replace(
        b"</worksheet>",
        b"<headerFooter><oddHeader/><oddFooter>&amp;CP&#225;gina &amp;P</oddFooter></headerFooter></worksheet>",
    )
    arquivo = _com_parte(REAIS / "testEXCEL.xlsx", tmp_path / "p.xlsx", "xl/worksheets/sheet1.xml", xml, True)
    assert lince.ler(arquivo).metadados["cabeçalho/rodapé"] == ["Página [página]"]


def test_caixa_de_texto_no_cabecalho_do_word_sai_uma_vez(tmp_path):
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    mc = "http://schemas.openxmlformats.org/markup-compatibility/2006"
    caixa = (
        f'<w:hdr xmlns:w="{w}" xmlns:mc="{mc}"><w:p><w:r><w:t>Topo</w:t></w:r><w:r><mc:AlternateContent>'
        "<mc:Choice><w:txbxContent><w:p><w:r><w:t>Confidencial</w:t></w:r></w:p></w:txbxContent></mc:Choice>"
        "<mc:Fallback><w:txbxContent><w:p><w:r><w:t>Confidencial</w:t></w:r></w:p></w:txbxContent></mc:Fallback>"
        "</mc:AlternateContent></w:r></w:p></w:hdr>"
    ).encode()
    arquivo = _com_parte(REAIS / "testWORD.docx", tmp_path / "c.docx", "word/header9.xml", caixa)
    bordas = lince.ler(arquivo).metadados["cabeçalho/rodapé"]
    assert "Confidencial" in bordas and "Topo" in bordas  # nem colado ("TopoConfidencial")
    from xml.etree import ElementTree as ET

    from lince.metadados import _paragrafos_word

    # antes da limpeza por conjunto: o mc:Fallback (cópia do mc:Choice) não entra
    assert _paragrafos_word(ET.fromstring(caixa), f"{{{w}}}") == ["Topo", "Confidencial"]


def test_xml_hostil_profundo_no_odf_nao_derruba_a_leitura(tmp_path):
    with zipfile.ZipFile(REAIS / "testFooter.odt") as z:
        estilos = z.read("styles.xml")
    fundo = b"<text:span>" * 5000 + b"fundo" + b"</text:span>" * 5000
    estilos = estilos.replace(b"Here is footer text", b"Here is footer text" + fundo, 1)
    arquivo = _com_parte(REAIS / "testFooter.odt", tmp_path / "f.odt", "styles.xml", estilos, True)
    from lince.metadados import metadados_arquivo  # o leitor do corpo já recusa; os metadados rodam à parte

    assert any("fundo" in t for t in metadados_arquivo(str(arquivo))["cabeçalho/rodapé"])


def test_macro_no_xlsb_e_parte_corrompida(tmp_path):
    xlsb = _com_parte(REAIS / "poi_sample.xlsb", tmp_path / "m.xlsb", "xl/vbaProject.bin", b"\x00" * 64)
    assert lince.ler(xlsb).metadados["macros"] is True
    corrompido = tmp_path / "c.docx"
    corrompido.write_bytes((REAIS / "testWORD.docx").read_bytes())
    with zipfile.ZipFile(corrompido) as z:
        info = z.getinfo("docProps/core.xml")
    dados = bytearray(corrompido.read_bytes())
    inicio = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    dados[inicio : inicio + 8] = b"\xff" * 8  # stream deflate inválido: zlib.error ao ler
    corrompido.write_bytes(bytes(dados))
    assert "docProps/core.xml" in lince.ler(corrompido).metadados["partes_ilegiveis"]


class _OleFalso:
    """Só o que os leitores de legados usam do olefile: streams sintéticos para estrutura hostil ou rara."""

    def __init__(self, **streams):
        self.streams = {k.replace("_", " "): v for k, v in streams.items()}

    def exists(self, nome):
        return nome in self.streams

    def get_size(self, nome):
        return len(self.streams[nome])

    def openstream(self, nome):
        return io.BytesIO(self.streams[nome])


def _reg(tipo, dados=b""):
    return struct.pack("<HH", tipo, len(dados)) + dados


def _bof(vers):
    return _reg(0x0809, struct.pack("<HH", vers, 0x10) + bytes(12))


def test_xls_biff5_usa_o_codepage_e_le_aba_oculta():
    cabecalho = "&CСекрет".encode("cp1251")  # BIFF5: cch de 1 byte e bytes na página de código do CODEPAGE
    aba = _bof(0x0500) + _reg(0x14, bytes([len(cabecalho)]) + cabecalho) + _reg(0x0200, bytes(10))
    nome = "Лист".encode("cp1251")
    globais_sem_aba = _bof(0x0500) + _reg(0x42, struct.pack("<H", 1251))
    tam_aba = len(_reg(0x85, bytes(6) + bytes([len(nome)]) + nome))
    pos = len(globais_sem_aba) + tam_aba + len(_reg(0x0A))
    globais = globais_sem_aba + _reg(0x85, struct.pack("<IBB", pos, 1, 0) + bytes([len(nome)]) + nome) + _reg(0x0A)
    r = legados.xls(_OleFalso(Book=globais + aba), 10**6)
    assert r["ocultas"] == ["Лист"] and r["bordas"] == ["&CСекрет"]


def test_xls_le_o_cabecalho_da_primeira_pagina_do_excel_2007_e_resiste_a_aba_repetida():
    texto = "Confidencial"
    hf = bytes(30) + struct.pack("<4H", 0, 0, len(texto), 0) + struct.pack("<HB", len(texto), 0) + texto.encode()
    aba = _bof(0x0600) + _reg(0x089C, hf) + _reg(0x14, struct.pack("<HB", 2, 0) + b"oi")
    pos = len(_bof(0x0600)) + 5000 * len(_reg(0x85, bytes(8))) + len(_reg(0x0A))
    globais = _bof(0x0600) + _reg(0x85, struct.pack("<IBBBB", pos, 0, 0, 0, 0)) * 5000 + _reg(0x0A)
    inicio = time.monotonic()
    r = legados.xls(_OleFalso(Workbook=globais + aba + _reg(0x0200, bytes(14))), 10**8)
    assert r["bordas"] == ["Confidencial", "oi"] and time.monotonic() - inicio < 2  # 5000 abas no mesmo lugar: uma


def _rec(ver, tipo, corpo):
    return struct.pack("<HHI", ver, tipo, len(corpo)) + corpo


def _documento_ppt(tem_macro):
    atomo = _rec(0x2, 0x0400, struct.pack("<III", 3, tem_macro, 2))
    return _rec(0xF, 0x03E8, _rec(0xF, 0x03FF, atomo))


def test_ppt_ignora_a_versao_antiga_deixada_pelo_salvamento_rapido():
    antigo, atual = _documento_ppt(1), _documento_ppt(0)  # a macro foi apagada no último salvamento
    diretorio_em = len(antigo) + len(atual)
    diretorio = _rec(0x0, 0x1772, struct.pack("<II", 1 | (1 << 20), len(antigo)))
    edicao_em = diretorio_em + len(diretorio)
    edicao = _rec(0x0, 0x0FF5, struct.pack("<IHBBIII", 0, 0, 0, 3, 0, diretorio_em, 1) + bytes(8))
    stream = antigo + atual + diretorio + edicao
    usuario = _rec(0x0, 0x0FF6, struct.pack("<III", 20, 0xE391C05F, edicao_em) + bytes(8))
    assert legados.ppt(_OleFalso(PowerPoint_Document=stream, Current_User=usuario), 10**6)["macro"] is False
    # sem a cadeia de edições, a varredura do stream inteiro acharia a macro antiga: o teste prova a diferença
    assert legados.ppt(_OleFalso(PowerPoint_Document=stream), 10**6)["macro"] is True


def test_piece_table_hostil_nao_multiplica_o_texto():
    def documento(cps, fc):
        plc = struct.pack(f"<{len(cps)}I", *cps) + b"".join(struct.pack("<HIH", 0, fc, 0) for _ in cps[1:])
        clx = b"\x02" + struct.pack("<I", len(plc)) + plc
        wd = bytearray(struct.pack("<II", 0, len(clx)).rjust(33 * 8 + 8, b"\0") + b"a" * 10_000)
        return bytes(wd), clx

    wd, clx = documento([0, 2**31, 0, 2**31], 0x40000000)  # posições fora de ordem: recusa
    assert legados._texto_por_cp(wd, clx, 0, 0, 2**31) == ""
    wd, clx = documento([0, 2**31 - 1], 0x40000000)  # uma peça que diz ter 2 bilhões de caracteres
    assert len(legados._texto_por_cp(wd, clx, 0, 0, 2**31 - 1)) <= len(wd)


def test_campos_do_word_tem_custo_linear_e_aninhamento_com_teto():
    inicio = time.monotonic()
    assert legados._sem_campos("\x13 X \x14r\x15" * 100_000 + "a" * 100_000).count("r") == 100_000
    assert time.monotonic() - inicio < 2
    with pytest.raises(ValueError):  # vira partes_ilegiveis em metadados_arquivo
        legados._sem_campos("\x13\x14" * 50_000 + "a" * 50_000)


def test_stream_ole_que_declara_mais_bytes_que_o_arquivo_e_recusado(tmp_path):
    from lince.metadados import metadados_arquivo

    dados = bytearray((REAIS / "testWORD.doc").read_bytes())
    entrada = dados.index("WordDocument".encode("utf-16-le"))
    struct.pack_into("<I", dados, entrada + 0x78, 0x7FFFFFFF)  # tamanho do stream na entrada do diretório
    (tmp_path / "gigante.doc").write_bytes(bytes(dados))
    assert metadados_arquivo(tmp_path / "gigante.doc").get("partes_ilegiveis") == ["cabeçalho, rodapé e macros"]
