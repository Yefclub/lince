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


@pytest.mark.parametrize("nome", ["testWORD_macros.docm", "poi_SimpleMacro.xlsm", "poi_SimpleMacro.pptm"])
def test_macro_e_sinalizada_na_leitura(nome):
    assert lince.ler(REAIS / nome).metadados["macros"] is True


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
        assert _vba(novo) == _vba(dados)
        assert codigo.pptx.integridade(novo) == []
