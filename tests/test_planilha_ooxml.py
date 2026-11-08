"""Planilha do Excel (.xlsx/.xlsm): imagens e gráficos lidos sem carregar a planilha inteira."""

import io
import re
import zipfile

import openpyxl
from openpyxl.chart import BarChart, Reference
from openpyxl.drawing.image import Image as XImg
from PIL import Image

import lince


def _png(cor="#2561C1"):
    b = io.BytesIO()
    Image.new("RGB", (200, 100), cor).save(b, "PNG")
    return b.getvalue()


def _ler(p):
    vistas = []
    r = lince.Leitor(ia=lince.FuncaoLeitor(lambda img, instr: vistas.append(img) or "Lido.", "m"), agrupar=False)
    return r.ler(p), vistas


def _planilha(tmp_path, celula="AB5"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Painel"
    for linha in [("Região", "Vendas"), ("Norte", 120), ("Sul", 80)]:
        ws.append(linha)
    ws.add_image(XImg(io.BytesIO(_png())), celula)
    grafico = BarChart()
    grafico.title = "Vendas por região"
    grafico.add_data(Reference(ws, min_col=2, min_row=1, max_row=3), titles_from_data=True)
    grafico.set_categories(Reference(ws, min_col=1, min_row=2, max_row=3))
    ws.add_chart(grafico, "D10")
    p = tmp_path / "painel.xlsx"
    wb.save(p)
    return p


def test_imagem_com_a_celula_certa_depois_da_coluna_z(tmp_path):
    r, vistas = _ler(_planilha(tmp_path))
    assert len(vistas) == 1 and "**Imagem (aba Painel, célula AB5):**" in r.markdown


def test_grafico_sem_cache_le_os_valores_do_intervalo(tmp_path):
    md = lince.ler(_planilha(tmp_path)).markdown
    assert "**Gráfico (barras): Vendas por região**" in md
    assert "| Norte | 120 |" in md.split("**Gráfico")[1] and "| Sul | 80 |" in md.split("**Gráfico")[1]


def test_planilha_nunca_e_carregada_inteira(tmp_path, monkeypatch):
    import openpyxl.reader.excel as leitor_excel

    original, cargas = leitor_excel.load_workbook, []

    def vigia(*a, **k):
        cargas.append(k.get("read_only", False))
        return original(*a, **k)

    monkeypatch.setattr(leitor_excel, "load_workbook", vigia)
    monkeypatch.setattr(openpyxl, "load_workbook", vigia)
    lince.ler(_planilha(tmp_path))
    assert cargas and all(cargas)  # só em modo somente leitura, e só pelo gráfico sem cache


def test_emf_na_planilha_fica_marcado_em_vez_de_sumir(tmp_path):
    base = _planilha(tmp_path)
    p = tmp_path / "emf.xlsx"
    emf = b"\x01\x00\x00\x00" + b"\x00" * 36 + b" EMF" + b"\x00" * 60
    with zipfile.ZipFile(base) as zi, zipfile.ZipFile(p, "w") as zo:
        for n in zi.namelist():
            dados = zi.read(n)
            if n.startswith("xl/media/"):
                n, dados = re.sub(r"\.\w+$", ".emf", n), emf
            elif n.endswith(".rels"):
                dados = re.sub(rb"(media/image\d+)\.\w+", rb"\1.emf", dados)
            elif n == "[Content_Types].xml":
                dados = dados.replace(b"<Default ", b'<Default Extension="emf" ContentType="image/x-emf"/><Default ', 1)
            zo.writestr(n, dados)
    r, vistas = _ler(p)
    assert not vistas and "[aba Painel, célula AB5: imagem EMF não lida]" in r.markdown
