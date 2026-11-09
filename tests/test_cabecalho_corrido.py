"""Cabeçalho e rodapé corridos saem do corpo sem levar o conteúdo: lote do mesmo modelo, linha numerada e repetição
que faz parte do documento ficam no texto (issue #33, itens 1 e 2)."""

import io

import pytest
from docx import Document
from PIL import Image, ImageDraw
from pptx import Presentation
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import lince
from lince.montagem import _sem_cabecalho_corrido


@pytest.mark.parametrize(
    ("fonte_da_marca", "rodape"),
    [(("Helvetica-Bold", 14), True), (("Helvetica", 9), True), (("Helvetica", 9), False)],
    ids=["marca-em-titulo", "marca-em-linha-comum", "marca-em-linha-comum-sem-rodape"],
)
def test_lote_de_faturas_do_mesmo_modelo_fica_inteiro(tmp_path, fonte_da_marca, rodape):
    # em Helvetica 9 a marca chega à regra como linha comum, sem a proteção de título que o negrito 14 ganha do leitor
    p = tmp_path / "faturas.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    for n in range(12):
        c.setFont(*fonte_da_marca)
        c.drawString(60, 800, "Empresa X - Fatura de serviço")
        c.setFont("Helvetica", 11)
        for k, ln in enumerate(
            [f"Fatura nº {1000 + n}", f"Cliente: Transportadora {n + 1} Ltda", f"Vencimento: {10 + n}/12/2026",
             f"Valor total: R$ {1500 + 37 * n},00", f"Pedido de compra {7700 + n} referente ao frete {n + 3}"]
        ):  # fmt: skip
            c.drawString(60, 770 - k * 18, ln)
        if rodape:
            c.setFont("Helvetica", 8)
            c.drawString(60, 40, f"Documento emitido eletronicamente - folha {n + 1}")
        c.showPage()
    c.save()
    r = lince.ler(p)
    for conteudo in ("Fatura nº", "Cliente: Transportadora", "Vencimento", "Valor total", "Pedido de compra"):
        assert r.markdown.count(conteudo) == 12  # o conteúdo de cada uma das 12 faturas fica
    assert "R$ 1907,00" in r.markdown and "Transportadora 12 Ltda" in r.markdown  # o que muda não é cabeçalho
    assert r.markdown.count("emitido eletronicamente") == 0  # o rodapé com o número da folha, sim
    bordas = r.metadados.get("cabeçalho/rodapé", [])
    if rodape:
        (rodape_lido,) = bordas  # com o rodapé, a marca fixa levaria a remoção além de FRACAO_MAX: ela fica
        assert rodape_lido.startswith("Documento emitido") and rodape_lido.endswith("folha #")
        assert r.markdown.count("Empresa X") == 12
    else:  # sem ele, a marca é o cabeçalho fixo da regra (curta, idêntica na borda de todas): vai para os metadados
        assert bordas == ["Empresa X-Fatura de serviço"] and "Empresa X" not in r.markdown


def test_faturas_linha_a_linha_so_perdem_a_marca_e_o_rodape():
    # cada linha da fatura separada, como sai de um leitor que não junta o parágrafo: o número do cliente coincide com
    # o da página, mas não está rotulado como página, então a linha é conteúdo
    descricao = "Descrição: frete rodoviário de carga seca entre o armazém de Paranaguá e a unidade de Ponta Grossa."
    nativas = _paginas(
        *(
            ["Empresa X - Fatura de serviço", f"Cliente: Transportadora {n + 1} Ltda", f"Fatura nº {1000 + n}",
             descricao, descricao.replace("seca", "a granel"), f"Valor total: R$ {1500 + 37 * n},00",
             f"Vencimento: {10 + n}/12/2026", f"Documento emitido eletronicamente - folha {n + 1}"]
            for n in range(12)
        )
    )  # fmt: skip
    saiu = _sem_cabecalho_corrido(nativas)
    assert saiu == {"Empresa X - Fatura de serviço", "Documento emitido eletronicamente - folha #"}
    for n, (_, blocos) in enumerate(nativas):
        assert blocos[1] == f"Cliente: Transportadora {n + 1} Ltda" and blocos[2] == f"Fatura nº {1000 + n}"
        assert blocos[5].startswith("Valor total") and blocos[6].startswith("Vencimento")


def test_carne_com_uma_parcela_por_pagina_fica_inteiro(tmp_path):
    p = tmp_path / "carne.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    for n in range(12):
        c.setFont("Helvetica", 9)
        c.drawString(60, 800, f"Parcela {n + 1} de 12")  # o número da parcela é o da página, sem rótulo de página
        c.setFont("Helvetica", 11)
        for k, ln in enumerate(
            [f"Vencimento: 10/{n + 1:02d}/2027", f"Valor da parcela: R$ {250 + n},00 com desconto até o vencimento",
             f"Nosso número {93100 + 7 * n} - carnê do financiamento 4512"]
        ):  # fmt: skip
            c.drawString(60, 770 - k * 18, ln)
        c.showPage()
    c.save()
    r = lince.ler(p)
    assert all(f"Parcela {n} de 12" in r.markdown for n in range(1, 13))
    assert "cabeçalho/rodapé" not in r.metadados


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
    nativas = _paginas(*([f"Relatório anual — pág. {n - 1}", corpo, str(n - 1)] for n in range(2, 7)))
    assert _sem_cabecalho_corrido(nativas) == {"Relatório anual — pág. #", "#"}  # o número sozinho também
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


def test_pagina_n_barra_m_sozinha_na_ultima_linha_sai():
    # o "i" de "Página" não é o número romano 1: o número da página é o que vem depois do rótulo
    corpo = "\n".join(f"Cláusula {k}: o texto de verdade do contrato de fornecimento de grãos." for k in range(8))
    nativas = _paginas(*([corpo, f"Página {n + 1}/4"] for n in range(4)))
    assert _sem_cabecalho_corrido(nativas) == {"Página #/4"}
    assert all(b[1] == "" and b[0] == corpo for _, b in nativas)


def test_pdf_de_duas_paginas_perde_o_rodape_pagina_n_de_2(tmp_path):
    # duas páginas bastam para o número sozinho na última linha (a forma rotulada no meio da linha pede três)
    p = tmp_path / "parecer.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    for n in range(2):
        c.setFont("Helvetica", 11)
        for k in range(6):
            c.drawString(60, 780 - k * 18, f"Item {n + 1}.{k + 1}: o parecer recomenda a compra de {k + 3} silos.")
        c.setFont("Helvetica", 8)
        c.drawString(270, 40, f"Página {n + 1} de 2")
        c.showPage()
    c.save()
    r = lince.ler(p)
    assert "Página" not in r.markdown and r.markdown.count("o parecer recomenda") == 12
    assert r.metadados["cabeçalho/rodapé"] == ["Página # de 2"]


@pytest.mark.parametrize("forma", ["- {} -", "— {} —", "({})"])
def test_numero_entre_tracos_ou_parenteses_sai(forma):
    corpo = "\n".join(f"Cláusula {k}: o texto de verdade do contrato de fornecimento de grãos." for k in range(8))
    nativas = _paginas(*([corpo, forma.format(n + 1)] for n in range(4)))
    assert _sem_cabecalho_corrido(nativas) == {forma.format("#")}
    assert all(b[1] == "" for _, b in nativas)
    carne = _paginas(*([corpo, forma.format(40 + 3 * n)] for n in range(4)))  # muda, mas não com a página: fica
    assert _sem_cabecalho_corrido(carne) == set() and carne[1][1][1] == forma.format(43)


@pytest.mark.parametrize("forma", ["{})", "({}"])
def test_numero_com_parentese_so_de_um_lado_e_item_de_lista_e_fica(forma):
    corpo = "\n".join(f"Cláusula {k}: o texto de verdade do contrato de fornecimento de grãos." for k in range(8))
    nativas = _paginas(*([corpo, forma.format(n + 1)] for n in range(4)))  # "1)", "2)"...: bate com a página, mas não é
    assert _sem_cabecalho_corrido(nativas) == set()
    assert [b[1] for _, b in nativas] == [forma.format(n + 1) for n in range(4)]
