"""Todo formato aceito, com arquivo gravado por programa de verdade (Word, Excel, PowerPoint, LibreOffice…).

Origem e licença de cada arquivo em tests/fixtures/reais/ORIGEM.md.
"""

import io
import zipfile
from pathlib import Path

import openpyxl
import pytest
from docx import Document
from pptx import Presentation

import lince
from lince import codigo
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
    "testOpenOffice2.odt": "sample Open Office document",
    "testFooter.odt": "Here is some text on page 2",
    "testRTF.rtf": "Test d’indexation Word",
    "testEPUB.epub": "This is the text for chapter One",
    "testPPT.pptx": "Attachment Test",
    "poi_SampleShow.pptx": "Title of the first slide",
    "testPPT.pptm": "Attachment Test",
    "poi_SimpleMacro.pptm": "This is a macro slideshow",
    "testPPT.ppsx": "Attachment Test",
    "testPPT.ppsm": "Attachment Test",
    "testPPT.ppt": "Sample Powerpoint Slide",
    "poi_SampleShow.ppt": "Title of the first slide",
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
    "nome",
    [
        "testWORD_macros.docm",
        "poi_SimpleMacro.xlsm",
        "poi_SimpleMacro.pptm",
        "poi_SimpleMacro.doc",
        "poi_SimpleMacro.xls",
    ],
)
def test_macro_e_sinalizada_na_leitura(nome):
    assert lince.ler(REAIS / nome).metadados["macros"] is True


@pytest.mark.parametrize("nome", ["testWORD.docx", "testWORD.doc", "testEXCEL.xls", "testOpenOffice2.odt"])
def test_arquivo_sem_macro_nao_e_sinalizado(nome):
    assert "macros" not in lince.ler(REAIS / nome).metadados


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
    aba = (REAIS / "testEXCEL.xlsx").open("rb")
    with zipfile.ZipFile(aba) as z:
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
    assert bordas.count("Confidencial") == 1 and "Topo" in bordas  # nem colado ("TopoConfidencial") nem repetido


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
