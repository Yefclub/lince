"""A extração não segura os outros documentos: a trava é só da PDFium, e a análise visual do PDF tem teto de páginas
e respeita o prazo, sem perder o texto (issue #33, item 7)."""

import threading

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
