"""A página visual conserva texto digital utilizável quando a leitura falta."""

import io

from PIL import Image
from reportlab.pdfgen import canvas

import lince


def test_matematica_densa_preserva_o_paragrafo_sem_visao(tmp_path):
    arquivo = tmp_path / "matematica.pdf"
    c = canvas.Canvas(str(arquivo))
    frase = "Conclusão da inspeção registrada no texto nativo."
    c.drawString(60, 755, frase)
    for i in range(8):
        c.drawString(60, 690 - 32 * i, "x = 2 × 4 ± 3 ÷ 5 · 2 ± 1")
    c.save()
    leitor = lince.Leitor()
    plano = leitor.preparar(arquivo)
    (tarefa,) = plano.tarefas
    assert tarefa.tipo == "pagina"
    for resultado in (leitor.ler(arquivo), leitor.concluir(plano, {}), leitor.concluir(plano, {tarefa.id: ""})):
        assert frase in resultado.markdown and "[página: pendente]" in resultado.markdown
        assert resultado.cobertura.pendentes
    completa = leitor.concluir(plano, {tarefa.id: frase})
    assert completa.markdown.count(frase) == 1 and not completa.cobertura.pendentes


def test_metade_visual_e_metade_nativa_nao_repetem_as_linhas(tmp_path):
    arq = tmp_path / "duas.pdf"
    c = canvas.Canvas(str(arq), pagesize=(500, 900))
    c.setFont("Helvetica", 8)
    for i in range(66):
        c.drawString(30, 860 - i * 12, f"Registro {i:02d} com 2 × 3 ± 4 ÷ 5.")
    c.save()
    leitor = lince.Leitor()
    plano = leitor.preparar(arq)
    primeira, segunda = plano.tarefas
    with Image.open(io.BytesIO(primeira.imagem)) as cima, Image.open(io.BytesIO(segunda.imagem)) as baixo:
        assert cima.crop((0, cima.height - 2, cima.width, cima.height)).convert("L").getextrema()[0] >= 245
        assert baixo.crop((0, 0, baixo.width, 2)).convert("L").getextrema()[0] >= 245
    for t, nativa, visual in ((primeira, "Registro 65", "Registro 00"), (segunda, "Registro 00", "Registro 65")):
        r = leitor.concluir(plano, {t.id: visual + " confirmado pela visão."})
        assert nativa in r.markdown and r.markdown.count(visual) == 1
        assert "[página: pendente]" in r.markdown and r.cobertura.pendentes
