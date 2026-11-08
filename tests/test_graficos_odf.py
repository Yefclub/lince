"""Gráfico do LibreOffice (.ods, .odp, .odt): tipo, título e os dados que ele desenha entram na leitura."""

import zipfile
from pathlib import Path

import lince
from lince import office

REAIS = Path(__file__).parent / "fixtures" / "reais"


def test_grafico_da_planilha_e_da_apresentacao_do_libreoffice():
    for nome, tipo, titulo in (
        ("lince_grafico.ods", "barras", "Vendas por região"),
        ("lince_grafico.odp", "pizza", "Vendas"),
    ):
        md = lince.ler(REAIS / nome).markdown
        assert f"**Gráfico ({tipo}): {titulo}**" in md, nome
        assert "| Norte | 120 |" in md.split("**Gráfico")[1] and "| Sul | 80 |" in md.split("**Gráfico")[1], nome
        assert "Dados." not in md  # a referência de intervalo da célula não vira texto


def test_tabela_do_grafico_tem_teto_registrado(monkeypatch):
    monkeypatch.setattr(office, "_LINHAS_GRAFICO_MAX", 1)
    r = lince.ler(REAIS / "lince_grafico.ods")
    grafico = r.markdown.split("**Gráfico")[1]
    assert "| Norte | 120 |" in grafico and "| Sul |" not in grafico
    assert any("mais de 1 linhas" in x for x in r.metadados["partes_ilegiveis"])


def test_objeto_de_grafico_quebrado_nao_derruba_a_leitura(tmp_path):
    p = tmp_path / "quebrado.ods"
    with zipfile.ZipFile(REAIS / "lince_grafico.ods") as zi, zipfile.ZipFile(p, "w") as zo:
        for info in zi.infolist():
            dados = zi.read(info.filename)
            if info.filename.endswith("Object 1/content.xml"):
                dados = b"<office:document-content><quebrado"
            zo.writestr(info, dados)
    md = lince.ler(p).markdown
    assert "| Norte | 120 |" in md and "**Gráfico" not in md  # a tabela da planilha fica; o gráfico ilegível sai
