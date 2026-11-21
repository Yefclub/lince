"""O mapa de evidência do PDF: o índice com as páginas de cada seção, as páginas em que o Markdown traz a transcrição
de algo que se vê e a imagem da página original para quem responde. Nada disso muda o Markdown."""

import io

import pypdfium2 as pdfium
import pytest
from PIL import Image
from reportlab.lib import pdfencrypt
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import lince
from lince.estrutura import indice, secoes


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
