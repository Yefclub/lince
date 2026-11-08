"""Gráfico do Excel 97-2003 (.xls): tipo, título e séries entram na leitura; os valores já estão nas células."""

from pathlib import Path

import olefile

import lince
from lince import legados

REAIS = Path(__file__).parent / "fixtures" / "reais"


def test_tipo_e_series_de_cada_grafico():
    md = lince.ler(REAIS / "poi_WithTwoCharts.xls").markdown
    assert "**Gráfico (linhas): sem título**\nSéries: 1st Column, 2nd Column." in md
    assert "**Gráfico (área): sem título**\nSéries: Squares, Base Numbers." in md


def test_titulo_do_grafico():
    md = lince.ler(REAIS / "lince_grafico.xls").markdown
    assert "**Gráfico (barras): Vendas por região**" in md and "| Norte | 120 |" in md


def test_teto_de_graficos(monkeypatch):
    monkeypatch.setattr(legados, "GRAFICOS_XLS_MAX", 1)
    caminho = REAIS / "poi_WithTwoCharts.xls"
    with olefile.OleFileIO(caminho) as o:
        assert [g["tipo"] for g in legados.graficos_xls(o, caminho.stat().st_size)] == ["linhas"]


def test_xls_que_nao_e_ole_nao_ganha_parte_ilegivel(tmp_path):
    p = tmp_path / "exportado.xls"  # planilha do LibreOffice salva com a extensão antiga: o corpo lê pelo conteúdo
    p.write_bytes((REAIS / "lince_imagem_planilha.ods").read_bytes())
    r = lince.ler(p)
    assert "Pedidos" in r.markdown and not any("gráficos" in x for x in r.metadados.get("partes_ilegiveis", []))
