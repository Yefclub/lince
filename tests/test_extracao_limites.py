"""A extração não segura os outros documentos: a trava é só da PDFium, e a análise visual do PDF tem teto de páginas
e respeita o prazo, sem perder o texto (issue #33, item 7)."""

import threading

import pytest
from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

import lince
from lince import pdf as pdf_mod
from lince._trava import PDFIUM


def _pdf(tmp_path, paginas=4):
    p = tmp_path / "relatorio.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    for n in range(paginas):
        c.drawString(60, 780, f"Conteúdo da página {n + 1} do relatório anual.")
        c.showPage()
    c.save()
    return p


def test_office_nao_espera_a_trava_da_pdfium(tmp_path):
    d = Document()
    d.add_paragraph("Contrato de frete.")
    p = tmp_path / "c.docx"
    d.save(p)
    pegou, solta = threading.Event(), threading.Event()

    def segura():
        with PDFIUM:  # outro documento lendo um PDF enorme
            pegou.set()
            solta.wait(30)

    t = threading.Thread(target=segura)
    t.start()
    pegou.wait(5)
    resultado = {}
    leitura = threading.Thread(target=lambda: resultado.setdefault("r", lince.ler(p)))
    leitura.start()
    leitura.join(15)
    solta.set()
    t.join()
    assert "Contrato de frete." in resultado["r"].markdown  # leu enquanto a PDFium estava ocupada


def test_teto_de_paginas_da_analise_mantem_o_texto(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_mod, "PAGINAS_ANALISE_MAX", 2)
    r = lince.ler(_pdf(tmp_path))
    assert all(f"página {n} do relatório" in r.markdown for n in range(1, 5))
    assert r.cobertura.pendentes == [
        "páginas 3–4: só o texto, sem figuras nem páginas escaneadas lidas (teto de 2 páginas)"
    ]
    assert any("texto oculto não conferido: páginas 3–4" in a for a in r.alertas_injecao)


def test_prazo_vencido_na_extracao_mantem_o_texto(tmp_path):
    r = lince.ler(_pdf(tmp_path), prazo=1e-9)
    assert all(f"página {n} do relatório" in r.markdown for n in range(1, 5))
    assert any("(prazo esgotado)" in p for p in r.cobertura.pendentes)


def test_hifen_de_fim_de_linha_e_linear_e_decide_pelo_vocabulario():
    import time

    from lince.texto import desifenizar

    assert (
        desifenizar("nega- tive e propellant- based; propellant-based")
        == "negative e propellant-based; propellant-based"
    )
    assert desifenizar("o propellant- based e o based") == "o propellant-based e o based"  # a 2ª metade existe sozinha
    # o par com hífen vale inteiro, como duas partes vizinhas de uma palavra hifenizada, e não como trecho dela
    assert desifenizar("co- op e x-co-op") == "co-op e x-co-op"
    assert desifenizar("co- op e co-operation") == "coop e co-operation"
    hostil = " ".join(f"pala- vra{k}" for k in range(20_000))  # milhares de quebras numa página
    inicio = time.monotonic()
    desifenizar(hostil)
    assert time.monotonic() - inicio < 0.5  # era ~4 s, segurando a GIL


@pytest.mark.parametrize("texto", ["a" * 200_000, "a" * 200_000 + "- b", "x " + "a" * 200_000 + "-a" * 3])
def test_hifen_de_fim_de_linha_e_linear_numa_palavra_enorme(texto):
    """Um Tj de milhares de letras: sem âncora no começo da palavra, o `re` tentava de novo em cada letra e recuava
    até o começo a cada tentativa (20 mil letras, ~7 s; 200 mil, minutos)."""
    import time

    from lince.texto import desifenizar

    inicio = time.monotonic()
    saida = desifenizar(texto)
    assert time.monotonic() - inicio < 0.5
    assert saida == (texto.replace("- b", "b") if texto.endswith("- b") else texto)
