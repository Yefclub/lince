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
    assert 'fullCalcOnLoad="1"' in _parte(novo, "xl/workbook.xml")
    assert rel["avisos"]  # os valores calculados no código são os antigos até alguém abrir
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
