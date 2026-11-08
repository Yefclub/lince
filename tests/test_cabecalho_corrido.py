"""Cabeçalho e rodapé corridos saem do corpo sem levar o conteúdo: lote do mesmo modelo, linha numerada e repetição
que faz parte do documento ficam no texto (issue #33, itens 1 e 2)."""

import io

from docx import Document
from PIL import Image, ImageDraw
from pptx import Presentation
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import lince
from lince.montagem import _sem_cabecalho_corrido


def test_lote_de_faturas_do_mesmo_modelo_fica_inteiro(tmp_path):
    p = tmp_path / "faturas.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    for n in range(12):
        c.setFont("Helvetica-Bold", 14)
        c.drawString(60, 800, "Empresa X - Fatura de serviço")
        c.setFont("Helvetica", 11)
        for k, ln in enumerate(
            [f"Fatura nº {1000 + n}", f"Cliente: Transportadora {n + 1} Ltda", f"Vencimento: {10 + n}/12/2026",
             f"Valor total: R$ {1500 + 37 * n},00", f"Pedido de compra {7700 + n} referente ao frete {n + 3}"]
        ):  # fmt: skip
            c.drawString(60, 770 - k * 18, ln)
        c.setFont("Helvetica", 8)
        c.drawString(60, 40, f"Documento emitido eletronicamente - folha {n + 1}")
        c.showPage()
    c.save()
    r = lince.ler(p)
    assert r.markdown.count("Valor total") == 12 and r.markdown.count("Empresa X") == 12
    assert "R$ 1907,00" in r.markdown  # o valor que muda entre páginas não é cabeçalho
    assert r.markdown.count("emitido eletronicamente") == 0  # o rodapé com o número da folha, sim
    (rodape,) = r.metadados["cabeçalho/rodapé"]
    assert rodape.startswith("Documento emitido") and rodape.endswith("folha #")


def test_pdf_longo_com_linhas_numeradas_por_pagina_fica_inteiro(tmp_path):
    p = tmp_path / "longo.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    for n in range(30):
        c.setFont("Helvetica-Bold", 14)
        c.drawString(60, 810, f"Capítulo {n + 1}")
        c.setFont("Helvetica", 9)
        for k in range(50):
            c.drawString(60, 790 - k * 15, f"Linha {k + 1} da página {n + 1}: texto corrido do relatório.")
        c.showPage()
    c.save()
    r = lince.ler(p)
    assert r.markdown.count("texto corrido do relatório") == 1500
    assert r.markdown.count("Capítulo") == 30  # o título com o número da página é conteúdo


def test_escaneado_repetido_lido_pela_ia_fica_inteiro(tmp_path):
    p = tmp_path / "laudos.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    for n in range(4):
        im = Image.new("RGB", (900, 1200), "white")
        ImageDraw.Draw(im).text((80, 80), f"Laudo {n + 1}", fill="black")
        b = io.BytesIO()
        im.save(b, "PNG")
        c.drawImage(ImageReader(io.BytesIO(b.getvalue())), 0, 0, width=A4[0], height=A4[1])
        c.showPage()
    c.save()
    r = lince.Leitor(ia=lince.FuncaoLeitor(lambda img, instr: "Laudo de classificação de grãos - página 1", "m"))
    md = r.ler(p).markdown
    assert md.count("Laudo de classificação de grãos") == 4  # o texto lido pela IA nunca sai como cabeçalho


def test_repeticao_no_corpo_do_word_e_conteudo(tmp_path):
    d = Document()
    for n in range(1, 4):
        d.add_heading(f"Item {n}", 2)
        d.add_paragraph(f"Descrição do item {n} do checklist de segurança.")
        d.add_paragraph("Status: aprovado pela qualidade")
    d.sections[0].footer.paragraphs[0].text = "Formulário FQ-07 - uso interno"
    p = tmp_path / "check.docx"
    d.save(p)
    r = lince.ler(p)
    assert r.markdown.count("Status: aprovado pela qualidade") == 3
    assert "FQ-07" not in r.markdown and any("FQ-07" in x for x in r.metadados["cabeçalho/rodapé"])


def test_repeticao_nos_slides_e_conteudo(tmp_path):
    prs = Presentation()
    for n in range(1, 4):
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = f"Etapa {n}"
        s.placeholders[1].text = "Status: aprovado pela qualidade"
    p = tmp_path / "etapas.pptx"
    prs.save(p)
    assert lince.ler(p).markdown.count("Status: aprovado pela qualidade") == 3


def _paginas(*blocos_por_pagina):
    return [(i, list(blocos)) for i, blocos in enumerate(blocos_por_pagina)]


def test_so_sai_a_linha_curta_da_borda_em_que_so_o_numero_da_pagina_varia():
    corpo = "\n".join(f"Conteúdo da cláusula {k}, que é o texto de verdade do contrato." for k in range(10))
    longa = "Este parágrafo longo, que o leitor juntou numa linha só, cita a página {} e segue igual em todas elas."
    nativas = _paginas(
        *(
            [f"Contrato 2026 - folha {n}\nValor da parcela {100 * n}", corpo, longa.format(n),
             f"<!-- lido pela IA: t{n} -->\nTabela lida - folha {n}", f"# Seção {n}"]
            for n in range(1, 5)
        )
    )  # fmt: skip
    assert _sem_cabecalho_corrido(nativas) == {"Contrato 2026 - folha #"}
    for _, blocos in nativas:
        texto = "\n".join(blocos)
        assert "Contrato 2026" not in texto  # o cabeçalho com o número da página sai
        assert "Valor da parcela" in texto  # número que não é o da página não é cabeçalho
        assert "cita a página" in texto  # linha longa (parágrafo juntado) não é cabeçalho
        assert "Tabela lida" in texto and "# Seção" in texto  # leitura da IA e título ficam


def test_cabecalho_que_pesaria_demais_no_texto_nao_sai():
    nativas = _paginas(*([f"Relatório - folha {n}", "Dado."] for n in range(1, 5)))
    assert _sem_cabecalho_corrido(nativas) == set() and all("folha" in b[0] for _, b in nativas)


def test_numero_sozinho_so_sai_se_for_o_da_pagina():
    corpo = "\n".join(f"Cláusula {k}: o texto de verdade do contrato de fornecimento de grãos." for k in range(8))
    ano = _paginas(*([corpo, "2026"] for _ in range(4)))  # o mesmo número em toda página não é número de página
    assert _sem_cabecalho_corrido(ano) == set() and all(b[1] == "2026" for _, b in ano)
    total = _paginas(*([corpo, str(40 + 3 * n)] for n in range(4)))  # muda, mas não com a página
    _sem_cabecalho_corrido(total)
    assert [b[1] for _, b in total] == ["40", "43", "46", "49"]
    paginas = _paginas(*([corpo, str(n + 1)] for n in range(4)))
    _sem_cabecalho_corrido(paginas)
    assert all(b[1] == "" for _, b in paginas)


def test_numero_impresso_deslocado_do_indice_ainda_e_cabecalho():
    corpo = "\n".join(f"Cláusula {k}: o texto de verdade do contrato de fornecimento de grãos." for k in range(8))
    # capa e sumário sem número: a página física 3 é a "1" impressa
    nativas = _paginas(*([f"Relatório anual — {n - 1}", corpo, str(n - 1)] for n in range(2, 7)))
    assert _sem_cabecalho_corrido(nativas) == {"Relatório anual — #", "#"}  # o número sozinho também é registrado
    assert all(b[0] == "" and b[2] == "" for _, b in nativas)


def test_cabecalho_fixo_sem_numero_vai_para_os_metadados(tmp_path):
    p = tmp_path / "contrato.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    for n in range(60):
        c.setFont("Helvetica", 9)
        c.drawString(60, 810, "Contrato 123/2026 - Confidencial")
        c.setFont("Helvetica", 11)
        for k in range(3):
            c.drawString(
                60, 770 - k * 18, f"Cláusula {n + 1}.{k + 1}: entrega de {10 * n + k} toneladas de soja no porto."
            )
        c.showPage()
    c.save()
    r = lince.ler(p)
    assert "Contrato 123/2026" not in r.markdown and r.markdown.count("toneladas de soja") == 180
    assert r.metadados["cabeçalho/rodapé"] == ["Contrato 123/2026 - Confidencial"]


def test_numero_da_pagina_tirado_do_corpo_vai_para_os_metadados():
    corpo = "\n".join(f"Cláusula {k}: o texto de verdade do contrato de fornecimento de grãos." for k in range(8))
    nativas = _paginas(*([f"Cláusula {n}.0 em vigor.", corpo, str(n + 1)] for n in range(5)))
    assert _sem_cabecalho_corrido(nativas) == {"#"} and all(b[2] == "" for _, b in nativas)
    abreviado = _paginas(*([f"Cláusula {n}.0 em vigor.", corpo, f"Pág. {n + 1}"] for n in range(5)))
    assert _sem_cabecalho_corrido(abreviado) == {"Pág. #"}  # a forma que a página usava, para quem lê os metadados


def test_paginas_iguais_e_linha_que_muda_nao_sao_cabecalho_fixo():
    corpo = "\n".join(f"Cláusula {k}: o texto de verdade do contrato de fornecimento de grãos." for k in range(8))
    copias = _paginas(*([corpo] for _ in range(5)))  # páginas inteiras iguais: o igual entra no corpo, é conteúdo
    assert _sem_cabecalho_corrido(copias) == set() and all(b[0] == corpo for _, b in copias)
    carne = _paginas(*([f"Vencimento: 10/{n + 1:02d}/2026", f"Valor: R$ {100 + n},00", corpo] for n in range(12)))
    assert _sem_cabecalho_corrido(carne) == set()  # o que muda não é fixo, e o igual que segue no corpo também não
    assert all(b[0].startswith("Vencimento") and b[1].startswith("Valor") for _, b in carne)
