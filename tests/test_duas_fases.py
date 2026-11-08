"""Leitura em duas fases: o Lince extrai e diz o que a IA precisa ler; quem chama lê com o próprio modelo e o Lince
monta o mesmo resultado que sairia com a IA dele (issue #33, item 5)."""

import io
import json

from docx import Document
from docx.shared import Inches
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

import lince


def _png(cor="navy", tamanho=(600, 300)):
    im = Image.new("RGB", tamanho, "white")
    ImageDraw.Draw(im).rectangle([50, 50, 400, 250], fill=cor)
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def _docx(tmp_path):
    d = Document()
    d.add_paragraph("Relatório de moagem.")
    d.add_picture(io.BytesIO(_png()), width=Inches(3))
    d.add_paragraph("Entre as figuras.")
    d.add_picture(io.BytesIO(_png("darkgreen")), width=Inches(3))
    p = tmp_path / "r.docx"
    d.save(p)
    return p


def _pdf(tmp_path):
    png = tmp_path / "g.png"
    png.write_bytes(_png())
    p = tmp_path / "g.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    c.drawString(60, 780, "Moagem anual por unidade, em mil toneladas.")
    c.drawImage(str(png), 60, 400, width=450, height=250)
    c.save()
    return p


def _resposta(imagem, instrucao):
    cor = Image.open(io.BytesIO(imagem)).convert("RGB").getpixel((150, 100))
    return f"Figura com a cor {cor}."


def test_duas_fases_dao_o_mesmo_resultado_que_a_ia_do_lince(tmp_path):
    for p in (_docx(tmp_path), _pdf(tmp_path)):
        direto = lince.Leitor(ia=lince.FuncaoLeitor(_resposta, "m"), agrupar=False).ler(p)
        leitor = lince.Leitor(agrupar=False)
        plano = leitor.preparar(p)
        assert plano.tarefas and all(t.imagem.startswith(b"\x89PNG") and t.tokens_estimados > 0 for t in plano.tarefas)
        r = leitor.concluir(plano, {t.id: _resposta(t.imagem, t.instrucao) for t in plano.tarefas})
        assert r.markdown == direto.markdown and r.metadados == direto.metadados, p.name
        assert r.cobertura.pendentes == [] and r.cobertura.leituras_ia == len(plano.tarefas)


def test_plano_passa_por_json_e_diz_onde_fica_cada_leitura(tmp_path):
    leitor = lince.Leitor(agrupar=False)
    plano = lince.Plano.de_json(json.loads(json.dumps(leitor.preparar(_docx(tmp_path)).para_json())))
    assert [t.onde for t in plano.tarefas] == ["imagem 1", "imagem 2"]
    assert len({t.chave for t in plano.tarefas}) == 2 and all(t.tipo == "imagem" for t in plano.tarefas)
    pdf = leitor.preparar(_pdf(tmp_path))
    (t,) = pdf.tarefas
    assert t.pagina == 1 and len(t.caixa) == 4 and t.onde == ""


def test_tarefa_sem_resposta_fica_pendente_e_o_nativo_segue(tmp_path):
    leitor = lince.Leitor(agrupar=False)
    plano = leitor.preparar(_docx(tmp_path))
    primeira, segunda = plano.tarefas
    r = leitor.concluir(plano, {primeira.id: "Barra azul.", "inventada": "não entra"})
    assert "Barra azul." in r.markdown and "Entre as figuras." in r.markdown and "não entra" not in r.markdown
    assert r.cobertura.pendentes == [f"{segunda.id}: sem resposta"] and r.cobertura.leituras_ia == 1


def test_lote_de_recortes_vem_com_os_itens(tmp_path):
    d = Document()
    for k in range(3):
        d.add_paragraph(f"Ícone {k}.")
        d.add_picture(io.BytesIO(_png(["navy", "red", "gold"][k], (420, 200))), width=Inches(1))  # pequenas: lote
    p = tmp_path / "icones.docx"
    d.save(p)
    plano = lince.Leitor().preparar(p)
    lotes = [t for t in plano.tarefas if t.tipo == "lote"]
    assert lotes and [it["n"] for it in lotes[0].itens] == list(range(1, len(lotes[0].itens) + 1))
    resposta = json.dumps({str(it["n"]): f"recorte {it['n']}" for it in lotes[0].itens})
    r = lince.Leitor().concluir(plano, {lotes[0].id: resposta})
    assert "recorte 1" in r.markdown and "recorte 2" in r.markdown
