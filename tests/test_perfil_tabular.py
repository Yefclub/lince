"""Perfil de cada tabela da planilha: o que a IA precisa para consultar os dados sem ler tudo, igual nos dois
caminhos de leitura e nunca dizendo mais do que o texto tem."""

import datetime
import re
import zipfile
from pathlib import Path

import docx
import openpyxl
import pytest

import lince
from lince import office, perfil_tabular


def _vendas(tmp_path, linhas=8, nome="vendas.xlsx"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Vendas"
    ws.append(["Produto", "Qtd", "Data", "Pago", None, "Obs"])
    for n in range(linhas):
        obs = "x" * 200 if n == 0 else "a|b" if n == 1 else "dois  espaços" if n == 2 else None
        ws.append([f"item {n}", n * 1.5, datetime.date(2026, 1, n % 28 + 1), n % 2 == 0, None, obs])
        if n == 2:
            ws.append([])  # linha vazia no meio: está no texto, mas não é dado
    wb.create_sheet("Oculta").append(["segredo"])
    wb["Oculta"].sheet_state = "hidden"
    wb.create_sheet("Resumo").append(["Total", linhas])
    wb.defined_names["Faixa"] = openpyxl.workbook.defined_name.DefinedName("Faixa", attr_text="Vendas!$A$1:$B$9")
    p = tmp_path / nome
    wb.save(p)
    return p


def test_perfil_da_planilha_diz_colunas_tipos_amostra_e_linhas(tmp_path):
    r = lince.ler(_vendas(tmp_path))
    vendas, resumo = r.estrutura["perfil_tabular"]  # a oculta fica de fora, como no texto
    assert vendas["aba"] == "Vendas" and resumo["aba"] == "Resumo"
    assert [(c["nome"], c["tipo"]) for c in vendas["colunas"]] == [
        ("Produto", "texto"), ("Qtd", "numero"), ("Data", "data"), ("Pago", "booleano"), ("coluna 5", "vazio"),
        ("Obs", "texto"),
    ]  # fmt: skip
    assert vendas["linhas_extraidas"] == vendas["linhas_observadas"] == 8
    assert len(vendas["amostra"]) == 5 and vendas["amostra"][1][:2] == ["item 1", "1.5"]
    assert vendas["amostra"][0][5] == "x" * 120 + "…" and vendas["amostra"][1][5] == "a|b"
    assert (
        vendas["amostra"][2][5] == "dois  espaços"
    )  # como no texto: os espaços de dentro ficam  # sem escape do Markdown
    assert "extracao_cortada" not in vendas and r.estrutura["nomes_definidos"] == ["Faixa"]


def test_perfil_igual_nos_dois_caminhos_de_leitura(tmp_path, monkeypatch):
    p = _vendas(tmp_path)
    normal = lince.ler(p).estrutura["perfil_tabular"]
    monkeypatch.setattr(office, "_CELULAS_ANYDOC_MAX", 0)
    assert lince.ler(p).estrutura["perfil_tabular"] == normal


def test_aba_unica_leva_o_nome_da_estrutura(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.title = "Única"
    wb.active.append(["a", "b"])
    wb.active.append([1, 2])
    p = tmp_path / "unica.xlsx"
    wb.save(p)
    assert lince.ler(p).estrutura["perfil_tabular"][0]["aba"] == "Única"


def test_aba_cortada_no_texto_e_marcada_no_perfil(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "_CELULAS_ANYDOC_MAX", 0)
    monkeypatch.setattr(office, "CELULAS_TOTAL_MAX", 24)  # 6 colunas: cabeçalho e 3 linhas, e nada da Resumo
    vendas, resumo = lince.ler(_vendas(tmp_path)).estrutura["perfil_tabular"]
    assert vendas["extracao_cortada"] and vendas["linhas_extraidas"] == 3  # só o que está no texto
    assert resumo == {**resumo, "linhas_extraidas": 0, "extracao_cortada": True}  # o teto chegou antes dela


def test_aba_sem_espaco_para_a_primeira_linha_e_marcada_no_perfil(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "_CELULAS_ANYDOC_MAX", 0)
    monkeypatch.setattr(office, "CELULAS_TOTAL_MAX", 61)  # a Vendas inteira (10 x 6) cabe; a Resumo, não
    vendas, resumo = lince.ler(_vendas(tmp_path)).estrutura["perfil_tabular"]
    assert "extracao_cortada" not in vendas and resumo["extracao_cortada"] and resumo["linhas_extraidas"] == 0


def test_planilha_larga_declara_o_corte_das_colunas(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.append([f"c{i}" for i in range(70)])
    wb.active.append(list(range(70)))
    p = tmp_path / "larga.xlsx"
    wb.save(p)
    aba = lince.ler(p).estrutura["perfil_tabular"][0]
    assert aba["colunas_cortadas"] and len(aba["colunas"]) == perfil_tabular.COLUNAS_MAX


def test_tipos_pelo_texto_da_celula():
    casos = {"1.234,56": "numero", "-3e5": "numero", "12%": "numero", "2026-01-31": "data", "31/01/2026": "data",
             "2026-01-31 13:45:00": "data", "TRUE": "booleano", "falso": "booleano", "  ": "vazio", "R$ 10": "texto",
             "1.2.3.4a": "texto", "192.168.0.1": "texto", "1.2.3": "texto", "1,2,3,4": "texto", "1.234.56": "texto",
             "1.234.567,89": "numero", "1,234.5": "numero", "1234": "numero"}  # fmt: skip
    assert {t: perfil_tabular.tipo(t) for t in casos} == casos


REAIS = Path(__file__).parent / "fixtures" / "reais"


@pytest.mark.parametrize(
    "arquivo,esperado",
    [
        ("poi_45761.xls", [("VisibleSheet", 0)]),  # duas ocultas: o nome vem da única visível
        ("testFooter.ods", [("Sheet1", 1)]),
        ("poi_sample.xlsb", [("Sheet1", 9), ("rich test", 4)]),
    ],
)
def test_perfil_nos_outros_formatos_de_planilha(arquivo, esperado):
    perfil = lince.ler(REAIS / arquivo).estrutura["perfil_tabular"]
    assert [(p["aba"], p["linhas_extraidas"]) for p in perfil] == esperado


def test_tsv_tem_perfil_sem_nome_de_aba(tmp_path):
    p = tmp_path / "dados.tsv"
    p.write_text("cidade\tqtd\nCuritiba\t3\nLapa\t1.234\n", encoding="utf-8")
    (aba,) = lince.ler(p).estrutura["perfil_tabular"]
    assert aba["aba"] is None and [c["tipo"] for c in aba["colunas"]] == ["texto", "numero"]


def test_documento_que_nao_e_planilha_nao_tem_perfil(tmp_path):
    d = docx.Document()
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(1, 0).text = "a", "1"
    p = tmp_path / "tabela.docx"
    d.save(p)
    r = lince.ler(p)
    assert "| a |" in r.markdown and "perfil_tabular" not in r.estrutura


def test_tipos_saem_das_primeiras_linhas_observadas(tmp_path, monkeypatch):
    monkeypatch.setattr(perfil_tabular, "LINHAS_OBSERVADAS_MAX", 3)
    wb = openpyxl.Workbook()
    wb.active.append(["valor"])
    for v in [1, 2, 3, "a", "b", "c", "d"]:
        wb.active.append([v])
    p = tmp_path / "janela.xlsx"
    wb.save(p)
    (aba,) = lince.ler(p).estrutura["perfil_tabular"]
    assert aba["colunas"][0]["tipo"] == "numero" and aba["linhas_observadas"] == 3 and aba["linhas_extraidas"] == 7


def test_perfil_igual_nos_dois_caminhos_com_dado_fora_de_a1_e_aba_vazia(tmp_path, monkeypatch):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = " Jan  2026"  # espaços no nome: o perfil usa o nome do arquivo nos dois caminhos
    ws["C4"], ws["D4"], ws["C5"], ws["D5"] = "nome", "qtd", "x", 1
    wb.create_sheet("Vazia")
    p = tmp_path / "fora.xlsx"
    wb.save(p)
    normal = lince.ler(p)
    monkeypatch.setattr(office, "_CELULAS_ANYDOC_MAX", 0)
    fluxo = lince.ler(p)
    assert fluxo.estrutura["perfil_tabular"] == normal.estrutura["perfil_tabular"]
    assert [a["aba"] for a in normal.estrutura["perfil_tabular"]] == [" Jan  2026"]


def test_aviso_de_corte_conta_as_mesmas_linhas_do_perfil(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "_CELULAS_ANYDOC_MAX", 0)
    monkeypatch.setattr(office, "CELULAS_TOTAL_MAX", 36)  # cabeçalho, 3 linhas, a vazia e mais uma (6 x 6)
    r = lince.ler(_vendas(tmp_path))
    vendas = r.estrutura["perfil_tabular"][0]
    assert (
        vendas["linhas_extraidas"] == 4 and f"entregue com {vendas['linhas_extraidas']} linhas de dados" in r.markdown
    )


def test_nomes_definidos_so_do_usuario_uma_vez_e_corte_declarado(tmp_path):
    p = _vendas(tmp_path)
    q = tmp_path / "nomes.xlsx"
    nomes = (
        '<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">Vendas!$A$1:$F$9'
        '</definedName><definedName name="_xlnm.Print_Area" localSheetId="0">Vendas!$A$1:$F$9</definedName>'
        '<definedName name="Oculto" hidden="1">Vendas!$A$1</definedName>'
        '<definedName name="Meta" localSheetId="0">Vendas!$B$2</definedName>'
        '<definedName name="Meta" localSheetId="1">Resumo!$B$1</definedName>'
        + "".join(f'<definedName name="N{i}">Vendas!$A${i + 1}</definedName>' for i in range(40))
        + "</definedNames>"
    )
    with zipfile.ZipFile(p) as zi, zipfile.ZipFile(q, "w") as zo:
        for n in zi.namelist():
            dados = zi.read(n)
            if n == "xl/workbook.xml":
                dados = re.sub(rb"<definedNames>.*?</definedNames>", b"", dados).replace(
                    b"</sheets>", b"</sheets>" + nomes.encode()
                )
            zo.writestr(n, dados)
    e = lince.ler(q).estrutura
    assert e["nomes_definidos"][:3] == ["Meta", "N0", "N1"] and len(e["nomes_definidos"]) == 32
    assert e["nomes_definidos_cortados"]
