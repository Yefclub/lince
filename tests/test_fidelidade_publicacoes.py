import io
import json
import zipfile

from PIL import Image

import lince


def _epub(tmp_path, alt="d x", outras=()):
    path = tmp_path / "livro.epub"
    imagens = [(0, alt), *outras]
    cores = sorted({cor for cor, _ in imagens})
    elementos = " ".join(f'<img src="im{cor}.png" alt="{texto}"/>' for cor, texto in imagens)
    itens = "".join(f"<li><p>Nota {n} começa.</p><p>Continuação da nota {n}.</p></li>" for n in range(1, 12))
    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Livro</title></head><body>'
        f"<p>Um verso<br/>Outro verso<br/>Último verso</p><ol>{itens}</ol>"
        f"<p>Integrar {elementos} na equação.</p></body></html>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
            "<rootfiles>"
            '<rootfile full-path="livro.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles>"
            "</container>",
        )
        z.writestr(
            "livro.opf",
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="a">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="a">123</dc:identifier>'
            "<dc:title>Livro</dc:title>"
            "<dc:language>pt</dc:language>"
            "</metadata>"
            "<manifest>"
            '<item id="cap" media-type="application/xhtml+xml" href="cap.xhtml"/>'
            + "".join(f'<item id="im{cor}" media-type="image/png" href="im{cor}.png"/>' for cor in cores)
            + "</manifest>"
            "<spine>"
            '<itemref idref="cap"/>'
            "</spine>"
            "</package>",
        )
        z.writestr("cap.xhtml", xhtml)
        for cor in cores:
            png = io.BytesIO()
            Image.new("RGB", (200, 90), (cor, 30, 120)).save(png, "PNG")
            z.writestr(f"im{cor}.png", png.getvalue())
    return path


def test_epub_preserva_versos_e_paragrafos_de_notas(tmp_path):
    md = lince.ler(_epub(tmp_path)).markdown
    assert "Um verso  \nOutro verso  \nÚltimo verso" in md
    assert "10. Nota 10 começa." in md and "\n    Continuação da nota 10." in md


def test_epub_preserva_alt_so_quando_leitura_visual_falta(tmp_path):
    l = lince.Leitor()
    p = l.preparar(_epub(tmp_path))
    (t,) = p.tarefas
    sem = l.concluir(p, {})
    assert "d x" in sem.markdown and sem.cobertura.pendentes
    com = l.concluir(p, {t.id: "Representação da integral"})
    assert "Representação da integral" in com.markdown and "d x" not in com.markdown


def test_html_ancora_interna_e_titulo_conservados(tmp_path):
    p = tmp_path / "norma.html"
    p.write_text(
        '<a href="#etapa-3.1">Leia a etapa</a><section id="etapa-3.1"><h2>Decisão</h2>'
        "<p>Conferir os dados.</p></section>",
        encoding="utf-8",
    )
    r = lince.ler(p)
    assert "href=" not in r.markdown and "[Leia a etapa](#etapa-3.1)" in r.markdown
    assert '<a id="etapa-3.1"></a>' in r.markdown and "## Decisão" in r.markdown


def test_html_resolve_id_e_nome_do_mesmo_destino_sem_mudar_fragmentos(tmp_path):
    p = tmp_path / "destinos.html"
    p.write_text(
        '<p><a href="#secao--1">Atual</a> e <a href="#versao(a)--2">Anterior</a>.</p>'
        '<a id="secao--1" name="versao(a)--2"></a><p>Texto--explicativo.</p>',
        encoding="utf-8",
    )
    md = lince.ler(p).markdown
    for ident, rotulo in [("secao--1", "Atual"), ("versao(a)--2", "Anterior")]:
        assert f"[{rotulo}](#{ident})" in md
        assert md.count(f'<a id="{ident}"></a>') == 1
    assert "Texto—explicativo." in md


def test_html_prefere_id_real_ao_nome_legado_e_ao_fragmento_decodificado(tmp_path):
    p = tmp_path / "prioridade.html"
    p.write_text(
        '<a href="#A%2DB">Destino</a><a name="A%2DB">Antigo</a>'
        '<section id="A-B">Decodificado</section><section id="A%2DB">Literal</section>',
        encoding="utf-8",
    )
    md = lince.ler(p).markdown
    assert md.count('<a id="A%2DB"></a>') == 1 and '<a id="A-B"></a>' not in md
    assert md.index("Decodificado") < md.index('<a id="A%2DB"></a>') < md.index("Literal")


def test_alternativa_extensa_nao_expulsa_leitura_curta(tmp_path, monkeypatch):
    from lince import leitor

    monkeypatch.setattr(leitor, "SAIDA_MAX", 1500)
    l = lince.Leitor()
    plano = l.preparar(_epub(tmp_path, "texto " * 1000))
    (t,) = plano.tarefas
    resultado = l.concluir(plano, {t.id: "Resposta curta."})
    assert "Resposta curta." in resultado.markdown and len(resultado.markdown) < 1500
    assert not resultado.cobertura.pendentes


def test_ancora_em_codigo_fica_fora_do_bloco(tmp_path):
    from lxml import html
    from markdown_it import MarkdownIt

    p = tmp_path / "codigo.html"
    p.write_text('<a href="#x">Ir</a><pre><code id="x">print(42)</code></pre>', encoding="utf-8")
    md = lince.ler(p).markdown
    doc = html.fromstring(MarkdownIt("commonmark", {"html": True}).render(md))
    assert doc.xpath("//a[@id='x' and not(ancestor::pre)]")
    assert "print(42)" in doc.xpath("//pre")[0].text_content()


def test_prefixo_com_duas_alternativas_cabe_apesar_da_resposta_seguinte(tmp_path, monkeypatch):
    monkeypatch.setattr(lince.leitor, "SAIDA_MAX", 1500)
    l = lince.Leitor()
    plano = l.preparar(_epub(tmp_path, "reserva " * 1000, [(1, "Segunda reserva")]))
    a, b = plano.tarefas
    r = l.concluir(plano, {a.id: "Primeira leitura", b.id: "z" * 6000})
    assert "Primeira leitura" in r.markdown and "Segunda reserva" in r.markdown
    assert r.cobertura.pendentes == [f"{b.id}: resultado grande demais"]
    assert len(r.markdown) <= 1500
    monkeypatch.setattr(lince.leitor, "SAIDA_MAX", len(r.markdown))
    assert l.concluir(plano, {a.id: "Primeira leitura", b.id: "z" * 6000}).markdown == r.markdown


def test_prefixo_pode_voltar_a_caber_quando_uma_reserva_longa_sai(tmp_path, monkeypatch):
    monkeypatch.setattr(lince.leitor, "SAIDA_MAX", 1500)
    l = lince.Leitor(agrupar=False)
    plano = l.preparar(_epub(tmp_path, "Curta", [(1, "reserva " * 1000), (2, "Terceira")]))
    a, b, c = plano.tarefas
    r = l.concluir(plano, {a.id: "Um", b.id: "Dois", c.id: "z" * 6000})
    assert r.cobertura.pendentes == [f"{c.id}: resultado grande demais"]
    assert all(t in r.markdown for t in ("Um", "Dois", "Terceira")) and len(r.markdown) <= 1500


def test_lote_com_imagem_repetida_e_alternativas_proprias(tmp_path, monkeypatch):
    monkeypatch.setattr(lince.leitor, "SAIDA_MAX", 2800)
    l = lince.Leitor()
    outras = [(i, f"Descrição {i}") for i in range(1, 13)] + [(0, "alternativa repetida " * 500)]
    plano = l.preparar(_epub(tmp_path, "alternativa original " * 500, outras))
    primeiro, segundo = plano.tarefas
    assert [len(plano.estado["lotes"][t.id]) for t in plano.tarefas] == [12, 1]
    itens = {str(i): f"Leitura do item {i}." for i in range(1, 13)}
    itens["2"] = ""
    r = l.concluir(plano, {primeiro.id: json.dumps(itens), segundo.id: json.dumps({"1": "z" * 6000})})
    assert r.markdown.count("Leitura do item 1.") == 1 and "mesma imagem lida acima" in r.markdown
    assert "Descrição 1" in r.markdown and "Descrição 12" in r.markdown and "alternativa repetida" not in r.markdown
    assert f"{segundo.id}: resultado grande demais" in r.cobertura.pendentes and len(r.markdown) <= 2800
