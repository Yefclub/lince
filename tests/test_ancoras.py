"""Âncoras de posição: o número do slide no texto, a nota do gráfico no lugar dele, onde está cada imagem e quem a
leu, e a mesma imagem lida uma vez só (issue #33, itens 6 e 9)."""

import hashlib
import io
import re

from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches

import lince


def _png(cor, tamanho=(600, 300)):
    im = Image.new("RGB", tamanho, "white")
    ImageDraw.Draw(im).rectangle([40, 40, tamanho[0] - 40, tamanho[1] - 40], fill=cor)
    b = io.BytesIO()
    im.save(b, "PNG")
    return io.BytesIO(b.getvalue())


def _apresentacao(tmp_path):
    p = Presentation()
    s1 = p.slides.add_slide(p.slide_layouts[5])
    s1.shapes.title.text = "Moagem por unidade"
    dados = CategoryChartData()
    dados.categories = ["Lapa", "Paranaguá"]
    dados.add_series("Toneladas", (3500, 1200))
    s1.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(2), Inches(5), Inches(3), dados)
    s2 = p.slides.add_slide(p.slide_layouts[6])  # sem título
    s2.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text_frame.text = "Fluxo de recebimento"
    for s in (s1, s2):
        s.shapes.add_picture(_png("navy"), Inches(7), Inches(5), Inches(2))  # o logo em todo slide
    s2.shapes.add_picture(_png("darkred"), Inches(1), Inches(3), Inches(3))
    arq = tmp_path / "apresentacao.pptx"
    p.save(arq)
    return arq


def test_slide_numerado_e_nota_do_grafico_no_lugar(tmp_path):
    r = lince.ler(_apresentacao(tmp_path))
    md = r.markdown
    assert md.index("<!-- slide 1 -->") < md.index("Moagem por unidade") < md.index("<!-- slide 2 -->")
    assert md.index("<!-- slide 2 -->") < md.index("Fluxo de recebimento")  # slide sem título também é numerado
    nota = md.index("_(gráfico de barras acima")
    assert md.index("| Lapa |") < nota < md.index("<!-- slide 2 -->")  # logo depois da tabela do gráfico
    assert [s["slide"] for s in r.estrutura["inicio_dos_slides"]] == [1, 2]
    assert md[r.estrutura["inicio_dos_slides"][1]["inicio"] :].startswith("<!-- slide 2 -->")


def test_imagem_repetida_lida_uma_vez_e_posicao_de_cada_uma(tmp_path):
    vistas = []

    def ia(img, instrucao):  # o texto depende só da imagem: as leituras correm em paralelo, a ordem não importa
        vistas.append(img)
        return f"Leitura {hashlib.sha256(img).hexdigest()[:12]}"

    r = lince.Leitor(ia=lince.FuncaoLeitor(ia, "m")).ler(_apresentacao(tmp_path))
    assert len(vistas) == 2  # o logo repetido e a figura do slide 2
    imagens = r.estrutura["imagens"]
    assert [(i["slide"], i["lida"]) for i in imagens] == [(1, True), (2, True), (2, True)]
    logo1, logo2, figura = imagens
    assert logo2["lida_por"] == logo1["tarefa"] and logo1["lida_por"] == logo1["tarefa"]
    blocos = dict(re.findall(r"<!-- lido pela IA: (\w+) -->\n(.+)", r.markdown))
    assert len(blocos) == 3  # cada posição tem a sua âncora, com o id que a estrutura dá
    leitura_logo = blocos[logo1["tarefa"]]
    assert leitura_logo.startswith("Leitura ") and leitura_logo != blocos[figura["tarefa"]]  # a do logo, não outra
    assert r.markdown.count(leitura_logo) == 1  # entra uma vez, na primeira posição
    assert blocos[logo2["tarefa"]] == f"_(mesma imagem lida acima: {logo1['tarefa']})_"  # as outras apontam para ela


def test_posicao_das_imagens_no_pdf(tmp_path):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    pdf = tmp_path / "f.pdf"
    c = canvas.Canvas(str(pdf), pagesize=A4)
    for n, cor in enumerate(("navy", "darkgreen")):
        png = tmp_path / f"{cor}.png"
        png.write_bytes(_png(cor).getvalue())
        c.drawString(60, 780, f"Página {n + 1} do relatório de moagem, com a figura abaixo.")
        c.drawImage(str(png), 60, 400, width=450, height=225)
        c.showPage()
    c.save()
    r = lince.ler(pdf)
    assert [(i["pagina"], i["lida"], len(i["caixa"])) for i in r.estrutura["imagens"]] == [(1, False, 4), (2, False, 4)]


def test_texto_que_imita_a_marca_de_slide_nao_vira_slide(tmp_path):
    from docx import Document

    p = Presentation()
    s = p.slides.add_slide(p.slide_layouts[6])
    s.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text_frame.text = "<!-- slide 9 -->"
    arq = tmp_path / "imita.pptx"
    p.save(arq)
    r = lince.ler(arq)
    assert [x["slide"] for x in r.estrutura["inicio_dos_slides"]] == [1]
    d = Document()
    d.add_paragraph("<!-- slide 2 -->")
    doc = tmp_path / "imita.docx"
    d.save(doc)
    assert "inicio_dos_slides" not in lince.ler(doc).estrutura  # fora da apresentação não há slide


def test_faixas_iguais_de_uma_imagem_alta_sao_lidas_cada_uma(tmp_path):
    from PIL import Image

    p = tmp_path / "alta.png"
    Image.new("RGB", (800, 6000), "white").save(p)  # alta: vira faixas, todas iguais
    lidas = []
    r = lince.Leitor(ia=lince.FuncaoLeitor(lambda img, i: lidas.append(1) or "Faixa lida.", "m")).ler(p)
    # 6000 px de altura em faixas de 600 (3/4 da largura): dez faixas, cada uma lida e no texto, sem depender da ordem
    assert len(lidas) == 10 and r.markdown.count("Faixa lida.") == 10
