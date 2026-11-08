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
    novo, _, _ = pptx.aplicar(deck, [])
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


def test_substituir_encadeado_nao_amplifica_sem_limite(deck):
    c = pptx.extrair(deck)
    corpo = c["slides"][1]["formas"][1]["id"]
    novo, _, _ = pptx.aplicar(deck, [{"op": "texto", "alvo": corpo, "texto": "e" * 50}])
    with pytest.raises(OperacaoInvalida) as e:
        codigo.editar(novo, "d.pptx", [{"op": "substituir", "alvo": corpo, "buscar": "e", "trocar": "e" * 5000}] * 3)
    assert e.value.codigo == "texto-grande"


@pytest.mark.parametrize(
    "op",
    [
        {"op": "texto", "alvo": "sld:256/sp:2"},  # sem texto
        {"op": "texto", "alvo": "sld:256/sp:2/p:1,2", "texto": "x"},  # alvo mal formado
        {"op": "texto", "alvo": 256, "texto": "x"},  # alvo de outro tipo
        {"op": "celula_tabela", "alvo": "sld:258/sp:4/c:1", "texto": "x"},
        {"op": "valor_grafico", "alvo": "sld:259/sp:3/ser:0", "ponto": "a", "valor": 1},
        {"op": "valor_grafico", "alvo": "sld:259/sp:3/ser:0", "ponto": 0, "valor": "nan"},
        {"op": "valor_grafico", "alvo": "sld:256/sp:2", "ponto": 0, "valor": 1},  # forma que não é gráfico
        {"op": "notas", "slide": ["sld:256"], "texto": "x"},
        {"op": "ordem", "slides": "sld:256"},
        {"op": "texto", "alvo": "sld:256/sp:2", "texto": "x" * 200_000},
    ],
)
def test_pedido_malformado_vira_operacao_invalida_e_nao_erro_interno(deck, op):
    with pytest.raises(OperacaoInvalida) as e:
        codigo.editar(deck, "d.pptx", [op])
    assert e.value.indice == 0


def _paragrafo_xml(deck, parte, troca):
    saida = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(deck)) as o, zipfile.ZipFile(saida, "w") as z:
        for i in o.infolist():
            dados = o.read(i)
            if i.filename == parte:
                dados = troca(dados)
            z.writestr(i, dados)
    return saida.getvalue()


def test_quebra_de_linha_e_campo_sao_tratados(deck):
    # título com quebra de linha (a:br) e um parágrafo que só tem campo (número do slide)
    def troca(x):
        x = x.replace(
            b"<a:t>Relat\xc3\xb3rio trimestral</a:t></a:r>",
            b"<a:t>Relat\xc3\xb3rio</a:t></a:r><a:br/><a:r><a:t>trimestral</a:t></a:r>",
        )
        return x

    com_quebra = _paragrafo_xml(deck, "ppt/slides/slide1.xml", troca)
    c = pptx.extrair(com_quebra)
    titulo = c["slides"][0]["formas"][0]["paragrafos"][0]
    assert titulo["texto"] == "Relatório\vtrimestral"
    novo, rel = codigo.editar(
        com_quebra,
        "d.pptx",
        [
            {"op": "substituir", "alvo": "sld:256", "buscar": "trimestral", "trocar": "anual"},
            {"op": "texto", "alvo": titulo["id"], "texto": "Linha 1\vLinha 2"},
        ],
    )
    assert rel["codigo"]["slides"][0]["formas"][0]["paragrafos"][0]["texto"] == "Linha 1\vLinha 2"
    assert b"<a:br" in zipfile.ZipFile(io.BytesIO(novo)).read("ppt/slides/slide1.xml")


def test_remover_slide_apaga_o_conteudo_do_arquivo(deck):
    antes = set(zipfile.ZipFile(io.BytesIO(deck)).namelist())
    novo, rel = codigo.editar(deck, "d.pptx", [{"op": "remover_slide", "slide": "sld:259"}])
    depois = set(zipfile.ZipFile(io.BytesIO(novo)).namelist())
    sumiram = antes - depois
    assert "ppt/slides/slide4.xml" in sumiram
    assert any(n.startswith("ppt/charts/") for n in sumiram)  # o gráfico só dele saiu
    assert any(n.startswith("ppt/embeddings/") for n in sumiram)  # e a planilha embutida também
    assert not any(n.startswith("ppt/slideLayouts/") for n in sumiram)  # layout compartilhado fica
    assert pptx.integridade(novo) == [] and len(Presentation(io.BytesIO(novo)).slides) == 3


def test_grupo_de_formas_nao_herda_conteudo_dos_filhos(deck):
    prs = Presentation(io.BytesIO(deck))
    s = prs.slides[0]
    grupo = s.shapes.add_group_shape()
    caixa = grupo.shapes.add_textbox(Inches(1), Inches(5), Inches(3), Inches(1))
    caixa.text_frame.text = "Dentro do grupo"
    buf = io.BytesIO()
    prs.save(buf)
    c = pptx.extrair(buf.getvalue())
    tipos = {f["tipo"] for f in c["slides"][0]["formas"]}
    assert "grupo" in tipos
    dentro = next(
        f for f in c["slides"][0]["formas"] if f["tipo"] == "texto" and f["paragrafos"][0]["texto"] == "Dentro do grupo"
    )
    novo, rel = codigo.editar(
        buf.getvalue(), "d.pptx", [{"op": "texto", "alvo": dentro["id"], "texto": "Editado no grupo"}]
    )
    assert any(
        f.get("paragrafos", [{}])[0].get("texto") == "Editado no grupo" for f in rel["codigo"]["slides"][0]["formas"]
    )


def test_remover_slide_com_notas_leva_as_notas_junto(deck):
    novo, rel = codigo.editar(deck, "d.pptx", [{"op": "remover_slide", "slide": "sld:256"}])
    assert "/ppt/notesSlides/notesSlide1.xml" in rel["aplicadas"][0]["removidas"]
    assert pptx.integridade(novo) == []


def test_remover_slide_limpa_secoes_e_apresentacoes_personalizadas(deck):
    p14 = b"http://schemas.microsoft.com/office/powerpoint/2010/main"

    def troca(x):
        mostras = (
            b'<p:custShowLst><p:custShow name="Curta" id="0"><p:sldLst><p:sld r:id="rId10"/><p:sld r:id="rId11"/>'
            b'</p:sldLst></p:custShow><p:custShow name="So o grafico" id="1"><p:sldLst><p:sld r:id="rId11"/>'
            b"</p:sldLst></p:custShow></p:custShowLst>"
        )
        secoes = (
            b'<p:extLst><p:ext uri="{521415D9-36F7-43E2-AB2F-B90AF26B5E84}"><p14:sectionLst xmlns:p14="' + p14 + b'">'
            b'<p14:section name="Inicio" id="{00000000-0000-0000-0000-000000000001}"><p14:sldIdLst>'
            b'<p14:sldId id="256"/><p14:sldId id="257"/></p14:sldIdLst></p14:section>'
            b'<p14:section name="Numeros" id="{00000000-0000-0000-0000-000000000002}"><p14:sldIdLst>'
            b'<p14:sldId id="258"/><p14:sldId id="259"/></p14:sldIdLst></p14:section></p14:sectionLst>'
            b"</p:ext></p:extLst>"
        )
        x = x.replace(b"<p:defaultTextStyle>", mostras + b"<!-- comentario --><p:defaultTextStyle>")
        return x.replace(b"</p:presentation>", secoes + b"</p:presentation>")

    com_secoes = _paragrafo_xml(deck, "ppt/presentation.xml", troca)
    assert pptx.integridade(com_secoes) == []
    novo, _ = codigo.editar(com_secoes, "d.pptx", [{"op": "remover_slide", "slide": "sld:259"}])
    pres = zipfile.ZipFile(io.BytesIO(novo)).read("ppt/presentation.xml")
    assert b'id="259"' not in pres and b'r:id="rId11"' not in pres
    assert b'<p14:sldId id="258"/>' in pres  # o resto da seção fica
    assert b'name="Curta"' in pres and b"So o grafico" not in pres  # a mostra que ficou vazia sai
    assert pptx.integridade(novo) == [] and len(Presentation(io.BytesIO(novo)).slides) == 3
