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


def _reescrever(origem, destino, trocas):
    """Copia o pacote aplicando, a cada parte, as trocas (regex em bytes) dadas para ela."""
    with zipfile.ZipFile(origem) as zi, zipfile.ZipFile(destino, "w") as zo:
        for n in zi.namelist():
            dados = zi.read(n)
            for padrao, novo in trocas.get(n, []):
                dados = re.sub(padrao, novo, dados)
            zo.writestr(n, dados)
    return destino


def test_alvos_relativos_como_o_excel_grava(tmp_path):
    p = _reescrever(
        _planilha(tmp_path),
        tmp_path / "excel.xlsx",
        {
            "xl/_rels/workbook.xml.rels": [(rb'Target="/xl/', b'Target="')],
            "xl/worksheets/_rels/sheet1.xml.rels": [(rb'Target="/xl/', b'Target="../')],
            "xl/drawings/_rels/drawing1.xml.rels": [(rb'Target="/xl/', b'Target="../')],
        },
    )
    with zipfile.ZipFile(p) as z:
        assert b'Target="../media/' in z.read("xl/drawings/_rels/drawing1.xml.rels")
    r, vistas = _ler(p)
    assert len(vistas) == 1 and "**Imagem (aba Painel, célula AB5):**" in r.markdown


def test_ancora_absoluta_e_mesma_midia_duas_vezes(tmp_path):
    ancora = rb"<oneCellAnchor><from>.*?</from>(<ext[^>]*/>)(<pic>.*?</pic>)<clientData/></oneCellAnchor>"
    p = _reescrever(
        _planilha(tmp_path),
        tmp_path / "variantes.xlsx",
        {
            "xl/drawings/drawing1.xml": [
                (ancora, rb'<absoluteAnchor><pos x="0" y="0"/>\1\2<clientData/></absoluteAnchor>' * 2),  # a mesma, 2x
            ],
        },
    )
    r, vistas = _ler(p)
    assert len(vistas) == 1 and "**Imagem (aba Painel):**" in r.markdown  # sem célula, lida uma vez


def test_teto_de_imagens_da_planilha(tmp_path, monkeypatch):
    from lince import legados, office

    monkeypatch.setattr(legados, "IMAGENS_MAX", 1)
    wb = openpyxl.Workbook()
    for celula, cor in (("A1", "red"), ("A10", "blue")):
        wb.active.add_image(XImg(io.BytesIO(_png(cor))), celula)
    p = tmp_path / "duas.xlsx"
    wb.save(p)
    ilegiveis = []
    assert len(office._imagens_xlsx(p, ilegiveis)) == 1 and ilegiveis == [
        "imagens da planilha: mais de 1; o resto ficou de fora"
    ]


def test_planilha_com_macro_le_imagem_e_grafico(tmp_path):
    p = tmp_path / "painel.xlsm"
    p.write_bytes(_planilha(tmp_path).read_bytes())
    r, vistas = _ler(p)
    assert len(vistas) == 1 and "**Gráfico (barras): Vendas por região**" in r.markdown


def test_intervalo_sem_cache_tem_teto():
    from xml.etree import ElementTree as ET

    from lince import office

    def ref(texto):
        el = ET.Element(f"{{{office.NS['c']}}}numRef")
        ET.SubElement(el, f"{{{office.NS['c']}}}f").text = texto
        return el

    abas = {"Dados"}
    assert office._intervalo(ref("Dados!$B$2:$B$3"), abas) == ("Dados", 2, 2, 2, 3)
    for fora in ("Dados!A:A", "Dados!1:1", "Dados!A1:Z5000", "Outra!A1:A3", "lixo"):
        assert office._intervalo(ref(fora), abas) is None, fora


def test_cada_aba_e_lida_uma_vez_e_fechada_mesmo_com_erro(tmp_path, monkeypatch):
    from openpyxl.workbook.workbook import Workbook
    from openpyxl.worksheet._read_only import ReadOnlyWorksheet

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Dados"
    ws.append(["Mês"] + [f"S{n}" for n in range(10)])
    for m in range(12):
        ws.append([f"M{m}"] + [m * n for n in range(10)])
    grafico = BarChart()
    grafico.add_data(Reference(ws, min_col=2, max_col=11, min_row=1, max_row=13), titles_from_data=True)
    grafico.set_categories(Reference(ws, min_col=1, min_row=2, max_row=13))
    ws.add_chart(grafico, "M2")
    p = tmp_path / "series.xlsx"
    wb.save(p)

    original, leituras, fechados = ReadOnlyWorksheet.iter_rows, [], []
    monkeypatch.setattr(
        ReadOnlyWorksheet, "iter_rows", lambda self, *a, **k: leituras.append(1) or original(self, *a, **k)
    )
    monkeypatch.setattr(Workbook, "close", lambda self: fechados.append(1))
    md = lince.ler(p).markdown
    assert len(leituras) == 1 and fechados  # 10 séries com nome, categorias e valores: uma passada na aba
    assert "| M11 |" in md.split("**Gráfico")[1]

    def quebra(self, *a, **k):
        raise ValueError("aba quebrada")

    monkeypatch.setattr(ReadOnlyWorksheet, "iter_rows", quebra)
    fechados.clear()
    lince.ler(p)
    assert fechados  # o erro na aba não deixa o arquivo aberto


def test_imagem_por_relacao_externa_nao_e_lida(tmp_path):
    base = _planilha(tmp_path)
    p = tmp_path / "externa.xlsx"
    with zipfile.ZipFile(base) as zi, zipfile.ZipFile(p, "w") as zo:
        for n in zi.namelist():
            dados = zi.read(n)
            if n == "xl/drawings/drawing1.xml":  # uma segunda âncora, igual à da imagem, mas pela relação rId9
                ancora = re.search(rb"<oneCellAnchor>(?:(?!</oneCellAnchor>).)*?<pic>.*?</oneCellAnchor>", dados, re.S)
                dados = dados.replace(
                    b"</wsDr>", re.sub(rb'r:embed="[^"]+"', b'r:embed="rId9"', ancora.group(0)) + b"</wsDr>"
                )
            elif n == "xl/drawings/_rels/drawing1.xml.rels":
                dados = dados.replace(
                    b"</Relationships>",
                    b'<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
                    b'image" Target="../media/externa.png" TargetMode="External"/></Relationships>',
                )
            zo.writestr(n, dados)
        zo.writestr("xl/media/externa.png", _png("red"))  # existe no pacote: só o TargetMode a barra
    r, vistas = _ler(p)
    assert len(vistas) == 1 and Image.open(io.BytesIO(vistas[0])).convert("RGB").getpixel((5, 5)) == (37, 97, 193)
