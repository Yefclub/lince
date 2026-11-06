"""Documento como código no XLSX: células com id Aba!A1, escrita cirúrgica e recálculo pelo Excel ao abrir."""

import datetime
import io
import zipfile

import openpyxl
import pytest
from openpyxl.chart import BarChart, Reference
from openpyxl.worksheet.table import Table

from lince import codigo
from lince.codigo import OperacaoInvalida, xlsx


@pytest.fixture
def planilha():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Fretes"
    ws.append(["Rota", "Toneladas", "Preço"])
    for linha in [["PR-SP", 100, 38.4], ["PR-SC", 200, 41.2], ["SP-MG", 150, 52.3]]:
        ws.append(linha)
    ws["E2"] = "=SUM(B2:B4)"
    ws["A7"] = datetime.date(2026, 11, 6)
    ws.merge_cells("A9:C9")
    ws["A9"] = "Mesclada"
    ws.add_table(Table(displayName="TabFretes", ref="A1:C4"))
    grafico = BarChart()
    grafico.add_data(Reference(ws, min_col=2, min_row=1, max_row=4), titles_from_data=True)
    ws.add_chart(grafico, "H2")
    resumo = wb.create_sheet("Resumo")
    resumo["A1"] = "=SUM(Fretes!B2:B4)"
    resumo["A2"] = '="Fretes!A1"'
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _celula(c, cid):
    return next(x for aba in c["abas"] for x in aba["celulas"] if x["id"] == cid)


def _parte(dados, nome):
    return zipfile.ZipFile(io.BytesIO(dados)).read(nome).decode()


def test_codigo_expoe_celulas_formulas_datas_tabelas_e_mesclagens(planilha):
    c = xlsx.extrair(planilha)
    assert [a["id"] for a in c["abas"]] == ["Fretes", "Resumo"]
    assert _celula(c, "Fretes!B3")["valor"] == 200
    assert _celula(c, "Fretes!E2")["formula"] == "=SUM(B2:B4)"
    assert _celula(c, "Fretes!A7")["data"] == "2026-11-06"
    aba = c["abas"][0]
    assert aba["mescladas"] == ["A9:C9"] and aba["tabelas"][0]["nome"] == "TabFretes"


def test_ida_e_volta_sem_operacoes_preserva_todas_as_partes(planilha):
    novo, _, _ = xlsx.aplicar(planilha, [])
    assert codigo.comparar(planilha, novo)["partes_alteradas"] == []


def test_celulas_e_intervalo_mudam_so_a_aba_e_o_excel_recalcula(planilha):
    novo, rel = codigo.editar(
        planilha,
        "p.xlsx",
        [
            {"op": "celula", "alvo": "Fretes!B3", "valor": 250, "se_valor": 200},
            {"op": "intervalo", "alvo": "Fretes!A5", "valores": [["MS-PR", 80, 33.0]]},
            {"op": "celula", "alvo": "Fretes!E2", "formula": "=SUM(B2:B5)"},
        ],
    )
    assert rel["diff"]["partes_alteradas"] == ["/xl/workbook.xml", "/xl/worksheets/sheet1.xml"]
    assert any("recalcula" in a for a in rel["avisos"])  # os valores calculados no código são os antigos
    assert any("abaixo da tabela TabFretes" in a for a in rel["avisos"])  # a linha 5 não entra na tabela sozinha
    wb = openpyxl.load_workbook(io.BytesIO(novo))
    assert [c.value for c in wb["Fretes"][5][:3]] == ["MS-PR", 80, 33.0]
    assert wb["Fretes"]["B3"].value == 250 and wb["Fretes"]["E2"].value == "=SUM(B2:B5)"
    assert xlsx.integridade(novo) == []


def test_renomear_aba_atualiza_formulas_graficos_e_preserva_texto(planilha):
    novo, rel = codigo.editar(planilha, "p.xlsx", [{"op": "renomear_aba", "aba": "Fretes", "nome": "Fretes 2026"}])
    wb = openpyxl.load_workbook(io.BytesIO(novo))
    assert wb.sheetnames == ["Fretes 2026", "Resumo"]
    assert wb["Resumo"]["A1"].value == "=SUM('Fretes 2026'!B2:B4)"
    assert wb["Resumo"]["A2"].value == '="Fretes!A1"'  # texto entre aspas não é referência
    assert "<f>'Fretes 2026'!$B$2:$B$4</f>" in _parte(novo, "xl/charts/chart1.xml")
    _, rel2 = codigo.editar(novo, "p.xlsx", [{"op": "celula", "alvo": "'Fretes 2026'!A10", "valor": " oi "}])
    assert _celula(rel2["codigo"], "Fretes 2026!A10")["valor"] == " oi "


def test_cadeia_de_calculo_antiga_sai_quando_formula_muda(planilha):
    def com_cadeia(dados):
        saida = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(dados)) as o, zipfile.ZipFile(saida, "w") as z:
            for i in o.infolist():
                conteudo = o.read(i)
                if i.filename == "[Content_Types].xml":
                    conteudo = conteudo.replace(
                        b"</Types>",
                        b'<Override PartName="/xl/calcChain.xml" ContentType="application/vnd.openxmlformats-'
                        b'officedocument.spreadsheetml.calcChain+xml"/></Types>',
                    )
                elif i.filename == "xl/_rels/workbook.xml.rels":
                    conteudo = conteudo.replace(
                        b"</Relationships>",
                        b'<Relationship Id="rId99" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                        b'relationships/calcChain" Target="calcChain.xml"/></Relationships>',
                    )
                z.writestr(i, conteudo)
            z.writestr(
                "xl/calcChain.xml",
                '<calcChain xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><c r="E2" i="1"/>'
                "</calcChain>",
            )
        return saida.getvalue()

    dados = com_cadeia(planilha)
    assert xlsx.integridade(dados) == []
    novo, rel = codigo.editar(dados, "p.xlsx", [{"op": "celula", "alvo": "Fretes!E2", "valor": 10}])
    assert rel["diff"]["partes_removidas"] == ["/xl/calcChain.xml"]
    assert "calcChain" not in _parte(novo, "[Content_Types].xml") + _parte(novo, "xl/_rels/workbook.xml.rels")
    assert xlsx.integridade(novo) == []


def test_recusas_explicam_o_motivo(planilha):
    def erro(op):
        with pytest.raises(OperacaoInvalida) as e:
            codigo.editar(planilha, "p.xlsx", [op])
        return e.value.codigo, str(e.value)

    assert erro({"op": "celula", "alvo": "Fretes!B9", "valor": 1})[1].endswith("mesclagem A9:C9")
    assert "cabeçalho da tabela" in erro({"op": "celula", "alvo": "Fretes!B1", "valor": "x"})[1]
    assert erro({"op": "celula", "alvo": "Nada!A1", "valor": 1})[0] == "alvo-inexistente"
    assert erro({"op": "celula", "alvo": "Fretes!B3", "valor": 1, "se_valor": 999})[0] == "conflito"
    assert erro({"op": "celula", "alvo": "Fretes!ZZZZ1", "valor": 1})[0] == "alvo-invalido"
    assert erro({"op": "celula", "alvo": "Fretes!A1", "formula": "SUM(A1)"})[0] == "operacao-invalida"
    assert erro({"op": "celula", "alvo": "Fretes!A11", "valor": float("nan")})[0] == "operacao-invalida"
    assert erro({"op": "renomear_aba", "aba": "Fretes", "nome": "resumo"})[0] == "conflito"
    assert erro({"op": "renomear_aba", "aba": "Fretes", "nome": "a/b"})[0] == "operacao-invalida"


# ------------------------------------------------------------------ pacote escrito à mão, no formato do Excel
LIVRO = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">{pr}<sheets>
<sheet name="Dados" sheetId="1" r:id="rId1"/><sheet name="O'Neil" sheetId="2" r:id="rId2"/>
<sheet name="Grafico" sheetId="3" r:id="rId3"/></sheets>
<definedNames><definedName name="Total">Dados!$B$2:$B$3</definedName>
<definedName name="Tres">'Dados:O''Neil'!$A$1</definedName></definedNames>{calc}</workbook>"""
ABA_DADOS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:x14="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main"
 xmlns:xm="http://schemas.microsoft.com/office/excel/2006/main"><dimension ref="A1:D5"/>
<cols><col min="4" max="4" width="12" style="1" customWidth="1"/></cols><sheetData>
<row r="1" spans="1:4"><c r="A1" t="s"><v>0</v></c><c r="C1"><v>5</v></c></row>
<row r="2" spans="1:4"><c r="B2"><v>10</v></c><c r="C2"><f t="shared" ref="C2:C3" si="0">B2*2</f><v>20</v></c></row>
<row r="3" spans="1:4"><c r="B3"><v>4</v></c><c r="C3"><f t="shared" si="0"/><v>8</v></c></row>
<row r="4"><c r="A4"><f t="array" ref="A4:B4">B2:B3*1</f><v>10</v></c><c r="C4" s="1"><v>46332</v></c>
<c r="D4" vm="1"><v>#VALUE!</v></c></row>
<row r="5"><c r="A5"><f>SUM(Dados!B2:B3)  +  'O''Neil'!A1</f><v>15</v></c><c r="B5" t="d"><v>2026-11-06</v></c></row>
</sheetData><hyperlinks><hyperlink ref="A1" location="'O''Neil'!A1" display="ir"/></hyperlinks>
<extLst><ext uri="{CCE6A557-97BC-4b89-ADB6-D9C93CAAB3DF}"><x14:dataValidations count="1">
<x14:dataValidation type="list">
<x14:formula1><xm:f>'O''Neil'!$A$1:$A$3</xm:f></x14:formula1><xm:sqref>E1</xm:sqref></x14:dataValidation>
</x14:dataValidations></ext></extLst></worksheet>"""
ABA_ONEIL = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
<row r="1"><c r="A1"><v>1</v></c></row></sheetData><sheetProtection sheet="1"/></worksheet>"""
ESTILOS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cellXfs count="2">
<xf numFmtId="0"/><xf numFmtId="14" applyNumberFormat="1"/></cellXfs></styleSheet>"""
TEXTOS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="1" uniqueCount="1">
<si><t>Rota</t></si></sst>"""
CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml"
 ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml"
 ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/worksheets/sheet2.xml"
 ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/chartsheets/sheet1.xml"
 ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.chartsheet+xml"/>
</Types>"""
RAIZ = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1"
 Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
 Target="xl/workbook.xml"/>
</Relationships>"""
RELS_LIVRO = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
 Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
 Target="worksheets/sheet2.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chartsheet"
 Target="chartsheets/sheet1.xml"/>
<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
 Target="styles.xml"/>
<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings"
 Target="sharedStrings.xml"/>
</Relationships>"""


def _livro(pr="", calc="", protegido=False):
    livro = LIVRO.format(pr=pr, calc=calc)
    if protegido:
        livro = livro.replace("<sheets>", '<workbookProtection lockStructure="1"/><sheets>')
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as z:
        for nome, conteudo in [
            ("[Content_Types].xml", CT),
            ("_rels/.rels", RAIZ),
            ("xl/workbook.xml", livro),
            ("xl/_rels/workbook.xml.rels", RELS_LIVRO),
            ("xl/worksheets/sheet1.xml", ABA_DADOS),
            ("xl/worksheets/sheet2.xml", ABA_ONEIL),
            (
                "xl/chartsheets/sheet1.xml",
                '<chartsheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>',
            ),
            ("xl/styles.xml", ESTILOS),
            ("xl/sharedStrings.xml", TEXTOS),
        ]:
            z.writestr(nome, conteudo)
    return saida.getvalue()


def test_leitura_de_datas_no_formato_do_excel():
    c = xlsx.extrair(_livro())
    assert _celula(c, "Dados!C4")["data"] == "2026-11-06"
    assert _celula(c, "Dados!B5")["data"] == "2026-11-06"  # t="d": data ISO no próprio arquivo
    c1904 = xlsx.extrair(_livro(pr='<workbookPr date1904="1"/>'))
    assert _celula(c1904, "Dados!C4")["data"] == "2030-11-07"
    assert c["abas"][1]["protegida"]


def test_renomear_troca_toda_referencia_e_preserva_o_resto():
    novo, rel = codigo.editar(_livro(), "p.xlsx", [{"op": "renomear_aba", "aba": "O'Neil", "nome": "Vendas 2026"}])
    aba = _parte(novo, "xl/worksheets/sheet1.xml")
    assert "<f>SUM(Dados!B2:B3)  +  'Vendas 2026'!A1</f>" in aba  # espaços preservados
    assert "<xm:f>'Vendas 2026'!$A$1:$A$3</xm:f>" in aba  # validação do Excel 2010+, em extLst
    assert "location=\"'Vendas 2026'!A1\"" in aba
    assert "<definedName name=\"Tres\">'Dados:Vendas 2026'!$A$1</definedName>" in _parte(novo, "xl/workbook.xml")
    assert rel["aplicadas"][0]["referencias"] == 4
    assert xlsx.integridade(novo) == []


def test_renomear_recusa_nome_de_qualquer_aba_e_estrutura_protegida():
    with pytest.raises(OperacaoInvalida) as e:
        codigo.editar(_livro(), "p.xlsx", [{"op": "renomear_aba", "aba": "Dados", "nome": "grafico"}])
    assert e.value.codigo == "conflito"  # aba de gráfico também conta
    with pytest.raises(OperacaoInvalida) as e:
        codigo.editar(_livro(protegido=True), "p.xlsx", [{"op": "renomear_aba", "aba": "Dados", "nome": "X"}])
    assert "protegida" in str(e.value)


def test_escrita_respeita_formulas_estilos_e_metadados():
    novo, rel = codigo.editar(
        _livro(),
        "p.xlsx",
        [
            {"op": "celula", "alvo": "Dados!B1", "valor": 7},  # entra antes do C1 que já existe
            {"op": "celula", "alvo": "Dados!D5", "valor": 46000},  # coluna D tem estilo de data
            {"op": "celula", "alvo": "Dados!D4", "valor": 3},  # tinha vm (valor rico)
            {"op": "celula", "alvo": "Dados!E2", "formula": "=xlookup(B2,B2:B3,C2:C3)"},
        ],
    )
    aba = _parte(novo, "xl/worksheets/sheet1.xml")
    assert '<c r="A1" t="s"><v>0</v></c><c r="B1"><v>7</v></c><c r="C1">' in aba
    assert '<row r="1">' in aba and 'spans="1:4"' in aba  # a linha 1 perdeu o spans; as outras não
    assert '<c r="D5" s="1"><v>46000</v></c>' in aba
    assert '<c r="D4"><v>3</v></c>' in aba
    assert "<f>_xlfn.XLOOKUP(B2,B2:B3,C2:C3)</f>" in aba
    assert '<calcPr fullCalcOnLoad="1"/></workbook>' in _parte(novo, "xl/workbook.xml").replace("\n", "")
    assert xlsx.integridade(novo) == []


def test_calcpr_entra_na_posicao_do_esquema():
    novo, _ = codigo.editar(_livro(), "p.xlsx", [{"op": "celula", "alvo": "Dados!B2", "valor": 1}])
    livro = _parte(novo, "xl/workbook.xml").replace("\n", "")
    assert livro.index("</definedNames>") < livro.index("<calcPr") < livro.index("</workbook>")
    novo, _ = codigo.editar(
        _livro(calc='<calcPr calcId="191029"/>'), "p.xlsx", [{"op": "celula", "alvo": "Dados!B2", "valor": 1}]
    )
    assert '<calcPr calcId="191029" fullCalcOnLoad="1"/>' in _parte(novo, "xl/workbook.xml")


@pytest.mark.parametrize(
    ("op", "trecho"),
    [
        ({"op": "celula", "alvo": "Dados!B4", "valor": 1}, "fórmula matricial A4:B4"),
        ({"op": "celula", "alvo": "Dados!C2", "valor": 1}, "origem de uma fórmula compartilhada"),
        ({"op": "celula", "alvo": "O'Neil!A2", "valor": 1}, "está protegida"),
        ({"op": "celula", "alvo": "Dados!A9", "formula": "=SOMA(A1)"}, "função desconhecida SOMA"),
        ({"op": "celula", "alvo": "Dados!A9", "formula": "=SUM(A1;A2)"}, "vírgula"),
        ({"op": "celula", "alvo": "Dados!A9", "formula": "=SUM(A1"}, "sem par"),
        ({"op": "celula", "alvo": "Dados!A9", "formula": "=FILTER(A:A,B:B>1)"}, "matriz dinâmica"),
        ({"op": "celula", "alvo": "Dados!A9", "valor": 10**400}, "grande demais"),
        ({"op": "celula", "alvo": "Dados!A9", "formula": "=A1*"}, "termina sem valor"),
        ({"op": "celula", "alvo": "Dados!A9", "formula": "=()"}, "parêntese vazio"),
        ({"op": "celula", "alvo": "Dados!A9", "formula": "=SUM(A1)(B1)"}, "falta operador"),
        ({"op": "celula", "alvo": "Dados!A9", "formula": "=Dadoss!A1"}, "não existe"),
        ({"op": "celula", "alvo": "Dados!A9", "formula": "=[1]Dados!A1"}, "outro arquivo"),
        ({"op": "celula", "alvo": "Dados!A9", "formula": "=_xlfn._xlws.FILTER(A:A,B:B)"}, "matriz dinâmica"),
        ({"op": "celula", "alvo": "Dados!A9", "formula": "=_xlfn.QUALQUER(A1)"}, "desconhecida"),
        ({"op": "intervalo", "alvo": "Dados!A9", "valores": [[]]}, "não vazias"),
        ({"op": "renomear_aba", "aba": "Dados", "nome": "🙂" * 16}, "31 caracteres"),
        ({"op": "intervalo", "alvo": "Dados!A9", "valores": [["=" + "1+" * 5000 + "1"]]}, "8192"),
    ],
)
def test_escritas_que_quebrariam_o_arquivo_sao_recusadas(op, trecho):
    with pytest.raises(OperacaoInvalida) as e:
        codigo.editar(_livro(), "p.xlsx", [op])
    assert trecho in str(e.value)


def test_pacote_fora_do_padrao_e_recusado_sem_erro_interno(planilha):
    saida = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(planilha)) as o, zipfile.ZipFile(saida, "w") as z:
        for i in o.infolist():
            if i.filename != "_rels/.rels":
                z.writestr(i, o.read(i))
    with pytest.raises(OperacaoInvalida) as e:
        codigo.editar(saida.getvalue(), "p.xlsx", [{"op": "celula", "alvo": "Fretes!A1", "valor": 1}])
    assert e.value.codigo == "operacao-invalida"


def test_intervalo_grande_e_linear():
    import time

    inicio = time.monotonic()
    _, rel = codigo.editar(
        _livro(), "p.xlsx", [{"op": "intervalo", "alvo": "Dados!F1", "valores": [[k] for k in range(20_000)]}]
    )
    assert rel["aplicadas"][0]["celulas"] == 20_000 and time.monotonic() - inicio < 10


def test_formula_valida_sai_no_formato_do_arquivo():
    novo, _ = codigo.editar(
        _livro(),
        "p.xlsx",
        [
            {"op": "celula", "alvo": "Dados!A9", "formula": "=networkdays.intl(B2,B3)+IF(B2,,1)"},
            {"op": "celula", "alvo": "Dados!A10", "formula": "=SUM('Dados:O''Neil'!A1)+TODAY()"},
        ],
    )
    aba = _parte(novo, "xl/worksheets/sheet1.xml")
    assert "<f>_xlfn.NETWORKDAYS.INTL(B2,B3)+IF(B2,,1)</f>" in aba
    assert "<f>SUM('Dados:O''Neil'!A1)+TODAY()</f>" in aba


def test_renomear_intervalo_3d_sem_aspas_e_codigo_de_erro():
    def ajustar(livro):
        return (
            livro.replace('name="Dados"', 'name="Jan"')
            .replace("Dados!$B$2:$B$3", "SUM(Jan:Ref!$B$2)+#REF!")
            .replace("'Dados:O''Neil'!$A$1", "#DIV/0!")
            .replace('name="Grafico"', 'name="Ref"')
        )

    dados = _trocar(_livro(), "xl/workbook.xml", ajustar)
    for antigo, novo, esperado in [
        ("Jan", "Janeiro", "SUM('Janeiro:Ref'!$B$2)+#REF!"),
        ("Ref", "Dez 2026", "SUM('Jan:Dez 2026'!$B$2)+#REF!"),
    ]:
        resultado, _ = codigo.editar(dados, "p.xlsx", [{"op": "renomear_aba", "aba": antigo, "nome": novo}])
        livro = _parte(resultado, "xl/workbook.xml")
        assert f">{esperado}</definedName>" in livro and ">#DIV/0!</definedName>" in livro


def test_renomear_recusa_referencia_em_lugar_que_nao_atualiza():
    def com_controle(aba):
        return aba.replace(
            "<sheetData>", "<sheetData><!-- --><x:ctrl xmlns:x=\"urn:teste\" linkedCell=\"'O''Neil'!A1\"/>", 1
        )

    dados = _trocar(_livro(), "xl/worksheets/sheet1.xml", com_controle)
    with pytest.raises(OperacaoInvalida) as e:
        codigo.editar(dados, "p.xlsx", [{"op": "renomear_aba", "aba": "O'Neil", "nome": "Vendas"}])
    assert "/xl/worksheets/sheet1.xml" in str(e.value) and "renomeie no Excel" in str(e.value)


def test_partes_de_cada_renomear_e_titulos_do_arquivo():
    app = (
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><TitlesOfParts>'
        '<vt:vector size="1" baseType="lpstr"><vt:lpstr>Dados</vt:lpstr></vt:vector></TitlesOfParts></Properties>'
    )
    dados = _com_parte(_livro(), "docProps/app.xml", app)
    _, rel = codigo.editar(
        dados,
        "p.xlsx",
        [
            {"op": "renomear_aba", "aba": "Dados", "nome": "Base"},
            {"op": "renomear_aba", "aba": "Base", "nome": "Base 2"},
        ],
    )
    # o workbook.xml já tinha sido tocado pela 1ª operação e continua na lista da 2ª
    assert rel["aplicadas"][1]["partes"] == ["/docProps/app.xml", "/xl/workbook.xml", "/xl/worksheets/sheet1.xml"]
    novo, _ = codigo.editar(dados, "p.xlsx", [{"op": "renomear_aba", "aba": "Dados", "nome": "Base"}])
    assert "<vt:lpstr>Base</vt:lpstr>" in _parte(novo, "docProps/app.xml")


def test_numero_nao_finito_no_arquivo_vira_texto_na_leitura():
    dados = _trocar(_livro(), "xl/worksheets/sheet1.xml", lambda x: x.replace("<v>5</v>", "<v>NaN</v>"))
    assert _celula(xlsx.extrair(dados), "Dados!C1")["valor"] == "NaN"


def test_muitas_mesclagens_nao_deixam_a_escrita_quadratica():
    import time

    mescladas = "".join(f'<mergeCell ref="A{k}:B{k}"/>' for k in range(100, 20_100))
    dados = _trocar(
        _livro(),
        "xl/worksheets/sheet1.xml",
        lambda x: x.replace("</sheetData>", f'</sheetData><mergeCells count="20000">{mescladas}</mergeCells>'),
    )
    inicio = time.monotonic()
    _, rel = codigo.editar(
        dados, "p.xlsx", [{"op": "intervalo", "alvo": "Dados!F1", "valores": [[k] for k in range(20_000)]}]
    )
    assert rel["aplicadas"][0]["celulas"] == 20_000 and time.monotonic() - inicio < 10
    with pytest.raises(OperacaoInvalida):
        codigo.editar(dados, "p.xlsx", [{"op": "celula", "alvo": "Dados!B500", "valor": 1}])


def test_pedido_malformado_nao_vira_erro_interno():
    with pytest.raises(OperacaoInvalida):
        codigo.editar(_livro(), "p.xlsx", [{"op": []}])


def _trocar(dados, parte, troca):
    saida = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(dados)) as o, zipfile.ZipFile(saida, "w") as z:
        for i in o.infolist():
            conteudo = o.read(i)
            z.writestr(i, troca(conteudo.decode()).encode() if i.filename == parte else conteudo)
    return saida.getvalue()


def _com_parte(dados, nome, conteudo):
    saida = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(dados)) as o, zipfile.ZipFile(saida, "w") as z:
        for i in o.infolist():
            z.writestr(i, o.read(i))
        z.writestr(nome, conteudo)
    return saida.getvalue()


@pytest.mark.parametrize(
    "formula",
    [
        "=SUM(A1:)",
        "=A1:B",
        "=1A+1",
        "=1.2.3",
        "=Dados!",
        '="a" "b"',
        "=SUM({A1,B1})",
        "={1,2;3}",
        '="' + "x" * 300 + '"',
        "=IF(A1,-)",
        "=A1*1,5",
        "=Nada*2",
        "=XFE1",
    ],
)
def test_lista_branca_recusa_o_que_o_excel_repararia(formula):
    with pytest.raises(OperacaoInvalida):
        codigo.editar(_livro(), "p.xlsx", [{"op": "celula", "alvo": "Dados!A9", "formula": formula}])


@pytest.mark.parametrize(
    "formula",
    [
        "=Dados!A1:Dados!B2",
        "=A1:INDEX(B:B,5)",
        "=INDEX(B:B,5):A10",
        "=SUM(A1:B2 B1:C3)",
        "=Total*2",
        "={1,-2;3,4}",
        "=SUM((A1,B1))",
        "=-+A1",
        "=TRUE",
        '=ARABIC("X")',
        "=1:1",
        "=$A:$B",
    ],
)
def test_lista_branca_aceita_formula_valida_comum(formula):
    codigo.editar(_livro(), "p.xlsx", [{"op": "celula", "alvo": "Dados!A9", "formula": formula}])


def test_formula_enorme_e_recusada_antes_de_tokenizar():
    import time

    inicio = time.monotonic()
    with pytest.raises(OperacaoInvalida) as e:
        codigo.editar(
            _livro(), "p.xlsx", [{"op": "intervalo", "alvo": "Dados!A9", "valores": [["=" + "[a]" * 300_000]]}]
        )
    assert "8192" in str(e.value) and time.monotonic() - inicio < 2


def test_prazo_vale_tambem_para_renomear(monkeypatch):
    monkeypatch.setattr(xlsx, "EDICAO_PRAZO", -1)
    with pytest.raises(OperacaoInvalida) as e:
        codigo.editar(_livro(), "p.xlsx", [{"op": "renomear_aba", "aba": "Dados", "nome": "Base"}])
    assert "divida" in str(e.value)


def test_renomear_em_arquivo_comum_do_excel():
    app = (
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><TitlesOfParts>'
        '<vt:vector size="2" baseType="lpstr"><vt:lpstr>Dados</vt:lpstr><vt:lpstr>Dados!Print_Area</vt:lpstr>'
        "</vt:vector></TitlesOfParts></Properties>"
    )
    comentario = (
        '<comments xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><commentList>'
        '<comment ref="A1"><text><r><t>Conferir Dados!</t></r></text></comment></commentList></comments>'
    )
    dados = _com_parte(_com_parte(_livro(), "docProps/app.xml", app), "xl/comments1.xml", comentario)
    novo, _ = codigo.editar(dados, "p.xlsx", [{"op": "renomear_aba", "aba": "Dados", "nome": "Base"}])
    assert "<vt:lpstr>Base!Print_Area</vt:lpstr>" in _parte(novo, "docProps/app.xml")
    codigo.editar(dados, "p.xlsx", [{"op": "renomear_aba", "aba": "Dados", "nome": "DADOS"}])  # só a caixa


def test_aba_com_nome_de_erro_e_parte_ilegivel():
    dados = _trocar(_livro(), "xl/workbook.xml", lambda x: x.replace('name="Grafico"', 'name="Ref"'))
    dados = _trocar(dados, "xl/worksheets/sheet1.xml", lambda x: x.replace("<v>#VALUE!</v>", "<v>#REF!</v>"))
    codigo.editar(dados, "p.xlsx", [{"op": "renomear_aba", "aba": "Ref", "nome": "Graf"}])  # #REF! não é a aba
    ilegivel = _com_parte(_livro(), "xl/estranho.xml", "<!DOCTYPE x><x>O'Neil!A1</x>")
    with pytest.raises(OperacaoInvalida):
        codigo.editar(ilegivel, "p.xlsx", [{"op": "renomear_aba", "aba": "O'Neil", "nome": "Vendas"}])
