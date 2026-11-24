"""O teto inclui as leituras ampliadas da segunda passada."""

import pytest
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import lince


@pytest.mark.parametrize(("teto", "esperadas"), [(0, 0), (1, 1), (3, 3), (None, 4)])
def test_teto_inclui_segunda_passada(tmp_path, teto, esperadas):
    pdf = tmp_path / "figuras.pdf"
    c = canvas.Canvas(str(pdf))
    for cor in ("red", "blue"):
        c.drawString(60, 760, "Boletim com figura de inspeção.")
        c.drawImage(ImageReader(Image.new("RGB", (600, 350), cor)), 60, 350, width=400, height=250)
        c.showPage()
    c.save()
    chamadas = []

    def ler(imagem, instrucao):
        chamadas.append(instrucao)
        return "confirmado" if "SEGUNDA LEITURA" in instrucao else "[ilegível]"

    leitor = lince.Leitor(ia=lince.FuncaoLeitor(ler), esforco="max", max_leituras=teto, agrupar=False)
    resultado = leitor.ler(pdf)
    assert len(chamadas) == resultado.cobertura.leituras_ia == len(resultado.uso) == esperadas
    if teto == 1:
        assert all("SEGUNDA LEITURA" not in chamada for chamada in chamadas)
        assert resultado.cobertura.pendentes


def test_cache_interno_do_provedor_tambem_consume_uma_chamada(tmp_path):
    class ProvedorComCache:
        modelo = "cache-do-provedor"
        chamadas = 0

        def ler(self, imagem, instrucao, extras=None):
            self.chamadas += 1
            return lince.RespostaIA("[ilegível]", lince.Uso(self.modelo, cache=True))

    pdf = tmp_path / "figura.pdf"
    c = canvas.Canvas(str(pdf))
    c.drawString(60, 760, "Boletim com figura.")
    c.drawImage(ImageReader(Image.new("RGB", (300, 200), "red")), 60, 400, 300, 200)
    c.save()
    provedor = ProvedorComCache()
    lince.Leitor(ia=provedor, esforco="max", max_leituras=1).ler(pdf)
    assert provedor.chamadas == 1
