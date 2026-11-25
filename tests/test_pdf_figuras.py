"""Recortes de figuras construídos pela entrada pública do leitor."""

import pytest
from PIL import Image
from reportlab.pdfgen import canvas

from lince import Leitor


def _desenhos(caminho):
    return [t.caixa for t in Leitor().preparar(caminho).tarefas if t.tipo == "desenho"]


def _cobre(caixa, x, y):
    return caixa[0] <= x <= caixa[2] and caixa[1] <= y <= caixa[3]


def _linhas(c, x, y, textos, tamanho=9, passo=11):
    c.setFont("Helvetica", tamanho)
    for i, texto in enumerate(textos):
        c.drawString(x, y - i * passo, texto)


def test_molduras_proximas_delimitam_cada_figura(tmp_path):
    caminho = tmp_path / "figuras-enquadradas.pdf"
    c = canvas.Canvas(str(caminho), pagesize=(600, 800))
    for i, (x, y) in enumerate(((90, 500), (312, 500), (90, 280)), 1):
        c.rect(x, y, 200, 166)
        _linhas(c, x + 8, y + 150, [f"Figure {i}. Título do quadro"])
        c.lines([(x + 22, y + 117, x + 174, y + 55), (x + 22, y + 74, x + 174, y + 127)])
        _linhas(c, x + 8, y + 30, ["Nota: origem do dado", "Última linha do quadro"])
    _linhas(c, 312, 420, ["Prosa da coluna vizinha", "Mais contexto da seção"])
    c.save()

    caixas = _desenhos(caminho)
    assert len(caixas) == 3
    for x, y in ((90, 500), (312, 500), (90, 280)):
        (caixa,) = [g for g in caixas if _cobre(g, x + 20, y + 150)]
        assert _cobre(caixa, x + 20, y + 19)
        assert not _cobre(caixa, 312, 420)


def test_legenda_externa_inteira_sem_atravessar_coluna(tmp_path):
    caminho = tmp_path / "legenda-externa.pdf"
    c = canvas.Canvas(str(caminho), pagesize=(600, 800))
    c.setFillColorRGB(0.3, 0.4, 0.7)
    c.roundRect(319, 345, 217, 168, 4, fill=1, stroke=0)
    c.setFillColorRGB(0, 0, 0)
    _linhas(
        c,
        312,
        324,
        ["FIG. 2. Detalhes.", "A legenda cobre todos os resultados a partir da amostra usada.", "Fim da legenda."],
    )
    _linhas(c, 52, 346, ["Texto da outra coluna e sua última palavra."])
    _linhas(c, 312, 267, ["Novo parágrafo fora da figura, ainda na coluna direita."])
    c.save()

    (caixa,) = _desenhos(caminho)
    assert _cobre(caixa, 312, 302)
    assert _cobre(caixa, 565, 313)
    assert caixa[0] > 292
    assert caixa[1] > 275


def test_moldura_compacta_em_volta_de_prosa_fica_nativa(tmp_path):
    caminho = tmp_path / "moldura-e-prosa.pdf"
    c = canvas.Canvas(str(caminho), pagesize=(600, 800))
    c.rect(91, 500, 155, 76)
    _linhas(c, 98, 559, ["Observações sobre a viagem", "Próximos passos de revisão", "Organização dos horários"])
    c.save()

    assert not _desenhos(caminho)
    assert "Observações sobre a viagem" in Leitor().ler(caminho).markdown


def test_reune_partes_de_formulario_sem_delimitadores_proprios(tmp_path):
    caminho = tmp_path / "formulario.pdf"
    c = canvas.Canvas(str(caminho), pagesize=(600, 800))
    c.setFillColorRGB(0.8, 0.8, 0.8)
    c.roundRect(35, 80, 515, 320, 4, fill=1, stroke=0)
    c.roundRect(35, 555, 515, 150, 4, fill=1, stroke=0)
    c.setFillColorRGB(0, 0, 0)
    _linhas(c, 45, 529, ["Último item da seção inicial", "Dados complementares"], passo=32)
    _linhas(c, 45, 481, ["Informação entre as duas partes"])
    _linhas(c, 45, 457, ["Primeiro item da seção final", "Descrição dos campos"], passo=32)
    c.save()

    (caixa,) = _desenhos(caminho)
    assert _cobre(caixa, 45, 481)
    assert _cobre(caixa, 45, 695) and _cobre(caixa, 45, 96)


def _poligono(c, x, y, w, h):
    p = c.beginPath()
    p.moveTo(x, y + h * 0.5)
    p.lineTo(x + w * 0.45, y + h)
    p.lineTo(x + w, y + h * 0.7)
    p.lineTo(x + w * 0.8, y)
    p.lineTo(x + w * 0.3, y + h * 0.2)
    p.close()
    c.drawPath(p)


def test_grafico_compacto_atras_de_texto_e_painel_de_fundo(tmp_path):
    caminho = tmp_path / "painel-e-grafico.pdf"
    c = canvas.Canvas(str(caminho), pagesize=(600, 800))
    c.setFillColorRGB(0.9, 0.9, 0.9)
    c.rect(38, 46, 524, 704, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    _linhas(c, 56, 480, [f"Prosa nativa no painel, linha {i}." for i in range(8)], passo=27)
    for i in range(3):
        _poligono(c, 120 + 3 * i, 590 + 3 * i, 101 - 6 * i, 94 - 6 * i)
        _poligono(c, 390 + 3 * i, 590 + 3 * i, 101 - 6 * i, 94 - 6 * i)
    _linhas(c, 75, 705, ["Gráfico 2.1 – Radar de medidas"])
    _linhas(c, 99, 671, ["Série A", "Série B", "Série C"], tamanho=7)
    _linhas(c, 73, 569, ["Nota: primeiros dados", "com descrição mais longa para a segunda linha", "e fim."], 7, 9)
    c.rect(380, 310, 70, 70, stroke=1, fill=0)
    c.save()

    (caixa,) = _desenhos(caminho)
    assert _cobre(caixa, 75, 705)
    assert _cobre(caixa, 73, 551)
    assert not _cobre(caixa, 390, 610)
    resultado = Leitor().ler(caminho)
    assert "Prosa nativa no painel" in resultado.markdown


def test_painel_preenchido_e_unico_elemento_grafico(tmp_path):
    caminho = tmp_path / "forma-colorida.pdf"
    c = canvas.Canvas(str(caminho), pagesize=(600, 800))
    c.setFillColorRGB(0.1, 0.4, 0.7)
    c.rect(50, 70, 500, 660, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    _linhas(c, 56, 748, ["Amostra da cor aplicada ao produto."])
    c.save()

    (caixa,) = _desenhos(caminho)
    assert _cobre(caixa, 70, 400) and _cobre(caixa, 535, 400)


def test_panorama_separa_mapa_e_colunas_laterais(tmp_path):
    caminho = tmp_path / "panorama.pdf"
    raster = tmp_path / "mapa.png"
    Image.new("RGB", (200, 140), "lightblue").save(raster)
    c = canvas.Canvas(str(caminho), pagesize=(1600, 500))
    c.drawImage(str(raster), 90, 0, 790, 500)
    c.setFillColorRGB(0.9, 0.9, 0.85)
    c.rect(880, 0, 720, 500, fill=1, stroke=0)
    c.setFillColorRGB(0.13, 0.3, 0.45)
    c.rect(0, 0, 90, 500, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    _linhas(c, 10, 380, ["Aviso lateral."], tamanho=8)
    for x in (892, 1250):
        c.setFillColorRGB(0.3, 0.3, 0.3)
        c.roundRect(x, 470, 336, 16, 3, fill=1, stroke=0)
        c.setFillColorRGB(1, 1, 1)
        _linhas(c, x + 3, 473, ["Painel de informações"])
        c.setFillColorRGB(0, 0, 0)
        _linhas(c, x + 3, 450, ["Conteúdo da coluna", "Fim das informações"])
    c.save()

    leitor = Leitor()
    plano = leitor.preparar(caminho)
    (mapa,) = [t.caixa for t in plano.tarefas if t.tipo == "imagem"]
    paineis = [t.caixa for t in plano.tarefas if t.tipo == "desenho"]
    assert mapa[0] == 0 and 880 <= mapa[2] < 892
    assert len(paineis) == 2
    for x in (895, 1253):
        (caixa,) = [g for g in paineis if _cobre(g, x, 473)]
        assert _cobre(caixa, x, 439)
    assert "Aviso lateral." in leitor.concluir(plano, {}).markdown


def test_faixa_ilustrada_de_rodape_divide_a_figura_sem_perder_aviso(tmp_path):
    caminho = tmp_path / "mapa-com-faixa.pdf"
    raster = tmp_path / "promocional.png"
    Image.new("RGB", (80, 40), "lightblue").save(raster)
    c = canvas.Canvas(str(caminho), pagesize=(840, 600))
    c.setFillColorRGB(0.2, 0.2, 0.6)
    c.rect(0, 0, 840, 100, stroke=0, fill=1)
    c.drawImage(str(raster), 450, 0, 190, 100)
    c.setFillColorRGB(0.8, 0.8, 0.8)
    c.roundRect(5, 104, 830, 490, 8, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    _linhas(c, 65, 569, ["Título do mapa"])
    _linhas(c, 65, 111, ["Aviso de uso do mapa"])
    _linhas(c, 65, 48, ["Texto da faixa ilustrada"])
    c.save()

    tarefas = Leitor().preparar(caminho).tarefas
    (mapa,) = [t.caixa for t in tarefas if t.tipo == "desenho"]
    (faixa,) = [t.caixa for t in tarefas if t.tipo == "imagem"]
    assert mapa[1] == faixa[3] == 100
    assert _cobre(mapa, 65, 111) and _cobre(mapa, 65, 569)
    assert _cobre(faixa, 65, 48) and not _cobre(faixa, 65, 111)


@pytest.mark.parametrize("com_clip", [True, False])
def test_residuo_so_e_descartado_se_nao_pinta_fora_do_quadro(tmp_path, com_clip):
    caminho = tmp_path / "limite-de-pintura.pdf"
    c = canvas.Canvas(str(caminho), pagesize=(600, 800))
    c.rect(250, 360, 235, 210)
    _linhas(c, 259, 550, ["Figure 1. Medição de pressão"])
    _linhas(c, 259, 384, ["Dados revisados", "Fim do quadro"])
    c.saveState()
    if com_clip:
        recorte = c.beginPath()
        recorte.rect(260, 394, 210, 145)
        c.clipPath(recorte, stroke=0, fill=0)
    c.setLineWidth(2)
    p = c.beginPath()
    p.moveTo(267, 300)
    p.lineTo(314, 512)
    p.lineTo(397, 425)
    p.lineTo(461, 657)
    c.drawPath(p)
    c.restoreState()
    c.save()

    caixas = _desenhos(caminho)
    assert len(caixas) == (1 if com_clip else 2)
    assert any(_cobre(g, 259, 550) and _cobre(g, 259, 373) for g in caixas)


def test_rotulos_alinhados_acima_do_diagrama_com_legenda_abaixo(tmp_path):
    caminho = tmp_path / "arquiteturas.pdf"
    c = canvas.Canvas(str(caminho), pagesize=(600, 800))
    c.lines([(70, 405, 250, 620), (74, 618, 238, 420)])
    for x, nome in [(79, "VGG-19"), (132, "34 camadas"), (196, "Residual")]:
        _linhas(c, x, 637, [nome], tamanho=7)
        _linhas(c, x, 626, ["entrada"], tamanho=5)
    _linhas(c, 58, 390, ["Figure 3. Arquiteturas", "Legenda completa do desenho."])
    _linhas(c, 317, 630, ["Prosa da coluna que não entra no recorte."])
    c.save()
    (cx,) = _desenhos(caminho)
    assert _cobre(cx, 81, 638) and _cobre(cx, 211, 638) and cx[2] < 317


def test_moldura_com_titulo_nao_adota_titulo_da_moldura_de_baixo(tmp_path):
    caminho = tmp_path / "quadros.pdf"
    c = canvas.Canvas(str(caminho), pagesize=(600, 800))
    for i, (x, y, largura) in enumerate(((70, 530, 405), (70, 355, 194)), 1):
        c.rect(x, y, largura, 155)
        _linhas(c, x + 7, y + 142, [f"Figure {i}. Quadro"])
        c.line(x + 12, y + 52, x + largura - 12, y + 116)
        _linhas(c, x + 7, y + 24, ["Note: origem da medida", "fim da nota."])
    c.save()
    caixas = _desenhos(caminho)
    cima = next(cx for cx in caixas if _cobre(cx, 88, 673))
    assert len(caixas) == 2 and not _cobre(cima, 91, 498)
