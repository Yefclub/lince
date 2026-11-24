from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

import lince


def test_texto_e_link_do_word_preservam_o_significado(tmp_path):
    doc = Document()
    sup = doc.styles.add_style("Potência", WD_STYLE_TYPE.CHARACTER)
    sup.font.superscript = True
    p = doc.add_paragraph("Fator: 10")
    p.add_run("3", style=sup)
    p = doc.add_table(1, 1).cell(0, 0).paragraphs[0]
    p.add_run("CO")
    p.add_run("2").font.subscript = True
    p = doc.add_paragraph("Prazo: ")
    p.add_run("90 dias").font.strike = True
    p.add_run(" 15 dias")
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), p.part.relate_to("https://example.org/revisao/23", RT.HYPERLINK, is_external=True))
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "Abrir revisão"
    r.append(t)
    link.append(r)
    p._p.append(link)
    path = tmp_path / "ordem.docx"
    doc.save(path)
    out = lince.ler(path)
    assert "10³" in out.markdown and "CO₂" in out.markdown
    assert "<del>90 dias</del>" in out.markdown and "15 dias" in out.markdown
    assert "[Abrir revisão](https://example.org/revisao/23)" in out.markdown
    assert not out.cobertura.pendentes


def test_rtf_conserva_hiperlink_e_rotulo(tmp_path):
    path = tmp_path / "ordem.rtf"
    path.write_bytes(
        rb'{\rtf1\ansi Antes. {\field{\*\fldinst HYPERLINK "https://example.org/revisao/23"}'
        rb"{\fldrslt Abrir manual}} Depois.}"
    )
    md = lince.ler(path).markdown
    assert "[Abrir manual](https://example.org/revisao/23)" in md
    assert md.index("Antes.") < md.index("Abrir manual") < md.index("Depois.")


def test_palavras_em_expoente_e_risco_duplo(tmp_path):
    doc = Document()
    p = doc.add_paragraph("Medida")
    p.add_run("total").font.superscript = True
    p = doc.add_paragraph("Versão ")
    p.add_run("anterior").font.double_strike = True
    p.add_run(" vigente")
    arq = tmp_path / "variantes.docx"
    doc.save(arq)
    r = lince.ler(arq)
    assert "Medida<sup>total</sup>" in r.markdown
    assert "<del>anterior</del> vigente" in r.markdown
    assert not r.metadados.get("partes_ilegiveis") and not r.cobertura.pendentes


def test_link_interno_aponta_para_bookmark_no_paragrafo(tmp_path):
    doc = Document()
    p = doc.add_paragraph("Consulte ")
    ref = OxmlElement("w:hyperlink")
    ref.set(qn("w:anchor"), "etapa_2")
    run = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "a próxima etapa"
    run.append(t)
    ref.append(run)
    p._p.append(ref)
    p = doc.add_paragraph()
    inicio = OxmlElement("w:bookmarkStart")
    inicio.set(qn("w:id"), "3")
    inicio.set(qn("w:name"), "etapa_2")
    p._p.append(inicio)
    p.add_run("Liberação da unidade.")
    fim = OxmlElement("w:bookmarkEnd")
    fim.set(qn("w:id"), "3")
    p._p.append(fim)
    arq = tmp_path / "interno.docx"
    doc.save(arq)
    md = lince.ler(arq).markdown
    assert "[a próxima etapa](#etapa_2)" in md and '<a id="etapa_2"></a>Liberação' in md
