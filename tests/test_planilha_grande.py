"""Planilha grande: lida em fluxo, sem montar o documento inteiro na memória, com tetos e aviso no próprio texto."""

import datetime
import re
import zipfile

import anydoc
import openpyxl
import pytest

import lince
from lince import office


def _planilha(tmp_path, linhas=3, nome="tipos.xlsx"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Vendas"
    ws.append(["texto", "inteiro", "decimal", "data", "datahora", "bool", "vazio", "pipe"])
    for n in range(linhas):
        ws.append(
            [f"  item {n}  ", n, n + 0.5, datetime.date(2026, 1, 1), datetime.datetime(2026, 1, 1, 13, 45),
             n % 2 == 0, None, "a|b"]
        )  # fmt: skip
    oculta = wb.create_sheet("Oculta")
    oculta.sheet_state = "hidden"
    oculta.append(["segredo", 1])
    wb.create_sheet("Vazia")
    resumo = wb.create_sheet("Resumo")
    resumo.append(["Total", linhas])
    p = tmp_path / nome
    wb.save(p)
    return p


def test_em_fluxo_escreve_igual_ao_leitor_do_corpo(tmp_path, monkeypatch):
    p = _planilha(tmp_path)
    normal = lince.ler(p).markdown
    monkeypatch.setattr(office, "_CELULAS_ANYDOC_MAX", 0)
    assert lince.ler(p).markdown == normal and "segredo" not in normal  # a aba oculta fica de fora nos dois


def test_planilha_grande_nao_passa_pelo_anydoc_e_tem_teto_de_linhas(tmp_path, monkeypatch):
    p = _planilha(tmp_path, linhas=30)
    monkeypatch.setattr(office, "_CELULAS_ANYDOC_MAX", 100)
    monkeypatch.setattr(office, "LINHAS_POR_ABA_MAX", 10)

    def proibido(*a, **k):
        raise AssertionError("a planilha grande não pode passar pelo anydoc")

    monkeypatch.setattr(anydoc, "to_document", proibido)
    r = lince.ler(p)
    vendas = r.markdown.split("## Resumo")[0]
    assert vendas.count("| item ") == 10 and "| item 10 |" not in vendas
    assert "[TRUNCADO: aba Vendas entregue com 10 linhas de dados" in vendas and "| Total | 30 |" in r.markdown
    assert any("aba Vendas" in x for x in r.metadados["partes_ilegiveis"])


def test_teto_de_celulas_do_documento_avisa_as_abas_seguintes(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "_CELULAS_ANYDOC_MAX", 0)
    monkeypatch.setattr(office, "CELULAS_TOTAL_MAX", 40)
    r = lince.ler(_planilha(tmp_path, linhas=10))
    assert "[TRUNCADO: aba Vendas entregue com" in r.markdown
    assert "## Resumo\n\n[TRUNCADO: aba Resumo: teto de 40 células do documento" in r.markdown


def test_celula_longa_e_cortada_com_a_conta_do_resto(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "_CELULAS_ANYDOC_MAX", 0)
    wb = openpyxl.Workbook()
    wb.active.append(["coluna"])
    wb.active.append(["x" * 1200])
    p = tmp_path / "longa.xlsx"
    wb.save(p)
    assert f"| {'x' * 500}…[+700 caracteres] |" in lince.ler(p).markdown


@pytest.mark.parametrize("com_dimensao", [True, False])
def test_celulas_declaradas_sem_ler_as_celulas(tmp_path, com_dimensao):
    p = _planilha(tmp_path, linhas=99)  # Vendas: 8 colunas × 100 linhas
    if not com_dimensao:
        q = tmp_path / "sem.xlsx"
        with zipfile.ZipFile(p) as zi, zipfile.ZipFile(q, "w") as zo:
            for n in zi.namelist():
                dados = zi.read(n)
                if n.startswith("xl/worksheets/"):
                    dados, trocas = re.subn(rb"<dimension[^>]*/>", b"", dados)
                    assert trocas == 1
                zo.writestr(n, dados)
        p = q
    total = office._celulas_declaradas(p)
    assert total >= 800 if com_dimensao else total > 0
    if com_dimensao:
        assert total < 1000  # 800 da aba Vendas mais as dimensões pequenas das outras
