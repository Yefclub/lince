"""Associação dos pontos e dos conectores do Office ao que aparece no documento."""

import zipfile

import openpyxl
import pytest
from lxml import etree
from openpyxl.chart import BarChart, Reference
from pptx import Presentation
from pptx.chart.data import CategoryChartData, XyChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches

import lince

_C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"


def _pacote(origem, destino, mudar):
    with zipfile.ZipFile(origem) as zi, zipfile.ZipFile(destino, "w") as zo:
        for info in zi.infolist():
            dados = zi.read(info)
            if info.filename.endswith("charts/chart1.xml"):
                raiz = etree.fromstring(dados)
                mudar(raiz)
                dados = etree.tostring(raiz)
            zo.writestr(info, dados)
    return destino


@pytest.mark.parametrize("cache", [False, True])
def test_xlsx_lacunas_e_zero_no_grafico(tmp_path, cache):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Caixa d'Água"
    ws.append(["Mês", "Pedidos"])
    for linha in [("Jan", None), ("Fev", 0), ("Mar", None), ("Abr", 40), ("Mai", None)]:
        ws.append(linha)
    g = BarChart()
    g.add_data(Reference(ws, min_col=2, min_row=1, max_row=6), titles_from_data=True)
    g.set_categories(Reference(ws, min_col=1, min_row=2, max_row=6))
    ws.add_chart(g, "D1")
    p = tmp_path / "base.xlsx"
    wb.save(p)
    if cache:

        def gravar_cache(raiz):
            ref = raiz.find(f".//{_C}val/{_C}numRef")
            num = etree.SubElement(ref, _C + "numCache")
            etree.SubElement(num, _C + "ptCount", val="5")
            for idx, v in [(3, "40"), (1, "0")]:
                etree.SubElement(etree.SubElement(num, _C + "pt", idx=str(idx)), _C + "v").text = v

        p = _pacote(p, tmp_path / "cache.xlsx", gravar_cache)
    tabela = lince.ler(p).markdown.split("**Gráfico")[1]
    assert tabela.splitlines()[-5:] == ["| Jan |  |", "| Fev | 0 |", "| Mar |  |", "| Abr | 40 |", "| Mai |  |"]


def _slide():
    prs = Presentation()
    return prs, prs.slides.add_slide(prs.slide_layouts[6])


def test_pptx_tabela_esparsa_no_objeto_certo(tmp_path):
    prs, s = _slide()
    # Uma tabela comum com o mesmo cabeçalho e dois gráficos com o mesmo nome de série.
    t = s.shapes.add_table(2, 2, Inches(0), Inches(0), Inches(3), Inches(1)).table
    for linha, valores in zip(t.rows, [("Categoria", "Pedidos"), ("Tabela", "9")], strict=True):
        for celula, valor in zip(linha.cells, valores, strict=True):
            celula.text = valor
    for valores in [(None, 0, None, 40, None), (7, 8, 9, 10, 11)]:
        dados = CategoryChartData()
        dados.categories = ["Jan", "Fev", "Mar", "Abr", "Mai"]
        dados.add_series("Pedidos", valores)
        s.shapes.add_chart(XL_CHART_TYPE.LINE, Inches(0), Inches(2), Inches(4), Inches(3), dados)
    p = tmp_path / "pedidos.pptx"
    prs.save(p)
    md = lince.ler(p).markdown
    assert "| Jan |  |\n| Fev | 0 |\n| Mar |  |\n| Abr | 40 |\n| Mai |  |" in md
    assert md.index("| Tabela | 9 |") < md.index("| Fev | 0 |") < md.index("| Fev | 8 |")
    assert md.count("| Jan |") == 2


def test_pptx_dispersao_preserva_pares_de_cada_serie(tmp_path):
    prs, s = _slide()
    dados = XyChartData()
    for nome, pontos in [("Bomba Alfa", [(1.25, 8.75), (2.5, 16.4)]), ("Bomba Beta", [(10, 0)])]:
        serie = dados.add_series(nome)
        for x, y in pontos:
            serie.add_data_point(x, y)
    s.shapes.add_chart(XL_CHART_TYPE.XY_SCATTER, Inches(0), Inches(1), Inches(4), Inches(3), dados)
    p = tmp_path / "bombas.pptx"
    prs.save(p)
    md = lince.ler(p).markdown
    alfa, beta = md.split("**Bomba Alfa**")[1].split("**Bomba Beta**")
    assert "| 1.25 | 8.75 |" in alfa and "| 2.5 | 16.4 |" in alfa
    assert "| 10 | 0 |" in beta


@pytest.mark.parametrize(
    ("inicio", "fim", "texto", "mermaid"),
    [
        ("triangle", "none", "Destino → Origem", "-->"),
        ("none", "arrow", "Origem → Destino", "-->"),
        (None, None, "Origem — Destino", "---"),
        ("stealth", "triangle", "Origem ↔ Destino", "<-->"),
        ("oval", "diamond", "Origem — Destino", "---"),
    ],
)
def test_pptx_pontas_do_conector(tmp_path, inicio, fim, texto, mermaid):
    prs, s = _slide()
    a, b = [s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(2), Inches(1), Inches(1)) for x in (0, 5)]
    a.text, b.text = "Origem", "Destino"
    c = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(1), Inches(2), Inches(5), Inches(2))
    c.begin_connect(a, 3)
    c.end_connect(b, 1)
    for tag, estilo in [("headEnd", inicio), ("tailEnd", fim)]:
        if estilo:
            ponta = OxmlElement("a:" + tag)
            ponta.set("type", estilo)
            c.line._get_or_add_ln().append(ponta)
    p = tmp_path / "conector.pptx"
    prs.save(p)
    md = lince.ler(p).markdown
    assert f"- {texto}" in md
    primeiro, segundo = ("Destino", "Origem") if inicio == "triangle" else ("Origem", "Destino")
    assert f'n1["{primeiro}"]' in md and f'n2["{segundo}"]' in md and f"n1 {mermaid} n2" in md


def test_dispersao_com_lacunas_cruza_os_eixos_pelo_indice(tmp_path):
    prs, slide = _slide()
    dados = XyChartData()
    serie = dados.add_series("Ensaio")
    for x, y in [(1, 4), (2, 5), (3, 6)]:
        serie.add_data_point(x, y)
    slide.shapes.add_chart(XL_CHART_TYPE.XY_SCATTER, Inches(0), Inches(1), Inches(3), Inches(2), dados)
    p = tmp_path / "origem.pptx"
    prs.save(p)

    def esparsar(raiz):
        for eixo, idx in [("xVal", "0"), ("yVal", "1")]:
            ponto = raiz.find(f".//{_C}{eixo}//{_C}pt[@idx='{idx}']")
            ponto.getparent().remove(ponto)

    md = lince.ler(_pacote(p, tmp_path / "esparso.pptx", esparsar)).markdown
    assert "|  | 4 |\n| 2 |  |\n| 3 | 6 |" in md


def test_teto_de_pontos_preserva_o_slide_e_indica_o_grafico(tmp_path, monkeypatch):
    from lince import office

    prs, slide = _slide()
    dados = CategoryChartData()
    dados.categories = ["Jan", "Fev", "Mar"]
    dados.add_series("Medição", (None, 0, None))
    slide.shapes.add_chart(XL_CHART_TYPE.LINE, Inches(0), Inches(1), Inches(3), Inches(2), dados)
    slide.shapes.add_textbox(Inches(0), Inches(3), Inches(3), Inches(1)).text_frame.text = "Fim do relatório"
    p = tmp_path / "pontos.pptx"
    prs.save(p)
    monkeypatch.setattr(office, "_CELULAS_GRAFICOS_MAX", 5)
    r = lince.ler(p)
    assert "Fim do relatório" in r.markdown and "[tabela do gráfico não lida]" in r.markdown
    # Um complemento nativo ilegível deixa a parte e o motivo nos metadados.
    assert any("chart1.xml" in aviso and "teto" in aviso for aviso in r.metadados["partes_ilegiveis"])


def test_dispersao_preserva_os_titulos_dos_eixos_do_grupo(tmp_path):
    prs, slide = _slide()
    dados = XyChartData()
    dados.add_series("Medição").add_data_point(2, 8)
    slide.shapes.add_chart(XL_CHART_TYPE.XY_SCATTER, Inches(0), Inches(1), Inches(3), Inches(2), dados)
    p = tmp_path / "origem.pptx"
    prs.save(p)

    def titular(raiz):
        ids = [e.get("val") for e in raiz.findall(f".//{_C}scatterChart/{_C}axId")]
        for eixo in raiz.findall(f".//{_C}valAx"):
            i = ids.index(eixo.find(_C + "axId").get("val"))
            tx = etree.SubElement(etree.SubElement(eixo, _C + "title"), _C + "tx")
            rich = etree.SubElement(tx, _C + "rich")
            a = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
            par = etree.SubElement(rich, a + "p")
            etree.SubElement(etree.SubElement(par, a + "r"), a + "t").text = ["Horas (h)", "Energia (kWh)"][i]
        # A ordem física dos eixos não determina a coordenada.
        primeiro = raiz.find(f".//{_C}valAx")
        primeiro.getparent().append(primeiro)

    md = lince.ler(_pacote(p, tmp_path / "eixos.pptx", titular)).markdown
    assert "| Horas (h) | Energia (kWh) |" in md and "| 2 | 8 |" in md


@pytest.mark.parametrize("atributo", ["idx", "val"])
def test_cache_invalido_fica_isolado_no_proprio_grafico(tmp_path, atributo):
    prs, slide = _slide()
    for nome in ("Primeira", "Segunda"):
        dados = CategoryChartData()
        dados.categories = ["Jan", "Fev"]
        dados.add_series(nome, (2, 8))
        slide.shapes.add_chart(XL_CHART_TYPE.LINE, Inches(0), Inches(1), Inches(3), Inches(2), dados)
    p = tmp_path / "origem.pptx"
    prs.save(p)

    def sem_indice(raiz):
        tag = "pt" if atributo == "idx" else "ptCount"
        del raiz.find(f".//{_C}val//{_C}{tag}").attrib[atributo]

    r = lince.ler(_pacote(p, tmp_path / "campo.pptx", sem_indice))
    assert any("chart1.xml" in aviso and atributo in aviso for aviso in r.metadados["partes_ilegiveis"])
    assert "[tabela do gráfico não lida]" in r.markdown and "| Categoria | Segunda |" in r.markdown
    assert r.markdown.count("| Fev | 8 |") == 1


def test_orcamento_antecede_a_leitura_dos_intervalos_sem_cache(tmp_path, monkeypatch):
    from lince import office

    wb = openpyxl.Workbook()
    ws = wb.active
    for linha in [("Mês", "Primeira", "Segunda"), ("Jan", 2, 11), ("Fev", 8, 22)]:
        ws.append(linha)
    for col in [2, 3]:
        g = BarChart()
        g.add_data(Reference(ws, min_col=col, min_row=1, max_row=3), titles_from_data=True)
        g.set_categories(Reference(ws, min_col=1, min_row=2, max_row=3))
        ws.add_chart(g, "D1")
    p = tmp_path / "orcamento.xlsx"
    wb.save(p)
    intervalos = []
    original = office._valores_sem_cache

    def vigiar(path, refs):
        intervalos.extend(refs)
        return original(path, refs)

    monkeypatch.setattr(office, "_valores_sem_cache", vigiar)
    monkeypatch.setattr(office, "_CELULAS_GRAFICOS_MAX", 5)
    r = lince.ler(p)
    assert "| Fev | 8 |" in r.markdown.split("**Gráfico")[1]
    assert "[tabela do gráfico não lida]" in r.markdown.split("**Gráfico")[2]
    assert intervalos and all(r[1] <= 2 for r in intervalos)
    intervalos.clear()
    monkeypatch.setattr(office, "_CELULAS_GRAFICOS_MAX", 1)
    assert lince.ler(p).markdown.count("[tabela do gráfico não lida]") == 2 and not intervalos
