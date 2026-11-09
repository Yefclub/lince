"""PDF com /Rotate e caixas da página herdadas do /Pages (issue #47): o recorte que vai para a IA é o da figura, de
pé como a página é exibida."""

import sys
from pathlib import Path

import pytest
from PIL import Image, ImageChops

import lince

sys.path.insert(0, str(Path(__file__).parent))
from test_issue46 import TEXTO, _pdf  # noqa: E402


def _girado(p, rotacao):
    p.write_bytes(p.read_bytes().replace(b"/Type /Page /Parent", b"/Type /Page /Rotate %d /Parent" % rotacao))
    return p


def _recorte(p, pasta):
    plano = lince.Leitor().planejar(p, pasta)
    (t,) = plano["tarefas"]
    return Image.open(pasta / "ia" / f"{t['id']}.png").convert("RGB")


def _nao_branco(im):
    cinza = im.convert("L")
    return sum(1 for v in cinza.get_flattened_data() if v < 245) / (im.width * im.height)


@pytest.mark.parametrize("rotacao", [90, 180, 270])
def test_recorte_da_figura_na_pagina_girada_e_a_figura_de_pe(tmp_path, rotacao):
    conteudo = TEXTO + "q 200 0 0 150 100 500 cm /Im1 Do Q"
    reto = _recorte(_pdf(tmp_path / "r0.pdf", [0, 0, 612, 792], conteudo), tmp_path / "p0")
    girado = _recorte(_girado(_pdf(tmp_path / "r.pdf", [0, 0, 612, 792], conteudo), rotacao), tmp_path / "p")
    assert _nao_branco(girado) == pytest.approx(_nao_branco(reto), abs=0.02)  # a figura, não um pedaço em branco
    esperado = reto.rotate(-rotacao, expand=True)  # /Rotate é horário: o recorte sai como a página aparece
    assert girado.size == esperado.size
    assert ImageChops.difference(girado, esperado).getbbox() is None


def test_pagina_inteira_girada_vai_de_pe(tmp_path, monkeypatch):
    from lince import pdf as pdf_mod

    monkeypatch.setattr(pdf_mod, "densidade_matematica", lambda md: 9.0)  # a rota "pagina", como numa escaneada

    def pagina(rotacao):
        p = _girado(_pdf(tmp_path / f"e{rotacao}.pdf", [0, 0, 612, 792], TEXTO), rotacao)
        return _recorte(p, tmp_path / f"p{rotacao}")

    reta, girada = pagina(0), pagina(90)
    assert reta.height > reta.width and girada.width > girada.height  # retrato com /Rotate 90 é exibido em paisagem


def test_cropbox_com_mediabox_herdada_vale_a_metade_certa(tmp_path):
    conteudo = "BT /F1 12 Tf 700 700 Td (Texto nativo visivel) Tj ET q 200 0 0 150 700 300 cm /Im1 Do Q"
    p = _pdf(tmp_path / "a.pdf", [0, 0, 1224, 792], conteudo, cropbox=[612, 0, 1224, 792], herdada=True)
    r = lince.Leitor().ler(p)
    assert "Texto nativo visivel" in r.markdown
    assert [x for x in r.cobertura.pendentes if x.endswith("sem modelo de visão configurado")]  # a figura é lida


def test_cropbox_fora_da_mediabox_herdada_vale_a_mediabox(tmp_path):
    conteudo = "BT /F1 12 Tf 1072 700 Td (Texto nativo visivel) Tj ET q 200 0 0 150 1100 300 cm /Im1 Do Q"
    p = _pdf(tmp_path / "b.pdf", [1000, 0, 1612, 792], conteudo, cropbox=[0, 0, 100, 100], herdada=True)
    r = lince.Leitor().ler(p)
    assert "Texto nativo visivel" in r.markdown and not any("erro na página" in x for x in r.cobertura.pendentes)
    assert [x for x in r.cobertura.pendentes if x.endswith("sem modelo de visão configurado")]


def test_releitura_ampliada_da_pagina_girada_vai_de_pe(tmp_path):
    import io

    ampliadas = {}

    def leitor(rotacao):
        def ia(img, instrucao):
            if "SEGUNDA LEITURA" in instrucao:
                ampliadas[rotacao] = Image.open(io.BytesIO(img)).size
                return "Figura lida de novo."
            return "valor [ilegível]"  # a 1ª leitura pede a releitura ampliada

        return lince.Leitor(ia=lince.FuncaoLeitor(ia, "m"), esforco="max", agrupar=False)

    for rotacao in (0, 90):
        conteudo = TEXTO + "q 200 0 0 150 100 500 cm /Im1 Do Q"
        p = _girado(_pdf(tmp_path / f"a{rotacao}.pdf", [0, 0, 612, 792], conteudo), rotacao)
        leitor(rotacao).ler(p)
    (w0, h0), (w90, h90) = ampliadas[0], ampliadas[90]
    assert (w90, h90) == (h0, w0)  # o mesmo pedaço da figura, girado como a página é exibida


def _salvo_girado(origem, destino, rotacao):
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(origem))
    doc[0].set_rotation(rotacao)
    doc.save(str(destino))
    doc.close()
    return destino


def test_releitura_de_duas_figuras_na_mesma_pagina_girada_vai_de_pe(tmp_path):
    import io

    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    for nome, cor in (("a", "navy"), ("b", "darkred")):
        Image.new("RGB", (400, 300), cor).save(tmp_path / f"{nome}.png")
    reto = tmp_path / "duas.pdf"
    c = canvas.Canvas(str(reto), pagesize=letter)
    c.drawString(60, 760, "Planta com duas figuras.")
    c.drawImage(str(tmp_path / "a.png"), 60, 450, width=240, height=180)  # deitadas: mais largas que altas
    c.drawImage(str(tmp_path / "b.png"), 320, 100, width=240, height=180)
    c.save()
    girado = _salvo_girado(reto, tmp_path / "duas90.pdf", 90)

    def ampliadas(p):
        vistas = []

        def ia(img, instrucao):
            if "SEGUNDA LEITURA" in instrucao:
                vistas.append(Image.open(io.BytesIO(img)).size)
                return "Figura lida de novo."
            return "valor [ilegível]"

        lince.Leitor(ia=lince.FuncaoLeitor(ia, "m"), esforco="max", agrupar=False).ler(p)
        return vistas

    retas, giradas = ampliadas(reto), ampliadas(girado)
    assert len(giradas) == 2  # as duas figuras: a segunda também de pé, não só a primeira
    assert giradas == [(h, w) for w, h in retas]


@pytest.mark.parametrize("rotacao", [90, 270])
def test_metades_da_pagina_girada_tem_a_caixa_de_cada_metade(tmp_path, monkeypatch, rotacao):
    from reportlab.pdfgen import canvas

    from lince import pdf as pdf_mod

    monkeypatch.setattr(pdf_mod, "densidade_matematica", lambda md: 9.0)  # a rota "pagina", como numa escaneada
    reto = tmp_path / "paisagem.pdf"  # gravada deitada (792×612): com /Rotate 90 ou 270 é exibida em retrato
    c = canvas.Canvas(str(reto), pagesize=(792, 612))
    c.saveState()
    c.translate(0, 612) if rotacao == 90 else c.translate(792, 0)
    c.rotate(-90 if rotacao == 90 else 90)
    for k in range(70):  # linhas que, de pé, enchem a página: ela é dividida em duas metades
        c.drawString(40, 770 - k * 11, f"Linha {k + 1} do laudo digitalizado, com texto corrido.")
    c.restoreState()
    c.save()
    plano = lince.Leitor().planejar(_salvo_girado(reto, tmp_path / "g.pdf", rotacao), tmp_path / "p")
    a, b = sorted(plano["tarefas"], key=lambda t: t["id"])
    assert (a["id"], b["id"]) == ("p001a", "p001b")
    (ax0, _, ax1, _), (bx0, _, bx1, _) = a["caixa"], b["caixa"]
    assert ax1 - ax0 < 0.6 * 792 and bx1 - bx0 < 0.6 * 792  # cada metade, não a página inteira
    if rotacao == 90:  # o alto da página exibida é a esquerda da página gravada
        assert ax0 == 0 and bx1 == pytest.approx(792)
    else:  # e com 270, a direita
        assert ax1 == pytest.approx(792) and bx0 == 0


def test_pagina_quadrada_girada_tem_a_mesma_resolucao(tmp_path, monkeypatch):
    from lince import pdf as pdf_mod

    monkeypatch.setattr(pdf_mod, "densidade_matematica", lambda md: 9.0)  # a rota "pagina", como numa escaneada
    tamanhos = []
    for rotacao in (0, 90):
        p = _girado(_pdf(tmp_path / f"q{rotacao}.pdf", [0, 0, 700, 700], TEXTO), rotacao)
        tamanhos.append(_recorte(p, tmp_path / f"q{rotacao}").size)
    assert tamanhos[0] == tamanhos[1]  # girada, a quadrada não perde metade da resolução
