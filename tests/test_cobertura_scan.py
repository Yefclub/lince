"""A tarefa recebida e a transcrição parcial são estados distintos em scans."""

import io

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from lince import FuncaoLeitor, Leitor


def _digitalizado(arquivo, partes=False, rodape="", linhas=None, assinatura="", lateral="", cabecalho=""):
    imagem = Image.new("RGB", (1500, 2400), "white")
    desenho = ImageDraw.Draw(imagem)
    fonte = ImageFont.load_default(size=27)
    if linhas is None:
        linhas = [f"Setor de inspeção {n:03}: Turno recebeu nota e registrou cada ponto." for n in range(70)]
    for n, linha in enumerate(linhas):
        desenho.text((50, 25 + 32 * n), linha, font=fonte, fill="black")
    pdf = canvas.Canvas(str(arquivo), pagesize=(500, 800))
    if partes:
        largura = 410 if lateral else 500
        pdf.drawImage(ImageReader(imagem.crop((0, 0, 1500, 1200))), 0, 425, largura, 355)
        pdf.drawImage(ImageReader(imagem.crop((0, 1200, 1500, 2400))), 0, 30, largura, 365)
        if assinatura:
            pdf.drawString(35, 408, assinatura)
        if lateral:
            pdf.drawString(427, 550, lateral)
    else:
        pdf.drawImage(ImageReader(imagem), 0, 35 if rodape else 0, 500, 765 if rodape else 800)
    if cabecalho:
        pdf.drawString(35, 786, cabecalho)
    if rodape:
        pdf.drawString(35, 20, rodape)
        pdf.setFillColorRGB(1, 1, 1)
        pdf.drawString(120, 10, "OCULTO_NO_RODAPE")
        pdf.setFillColorRGB(0, 0, 0)
        pdf.setFillAlpha(0)
        pdf.drawString(130, 20, "OCULTO_TRANSPARENTE")
    pdf.save()
    return linhas


def test_scan_preserva_recortes_e_distingue_faixa_ausente_de_parcial(tmp_path):
    arquivo = tmp_path / "scan.pdf"
    linhas = _digitalizado(arquivo)
    leitor = Leitor(agrupar=False)
    plano = leitor.preparar(arquivo)
    superior, inferior = plano.tarefas
    assert superior.pagina == inferior.pagina == 1
    assert superior.caixa[3] == 800 and inferior.caixa[1] == 0
    assert superior.caixa[1] < inferior.caixa[3]
    with Image.open(io.BytesIO(superior.imagem)) as a, Image.open(io.BytesIO(inferior.imagem)) as b:
        sobreposicao = round((inferior.caixa[3] - superior.caixa[1]) * a.height / (800 - superior.caixa[1]))
        assert (
            a.crop((0, a.height - sobreposicao, a.width, a.height)).tobytes()
            == b.crop((0, 0, b.width, sobreposicao)).tobytes()
        )

    acima = "\n".join(linhas[:35])
    abaixo = "\n".join(linhas[35:])
    completo = leitor.concluir(plano, {superior.id: acima, inferior.id: abaixo})
    assert len(completo.cobertura.pendentes) == 2 and linhas[-1] in completo.markdown
    assert all("integralidade da transcrição visual não verificada" in p for p in completo.cobertura.pendentes)

    faltou = leitor.concluir(plano, {superior.id: acima})
    assert f"{inferior.id}: sem resposta" in faltou.cobertura.pendentes
    assert not any("transcrição visual parcial" in p for p in faltou.cobertura.pendentes)
    assert "nao_lida" in faltou.estrutura["paginas_visuais"][0]["alertas"]

    resposta = "\n".join(linhas[35:49])
    parcial = leitor.concluir(plano, {superior.id: acima, inferior.id: resposta})
    (pendencia,) = [p for p in parcial.cobertura.pendentes if "transcrição visual parcial" in p]
    assert f"página 1, faixa 2/2 ({inferior.id})" in pendencia and "transcrição visual parcial" in pendencia
    assert linhas[48] in parcial.markdown
    assert "nao_lida" not in parcial.estrutura["paginas_visuais"][0].get("alertas", [])

    def leitura(_, instrucao):
        return resposta if "parte 2 de 2" in instrucao else acima

    direta = Leitor(ia=FuncaoLeitor(leitura, "teste"), agrupar=False).ler(arquivo)
    assert direta.cobertura.pendentes == parcial.cobertura.pendentes
    assert direta.markdown == parcial.markdown


def test_scan_misto_preserva_texto_digital_sem_duplicar(tmp_path):
    for partes, rodape in ((False, "17"), (True, "Validado digitalmente")):
        arquivo = tmp_path / f"misto-{partes}.pdf"
        linhas = _digitalizado(arquivo, partes=partes, rodape=rodape)
        leitor = Leitor(agrupar=False)
        plano = leitor.preparar(arquivo)
        cima, baixo = plano.tarefas
        assert cima.caixa[1] < baixo.caixa[3]
        fonte = {cima.id: "\n".join(linhas[:35]), baixo.id: "\n".join(linhas[35:])}
        respostas = [fonte, {cima.id: fonte[cima.id]}, fonte | {baixo.id: fonte[baixo.id] + f"\n**{rodape}**"}]
        for textos in respostas:
            r = leitor.concluir(plano, textos)
            assert sum(linha.strip("*") == rodape for linha in r.markdown.splitlines()) == 1
            assert "OCULTO_" not in r.markdown
        r = leitor.concluir(plano, fonte | {baixo.id: linhas[35]})
        assert rodape in r.markdown and linhas[35] in r.markdown
        assert any("faixa 2/2" in p and "transcrição visual parcial" in p for p in r.cobertura.pendentes)


def test_scan_sem_texto_aceita_descricao_curta(tmp_path):
    arquivo = tmp_path / "ilustracao.pdf"
    imagem = Image.new("RGB", (800, 1000), "white")
    desenho = ImageDraw.Draw(imagem)
    desenho.polygon([(0, 730), (260, 250), (450, 620), (680, 420), (800, 720)], fill="darkgreen")
    desenho.rectangle((0, 730, 800, 1000), fill="darkblue")
    desenho.ellipse((560, 120, 655, 215), fill="orange")
    pdf = canvas.Canvas(str(arquivo), pagesize=(400, 500))
    pdf.drawImage(ImageReader(imagem), 0, 0, 400, 500)
    pdf.save()
    leitor = Leitor(ia=FuncaoLeitor(lambda *_: "Montanhas perto de um lago.", "teste"))
    resultado = leitor.ler(arquivo)
    assert "Montanhas perto de um lago." in resultado.markdown
    assert not resultado.cobertura.pendentes


def test_scan_ornamentado_nao_confunde_o_ornamento_com_texto_faltante(tmp_path):
    arquivo = tmp_path / "ornamento.pdf"
    imagem = Image.new("RGB", (800, 1000), "white")
    desenho = ImageDraw.Draw(imagem)
    for y in (44, 86, 128):
        for x in range(40, 760, 13):
            desenho.ellipse((x, y, x + 8, y + 28), outline="black", width=2)
    fonte = ImageFont.load_default(size=24)
    linhas = [f"Ensaio {n + 1:02d}: leitura registrada." for n in range(14)]
    for n, linha in enumerate(linhas):
        desenho.text((48, 220 + n * 43), linha, font=fonte, fill="black")
    pdf = canvas.Canvas(str(arquivo), pagesize=(400, 500))
    pdf.drawImage(ImageReader(imagem), 0, 0, 400, 500)
    pdf.save()

    leitor = Leitor()
    plano = leitor.preparar(arquivo)
    (tarefa,) = plano.tarefas
    completo = leitor.concluir(plano, {tarefa.id: "\n".join(linhas)})
    (pendencia,) = completo.cobertura.pendentes
    assert "integralidade da transcrição visual não verificada" in pendencia
    parcial = leitor.concluir(plano, {tarefa.id: "\n".join(linhas[:7])})
    assert any("transcrição visual parcial" in p for p in parcial.cobertura.pendentes)


def test_scan_misto_intercala_texto_digital_no_vao_e_na_lateral(tmp_path):
    arquivo = tmp_path / "dois-rasters.pdf"
    assinatura, lateral = "Assinatura digital conferida", "Selo digital"
    linhas = _digitalizado(arquivo, partes=True, assinatura=assinatura, lateral=lateral)
    leitor = Leitor(agrupar=False)
    plano = leitor.preparar(arquivo)
    cima, baixo = plano.tarefas
    assert cima.caixa[1] < baixo.caixa[3]
    acima, abaixo = "\n".join(linhas[:35]), "\n".join(linhas[35:])
    for fim in (abaixo, linhas[35]):
        r = leitor.concluir(plano, {cima.id: acima, baixo.id: fim})
        assert r.markdown.index(linhas[0]) < r.markdown.index(assinatura) < r.markdown.index(linhas[35])
        assert lateral in r.markdown
    repetida = leitor.concluir(plano, {cima.id: acima + "\n" + assinatura + "\n" + lateral, baixo.id: abaixo})
    assert repetida.markdown.count(assinatura) == 1
    assert repetida.markdown.count(lateral) == 1


def test_scan_misto_consumo_da_linha_digital_fica_na_faixa_certa(tmp_path):
    texto = "Controle nativo do laboratório"
    for divisoes in (False, True):
        arquivo = tmp_path / f"repetido-{divisoes}.pdf"
        linhas = None if divisoes else [f"Linha {n} da transcrição escaneada." for n in range(32)]
        fonte = _digitalizado(arquivo, partes=True, rodape=texto, linhas=linhas, cabecalho=texto)
        leitor = Leitor(agrupar=False)
        plano = leitor.preparar(arquivo)
        assert len(plano.tarefas) == (2 if divisoes else 1)
        respostas = {t.id: "\n".join(fonte[k * 35 : k * 35 + 35]) for k, t in enumerate(plano.tarefas)}
        respostas[plano.tarefas[0].id] = texto + "\n" + respostas[plano.tarefas[0].id]
        r = leitor.concluir(plano, respostas)
        assert r.markdown.count(texto) == 2
        assert r.markdown.strip().endswith(texto)
