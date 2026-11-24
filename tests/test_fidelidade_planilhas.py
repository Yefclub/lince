"""Controles públicos de células e objetos, com os mesmos valores nas duas rotas do XLSX."""

import re
import zipfile
from pathlib import Path

import openpyxl
import pytest
from lxml import etree

import lince

REAIS = Path(__file__).parent / "fixtures" / "reais"


def _movimento(tmp_path, grande):
    wb = openpyxl.Workbook()
    aba = wb.active
    aba.title = "Movimento"
    aba.append(["Identificador", "Percentual", "Moeda", "Antigo", "Sem resultado", "Texto", "Zero"])
    aba.append([42, 0.15, 1240.5, "=20+1", "=7+11", "00042", "=1-1"])
    aba["A2"].number_format = "00000"
    aba["B2"].number_format = "0%"
    aba["C2"].number_format = '"R$" #,##0.00'
    outra = wb.create_sheet("Outra")
    outra["A1"] = "Início"
    if grande:
        outra["Z10000"] = "Fim"
    oculta = wb.create_sheet("Oculta")
    oculta["A1"] = "=10+9"
    oculta.sheet_state = "hidden"
    origem, caminho = tmp_path / "origem.xlsx", tmp_path / "movimento.xlsx"
    wb.save(origem)
    with zipfile.ZipFile(origem) as entrada, zipfile.ZipFile(caminho, "w") as saida:
        for parte in entrada.namelist():
            dados = entrada.read(parte)
            if parte == "xl/worksheets/sheet1.xml":
                for formula, valor in [(b"20+1", b"98"), (b"1-1", b"0")]:
                    dados = dados.replace(formula + b"</f><v></v>", formula + b"</f><v>" + valor + b"</v>")
            saida.writestr(parte, dados)
    return lince.ler(caminho)


@pytest.mark.parametrize("grande", [False, True])
def test_mascara_e_unidade_quando_so_a_outra_aba_cresce(tmp_path, grande):
    leitura = _movimento(tmp_path, grande)
    assert "| 00042 | 15% | R$ 1,240.50 | 98 |" in leitura.markdown
    assert leitura.estrutura["perfil_tabular"][0]["amostra"][0][:4] == ["00042", "15%", "R$ 1,240.50", "98"]


@pytest.mark.parametrize("grande", [False, True])
def test_formula_identifica_cache_e_endereco_sem_resultado(tmp_path, grande):
    leitura = _movimento(tmp_path, grande)
    assert re.search(r"Movimento.*resultado salvo.*D2.*G2", leitura.markdown)
    assert re.search(r"Movimento.*sem resultado salvo.*E2", leitura.markdown)
    assert all(f in leitura.markdown for f in ("=20+1", "=7+11", "=1-1"))
    assert any("E2" in p and "sem resultado salvo" in p for p in leitura.cobertura.pendentes)
    assert "Oculta" not in leitura.markdown


@pytest.mark.parametrize("ext,sep", [("csv", ";"), ("tsv", "\t")])
def test_campo_vazio_permanece_vazio_na_tabela(tmp_path, ext, sep):
    arquivo = tmp_path / ("dados." + ext)
    arquivo.write_text(f"Região{sep}Código{sep}Obs\nPará{sep}{sep}a|b\nNorte{sep}00000{sep}fim\n", encoding="utf-8-sig")
    leitura = lince.ler(arquivo)
    assert "| Pará |  | a\\|b |" in leitura.markdown
    assert leitura.estrutura["perfil_tabular"][0]["amostra"][0] == ["Pará", "", "a|b"]


@pytest.mark.parametrize("arquivo", ["testEXCEL.xlsx", "testEXCEL.xls", "testCSV.csv"])
def test_perfil_reconhece_a_tabela_entre_titulo_e_rodape(arquivo):
    leitura = lince.ler(REAIS / arquivo)
    perfil = leitura.estrutura["perfil_tabular"][0]
    assert perfil["cabecalho"] == ["Number", "Square"]
    assert perfil["linhas_extraidas"] == perfil["linhas_observadas"] == 15
    assert perfil["amostra"][0] == ["1", "1"]
    assert "Sample" in leitura.markdown and "Written and saved" in leitura.markdown


def test_desenho_xlsb_entrega_grafico_e_texto_das_formas():
    leitura = lince.ler(REAIS / "poi_testVarious.xlsb")
    assert "test textbox" in leitura.markdown and "test WordArt" in leitura.markdown
    assert "Gráfico (barras)" in leitura.markdown
    assert "| Categoria | merchant1 | merchant2 |" in leitura.markdown
    assert "| March | 1 | 2 |" in leitura.markdown and "| April | 3 | 4 |" in leitura.markdown


@pytest.mark.parametrize("externo", [True, False])
def test_grafico_xlsb_ilegivel_preserva_texto_e_nao_publica_cache_orfao(tmp_path, externo):
    caminho = tmp_path / "desenho.xlsb"
    with zipfile.ZipFile(REAIS / "poi_testVarious.xlsb") as origem, zipfile.ZipFile(caminho, "w") as saida:
        for parte in origem.namelist():
            dados = origem.read(parte)
            if externo and parte == "xl/drawings/_rels/drawing1.xml.rels":
                dados = dados.replace(b'Target="../charts/chart1.xml"', b'TargetMode="External" Target="exemplo.xml"')
            if not externo and parte == "xl/charts/chart1.xml":
                dados = dados.replace(b"barChart", b"tipoSemSuporte")
            saida.writestr(parte, dados)
        saida.writestr("xl/charts/chart99.xml", origem.read("xl/charts/chart1.xml"))
    leitura = lince.ler(caminho)
    assert "test textbox" in leitura.markdown and "Gráfico (barras)" not in leitura.markdown
    assert any("gráfico" in parte for parte in leitura.metadados["partes_ilegiveis"])
    assert any("gráfico" in parte for parte in leitura.cobertura.pendentes)


def test_formula_sem_resultado_em_aba_sem_dados_e_limite_de_enderecos(tmp_path):
    wb = openpyxl.Workbook()
    aba = wb.active
    for n in range(1, 36):
        aba.cell(n, 1, "=1+1")
    arquivo = tmp_path / "formulas.xlsx"
    wb.save(arquivo)
    leitura = lince.ler(arquivo)
    assert "sem resultado salvo (35): A1 " in leitura.markdown
    assert "A32 " in leitura.markdown and ", …" in leitura.markdown and "A33" not in leitura.markdown


@pytest.mark.parametrize("grande", [False, True])
def test_formula_oculta_de_aba_protegida_so_informa_estado_e_endereco(tmp_path, grande):
    wb = openpyxl.Workbook()
    aba = wb.active
    aba["A1"] = "Valor"
    aba["A2"] = "=100*0.06175"
    aba["A2"].protection = openpyxl.styles.Protection(hidden=True)
    aba.protection.sheet = True
    if grande:
        wb.create_sheet("Grande")["Z10000"] = "Fim"
        wb["Grande"]["A1"] = "Início"
    caminho = tmp_path / "protegida.xlsx"
    wb.save(caminho)
    leitura = lince.ler(caminho)
    assert "sem resultado salvo (1): A2." in leitura.markdown
    assert "0.06175" not in leitura.markdown and not any("0.06175" in p for p in leitura.cobertura.pendentes)


def test_desenhos_xlsb_ignoram_forma_e_grupo_ocultos(tmp_path):
    caminho = tmp_path / "ocultos.xlsb"
    xdr = "{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}"
    with zipfile.ZipFile(REAIS / "poi_testVarious.xlsb") as entrada, zipfile.ZipFile(caminho, "w") as saida:
        for parte in entrada.namelist():
            dados = entrada.read(parte)
            if parte == "xl/drawings/drawing1.xml":
                raiz = etree.fromstring(dados)
                forma = raiz.find(f".//{xdr}sp")
                forma.find(f"*/{xdr}cNvPr").set("hidden", "true")
                grafico = raiz.find(f".//{xdr}graphicFrame")
                grupo = etree.SubElement(grafico.getparent(), xdr + "grpSp")
                prop = etree.SubElement(grupo, xdr + "nvGrpSpPr")
                etree.SubElement(prop, xdr + "cNvPr", hidden="1")
                grupo.append(grafico)
                dados = etree.tostring(raiz)
            saida.writestr(parte, dados)
    md = lince.ler(caminho).markdown
    assert "test WordArt" in md and "test textbox" not in md and "Gráfico (barras)" not in md


def test_xlsb_declara_caixa_de_texto_vml_sem_confundir_com_nota_de_celula(tmp_path):
    origem = REAIS / "poi_testVarious.xlsb"
    assert not lince.ler(origem).cobertura.pendentes
    caminho = tmp_path / "com-vml.xlsb"
    with zipfile.ZipFile(origem) as entrada, zipfile.ZipFile(caminho, "w") as saida:
        for parte in entrada.namelist():
            dados = entrada.read(parte)
            if parte == "xl/drawings/vmlDrawing1.vml":
                dados = dados.replace(b'ObjectType="Note"', b'ObjectType="Text"', 1)
            saida.writestr(parte, dados)
    leitura = lince.ler(caminho)
    assert "test textbox" in leitura.markdown and "Gráfico (barras)" in leitura.markdown
    assert any("vmlDrawing1.vml" in aviso for aviso in leitura.cobertura.pendentes)


def test_linha_esparsa_na_coluna_dos_dados_segue_no_perfil(tmp_path):
    arquivo = tmp_path / "esparso.csv"
    arquivo.write_text("Título,,\n,,\n,Cidade,Pedidos\n,Curitiba,12\n,,\n,Lapa,\n,,\nRodapé,,\n", encoding="utf-8")
    leitura = lince.ler(arquivo)
    perfil = leitura.estrutura["perfil_tabular"][0]
    assert perfil["amostra"] == [["Curitiba", "12"], ["Lapa"]] and perfil["linhas_extraidas"] == 2
