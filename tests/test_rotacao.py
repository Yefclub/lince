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
