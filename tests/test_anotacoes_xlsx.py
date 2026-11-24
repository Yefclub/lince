"""Células com significado além do valor exibido, nas duas rotas do XLSX."""

import openpyxl
import pytest
from openpyxl.comments import Comment
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Font
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.hyperlink import Hyperlink

import lince
from lince import office


@pytest.fixture(params=[0, 10**9], ids=["fluxo", "anydoc"])
def leitura(tmp_path, monkeypatch, request):
    monkeypatch.setattr(office, "_CELULAS_ANYDOC_MAX", request.param)

    def ler(wb):
        arquivo = tmp_path / "dados.xlsx"
        wb.save(arquivo)
        return lince.ler(arquivo)

    return ler


def test_links_notas_e_validacoes_identificam_a_celula_da_aba_visivel(leitura):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Campos"
    ws["B4"] = "Identificador"
    ws["B4"].hyperlink = Hyperlink(ref="B4", target="https://example.org/campo--manual", display="Manual de referência")
    ws["B4"].comment = Comment("Usar o código completo da publicação.", "Editora")
    ws["B5"] = "Índice"
    ws["B5"].hyperlink = Hyperlink(ref="B5", location="Campos!B4")
    dv = DataValidation(type="textLength", operator="lessThanOrEqual", formula1=100)
    dv.error = "Máximo de 100 caracteres."
    dv.add("C5:C501")
    ws.add_data_validation(dv)
    oculta = wb.create_sheet("Oculta")
    oculta.sheet_state = "hidden"
    oculta["A1"].comment = Comment("Texto fora da leitura.", "Editora")
    md = leitura(wb).markdown
    assert "Campos!B4" in md and "[Manual de referência](https://example.org/campo--manual)" in md and "Campos!B5" in md
    assert "Usar o código completo da publicação." in md and "Texto fora da leitura." not in md
    assert all(x in md for x in ("Campos!C5:C501", "textLength", "lessThanOrEqual", "100", dv.error))


def test_recuos_e_tipos_numericos_com_mascara(leitura):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Dívida"
    ws.append(["Grupo", "Montante", "Símbolo"])
    for linha, (nome, valor) in enumerate([("   União", 12500), ("      Crédito", 0), ("   Local", 0)], 2):
        ws.append([nome, valor, "-"])
        ws.cell(linha, 2).number_format = '#" "##0;[Red]-#" "##0;"-"'
    r = leitura(wb)
    col = r.estrutura["perfil_tabular"][0]["colunas"]
    assert [c["tipo"] for c in col] == ["texto", "numero", "texto"]
    assert "Dívida!A2" in r.markdown and "3 espaços" in r.markdown
    assert "Dívida!A3" in r.markdown and "6 espaços" in r.markdown


def test_blocos_com_titulos_e_rotulos_em_multiplas_linhas(leitura):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Relatório", None, None, None, "Relatório", None, None, "Índice"])
    ws.append(["Método A", None, None, None, "Método B"])
    ws.append([])
    ws.append([None, "Lower", "Upper", None, None, "Lower", "Upper"])
    ws.append([None, "decile", "decile", None, None, "decile", "decile"])
    ws.append([])
    ws.append(["Média", 17, 51, None, "Média", 12, 36])
    r = leitura(wb)
    blocos = r.estrutura["perfil_tabular"]
    assert len(blocos) == 2
    for perfil, metodo, valor in zip(blocos, ["Método A", "Método B"], ["51", "36"], strict=True):
        assert metodo in perfil["titulo"] and perfil["colunas"][2]["nome"] == "Upper decile"
        assert perfil["amostra"][0][2] == valor and perfil["linhas_extraidas"] == 1


def test_negrito_condicional_usa_valores_brutos_e_as_referencias_relativas(leitura):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Países"
    ws.append(["País", "Diferença", "Erro"])
    for nome, dif, erro in [("Um", 11.37, 3.02), ("Dois", 3.39, 3.47), ("Três", 3.924, 2.0)]:
        ws.append([nome, dif, erro])
        ws.cell(ws.max_row, 2).number_format = "0"
    regra = FormulaRule(formula=["ABS(B2/C2)>1.96"], font=Font(bold=True))
    ws.conditional_formatting.add("B2:B4", regra)
    md = leitura(wb).markdown
    linha = next(l for l in md.splitlines() if "ABS(B2/C2)>1.96" in l)
    assert "Países!B2:B4" in linha and "negrito" in linha
    assert "satisfeita em B2, B4" in linha and "B3" not in linha


def test_teto_de_anotacoes_declara_corte_e_preserva_o_corpo(leitura, monkeypatch):
    from lince import xlsx_anotacoes

    monkeypatch.setattr(xlsx_anotacoes, "ANOTACOES_MAX", 1)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Primeira", "Segunda"])
    for c in ("A1", "B1"):
        ws[c].comment = Comment("Nota de " + c, "Editora")
    r = leitura(wb)
    assert "Nota de A1" in r.markdown and "Nota de B1" not in r.markdown and "| Primeira | Segunda |" in r.markdown
    assert any("limite de extração" in p for p in r.cobertura.pendentes)


def test_anotacoes_respeitam_o_corte_de_linhas(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "_CELULAS_ANYDOC_MAX", 0)
    monkeypatch.setattr(office, "LINHAS_POR_ABA_MAX", 1)
    wb = openpyxl.Workbook()
    ws = wb.active
    for i in range(1, 4):
        ws.append([f"Item {i}"])
        ws.cell(i, 1).comment = Comment(f"Nota específica {i}", "Editora")
    restricao = DataValidation(type="textLength", operator="lessThanOrEqual", formula1=5)
    restricao.add("A2:A30")
    ws.add_data_validation(restricao)
    p = tmp_path / "corte.xlsx"
    wb.save(p)
    r = lince.ler(p)
    assert "Nota específica 2" in r.markdown and "Nota específica 3" not in r.markdown
    assert "Sheet!A2:A2" in r.markdown and "textLength" in r.markdown


def test_grupos_de_medidas_da_mesma_tabela_conservam_rotulo_de_linha(leitura):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Tabela única"])
    ws.append([None, "Primeiro grupo", None, None, "Segundo grupo"])
    ws.append(["Região", "Média", "Erro", None, "Média", "Erro"])
    ws.append(["Sul", 1, 2, None, 3, 4])
    assert len(leitura(wb).estrutura["perfil_tabular"]) == 1


def test_expressao_fora_do_subconjunto_conserva_regra_sem_afirmar_negrito(leitura):
    wb = openpyxl.Workbook()
    wb.active["A1"] = 1
    regra = FormulaRule(formula=["COUNTIF(A:A,1)>2"], font=Font(bold=True))
    wb.active.conditional_formatting.add("A1:A50", regra)
    md = leitura(wb).markdown
    assert "COUNTIF(A:A,1)>2" in md and "aplicação não avaliada" in md
    assert "regra satisfeita" not in md


def test_dados_textuais_em_blocos_paralelos_permanecem_na_amostra(leitura):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Equipe A", None, None, "Equipe B"])
    ws.append(["Região", "Categoria", None, "Região", "Categoria"])
    ws.append(["Norte", "Azul", None, "Sul", "Verde"])
    ws.append(["Leste", "Rosa", None, "Oeste", "Ouro"])
    a, b = leitura(wb).estrutura["perfil_tabular"]
    assert a["cabecalho"] == b["cabecalho"] == ["Região", "Categoria"]
    assert a["amostra"] == [["Norte", "Azul"], ["Leste", "Rosa"]]
    assert b["amostra"] == [["Sul", "Verde"], ["Oeste", "Ouro"]]
    assert a["linhas_extraidas"] == b["linhas_extraidas"] == 2


def test_anotacoes_e_tipos_seguem_as_celulas_entregues_da_aba(leitura):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Interno", "Símbolo", "Saldo"])
    ws.append([42, "-", 0])
    ws.append([43, "Oculto na linha", "erro"])
    ws.append([44, "-", 0])
    ws.column_dimensions["A"].hidden = True
    ws.row_dimensions[3].hidden = True
    ws["A2"].comment = Comment("Nota da coluna interna", "Editora")
    ws["C3"].comment = Comment("Nota da linha interna", "Editora")
    ws["C4"].comment = Comment("Nota do saldo", "Editora")
    for ref in ["C2", "C4"]:
        ws[ref].number_format = '0;[Red]-0;"-"'
    r = leitura(wb)
    assert ("Nota da coluna interna" in r.markdown) == ("| Interno |" in r.markdown)
    assert ("Nota da linha interna" in r.markdown) == ("Oculto na linha" in r.markdown)
    assert "Nota do saldo" in r.markdown
    tipos = {c["nome"]: c["tipo"] for c in r.estrutura["perfil_tabular"][0]["colunas"]}
    assert tipos["Símbolo"] == "texto" and tipos["Saldo"] == "numero"
