"""Leiautes de boletim: fundos de seção que não são figura e colunas lado a lado lidas uma de cada vez."""

import io

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from lince import Leitor

LATERAL = [
    ("Pessoas ocupadas", ["Estimada em cem mil", "pessoas no trimestre, com", "alta de dois por cento",
                          "sobre o mesmo período", "do ano anterior e sem", "variação significativa."]),
    ("Pessoas desocupadas", ["Estimada em dez mil", "pessoas no trimestre, com", "queda de cinco por cento",
                             "sobre o mesmo período", "do ano anterior e com", "variação significativa."]),
    ("Nível da ocupação", ["Estimado em cinquenta e", "oito por cento, com alta", "de três décimos sobre o",
                           "mesmo período do ano", "anterior e sem nenhuma", "variação significativa."]),
]  # fmt: skip
CORPO = [
    "A taxa de desocupação ficou em cinco vírgula seis por cento no trimestre encerrado em setembro,",
    "a menor da série histórica iniciada em dois mil e doze, com recuo em todas as grandes regiões do",
    "país e queda mais forte no Nordeste, onde o mercado de trabalho reagiu ao comércio e aos serviços.",
]


def _linhas(c, x, y, textos, tamanho=9, passo=11, fonte="Helvetica"):
    c.setFont(fonte, tamanho)
    for i, texto in enumerate(textos):
        c.drawString(x, y - i * passo, texto)


def _variante(tema):
    return [linha.replace("desocupação", tema).replace("Nordeste", "Sul").replace("série", "base") for linha in CORPO]


def _boletim(caminho):
    """Uma página de boletim: faixas de cabeçalho atrás do título, caixas de canto arredondado na coluna lateral, a
    moldura das seções do corpo e um gráfico de linha no meio dela."""
    c = canvas.Canvas(str(caminho), pagesize=(595, 842))
    # o cabeçalho: uma caixa de canto arredondado, uma faixa larga e uma tira, cada uma atrás de uma linha de texto
    for x, y, largura, altura, texto, tamanho in (
        (160, 763, 412, 58, "Boletim", 20),
        (445, 761, 127, 34, "Trabalho", 11),
        (25, 768, 547, 26, "Pesquisa trimestral do trabalho", 13),
        (160, 745, 410, 17, "Terceiro trimestre", 10),
    ):
        c.setFillColorRGB(0.3, 0.5, 0.8)
        c.roundRect(x, y, largura, altura, 8, stroke=0, fill=1)
        c.setFillColorRGB(1, 1, 1)
        _linhas(c, x + 12, y + altura / 2 - tamanho / 3, [texto], tamanho, fonte="Helvetica-Bold")
    c.setFillColorRGB(0.8, 0.9, 1)
    for k, (titulo, linhas) in enumerate(LATERAL):
        topo = 740 - 180 * k
        c.setFillColorRGB(0.8, 0.9, 1)
        c.roundRect(25, topo - 160, 155, 160, 12, stroke=0, fill=1)
        c.setFillColorRGB(0, 0, 0)
        _linhas(c, 32, topo - 20, [titulo], 12, fonte="Helvetica-Bold")
        _linhas(c, 32, topo - 44, linhas)
    c.setStrokeColorRGB(0.2, 0.6, 0.4)
    c.roundRect(160, 40, 412, 710, 14, stroke=1, fill=0)
    c.setFillColorRGB(0, 0, 0)
    _linhas(c, 175, 720, ["Taxa de desocupação"], 14, fonte="Helvetica-Bold")
    _linhas(c, 175, 698, CORPO, 8)  # linhas de base próximas das da coluna lateral, sem coincidir
    c.setStrokeColorRGB(0.7, 0.7, 0.7)
    c.rect(180, 420, 370, 220)
    c.setStrokeColorRGB(0.2, 0.4, 0.8)
    pontos = [(200 + 25 * k, 600 - 9 * k - 12 * (k % 3)) for k in range(14)]
    c.lines([(*a, *b) for a, b in zip(pontos, pontos[1:], strict=False)])
    c.setFillColorRGB(0, 0, 0)
    for k, (x, y) in enumerate(pontos):
        _linhas(c, x - 6, y + 8, [f"{8.7 - 0.2 * k:.1f}".replace(".", ",")], 7)
        c.saveState()
        c.translate(x + 2, 426)
        c.rotate(90)
        _linhas(c, 0, 0, [f"{k % 4 + 1}º trimestre"], 6)
        c.restoreState()
    _linhas(c, 175, 380, ["Rendimento médio real"], 14, fonte="Helvetica-Bold")
    _linhas(c, 175, 358, _variante("renda"), 8)
    _linhas(c, 175, 302, _variante("informalidade"), 8)
    _linhas(c, 175, 242, _variante("subocupação"), 8)
    c.save()


def test_fundos_de_secao_nao_viram_figura_e_o_grafico_vira(tmp_path):
    """A moldura das seções e as caixas da coluna lateral envolvem prosa; as faixas do cabeçalho, uma linha cada.
    Nenhuma delas é figura: só o gráfico vai para a leitura visual, uma vez."""
    p = tmp_path / "boletim.pdf"
    _boletim(p)

    desenhos = [t.caixa for t in Leitor().preparar(p).tarefas if t.tipo == "desenho"]

    assert len(desenhos) == 1
    l, b, r, t = desenhos[0]
    assert l >= 160 and r <= 560 and b >= 400 and t <= 660  # o gráfico, não a página


def test_coluna_lateral_com_caixas_e_lida_caixa_por_caixa(tmp_path):
    """A coluna lateral estreita (um quarto da página) ao lado do corpo: o extrator emenda as linhas das duas na mesma
    altura; cada caixa sai inteira, e o parágrafo do corpo também."""
    p = tmp_path / "boletim.pdf"
    _boletim(p)

    md = Leitor().ler(p).markdown

    for _, linhas in LATERAL:
        assert " ".join(linhas) in md
    assert " ".join(CORPO) in md
    # o gráfico fica na seção dele, e não depois da linha da coluna lateral que está logo acima dele
    assert md.index(" ".join(CORPO)) < md.index("[figura") < md.index("Pessoas desocupadas")


def _avisos(caminho):
    """Uma imagem grande em cima e, embaixo, uma faixa com quatro avisos lado a lado, cada um começando pelo título em
    negrito na mesma linha do texto, como num folheto de parque."""
    avisos = [
        ("Mantenha distância.", ["Fique a cem", "metros dos ursos e dos lobos e a", "vinte e cinco dos outros animais",
                                 "da reserva em qualquer caso."]),
        ("Ande em grupo.", ["Em trilhas", "com pouca visibilidade faça barulho", "para não surpreender os animais",
                            "e leve sempre o spray de pimenta."]),
        ("Use os recuos.", ["Se vir um", "animal da estrada não pare na pista", "e siga até o próximo recuo antes",
                            "de descer do carro com cuidado."]),
        ("Dê passagem.", ["Mais de cem", "animais morrem atropelados todo ano", "nas estradas do parque e o limite",
                          "de velocidade aqui é bem baixo."]),
    ]  # fmt: skip
    c = canvas.Canvas(str(caminho), pagesize=(792, 1224))
    imagem = io.BytesIO()
    Image.effect_noise((400, 300), 40).convert("RGB").save(imagem, "PNG")
    c.drawImage(ImageReader(io.BytesIO(imagem.getvalue())), 20, 300, 750, 880)
    c.setFillColorRGB(0.8, 0.9, 1)
    c.rect(12, 40, 768, 150, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    for k, (titulo, linhas) in enumerate(avisos):
        x = 30 + 190 * k
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x, 170, titulo)
        c.setFont("Helvetica", 10)
        c.drawString(x + c.stringWidth(titulo + " ", "Helvetica-Bold", 10), 170, linhas[0])
        _linhas(c, x, 157, linhas[1:], 10, 13)
    c.save()
    return [f"**{titulo}** {' '.join(linhas)}" for titulo, linhas in avisos]


def test_avisos_lado_a_lado_sao_lidos_um_de_cada_vez(tmp_path):
    p = tmp_path / "avisos.pdf"
    avisos = _avisos(p)

    md = Leitor().ler(p).markdown

    for aviso in avisos:
        assert aviso in md


def _grafico(c, x, y, titulo, quadro_interno=True):
    """Gráfico com fundo próprio (título, unidade, rótulos, fonte) e, dentro, o quadro da área de traçado."""
    c.setFillColorRGB(0.97, 0.97, 0.97)
    c.rect(x, y, 300, 170, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    _linhas(c, x + 8, y + 155, [titulo, "milhões de barris"], 8)
    if quadro_interno:  # com rótulos dentro, como "previsão" e o nome das séries na área de traçado da EIA
        c.rect(x + 20, y + 30, 270, 100)
        _linhas(c, x + 200, y + 120, ["previsão", "estoque total de gasolina", "faixa mensal de 2019 a 2023"], 6, 30)
    pontos = [(x + 25 + 20 * k, y + 50 + 25 * ((k * 7) % 3)) for k in range(13)]
    c.lines([(*a, *b) for a, b in zip(pontos, pontos[1:], strict=False)])
    for k in range(0, 13, 3):
        _linhas(c, x + 22 + 20 * k, y + 18, [f"Jan {2019 + k // 3}"], 6)
    _linhas(c, x + 8, y + 6, ["Fonte dos dados: Administração de Informação de Energia, novembro de 2024"], 6)


def test_area_de_tracado_dentro_do_grafico_nao_vira_segunda_figura(tmp_path):
    """O quadro da área de traçado fica dentro do recorte do gráfico inteiro: uma tarefa só, com título e eixos."""
    p = tmp_path / "graficos.pdf"
    c = canvas.Canvas(str(p), pagesize=(612, 792))
    for k in range(3):
        _grafico(c, 160, 560 - 250 * k, f"Estoques de gasolina {k + 1}")
    c.save()

    desenhos = [t.caixa for t in Leitor().preparar(p).tarefas if t.tipo == "desenho"]

    assert len(desenhos) == 3
    for caixa in desenhos:
        assert caixa[2] - caixa[0] >= 280  # o gráfico inteiro, não a área de traçado


def test_quadro_de_figura_com_notas_longas_continua_figura(tmp_path):
    """O quadro com "Figura 10." no topo e notas de rodapé longas dentro é a figura, não moldura de página."""
    p = tmp_path / "figura.pdf"
    c = canvas.Canvas(str(p), pagesize=(612, 792))
    c.roundRect(54, 73, 505, 420, 6)
    _linhas(c, 64, 475, ["Figura 10.", "Variação do número de pessoas em pobreza depois de cada benefício"], 9)
    for k in range(12):
        c.setFillColorRGB(0.2, 0.3, 0.7)
        c.rect(300, 430 - 22 * k, 20 + 15 * k, 14, stroke=0, fill=1)
        c.setFillColorRGB(0, 0, 0)
        _linhas(c, 180, 433 - 22 * k, [f"Benefício {k + 1}"], 8)
    notas = [
        f"Nota {k + 1}: o benefício inclui a parcela restituível do crédito e as transferências do programa."
        for k in range(9)
    ]
    _linhas(c, 64, 160, notas, 7, 9)
    c.save()

    desenhos = [t.caixa for t in Leitor().preparar(p).tarefas if t.tipo == "desenho"]

    assert len(desenhos) == 1 and desenhos[0][3] >= 470


def test_figura_fica_depois_do_paragrafo_dela_mesmo_com_frase_repetida(tmp_path):
    """A frase que fecha o parágrafo acima da figura aparece também em outro parágrafo: as duas linhas acima dela
    acham o lugar certo."""
    p = tmp_path / "ancora.pdf"
    c = canvas.Canvas(str(p), pagesize=(612, 792))
    fim = "em relação ao trimestre anterior, não houve variação significativa."
    _linhas(
        c, 72, 720, ["O rendimento médio subiu quatro por cento sobre o mesmo período do ano anterior;", fim], 10, 13
    )
    _grafico(c, 150, 480, "Rendimento médio real", quadro_interno=False)
    _linhas(
        c, 72, 420, ["O emprego com carteira subiu dois por cento sobre o mesmo período do ano anterior;", fim], 10, 13
    )
    c.save()

    md = Leitor().ler(p).markdown

    assert md.index("O rendimento médio") < md.index("[figura") < md.index("O emprego com carteira")


def test_tabela_com_grade_e_prosa_nas_celulas_dentro_de_moldura_continua_lida(tmp_path):
    """A moldura de canto arredondado com muitas linhas de prosa seria fundo de layout; com os traços verticais de uma
    grade dentro, é uma tabela de texto, e a região dela continua indo para a leitura."""
    p = tmp_path / "tabela.pdf"
    c = canvas.Canvas(str(p), pagesize=(612, 792))
    c.roundRect(50, 300, 510, 330, 6)
    _linhas(c, 60, 610, ["DIFERENÇAS ENTRE AS MEDIDAS"], 10, fonte="Helvetica-Bold")
    for k in range(5):
        y = 580 - 55 * k
        _linhas(c, 60, y, [f"Critério {k + 1}"], 8)
        _linhas(
            c,
            160,
            y,
            [
                "Famílias com renda abaixo da linha oficial",
                "medida antes dos impostos e benefícios",
                "do ano anterior ao da coleta.",
            ],
            8,
            10,
        )
        _linhas(
            c,
            360,
            y,
            [
                "Unidades de recursos com os benefícios",
                "não monetários e as despesas com saúde",
                "e transporte descontadas da renda.",
            ],
            8,
            10,
        )
        c.line(50, y + 12, 560, y + 12)
    for x in (150, 350):
        c.line(x, 310, x, 592)
    c.save()

    regioes = [t for t in Leitor().preparar(p).tarefas if t.tipo in ("desenho", "tabela")]

    assert regioes and all(t.caixa[3] - t.caixa[1] >= 200 for t in regioes)


def _item(x, y, texto, largura=None, tamanho=9, girado=0):
    from types import SimpleNamespace

    return SimpleNamespace(
        x=x, y=y, width=largura or 4.5 * len(texto), height=tamanho, text=texto, font_size=tamanho, is_bold=False,
        rotation=girado, render_mode=0, mcid=None,
    )  # fmt: skip


def test_linhas_emendadas_entre_celulas_de_grade_nao_sao_colunas():
    from lince import pdf_colunas

    esquerda = ["famílias com renda abaixo da", "linha oficial antes dos impostos", "e dos benefícios do governo"]
    direita = ["unidades de recursos com os", "benefícios não monetários e as", "despesas com saúde e transporte"]
    itens = [_item(60, 700 - 12 * k, a, 180) for k, a in enumerate(esquerda)]
    itens += [_item(300, 700 - 12 * k, b, 180) for k, b in enumerate(direita)]
    itens += [_item(60, 500 - 12 * k, f"outra linha de prosa {k} da página toda") for k in range(12)]
    md = "\n\n".join(f"{a} {b}" for a, b in zip(esquerda, direita, strict=True))

    assert pdf_colunas.fundidas(md, itens, 612)
    assert not pdf_colunas.fundidas(md, itens, 612, [(290, 650, 291, 720)])  # o traço da grade entre elas


def test_remontagem_em_colunas_sem_coluna_aceita_deixa_o_extrator():
    """Uma tabela de valores curtos não é texto corrido: nenhuma coluna aceita, nada a corrigir, a saída do extrator
    (com a tabela que ele achou) fica."""
    from lince import pdf_colunas

    itens = [_item(60 + 120 * j, 700 - 12 * i, f"valor {i}{j}", 40) for i in range(8) for j in range(4)]
    md = "\n".join(" ".join(f"valor {i}{j}" for j in range(4)) for i in range(8))

    assert pdf_colunas.refazer_em_colunas(md, itens, 612, lambda: None) is None


def test_remontagem_de_duas_colunas_leva_o_rotulo_girado():
    """O rótulo girado do eixo de um gráfico entra na remontagem pela posição: fora dela, faltaria uma palavra."""
    from lince import pdf_colunas

    lado = [
        "texto corrido da coluna",
        "que continua na linha de",
        "baixo sem fechar a frase",
        "e segue mais um pouco",
        "até chegar no fim dela",
        "com mais umas palavras",
    ]
    itens = [_item(30, 700 - 12 * k, a, 110) for k, a in enumerate(lado)]
    itens += [_item(200, 700 - 12 * k, f"{a} do corpo", 300) for k, a in enumerate(lado)]
    itens.append(_item(400, 600, "trimestre", 9, girado=90))
    md = " ".join(f"{a} {a} do corpo" for a in lado) + " trimestre"

    novo = pdf_colunas.refazer(md, itens, 612, lambda: None)

    assert novo is not None and "trimestre" in novo
    assert " ".join(lado) in " ".join(novo.split())


def test_desenho_que_e_parte_de_uma_tabela_vai_com_ela():
    """O sombreado das colunas de previsão (um terço da tabela, dentro dela) vai na leitura da tabela; o desenho do
    tamanho da "tabela" é um gráfico com os rótulos alinhados, e continua figura."""
    from lince.pdf import parte_de_tabela

    tabela = [10, 290, 590, 780]

    assert parte_de_tabela([270, 420, 590, 755], [tabela])
    assert not parte_de_tabela([12, 292, 588, 778], [tabela])
    assert not parte_de_tabela([270, 100, 590, 300], [tabela])  # fora dela


def test_tabela_com_grade_nao_passa_pela_remontagem_em_colunas(tmp_path, monkeypatch):
    """A "tabela" de prosa com traços verticais de grade é tabela de verdade: sem prova de linhas emendadas, ela não
    é reordenada como colunas de texto."""
    import sys
    from pathlib import Path

    from lince import pdf

    sys.path.insert(0, str(Path(__file__).parent))
    from test_pdf_estrutura_nativa import _riscos

    chamadas = []
    monkeypatch.setattr(pdf, "refazer_em_colunas", lambda *a: chamadas.append(a))
    p = tmp_path / "riscos.pdf"
    _riscos(p, grade=True)

    Leitor().ler(p)

    assert chamadas == []


def test_legenda_em_caixa_sem_amostra_de_cor_fica_no_grafico(tmp_path):
    """A caixa da legenda dentro do gráfico tem só rótulos curtos (o nome de cada série): é parte do gráfico, não
    fundo de layout."""
    import math

    p = tmp_path / "legenda.pdf"
    c = canvas.Canvas(str(p), pagesize=(595, 842))
    c.line(60, 450, 60, 750)
    c.line(60, 450, 540, 450)
    for serie, cor in enumerate(((1, 0, 0), (0, 0, 1), (0, 0.5, 0))):
        c.setStrokeColorRGB(*cor)
        traco = c.beginPath()
        traco.moveTo(60, 500 + serie * 50)
        for k in range(1, 20):
            traco.lineTo(60 + k * 25, 500 + serie * 50 + 30 * math.sin(k / 2 + serie))
        c.drawPath(traco)
    c.setStrokeColorRGB(0, 0, 0)
    c.setFillColorRGB(0.96, 0.96, 0.96)
    c.roundRect(380, 640, 150, 90, 8, fill=1, stroke=1)
    c.setFillColorRGB(0, 0, 0)
    _linhas(c, 395, 710, ["Série A observada", "Série B estimada", "Série C projetada"], 10, 20)
    _linhas(c, 50, 400, [" ".join(CORPO)[:90] for _ in range(12)], 9, 12)
    c.save()

    (caixa,) = [t.caixa for t in Leitor().preparar(p).tarefas if t.tipo == "desenho"]

    assert caixa[3] >= 725  # a legenda vai no recorte


def test_barras_com_o_rotulo_dentro_continuam_grafico(tmp_path):
    """Barras de mesma altura, cada uma com o nome e o valor dentro, são um gráfico, não faixas de cabeçalho."""
    p = tmp_path / "barras.pdf"
    c = canvas.Canvas(str(p), pagesize=(595, 842))
    setores = (("Agropecuária", 220), ("Indústria", 260), ("Construção", 180), ("Comércio", 340), ("Serviços", 420))
    for k, (nome, valor) in enumerate(setores):
        y = 650 - 30 * k
        c.setFillColorRGB(0, 0, 0.5)
        c.rect(80, y, valor, 20, fill=1, stroke=0)
        c.setFillColorRGB(1, 1, 1)
        _linhas(c, 85, y + 6, [f"{nome} {valor / 10:.1f}%"], 9, fonte="Helvetica-Bold")
    c.setFillColorRGB(0, 0, 0)
    _linhas(c, 50, 480, [" ".join(CORPO)[:90] for _ in range(10)], 9, 12)
    c.save()

    assert [t.tipo for t in Leitor().preparar(p).tarefas] == ["desenho"]


NOTAS = [
    "Os valores históricos aparecem sem sombra e as estimativas aparecem em cinza.",
    "Os totais podem não somar por causa do arredondamento de cada componente.",
    "As séries anteriores a dois mil e vinte foram revistas com a nova metodologia.",
]


def _caixa_de_texto(g, linhas):
    return [
        ((g[0] + 6, g[3] - 14 * (k + 1), g[2] - 6, g[3] - 14 * (k + 1) + 8), texto) for k, texto in enumerate(linhas)
    ]


def test_quadro_de_notas_da_tabela_nao_e_fundo_de_layout():
    """O quadro de notas colado na borda de baixo de uma tabela, ou começando pela marca "(a)", é da tabela: no
    recorte dela, as notas e a ligação com os rótulos das linhas. A caixa de texto de uma coluna lateral, empilhada em
    outra caixa de texto, é fundo de layout."""
    import pypdfium2.raw as c

    from lince.pdf_figuras import _fundo_de_layout

    caminho = c.FPDF_PAGEOBJ_PATH
    notas = [18, 278, 590, 336]
    borda = [18, 338, 590, 339]  # a linha de baixo da tabela, colada no quadro

    assert not _fundo_de_layout(notas, 0, [(caminho, notas), (caminho, borda)], _caixa_de_texto(notas, NOTAS))
    assert _fundo_de_layout(notas, 0, [(caminho, notas)], _caixa_de_texto(notas, NOTAS))
    com_marca = ["(a) " + texto for texto in NOTAS]
    assert not _fundo_de_layout(notas, 0, [(caminho, notas)], _caixa_de_texto(notas, com_marca))
    de_cima, de_baixo = [25, 407, 180, 583], [25, 229, 180, 405]
    objs = [(caminho, de_cima), (caminho, de_baixo)]
    textos = _caixa_de_texto(de_cima, NOTAS) + _caixa_de_texto(de_baixo, NOTAS)
    assert _fundo_de_layout(de_cima, 0, objs, textos)
