"""Gráfico do LibreOffice (.ods, .odp, .odt): tipo, título e os dados que ele desenha entram na leitura."""

import time
import zipfile
from pathlib import Path

import lince
from lince import legados, office

REAIS = Path(__file__).parent / "fixtures" / "reais"


def test_grafico_da_planilha_e_da_apresentacao_do_libreoffice():
    for nome, tipo, titulo in (
        ("lince_grafico.ods", "barras", "Vendas por região"),
        ("lince_grafico.odp", "pizza", "Vendas"),
    ):
        md = lince.ler(REAIS / nome).markdown
        assert f"**Gráfico ({tipo}): {titulo}**" in md, nome
        assert "| Norte | 120 |" in md.split("**Gráfico")[1] and "| Sul | 80 |" in md.split("**Gráfico")[1], nome
        assert "Dados." not in md  # a referência de intervalo da célula não vira texto


def test_tabela_do_grafico_tem_teto_registrado(monkeypatch):
    monkeypatch.setattr(office, "_LINHAS_GRAFICO_MAX", 1)
    r = lince.ler(REAIS / "lince_grafico.ods")
    grafico = r.markdown.split("**Gráfico")[1]
    assert "| Norte | 120 |" in grafico and "| Sul |" not in grafico
    assert any("mais de 1 linhas" in x for x in r.metadados["partes_ilegiveis"])


def test_objeto_de_grafico_quebrado_nao_derruba_a_leitura(tmp_path):
    p = tmp_path / "quebrado.ods"
    with zipfile.ZipFile(REAIS / "lince_grafico.ods") as zi, zipfile.ZipFile(p, "w") as zo:
        for info in zi.infolist():
            dados = zi.read(info.filename)
            if info.filename.endswith("Object 1/content.xml"):
                dados = b"<office:document-content><quebrado"
            zo.writestr(info, dados)
    md = lince.ler(p).markdown
    assert "| Norte | 120 |" in md and "**Gráfico" not in md  # a tabela da planilha fica; o gráfico ilegível sai


_NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:chart="urn:oasis:names:tc:opendocument:xmlns:chart:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
)


def _cel(texto="", repete=None):
    attr = f' table:number-columns-repeated="{repete}"' if repete is not None else ""
    return f"<table:table-cell{attr}><text:p>{texto}</text:p></table:table-cell>"


def _objeto(cabecalho, linhas, classe="chart:line", titulo="Mensal"):
    corpo = "".join(f"<table:table-row>{''.join(_cel(c) for c in linha)}</table:table-row>" for linha in linhas)
    return (
        f'<office:document-content {_NS}><office:body><office:chart><chart:chart chart:class="{classe}">'
        f"<chart:title><text:p>{titulo}</text:p></chart:title><table:table>"
        f"<table:table-header-rows><table:table-row>{cabecalho}</table:table-row></table:table-header-rows>"
        f"<table:table-rows>{corpo}</table:table-rows></table:table></chart:chart></office:chart></office:body>"
        "</office:document-content>"
    )


def _ods(tmp_path, *objetos):
    p = tmp_path / "graficos.ods"
    with zipfile.ZipFile(p, "w") as z:
        for n, conteudo in enumerate(objetos, 1):
            z.writestr(f"Object {n}/content.xml", conteudo)
    return p


def test_varias_series_com_cabecalho_em_header_rows(tmp_path):
    objeto = _objeto(_cel() + _cel("Meta") + _cel("Real"), [["Jan", "100", "90"], ["Fev", "110", "120"]])
    [g] = office.graficos_odf(_ods(tmp_path, objeto), [])
    md = office.grafico_md(g)
    assert "**Gráfico (linhas): Mensal**" in md and "| Categoria | Meta | Real |" in md
    assert "| Jan | 100 | 90 |" in md and "| Fev | 110 | 120 |" in md


def test_cabecalho_largo_e_repetido_tem_teto(tmp_path):
    hostil = _objeto(_cel() + _cel("S", 64) * 10_000, [[] for _ in range(1000)])
    ilegiveis = []
    inicio = time.monotonic()
    [g] = office.graficos_odf(_ods(tmp_path, hostil), ilegiveis)
    assert time.monotonic() - inicio < 5
    assert len(g["series"]) == office._SERIES_MAX and any("mais de 64 séries" in x for x in ilegiveis)


def test_repeticao_invalida_nao_derruba_os_outros_graficos(tmp_path):
    bom = _objeto(_cel() + _cel("Meta"), [["Jan", "100"]], titulo="Bom")
    ruim = _objeto(_cel() + _cel("Meta", "x"), [["Jan", "7"]], titulo="Ruim")
    graficos = office.graficos_odf(_ods(tmp_path, bom, ruim), [])
    assert [g["titulo"] for g in graficos] == ["Bom", "Ruim"] and graficos[1]["series"][0]["vals"] == ["7"]


def test_texto_longo_em_celula_repetida_tem_teto(tmp_path):
    longo = "x" * 200_000
    linhas = "".join(f"<table:table-row>{_cel(f'C{n}')}{_cel(longo, 64)}</table:table-row>" for n in range(20))
    objeto = _objeto(_cel() + _cel("S", 64), []).replace(
        "<table:table-rows></table:table-rows>", f"<table:table-rows>{linhas}</table:table-rows>"
    )
    ilegiveis = []
    [g] = office.graficos_odf(_ods(tmp_path, objeto), ilegiveis)
    md = office.grafico_md(g)
    assert len(md) < 20 * 64 * 1100 and any("caracteres, cortada" in x for x in ilegiveis)


def test_muitos_graficos_dividem_um_orcamento_unico(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "_CARACTERES_GRAFICOS_MAX", 1_200_000)
    linhas = "".join(f"<table:table-row>{_cel(f'C{n}')}{_cel('y' * 1024, 64)}</table:table-row>" for n in range(4))
    objeto = _objeto(_cel() + _cel("S", 64), []).replace(
        "<table:table-rows></table:table-rows>", f"<table:table-rows>{linhas}</table:table-rows>"
    )
    ilegiveis = []
    graficos = office.graficos_odf(
        _ods(tmp_path, *[objeto] * 50), ilegiveis
    )  # cada um custa ~265 mil nas células e outro tanto na tabela
    total = sum(len(office.grafico_md(g)) for g in graficos)
    assert 0 < len(graficos) < 50 and total < 1_200_000
    assert sum("os demais ficaram de fora" in x for x in ilegiveis) == 1


def test_linhas_vazias_tambem_pagam_o_orcamento(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "_CARACTERES_GRAFICOS_MAX", 2_000_000)
    vazio = _objeto(_cel() + _cel("S", 64), [[] for _ in range(1000)])  # a tabela de saída é cheia mesmo assim
    ilegiveis = []
    graficos = office.graficos_odf(_ods(tmp_path, *[vazio] * 40), ilegiveis)
    assert 0 < len(graficos) < 40 and sum(len(office.grafico_md(g)) for g in graficos) <= 2_000_000
    assert sum("os demais ficaram de fora" in x for x in ilegiveis) == 1


def test_imagem_da_planilha_do_libreoffice_e_lida_com_a_aba():
    vistas = []
    ia = lince.FuncaoLeitor(lambda img, instr: vistas.append(img) or "Retângulo azul.", "m")
    r = lince.Leitor(ia=ia, agrupar=False).ler(REAIS / "lince_imagem_planilha.ods")
    assert len(vistas) == 1 and "**Imagem (aba Painel):**" in r.markdown and "Retângulo azul." in r.markdown


def test_previa_do_grafico_nao_e_lida_como_imagem():
    ilegiveis = []
    assert office._imagens_ods(REAIS / "lince_grafico.ods", ilegiveis) == [] and not ilegiveis


def _ods_com_imagens(tmp_path, quadros):
    corpo = "".join(
        "<draw:frame>" + "".join(f'<draw:image xlink:href="Pictures/{n}"/>' for n in nomes) + "</draw:frame>"
        for nomes in quadros
    )
    p = tmp_path / "imagens.ods"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(
            "content.xml",
            '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
            'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
            'xmlns:xlink="http://www.w3.org/1999/xlink"><office:body><office:spreadsheet>'
            f'<table:table table:name="Painel">{corpo}</table:table></office:spreadsheet></office:body>'
            "</office:document-content>",
        )
        for nome in {n for nomes in quadros for n in nomes}:
            z.writestr(f"Pictures/{nome}", nome.encode())
    return p


def test_frame_com_svg_e_png_de_reserva_e_uma_imagem_so(tmp_path):
    achadas = office._imagens_ods(_ods_com_imagens(tmp_path, [["logo.svg", "logo.png"]]), [])
    assert [(onde, mime) for onde, mime, _ in achadas] == [("aba Painel", "image/svg+xml")]


def test_teto_de_imagens_da_planilha_fica_registrado(tmp_path, monkeypatch):
    monkeypatch.setattr(legados, "IMAGENS_MAX", 2)
    ilegiveis = []
    achadas = office._imagens_ods(_ods_com_imagens(tmp_path, [["a.png"], ["b.png"], ["c.png"], ["d.png"]]), ilegiveis)
    assert len(achadas) == 2 and ilegiveis == ["imagens da planilha: mais de 2; o resto ficou de fora"]
