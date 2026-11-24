"""Regressões de tabelas nativas e parágrafos repetidos num recorte vetorial."""

import json
from types import SimpleNamespace

from reportlab.pdfgen import canvas

from lince import Leitor, pdf
from lince.montagem import montar
from lince.pdf_tabelas import conferir_tabelas
from lince.texto import limpar_tabelas


def item(texto, x, y, largura, altura=10, fonte="Times"):
    return SimpleNamespace(text=texto, x=x, y=y, width=largura, height=altura, font=fonte, rotation=0, render_mode=0)


def conferir(texto, itens, filetes=()):
    md, tabelas = limpar_tabelas(texto)
    corpo = conferir_tabelas(md, tabelas, itens, filetes, lambda: None)
    return corpo, tabelas[0]


def test_reune_expoentes_e_linhas_fundidas_sem_perder_celula_ou_nota():
    texto = (
        "<sup>3</sup>\n\n| Consumo, | alfa | U = cm |\n| --- | --- | --- |\n| efetivo | | |\n"
        "| Produção Estoque | beta gama | V = s W = m |\n\n(i)\n<sup>−1</sup>\n\n<sup>2</sup>\n\nFim."
    )
    itens = [
        item("Consumo,", 10, 100, 45),
        item("alfa", 90, 100, 19),
        item("U = cm", 150, 100, 26),
        item("3", 176, 104, 3, 6),
        item("efetivo", 10, 88, 30),
        item("Produção", 10, 70, 44),
        item("beta", 90, 70, 18),
        item("(i)", 108, 74, 8, 6),
        item("V = s", 150, 70, 22),
        item("−1", 172, 74, 6, 6),
        item("Estoque", 10, 52, 40),
        item("gama", 90, 52, 22),
        item("W = m", 150, 52, 27),
        item("2", 177, 56, 3, 6),
    ]
    corpo, tb = conferir(texto, itens)
    assert corpo == "{{TABELA:0}}\n\nFim."
    assert "| Consumo, efetivo | alfa | U = cm<sup>3</sup> |" in tb["md"]
    assert "| Produção | beta<sup>(i)</sup> | V = s<sup>−1</sup> |" in tb["md"]
    assert "| Estoque | gama | W = m<sup>2</sup> |" in tb["md"]
    assert not tb["complexa"]
    itens[-1].x += 10  # não toca a base: não atribuir um expoente incerto
    _, duvidosa = conferir(texto, itens)
    assert duvidosa["complexa"] and duvidosa["caixa"] == [10, 52, 190, 110]
    assert "a conferir" in duvidosa["md"] and "<sup>2</sup>" in duvidosa["md"]


def test_expoente_invertido_e_ascii_de_fonte_simbolica():
    texto = "| Item | Sinal | Medida |\n| --- | --- | --- |\n| A | x | 5 3 = 243 |\n| B | x | 5 3 = 15 |"
    itens = [
        item("Item", 10, 115, 18),
        item("Sinal", 90, 115, 16),
        item("Medida", 150, 115, 32),
        item("A", 10, 100, 5),
        item("x", 90, 100, 5, fonte="XYZABC+Symbol"),
        item("3", 150, 100, 5),
        item("5", 155, 104, 3, 6),
        item("= 243", 160, 100, 18),
        item("B", 10, 80, 5),
        item("x", 90, 80, 5),
        item("5", 150, 80, 5),
        item("3", 158, 80, 5),
        item("= 15", 170, 80, 22),
    ]
    _, tb = conferir(texto, itens)
    assert "| A | x [símbolo a conferir] | 3<sup>5</sup> = 243 |" in tb["md"]
    assert "| B | x | 5 3 = 15 |" in tb["md"]
    assert tb["complexa"] and tb["caixa"] == [10, 80, 192, 125]


def test_cabecalho_composto_exige_divisorias_e_palavras_identicas():
    texto = "| Área | Preço | |\n| --- | --- | --- |\n| Antes | Depois | |\n| Setor | 72 | 48 |"
    itens = [
        item("Preço", 112, 130, 26),
        item("Área", 15, 120, 20),
        item("Antes", 83, 106, 24),
        item("Depois", 144, 106, 32),
        item("Setor", 10, 80, 30),
        item("72", 90, 80, 14),
        item("48", 150, 80, 14),
    ]
    filetes = [(60, 75, 61, 145), (120, 75, 121, 120)]
    _, tb = conferir(texto, itens, filetes)
    assert tb["md"].splitlines()[0] == "| Área | Preço / Antes | Preço / Depois |"
    assert "| Setor | 72 | 48 |" in tb["md"]
    for insuficientes in ([], [filetes[0]]):
        _, duvida = conferir(texto, itens, insuficientes)
        assert "| Antes | Depois |  |" in duvida["md"]


def test_leitura_do_pdf_com_subtitulos_deslocados(tmp_path, monkeypatch):
    arq = tmp_path / "grupos.pdf"
    c = canvas.Canvas(str(arq), pagesize=(420, 500))
    for texto, x, y in [
        ("Preço", 240, 420),
        ("Setor", 80, 406),
        ("Antes", 180, 382),
        ("Depois", 300, 382),
        ("Zona", 80, 352),
        ("14", 180, 352),
        ("25", 300, 352),
    ]:
        c.drawCentredString(x, y, texto)
    for x, topo in [(140, 450), (240, 400)]:
        c.rect(x, 320, 0.5, topo - 320, fill=1, stroke=0)
    c.save()
    extrair = pdf._extrair

    def extrair_com_deslocamento(dados, a):
        # O extrator decide a estrutura pelo conjunto da faixa: reproduz sua saída quebrada
        # sem depender de quais outras páginas estão nela. Posições e filetes vêm do PDF real.
        mds, ocr, itens = extrair(dados, a)
        mds[a] = "| Setor | Preço | |\n| --- | --- | --- |\n| Antes | Depois | |\n| Zona | 14 | 25 |"
        return mds, ocr, itens

    monkeypatch.setattr(pdf, "_extrair", extrair_com_deslocamento)
    resultado = Leitor().ler(arq)
    assert "| Setor | Preço / Antes | Preço / Depois |" in resultado.markdown
    assert "| Zona | 14 | 25 |" in resultado.markdown and not resultado.cobertura.pendentes


def test_paragrafo_do_boxe_completo_e_parcial(tmp_path):
    texto = " ".join(f"Amostra {i} estava em análise." for i in range(20))
    outro = " ".join(f"Amostra {i} aguarda revisão." for i in range(6))
    fora = texto.replace("análise", "trânsito")
    pagina = {
        "i": 0,
        "rota": "regioes",
        "md": "\n\n".join([texto, outro, fora]),
        "regioes": [{"tarefa": "r1", "depois_de": "", "desenho": True, "texto_dentro": texto + " " + outro}],
    }
    (tmp_path / "plano.json").write_text(json.dumps({"tipo": "pdf", "paginas": [pagina]}), encoding="utf8")
    md = montar(tmp_path, {"r1": texto + "\n\n" + outro[:100] + "\n\n" + fora})
    assert md.count(texto) == 1 and md.count(outro) == 1 and md.count(fora) == 2
    sem_resposta = montar(tmp_path, {})
    assert all(sem_resposta.count(t) == 1 for t in [texto, outro, fora])
