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


def _reescrever_abas(p, destino, troca):
    """Copia o pacote aplicando `troca` ao XML de cada aba."""
    with zipfile.ZipFile(p) as zi, zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as zo:
        for n in zi.namelist():
            dados = zi.read(n)
            zo.writestr(n, troca(dados) if n.startswith("xl/worksheets/sheet") else dados)
    return destino


def test_em_fluxo_igual_ao_leitor_do_corpo_em_forma_irregular(tmp_path, monkeypatch):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([None, "a", "b"])  # primeira coluna vazia
    ws.append([None, 1, 2])
    ws.append([])  # linha vazia no meio
    ws.append([None, 3, 4, 5, 6])  # linha mais larga que o cabeçalho
    ws["H20"].number_format = "0.00"  # dimensão maior que os dados
    p = tmp_path / "irregular.xlsx"
    wb.save(p)
    normal = lince.ler(p).markdown
    monkeypatch.setattr(office, "_CELULAS_ANYDOC_MAX", 0)
    assert lince.ler(p).markdown == normal


@pytest.mark.parametrize("ref", ["A1", "A1:B2"])
def test_dimensao_que_mente_para_menos_nao_esconde_linhas(tmp_path, monkeypatch, ref):
    monkeypatch.setattr(office, "_CELULAS_ANYDOC_MAX", 0)
    menor = _reescrever_abas(
        _planilha(tmp_path, linhas=50), tmp_path / "menor.xlsx",
        lambda x: re.sub(rb'<dimension ref="[^"]*"', f'<dimension ref="{ref}"'.encode(), x),
    )  # fmt: skip
    md = lince.ler(menor).markdown
    assert "| item 49 |" in md and "| Total | 50 |" in md and "TRUNCADO" not in md


def test_dimensao_mentirosa_pequena_ainda_desvia_do_anydoc(tmp_path):
    p = _reescrever_abas(
        _planilha(tmp_path, linhas=50), tmp_path / "menor.xlsx",
        lambda x: re.sub(rb'<dimension ref="[^"]*"', b'<dimension ref="A1:B2"', x),
    )  # fmt: skip
    with zipfile.ZipFile(p) as z:
        xml = max(i.file_size for i in z.infolist() if i.filename.startswith("xl/worksheets/"))
    assert office._celulas_declaradas(p) >= xml // office._BYTES_POR_CELULA > 4


@pytest.mark.parametrize(
    "de,para", [("worksheets/", "dados/"), ("workbook.xml", "livro.xml"), ("sheet1.xml", "sheet1.dat")]
)
def test_partes_em_outro_lugar_contam_na_estimativa(tmp_path, de, para):
    p = _planilha(tmp_path, linhas=50)
    q = tmp_path / "movida.xlsx"
    with zipfile.ZipFile(p) as zi, zipfile.ZipFile(q, "w") as zo:
        for n in zi.namelist():  # o nome muda na parte e em toda referência a ela (relações e tipos de conteúdo)
            zo.writestr(n.replace(de, para), zi.read(n).replace(de.encode(), para.encode()))
        abas = sum(i.file_size // office._BYTES_POR_CELULA for i in zo.infolist() if "/dados/" in i.filename or
                   "/worksheets/s" in i.filename)  # fmt: skip
    assert office._celulas_declaradas(q) >= abas > 0 and lince.ler(q).markdown == lince.ler(p).markdown


def test_linha_larga_conta_a_tabela_inteira_no_teto(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "_CELULAS_ANYDOC_MAX", 0)
    monkeypatch.setattr(office, "CELULAS_POR_ABA_MAX", 10**9)  # só o teto do documento segura
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"], ws["XFD1"] = "cabeçalho", "longe"
    for n in range(2, 200):
        ws.cell(n, 1, n)
    p = tmp_path / "larga.xlsx"
    wb.save(p)
    r = lince.ler(p)
    assert len(r.markdown) < 6 * office.CELULAS_TOTAL_MAX  # a tabela de 16.384 colunas não passa do teto
    assert "[TRUNCADO: aba Sheet entregue com" in r.markdown


def test_varredura_de_celulas_vazias_tem_teto(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "_CELULAS_ANYDOC_MAX", 0)
    monkeypatch.setattr(office, "_VARREDURA_MAX", 1000)
    md = lince.ler(_planilha(tmp_path, linhas=300)).markdown
    assert "[TRUNCADO: aba Vendas entregue com" in md
    assert "## Resumo\n\n[TRUNCADO: aba Resumo: teto de 1000 células varridas do documento atingido" in md


def test_aba_sem_espaco_para_a_primeira_linha_avisa_ela_e_as_seguintes(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "_CELULAS_ANYDOC_MAX", 0)
    monkeypatch.setattr(office, "CELULAS_TOTAL_MAX", 44)
    wb = openpyxl.Workbook()
    wb.active.title = "Primeira"
    for _ in range(5):
        wb.active.append(list(range(8)))  # 40 células
    for nome in ("Larga", "Outra"):
        wb.create_sheet(nome).append(list(range(10)))
    p = tmp_path / "abas.xlsx"
    wb.save(p)
    md = lince.ler(p).markdown
    assert "## Larga\n\n[TRUNCADO: aba Larga: teto de 44 células do documento atingido antes dela" in md
    assert "## Outra\n\n[TRUNCADO: aba Outra:" in md


def test_teto_de_celulas_por_aba_corta_so_a_aba(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "_CELULAS_ANYDOC_MAX", 0)
    monkeypatch.setattr(office, "CELULAS_POR_ABA_MAX", 80)
    md = lince.ler(_planilha(tmp_path, linhas=30)).markdown
    assert "[TRUNCADO: aba Vendas entregue com 9 linhas de dados" in md and "| Total | 30 |" in md


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
    with zipfile.ZipFile(p) as z:
        estimativa = sum(i.file_size // office._BYTES_POR_CELULA for i in z.infolist() if "worksheets/s" in i.filename)
    assert total >= (800 if com_dimensao else estimativa) and total >= estimativa
