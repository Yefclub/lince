"""Regressões públicas de regiões visuais com duas colunas e imagens compostas."""

from io import BytesIO

import pytest
from PIL import Image
from reportlab.pdfgen import canvas

from lince import Leitor, pdf


def test_formula_nao_incorpora_palavras_da_coluna_vizinha(tmp_path, monkeypatch):
    f = tmp_path / "medida.pdf"
    c = canvas.Canvas(str(f), pagesize=(420, 540))
    c.drawString(30, 480, "Medida no experimento")
    c.drawString(30, 340, "Q =")
    c.drawString(245, 340, "Legenda de outro quadro")
    c.drawString(60, 340, "A + B")
    c.drawString(180, 340, "(1)")
    c.drawString(340, 160, "A")
    c.drawString(30, 90, "x")
    c.save()
    original = pdf._extrair

    def extrair(dados, inicio):
        mds, ocr, itens = original(dados, inicio)
        mds[inicio] = "Medida no experimento\n\nQ = *A* + *B* (1)\n\nLegenda de outro quadro"
        return mds, ocr, itens

    monkeypatch.setattr(pdf, "_extrair", extrair)
    plano = Leitor().preparar(f)
    (tarefa,) = [t for t in plano.tarefas if t.tipo == "formula"]
    x0, y0, x1, y1 = tarefa.caixa
    assert x0 < 30 < 180 < x1 < 220
    assert 300 < y0 < 340 < y1 < 380


def test_formula_com_posicao_inconclusiva_conserva_nativo_com_resposta(tmp_path, monkeypatch):
    f = tmp_path / "ambigua.pdf"
    c = canvas.Canvas(str(f), pagesize=(420, 540))
    for y in (280, 460):
        c.drawString(45, y, "Q = 7 + 5 = 12 unidades")
    c.drawString(42, 105, "Conclusão do laboratório de metrologia.")
    c.save()
    texto = "Q = 7 + 5 = 12 unidades"
    extrair = pdf._extrair

    def mesmo_bloco(dados, inicio):
        mds, ocr, itens = extrair(dados, inicio)
        mds[inicio] = texto + "\n\n" + texto + "\n\nConclusão do laboratório de metrologia."
        return mds, ocr, itens

    monkeypatch.setattr(pdf, "_extrair", mesmo_bloco)
    leitor = Leitor()
    plano = leitor.preparar(f)
    assert plano.tarefas
    r = leitor.concluir(plano, {t.id: "Leitura parcial." for t in plano.tarefas})
    assert any("localização da fórmula" in p for p in r.cobertura.pendentes)
    assert r.markdown.count(texto) == 2 and "Leitura parcial." in r.markdown


def test_formula_com_ocorrencias_sobrepostas_fica_sem_caixa_unica():
    from types import SimpleNamespace

    from lince.pdf_formulas import localizar_formulas

    itens = [SimpleNamespace(text=c, x=12 * i, y=45, width=8, height=10) for i, c in enumerate("Q=Q=Q=Q=Q=Q=Q")]
    assert localizar_formulas(["Q = Q = Q = Q = Q"], itens, 400, lambda: None) == {}


def test_imagem_recortada_na_forma_usa_a_janela_visivel(tmp_path):
    from reportlab.lib.utils import ImageReader

    f = tmp_path / "painel.pdf"
    c = canvas.Canvas(str(f), pagesize=(420, 540))
    c.beginForm("mapa", 0, 0, 420, 540)
    c.drawImage(ImageReader(Image.new("RGB", (160, 100), "#4e79c1")), -120, -80, width=820, height=700)
    c.endForm()
    c.saveState()
    c.translate(16, 10)
    limite = c.beginPath()
    limite.rect(20, 200, 175, 200)
    c.clipPath(limite, stroke=0)
    c.doForm("mapa")
    c.restoreState()
    for i in range(9):
        c.drawString(231, 401 - i * 15, f"Horário do serviço {i} em outra coluna.")
    c.save()

    plano = Leitor().preparar(f)
    (tarefa,) = [t for t in plano.tarefas if t.tipo == "imagem"]
    x0, y0, x1, y1 = tarefa.caixa
    assert 28 <= x0 <= 37 and 200 <= y0 <= 210
    assert 211 <= x1 <= 220 and 410 <= y1 <= 418


def test_raster_contido_em_figura_legivel_nao_gera_segunda_leitura(tmp_path):
    from reportlab.lib.utils import ImageReader

    f = tmp_path / "figura.pdf"
    c = canvas.Canvas(str(f), pagesize=(600, 800))
    c.drawString(45, 750, "Leitura dos painéis em três escalas de pressão e volume.")
    c.drawString(45, 665, "Figura 1. Curvas para comparação.")
    c.rect(50, 425, 350, 210)
    c.line(75, 449, 355, 605)
    c.drawImage(ImageReader(Image.new("RGB", (50, 25), "red")), 140, 515, 90, 35)
    c.drawString(56, 400, "Os três eixos são definidos para cada faixa.")
    c.save()
    (tarefa,) = Leitor().preparar(f).tarefas
    assert tarefa.tipo == "desenho"

    with Image.open(BytesIO(tarefa.imagem)) as im:
        assert any(cor == (255, 0, 0) and n > 1000 for n, cor in im.convert("RGB").getcolors(im.width * im.height))


@pytest.mark.parametrize("rotacao", [0, 90, 180, 270])
def test_zoom_na_pagina_exibida_conserva_posicao_e_detalhe(tmp_path, rotacao):
    import pypdfium2
    from PIL import ImageChops

    from lince import imagens_das_paginas

    base = tmp_path / "base.pdf"
    f = tmp_path / "mapa.pdf"
    c = canvas.Canvas(str(base), pagesize=(400, 300))
    for x, y, cor in [(0, 0, "red"), (200, 0, "green"), (0, 150, "blue"), (200, 150, "orange")]:
        c.setFillColor(cor)
        c.rect(x, y, 200, 150, fill=1, stroke=0)
    c.save()
    with pypdfium2.PdfDocument(base) as doc:
        pg = doc[0]
        pg.set_cropbox(40, 30, 360, 270)
        pg.set_rotation(rotacao)
        pg.close()
        doc.save(f)
    with Image.open(BytesIO(imagens_das_paginas(f, [1], lado=512)[1])) as inteira:
        w, h = inteira.size
        esperado = inteira.crop((0, h // 2, w // 2, h))
    with Image.open(BytesIO(imagens_das_paginas(f, [1], lado=256, regiao=(0, 0.5, 0.5, 1))[1])) as detalhe:
        assert detalhe.size == esperado.size
        assert ImageChops.difference(detalhe.convert("RGB"), esperado.convert("RGB")).getbbox() is None
    with Image.open(BytesIO(imagens_das_paginas(f, [1], lado=1024, regiao=(0, 0.5, 0.5, 1))[1])) as zoom:
        assert max(zoom.size) == 1024
    for incorreta in [(0, 0, 1, 1.1), (0, 0.4, 1, 0.4), (False, 0, 1, 1), (0, 0, float("nan"), 1)]:
        with pytest.raises(ValueError):
            imagens_das_paginas(f, [1], regiao=incorreta)


def test_raster_com_texto_nativo_que_a_ia_nao_repetiu(tmp_path):
    from reportlab.lib.utils import ImageReader

    f = tmp_path / "grafica.pdf"
    c = canvas.Canvas(str(f), pagesize=(420, 540))
    c.drawImage(ImageReader(Image.new("RGB", (160, 100), "white")), 30, 200, 220, 150)
    c.drawString(45, 255, "Amostra de controle: 45 unidades.")
    c.drawString(45, 420, "Relatório de comparação dos resultados por laboratório.")
    c.save()
    leitor = Leitor()
    plano = leitor.preparar(f)
    (imagem,) = plano.tarefas
    assert imagem.tipo == "imagem"
    for respostas in ({}, {imagem.id: "Detalhe gráfico."}):
        assert "45 unidades" in leitor.concluir(plano, respostas).markdown
