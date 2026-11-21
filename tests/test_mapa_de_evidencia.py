"""O mapa de evidência do PDF: o índice com as páginas de cada seção, as páginas em que o Markdown traz a transcrição
de algo que se vê e a imagem da página original para quem responde. Nada disso muda o Markdown."""

import io
import time

import pypdfium2 as pdfium
import pytest
from PIL import Image
from reportlab.lib import pdfencrypt
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import lince
from lince.estrutura import indice, secoes
from lince.leitor import _paginas_visuais


def _pdf_com_marcadores(caminho):
    """5 páginas; os marcadores: Introdução (1), Métodos (2) > Coleta (3), Resultados (4)."""
    c = canvas.Canvas(str(caminho), pagesize=A4)
    titulos = {1: ("Introdução", 0), 2: ("Métodos", 0), 3: ("Coleta", 1), 4: ("Resultados", 0)}
    for n in range(1, 6):
        if n in titulos:
            titulo, nivel = titulos[n]
            c.bookmarkPage(f"s{n}")
            c.addOutlineEntry(titulo, f"s{n}", level=nivel)
            c.setFont("Helvetica-Bold", 18)
            c.drawString(72, 770, titulo)
        c.setFont("Helvetica", 11)
        for k in range(6):
            c.drawString(72, 700 - 18 * k, f"Parágrafo {k + 1} da página {n} com texto corrido do relatório.")
        c.showPage()
    c.save()


def test_pdf_com_marcadores_ganha_o_indice_com_a_faixa_de_paginas_de_cada_secao(tmp_path):
    p = tmp_path / "relatorio.pdf"
    _pdf_com_marcadores(p)
    r = lince.ler(p)

    mapa = r.estrutura["indice"]
    assert mapa["fonte"] == "sumario"
    faixas = [(x["titulo"], x["nivel"], x["pagina"], x["pagina_fim"]) for x in mapa["itens"]]
    # a seção que abre a página dela fecha a anterior na página de antes; a última vai até o fim do documento
    assert faixas == [
        ("Introdução", 1, 1, 1),
        ("Métodos", 1, 2, 3),
        ("Coleta", 2, 3, 3),
        ("Resultados", 1, 4, 5),
    ]
    # onde o título está no Markdown, como em `secoes`
    for x in mapa["itens"]:
        assert r.markdown[x["inicio"] :].lstrip("# ").startswith(x["titulo"])


def test_sem_marcadores_o_indice_vem_dos_titulos_sem_o_que_nao_e_secao():
    paginas = [
        "# Carta do Trimestre | Nota 5\n\n# Sumário\n\n# Atividade 2\n\n# Inflação 3",
        "# Carta do Trimestre | Nota 5\n\n# Atividade\n\n## A produção industrial cresceu no trimestre, puxada pela"
        " indústria de transformação e pelos serviços.\n\ntexto",
        "# Carta do Trimestre | Nota 5\n\ntexto que continua a atividade\n\n# Inflação\n\ntexto",
        "# Carta do Trimestre | Nota 5\n\n## Referências\n\n## [1] Silva, A. Título do artigo.\n\ntexto",
    ]
    md = "\n\n".join(f"<!-- página {n} · nativa -->\n\n{t}" for n, t in enumerate(paginas, 1))
    estrutura = {"paginas": 4} | secoes(md, pdf=True)

    mapa = indice(md, estrutura)

    assert mapa["fonte"] == "titulos"
    assert [(x["titulo"], x["pagina"], x["pagina_fim"]) for x in mapa["itens"]] == [
        ("Sumário", 1, 1),  # a entrada do sumário impresso ("Atividade 2") sai: o título dela aparece adiante
        ("Atividade", 2, 3),  # a seção que começa no meio da página divide a página 3 com a seguinte
        ("Inflação", 3, 4),
        ("Referências", 4, 4),  # o cabeçalho corrido, a frase em negrito e o item da bibliografia não são seção
    ]


def _figura(cor):
    im = Image.new("RGB", (400, 300), "white")
    im.paste(cor, (40, 40, 360, 260))
    b = io.BytesIO()
    im.save(b, "PNG")
    b.seek(0)
    return ImageReader(b)


def _pdf_com_figuras(caminho):
    """Página 1 só com texto; páginas 2 e 3 com uma figura diferente cada."""
    c = canvas.Canvas(str(caminho), pagesize=A4)
    for n in range(1, 4):
        c.setFont("Helvetica", 11)
        c.drawString(72, 780, f"Texto da página {n}.")
        if n > 1:
            c.drawImage(_figura((30, 90 * n, 200)), 72, 400, width=300, height=225)
        c.showPage()
    c.save()


def test_paginas_visuais_dizem_o_que_a_ia_transcreveu_e_o_que_a_leitura_nao_garante(tmp_path):
    p = tmp_path / "figuras.pdf"
    _pdf_com_figuras(p)
    leituras = iter(["Gráfico (barras): vendas\n\n| mês | valor |\n| --- | --- |\n| jan | ≈3 |", "[ilegível]"])
    ia = lince.FuncaoLeitor(lambda imagem, instrucao: next(leituras), "simulado")

    r = lince.Leitor(ia=ia, agrupar=False).ler(p)

    assert r.estrutura["paginas_visuais"] == [
        {"pagina": 2, "conteudo": ["imagem"], "alertas": ["estimado"]},
        {"pagina": 3, "conteudo": ["imagem"], "alertas": ["ilegivel"]},
    ]  # a página só de texto fica de fora: o Markdown dela é o texto do arquivo


def test_sem_ia_as_paginas_visuais_ficam_como_nao_lidas(tmp_path):
    p = tmp_path / "figuras.pdf"
    _pdf_com_figuras(p)

    r = lince.ler(p)

    alertas = [(v["pagina"], v["alertas"]) for v in r.estrutura["paginas_visuais"]]
    assert alertas == [(2, ["nao_lida"]), (3, ["nao_lida"])]


def test_imagens_das_paginas_trazem_a_pagina_como_o_visualizador_exibe(tmp_path):
    original, p = tmp_path / "original.pdf", tmp_path / "girado.pdf"
    c = canvas.Canvas(str(original), pagesize=A4)
    for texto in ("Página sem rotação.", "Página com /Rotate 90."):
        c.drawString(72, 780, texto)
        c.showPage()
    c.save()
    doc = pdfium.PdfDocument(str(original))
    doc[1].set_rotation(90)  # exibida deitada
    doc.save(str(p))
    doc.close()
    doc = pdfium.PdfDocument(str(p))
    # a PDFium aplica o /Rotate, como um visualizador
    exibidas = [doc[i].render(scale=1).to_pil().size for i in range(2)]
    doc.close()
    assert exibidas[0][0] < exibidas[0][1] and exibidas[1][0] > exibidas[1][1]

    imagens = lince.imagens_das_paginas(p, [2, 1], lado=800)

    assert list(imagens) == [2, 1]
    for n, (w, h) in zip((1, 2), exibidas, strict=True):
        tamanho = Image.open(io.BytesIO(imagens[n])).size
        assert max(tamanho) == 800
        assert (tamanho[0] > tamanho[1]) == (w > h)


@pytest.mark.parametrize("paginas", [[0], [4], [], list(range(1, 52)), ["1"]])
def test_imagens_das_paginas_recusam_pedido_fora_do_documento_ou_do_teto(tmp_path, paginas):
    p = tmp_path / "figuras.pdf"
    _pdf_com_figuras(p)

    with pytest.raises(ValueError):
        lince.imagens_das_paginas(p, paginas)


def test_imagens_das_paginas_do_pdf_protegido_pedem_a_senha(tmp_path):
    p = tmp_path / "protegido.pdf"
    cifra = pdfencrypt.StandardEncryption("segredo", "dono-do-pdf", strength=128)
    c = canvas.Canvas(str(p), pagesize=A4, encrypt=cifra)
    c.drawString(72, 780, "Conteúdo protegido.")
    c.showPage()
    c.save()

    with pytest.raises(lince.DocumentoProtegido):
        lince.imagens_das_paginas(p, [1])
    with pytest.raises(lince.SenhaIncorreta):
        lince.imagens_das_paginas(p, [1], senha="errada")
    assert list(lince.imagens_das_paginas(p, [1], senha="segredo")) == [1]


def _md(*paginas):
    md = "\n\n".join(f"<!-- página {n} · nativa -->\n\n{t}" for n, t in enumerate(paginas, 1))
    return md, {"paginas": len(paginas)} | secoes(md, pdf=True)


def test_indice_de_pagina_enorme_com_muitos_titulos_e_linear():
    md, estrutura = _md("".join(f"# Seção {k}\n\n{'texto corrido ' * 55}\n\n" for k in range(2000)))
    inicio = time.monotonic()

    mapa = indice(md, estrutura)

    assert time.monotonic() - inicio < 2  # 1,5 MB e 2000 títulos numa página: era quadrático (~100 s)
    assert len(mapa["itens"]) == 2000 and mapa["itens"][-1]["pagina_fim"] == 1


def test_titulo_longo_de_separadores_nao_trava_o_indice():
    md, estrutura = _md(*[f"# Título {k} {'_' * 20000} fim\n\ntexto" for k in range(3)], "# Conclusão\n\ntexto")
    inicio = time.monotonic()

    mapa = indice(md, estrutura)

    assert time.monotonic() - inicio < 1  # o padrão do número de página no fim era quadrático nesse título
    assert [x["titulo"] for x in mapa["itens"]] == ["Conclusão"]


def test_muitos_marcadores_na_mesma_pagina_grande_nao_travam_o_indice():
    md, estrutura = _md("texto corrido " * 75_000, "fim")
    sumario = [[1, f"Marcador {k}", 1] for k in range(2000)]
    inicio = time.monotonic()

    mapa = indice(md, estrutura, sumario)

    assert time.monotonic() - inicio < 2  # cada marcador varria a página inteira
    assert len(mapa["itens"]) == 2000 and "inicio" not in mapa["itens"][0]


def test_titulo_citado_no_texto_da_pagina_nao_encurta_a_secao_anterior():
    md, estrutura = _md(
        "# Métodos\n\ntexto",
        "Continuação dos métodos; os resultados vêm adiante.\n\n# Resultados\n\ntexto",
    )

    mapa = indice(md, estrutura, [[1, "Métodos", 1], [1, "Resultados", 2]])

    assert [(x["titulo"], x["pagina"], x["pagina_fim"]) for x in mapa["itens"]] == [
        ("Métodos", 1, 2),  # a continuação dela está na página 2
        ("Resultados", 2, 2),
    ]
    assert md[mapa["itens"][1]["inicio"] :].startswith("# Resultados")


def test_marcador_acha_o_titulo_sublinhado_ou_numerado_da_pagina():
    md, estrutura = _md("# 1. Introdução\n\ntexto", "# <u>Resultados</u>\n\ntexto")

    mapa = indice(md, estrutura, [[1, "Introdução", 1], [1, "Resultados", 2]])

    assert [(x["pagina"], x["pagina_fim"]) for x in mapa["itens"]] == [(1, 1), (2, 2)]
    assert all("inicio" in x for x in mapa["itens"])


def test_cabecalho_corrido_nao_prende_a_secao_na_pagina_seguinte_tambem_com_marcadores():
    cabecalho = "# Carta do Trimestre | Nota 5"
    md, estrutura = _md(
        f"{cabecalho}\n\n# Introdução\n\ntexto",
        f"{cabecalho}\n\n# Resultados\n\ntexto",
        f"{cabecalho}\n\ntexto",
    )

    mapa = indice(md, estrutura, [[1, "Introdução", 1], [1, "Resultados", 2]])

    assert [(x["titulo"], x["pagina_fim"]) for x in mapa["itens"]] == [("Introdução", 1), ("Resultados", 3)]


def test_titulo_que_se_repete_no_meio_das_paginas_continua_no_indice():
    md, estrutura = _md(*[f"# Capítulo {k}\n\ntexto\n\n## Exercícios\n\nlista" for k in range(1, 4)])

    mapa = indice(md, estrutura)

    assert [x["titulo"] for x in mapa["itens"]].count("Exercícios") == 3


def test_indice_que_parou_no_teto_diz_que_foi_cortado(tmp_path):
    p = tmp_path / "marcadores.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    c.drawString(72, 780, "Página com marcadores demais.")
    for k in range(lince.pdf.SUMARIO_MAX + 1):
        c.bookmarkPage(f"m{k}")
        c.addOutlineEntry(f"Marcador {k}", f"m{k}")
    c.showPage()
    c.save()

    mapa = lince.ler(p).estrutura["indice"]

    assert mapa["cortado"] is True and len(mapa["itens"]) == lince.pdf.SUMARIO_MAX


def test_paginas_escaneadas_iguais_aparecem_todas_nas_paginas_visuais(tmp_path):
    p = tmp_path / "scan.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    for _ in range(3):
        c.drawImage(_figura((90, 90, 90)), 0, 0, width=A4[0], height=A4[1])
        c.showPage()
    c.save()

    r = lince.ler(p)

    assert [(v["pagina"], v["conteudo"]) for v in r.estrutura["paginas_visuais"]] == [
        (1, ["escaneada"]),
        (2, ["escaneada"]),
        (3, ["escaneada"]),
    ]


def test_aproximado_numa_formula_e_simbolo_e_nao_estimativa():
    plano = {
        "posicoes": [
            {"id": "p001-r1", "modo": "formula", "pagina": 0},
            {"id": "p002-r1", "modo": "desenho", "pagina": 1},
        ],
        "tarefas_originais": [{"id": "p001-r1", "modo": "formula"}, {"id": "p002-r1", "modo": "desenho"}],
    }
    lidos = {"p001-r1": "$$e^x \\approx 1 + x$$ (≈ para x pequeno)", "p002-r1": "| jan | ≈3 |"}

    visuais = _paginas_visuais(plano, lidos)

    assert visuais == [
        {"pagina": 1, "conteudo": ["formula"]},
        {"pagina": 2, "conteudo": ["desenho"], "alertas": ["estimado"]},
    ]


@pytest.mark.parametrize(
    ("pedido", "erro"),
    [({"lado": 5000}, "o lado vai"), ({"lado": 2000, "paginas": list(range(1, 41))}, "imagens demais")],
)
def test_imagens_das_paginas_tem_teto_de_tamanho(tmp_path, pedido, erro):
    p = tmp_path / "figuras.pdf"
    _pdf_com_figuras(p)

    with pytest.raises(ValueError, match=erro):
        lince.imagens_das_paginas(p, **({"paginas": [1]} | pedido))


def test_imagens_das_paginas_recusam_a_pagina_pesada_como_a_leitura(tmp_path, monkeypatch):
    p = tmp_path / "pesada.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    for k in range(20):
        c.rect(50 + 10 * k, 50, 5, 5, fill=1)
    c.showPage()
    c.save()
    monkeypatch.setattr(lince.pdf, "OBJETOS_MAX", 10)

    with pytest.raises(ValueError, match="conteúdo demais"):
        lince.imagens_das_paginas(p, [1])


def test_imagens_das_paginas_respeitam_o_prazo(tmp_path):
    p = tmp_path / "figuras.pdf"
    _pdf_com_figuras(p)

    with pytest.raises(lince.PrazoEsgotado):
        lince.imagens_das_paginas(p, [1, 2], prazo=0)
