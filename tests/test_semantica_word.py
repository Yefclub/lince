from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn

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


def test_clausulas_por_estilo_conservam_nivel_reinicio_e_override(tmp_path):
    doc = Document()
    num = doc.part.numbering_part.element
    num.insert(
        0,
        parse_xml(
            f'<w:abstractNum {nsdecls("w")} w:abstractNumId="90">'
            '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="upperRoman"/>'
            '<w:pStyle w:val="Capitulo"/><w:lvlText w:val="%1."/></w:lvl>'
            '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
            '<w:pStyle w:val="Clausula"/><w:lvlText w:val="%1.%2"/></w:lvl>'
            "</w:abstractNum>"
        ),
    )
    num.append(
        parse_xml(
            f'<w:num {nsdecls("w")} w:numId="91"><w:abstractNumId w:val="90"/>'
            '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="3"/></w:lvlOverride></w:num>'
        )
    )
    for estilo in ("Capitulo", "Clausula"):
        pr = doc.styles.add_style(estilo, WD_STYLE_TYPE.PARAGRAPH).element.get_or_add_pPr()
        pr.append(parse_xml(f'<w:numPr {nsdecls("w")}><w:numId w:val="91"/></w:numPr>'))
    for titulo, itens in (("Requisitos", ["Escopo", "III.2 Prazos"]), ("Controle", ["Aceite"])):
        doc.add_paragraph(titulo, "Capitulo")
        for item in itens:
            doc.add_paragraph(item, "Clausula")
    path = tmp_path / "contrato.docx"
    doc.save(path)
    r = lince.ler(path)
    assert all(
        s in r.markdown for s in ("III. Requisitos", "III.1 Escopo", "III.2 Prazos", "IV. Controle", "IV.1 Aceite")
    )
    assert r.markdown.count("III.2") == 1 and not r.cobertura.pendentes


def test_caixas_legadas_preservam_estado_no_lugar_do_campo(tmp_path):
    doc = Document()
    p = doc.add_table(1, 1).cell(0, 0).paragraphs[0]
    for nome, estado, cache in (
        ("opção A", '<w:default w:val="1"/><w:checked w:val="0"/>', "☑"),
        ("opção B", '<w:default w:val="0"/><w:checked/>', "☐"),
        ("opção C", '<w:default w:val="on"/>', ""),
        ("opção D", "", ""),
    ):
        p._p.append(
            parse_xml(
                f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="begin"><w:ffData>'
                f"<w:checkBox><w:sizeAuto/>{estado}</w:checkBox></w:ffData></w:fldChar>"
                '<w:instrText> FORMCHECKBOX </w:instrText><w:fldChar w:fldCharType="separate"/>'
                f'<w:t>{cache}</w:t><w:fldChar w:fldCharType="end"/></w:r>'
            )
        )
        p.add_run(f" {nome} ")
    p.add_run("☐ literal")
    p = doc.add_paragraph()
    p._p.append(
        parse_xml(
            f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="begin"><w:ffData>'
            "<w:checkBox><w:checked/></w:checkBox></w:ffData></w:fldChar>"
            '<w:instrText> FORMTEXT </w:instrText><w:fldChar w:fldCharType="separate"/>'
            '<w:t>Resultado textual</w:t><w:fldChar w:fldCharType="end"/></w:r>'
        )
    )
    p = doc.add_paragraph()
    oculto = p.add_run()
    oculto.font.hidden = True
    oculto._r.append(
        parse_xml(
            f'<w:fldChar {nsdecls("w")} w:fldCharType="begin"><w:ffData><w:checkBox>'
            "<w:checked/></w:checkBox></w:ffData></w:fldChar>"
        )
    )
    oculto._r.append(parse_xml(f"<w:instrText {nsdecls('w')}> FORMCHECKBOX </w:instrText>"))
    oculto._r.append(parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="end"/>'))
    path = tmp_path / "formulario.docx"
    doc.save(path)
    r = lince.ler(path)
    assert "[ ] opção A [x] opção B [x] opção C [ ] opção D ☐ literal" in r.markdown
    assert r.markdown.count("[x]") == 2 and r.markdown.count("☐") == 1
    assert "Resultado textual" in r.markdown


def test_letras_do_word_repetem_em_ciclos_de_26_inclusive_no_nivel_pai(tmp_path):
    doc = Document()
    num = doc.part.numbering_part.element
    for nid, formato, mascara in [(90, "upperLetter", "%1."), (91, "lowerLetter", "%1)"), (92, "upperLetter", "%1")]:
        num.insert(
            0,
            parse_xml(
                f'<w:abstractNum {nsdecls("w")} w:abstractNumId="{nid}">'
                f'<w:lvl w:ilvl="0"><w:start w:val="26"/><w:numFmt w:val="{formato}"/>'
                f'<w:lvlText w:val="{mascara}"/></w:lvl>'
                '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
                '<w:lvlText w:val="AB: %1.%2"/></w:lvl></w:abstractNum>'
            ),
        )
        num.append(parse_xml(f'<w:num {nsdecls("w")} w:numId="{nid}"><w:abstractNumId w:val="{nid}"/></w:num>'))
        for nivel, texto in [(0, "fim"), (0, "primeira"), (0, "segunda"), (1, "filha")]:
            p = doc.add_paragraph(texto)
            p._p.get_or_add_pPr().append(
                parse_xml(f'<w:numPr {nsdecls("w")}><w:ilvl w:val="{nivel}"/><w:numId w:val="{nid}"/></w:numPr>')
            )
    path = tmp_path / "letras.docx"
    doc.save(path)
    r = lince.ler(path)
    assert all(s in r.markdown for s in ("Z. fim", "AA. primeira", "BB. segunda", "bb) segunda", "BB segunda"))
    assert "AB: BB.1 filha" in r.markdown and "AB: bb.1 filha" in r.markdown
    assert not r.cobertura.pendentes
