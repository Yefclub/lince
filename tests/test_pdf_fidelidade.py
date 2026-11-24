"""Leitura de campos AcroForm, colunas e página raster com camada de OCR."""

import io
import re

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from lince import Leitor


def test_campos_visiveis_valores_estados_e_branco(tmp_path):
    def formulario(nome, valor):
        arquivo = tmp_path / nome
        c = canvas.Canvas(str(arquivo), pagesize=(600, 800))
        c.drawString(20, 760, "Ficha de teste")
        f = c.acroForm
        f.textfield(name="Ordem", value=valor, x=100, y=650)
        f.choice(name="Status", options=["Pendente", "Aprovado"], value="Aprovado", x=100, y=600)
        f.checkbox(name="Aceite", checked=bool(valor), x=100, y=550)
        f.textfield(name="Restrito", value="segredo123", fieldFlags="password", x=100, y=450)
        f.textfield(name="Interno", value="oculto123", annotationFlags="hidden", x=100, y=400)
        f.textfield(name="Exterior", value="fora123", x=650, y=100)
        f.radio(name="Turno", value="Norte", selected=True, x=50, y=320)
        f.radio(name="Turno", value="Sul", selected=False, x=90, y=320)
        f.listbox(
            name="Unidades",
            options=["Alfa", "Beta", "Gama"],
            value=["Alfa", "Gama"],
            fieldFlags="multiSelect",
            x=100,
            y=190,
        )
        c.showPage()
        c.save()
        return Leitor().ler(arquivo)

    cheio = formulario("cheio.pdf", "OS-80317")
    vazio = formulario("vazio.pdf", "")
    assert "Ordem: OS-80317" in cheio.markdown and "OS-80317" not in vazio.markdown
    assert "Status: Aprovado" in cheio.markdown and "Pendente" not in cheio.markdown
    assert "Aceite: [x]" in cheio.markdown and "Aceite: [ ]" in vazio.markdown
    assert all(t not in cheio.markdown for t in ("segredo123", "oculto123", "fora123"))
    assert "Turno: [x] Norte" in cheio.markdown and "Turno: [ ] Sul" in cheio.markdown
    assert "Unidades: Alfa, Gama" in cheio.markdown and "Beta" not in cheio.markdown
    assert not cheio.cobertura.pendentes


def test_duas_colunas_com_titulos_e_tabela_sem_titulos(tmp_path):
    arquivo = tmp_path / "colunas.pdf"
    c = canvas.Canvas(str(arquivo), pagesize=(650, 800))
    termos = [
        "Etapa de coleta",
        "Liberar amostra",
        "Registrar peso",
        "Etapa de análise",
        "Medir densidade",
        "Validar resultado",
    ]
    for i in range(3):
        c.setFont("Helvetica-Bold" if i == 0 else "Helvetica", 15 if i == 0 else 11)
        for j in range(2):
            c.drawString(48 + j * 320, 700 - i * 26, termos[3 * j + i])
    c.showPage()
    for i in range(3):
        c.drawString(48, 700 - i * 26, f"Setor {i}")
        c.drawString(368, 700 - i * 26, f"Valor {i}")
    c.showPage()
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(325, 747, "Título comum às duas colunas")
    for i in range(3):
        c.drawString(48, 700 - i * 26, f"Rótulo {i}")
        c.drawString(368, 700 - i * 26, f"Dado {i}")
    c.showPage()
    c.save()
    md = Leitor().ler(arquivo).markdown
    pos = [md.index(t) for t in termos]
    assert pos == sorted(pos)
    assert re.search(r"Setor 0.*Valor 0.*Setor 1.*Valor 1", md, re.S)
    assert re.search(r"Título comum às duas colunas.*Rótulo 0.*Dado 0.*Rótulo 1.*Dado 1", md, re.S)


def test_imagem_de_pagina_com_ocr_tem_reserva_e_preserva_texto_digital(tmp_path):
    arquivo = tmp_path / "camadas.pdf"
    c = canvas.Canvas(str(arquivo), pagesize=(450, 620))
    img = ImageReader(Image.new("RGB", (225, 310), "lightgray"))
    linhas = [f"Amostra do lote {i} recebeu parecer favorável na conferência." for i in range(10)]
    for legenda in ("", "Texto digital independente"):
        c.drawImage(img, 0, 0, 450, 620)
        t = c.beginText(30, 560)
        t.setTextRenderMode(3)
        for linha in linhas:
            t.textLine(linha)
        t.setTextRenderMode(0)
        c.drawText(t)
        if legenda:
            c.drawString(30, 20, legenda)
        c.showPage()
    c.save()
    leitor = Leitor()
    plano = leitor.preparar(arquivo)
    assert plano.tarefas
    resposta = "\n\n".join(linhas)
    completo = leitor.concluir(plano, {t.id: resposta for t in plano.tarefas})
    primeira = completo.markdown.split("<!-- página 2")[0]
    assert primeira.count(linhas[0]) == 1
    assert "Texto digital independente" in completo.markdown
    parcial = leitor.concluir(plano, {t.id: linhas[0] for t in plano.tarefas})
    assert linhas[-1] in parcial.markdown and any(
        "transcrição visual parcial" in p for p in parcial.cobertura.pendentes
    )
    sem_ia = leitor.concluir(plano, {})
    assert linhas[0] in sem_ia.markdown and sem_ia.cobertura.pendentes


def test_aparencia_do_botao_sem_valor_e_teto_do_formulario(tmp_path, monkeypatch):
    from lince import pdf_formulario

    arquivo = tmp_path / "botao.pdf"
    c = canvas.Canvas(str(arquivo), pagesize=(460, 600))
    c.drawString(20, 550, "Ficha com botão")
    c.acroForm.checkbox(name="Botão", checked=True, fieldFlags="pushButton", x=60, y=410, size=45)
    c.acroForm.textfield(name="Protocolo", value="AB-123", x=40, y=280)
    c.showPage()
    c.save()
    leitor = Leitor()
    plano = leitor.preparar(arquivo)
    (tarefa,) = plano.tarefas
    with Image.open(io.BytesIO(tarefa.imagem)) as imagem:
        assert imagem.convert("L").getextrema()[0] < 100
    assert "AB-123" in leitor.concluir(plano, {tarefa.id: "Botão de confirmação"}).markdown
    monkeypatch.setattr(pdf_formulario, "ANOTACOES_MAX", 1)
    limitado = leitor.ler(arquivo)
    assert "Ficha com botão" in limitado.markdown and "AB-123" not in limitado.markdown
    assert any("teto de anotações" in p for p in limitado.cobertura.pendentes)
