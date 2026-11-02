"""Documento como código (PPTX): ids estáveis, edição cirúrgica, tudo ou nada, nada perdido."""

import io
import zipfile

import openpyxl
import pytest
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches, Pt

from lince import codigo
from lince.codigo import OperacaoInvalida, pptx
from lince.limites import DocumentoRecusado


@pytest.fixture
def deck():
    prs = Presentation()
    capa = prs.slides.add_slide(prs.slide_layouts[0])
    capa.shapes.title.text = "Relatório trimestral"
    run = capa.shapes.title.text_frame.paragraphs[0].runs[0]
    run.font.size, run.font.bold = Pt(44), True
    capa.notes_slide.notes_text_frame.text = "Falar da meta"
    lista = prs.slides.add_slide(prs.slide_layouts[1])
    lista.shapes.title.text = "Destaques"
    lista.placeholders[1].text = "Receita cresceu\nCusto caiu"
    tab = prs.slides.add_slide(prs.slide_layouts[5])
    tab.shapes.title.text = "Indicadores"
    t = tab.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(6), Inches(1)).table
    t.cell(0, 0).text, t.cell(0, 1).text, t.cell(1, 0).text, t.cell(1, 1).text = (
        "Indicador",
        "Valor",
        "Capacidade",
        "3.500",
    )
    graf = prs.slides.add_slide(prs.slide_layouts[5])
    graf.shapes.title.text = "Receita"
    dados = CategoryChartData()
    dados.categories = ["2024", "2025", "2026"]
    dados.add_series("Receita", (10, 12, 15))
    graf.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(2), Inches(6), Inches(4), dados)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _forma(c, ordem, tipo):
    return next(f for f in c["slides"][ordem]["formas"] if f["tipo"] == tipo)


def test_codigo_expoe_a_estrutura_com_ids_do_arquivo(deck):
    c = pptx.extrair(deck)
    assert [s["id"] for s in c["slides"]] == ["sld:256", "sld:257", "sld:258", "sld:259"]
    assert c["slides"][0]["notas"] == "Falar da meta"
    assert [p["texto"] for p in c["slides"][1]["formas"][1]["paragrafos"]] == ["Receita cresceu", "Custo caiu"]
    assert _forma(c, 2, "tabela")["celulas"][1][1]["texto"] == "3.500"
    assert _forma(c, 3, "grafico")["grafico"]["series"][0]["valores"] == [10, 12, 15]
    assert len(c["sha256"]) == 64


def test_ida_e_volta_sem_operacoes_preserva_todas_as_partes(deck):
    novo, _ = pptx.aplicar(deck, [])
    assert codigo.comparar(deck, novo)["partes_alteradas"] == []


def test_troca_de_texto_mantem_a_formatacao_e_so_toca_o_slide(deck):
    c = pptx.extrair(deck)
    titulo = c["slides"][0]["formas"][0]["id"]
    novo, rel = codigo.editar(
        deck,
        "d.pptx",
        [{"op": "texto", "alvo": titulo, "se_texto": "Relatório trimestral", "texto": "Resultado do 3º trimestre"}],
        base_sha256=c["sha256"],
    )
    run = Presentation(io.BytesIO(novo)).slides[0].shapes.title.text_frame.paragraphs[0].runs[0]
    assert run.text == "Resultado do 3º trimestre" and run.font.size == Pt(44) and run.font.bold
    assert rel["diff"]["partes_alteradas"] == ["/ppt/slides/slide1.xml"]
    assert rel["diff"]["partes_identicas"] == len(zipfile.ZipFile(io.BytesIO(deck)).namelist()) - 1


def test_ids_sobrevivem_a_reordenar_e_editar(deck):
    c = pptx.extrair(deck)
    ids = [s["id"] for s in c["slides"]]
    novo, rel = codigo.editar(deck, "d.pptx", [{"op": "ordem", "slides": list(reversed(ids))}])
    assert [s["id"] for s in rel["codigo"]["slides"]] == list(reversed(ids))
    titulo = rel["codigo"]["slides"][-1]["formas"][0]
    assert (
        titulo["id"] == c["slides"][0]["formas"][0]["id"] and titulo["paragrafos"][0]["texto"] == "Relatório trimestral"
    )


def test_conflito_nao_aplica_nada(deck):
    c = pptx.extrair(deck)
    alvo = c["slides"][0]["formas"][0]["id"]
    with pytest.raises(OperacaoInvalida) as e:
        codigo.editar(
            deck,
            "d.pptx",
            [
                {"op": "notas", "slide": "sld:256", "texto": "outra nota"},
                {"op": "texto", "alvo": alvo, "se_texto": "Texto que mudou", "texto": "x"},
            ],
        )
    assert e.value.codigo == "conflito" and e.value.indice == 1 and e.value.alvo == alvo


def test_base_divergente_e_recusada(deck):
    with pytest.raises(OperacaoInvalida, match="leia de novo") as e:
        codigo.editar(deck, "d.pptx", [{"op": "ordem", "slides": []}], base_sha256="0" * 64)
    assert e.value.codigo == "base-divergente"


def test_alvo_inexistente_e_operacao_desconhecida(deck):
    with pytest.raises(OperacaoInvalida) as e:
        codigo.editar(deck, "d.pptx", [{"op": "texto", "alvo": "sld:256/sp:999", "texto": "x"}])
    assert e.value.codigo == "alvo-inexistente"
    with pytest.raises(OperacaoInvalida, match="use uma de"):
        codigo.editar(deck, "d.pptx", [{"op": "apagar_tudo"}])


def test_lista_notas_tabela_e_substituicao(deck):
    c = pptx.extrair(deck)
    corpo = c["slides"][1]["formas"][1]["id"]
    celula = _forma(c, 2, "tabela")["celulas"][1][1]["id"]
    novo, rel = codigo.editar(
        deck,
        "d.pptx",
        [
            {"op": "texto", "alvo": corpo, "texto": "Receita cresceu 12%\nCusto caiu 3%\nMargem recorde"},
            {"op": "notas", "slide": "sld:256", "se_texto": "Falar da meta", "texto": "Falar da meta de 2027"},
            {"op": "celula_tabela", "alvo": celula, "se_texto": "3.500", "texto": "7.000"},
            {"op": "substituir", "alvo": "sld:257", "buscar": "Destaques", "trocar": "Destaques do trimestre"},
        ],
    )
    novo_c = rel["codigo"]
    assert [p["texto"] for p in novo_c["slides"][1]["formas"][1]["paragrafos"]] == [
        "Receita cresceu 12%",
        "Custo caiu 3%",
        "Margem recorde",
    ]
    assert novo_c["slides"][0]["notas"] == "Falar da meta de 2027"
    assert _forma(novo_c, 2, "tabela")["celulas"][1][1]["texto"] == "7.000"
    assert novo_c["slides"][1]["formas"][0]["paragrafos"][0]["texto"] == "Destaques do trimestre"
    assert len(Presentation(io.BytesIO(novo)).slides) == 4


def test_valor_de_grafico_muda_no_grafico_e_na_planilha_embutida(deck):
    c = pptx.extrair(deck)
    serie = _forma(c, 3, "grafico")["grafico"]["series"][0]["id"]
    novo, rel = codigo.editar(deck, "d.pptx", [{"op": "valor_grafico", "alvo": serie, "ponto": 1, "valor": 12.5}])
    assert _forma(rel["codigo"], 3, "grafico")["grafico"]["series"][0]["valores"] == [10, 12.5, 15]
    planilha = rel["aplicadas"][0]["partes"][1].lstrip("/")
    ws = openpyxl.load_workbook(io.BytesIO(zipfile.ZipFile(io.BytesIO(novo)).read(planilha))).active
    assert [c.value for c in ws["B"]][1:4] == [10, 12.5, 15]


def test_remover_slide_e_aviso_de_texto_que_cresce(deck):
    c = pptx.extrair(deck)
    titulo = c["slides"][0]["formas"][0]["id"]
    novo, rel = codigo.editar(
        deck,
        "d.pptx",
        [
            {"op": "remover_slide", "slide": "sld:258"},
            {"op": "texto", "alvo": titulo, "texto": "Um título muito, muito mais comprido do que o original"},
        ],
    )
    assert [s["id"] for s in rel["codigo"]["slides"]] == ["sld:256", "sld:257", "sld:259"]
    assert rel["avisos"] and "transbordar" in rel["avisos"][0]
    assert pptx.integridade(novo) == []


def test_formato_sem_edicao_e_xml_hostil_sao_recusados(deck, tmp_path):
    with pytest.raises(codigo.FormatoNaoSuportado):
        codigo.editar(b"%PDF-1.7", "a.pdf", [{"op": "ordem", "slides": []}])
    hostil = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(deck)) as o, zipfile.ZipFile(hostil, "w") as z:
        for i in o.infolist():
            dados = o.read(i)
            if i.filename == "ppt/slides/slide1.xml":
                dados = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a SYSTEM "file:///etc/passwd">]>' + dados[38:]
            z.writestr(i, dados)
    with pytest.raises(DocumentoRecusado, match="DOCTYPE"):
        pptx.extrair(hostil.getvalue())
