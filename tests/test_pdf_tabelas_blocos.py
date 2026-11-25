"""Regressões de associação entre blocos Markdown e a tabela desenhada no PDF."""

import pytest
from reportlab.pdfgen import canvas

from lince import Leitor, pdf
from lince import leitor as leitor_mod
from lince.montagem import tamanho_da_saida


def _extracao(monkeypatch, md):
    extrair = pdf._extrair

    def com_blocos(dados, inicio):
        mds, ocr, itens = extrair(dados, inicio)
        mds[inicio] = md
        return mds, ocr, itens

    monkeypatch.setattr(pdf, "_extrair", com_blocos)


def _desenhar(c, linha, y, xs):
    for x, celula in zip(xs, linha, strict=True):
        c.drawString(x, y, celula)


def test_reune_tabela_partida_dentro_da_mesma_moldura(tmp_path, monkeypatch):
    arq = tmp_path / "projecoes.pdf"
    c = canvas.Canvas(str(arq), pagesize=(420, 510))
    c.setFont("Helvetica", 8)
    c.drawString(55, 415, "Table 2. Historical Projection Errors")
    c.drawString(55, 395, "Percentage points")
    _desenhar(c, ["Variable", "2025", "2026"], 375, [55, 200, 290])
    _desenhar(c, ["Inflation", "1.7", "2.1"], 355, [55, 200, 290])
    _desenhar(c, ["Unemployment", "0.9", "1.4"], 335, [55, 200, 290])
    c.drawString(55, 315, "Note: Estimates in the summer.")
    c.drawString(55, 299, "For other forecasts, use the quarterly average.")
    c.drawString(55, 266, "A paragraph outside the frame.")
    for x in (48, 358):
        c.line(x, 293, x, 428)
    for y in (428, 386, 366, 346, 326, 293):
        c.line(48, y, 358, y)
    c.save()
    original = (
        "| Table 2. Historical Projection Errors | | |\n| --- | --- | --- |\n"
        "| Percentage points | | |\n| Variable | 2025 | 2026 |\n\n"
        "| Inflation | 1.7 | 2.1 |\n| --- | --- | --- |\n| Unemployment | 0.9 | 1.4 |\n\n"
        "Note: Estimates in the summer.\n\nFor other forecasts, use the quarterly average.\n\n"
        "A paragraph outside the frame."
    )
    _extracao(monkeypatch, original)
    leitor = Leitor()
    plano = leitor.preparar(arq)
    (t,) = plano.tarefas
    assert t.tipo == "tabela"
    assert t.caixa[1] < 293 < 428 < t.caixa[3] < 440
    assert t.caixa[1] > 266 + 8
    assert "For other forecasts" in t.instrucao
    nativo = leitor.concluir(plano, {}).markdown
    assert nativo.count("Historical Projection Errors") == nativo.count("For other forecasts") == 1
    assert "| Variable | 2025 | 2026 |" in nativo
    assert "| Inflation | 1.7 | 2.1 |" in nativo and "| Unemployment | 0.9 | 1.4 |" in nativo
    assert "A paragraph outside the frame." in nativo
    resposta_parcial = {
        t.id: (
            "| Variable | 2025 | 2026 |\n| --- | --- | --- |\n| Inflation | 1.7 | 2.1 |\n\n"
            "Unidade no quadro: pontos percentuais."
        )
    }
    parcial = leitor.concluir(plano, resposta_parcial)
    assert "| Unemployment | 0.9 | 1.4 |" in parcial.markdown and "For other forecasts" in parcial.markdown
    assert "Unidade no quadro: pontos percentuais." in parcial.markdown
    assert any("integralidade da transcrição da tabela não verificada" in p for p in parcial.cobertura.pendentes)
    assert tamanho_da_saida(plano.estado, resposta_parcial) >= len(parcial.markdown)
    visual = plano.estado["paginas"][0]["tabelas"][0]
    completa = leitor.concluir(plano, {t.id: visual})
    assert not completa.cobertura.pendentes and "lido pela IA" in completa.markdown
    assert "Texto nativo de referência" not in completa.markdown
    trocada = leitor.concluir(plano, {t.id: visual.replace("| Inflation | 1.7 | 2.1 |", "| Inflation | 2.1 | 1.7 |")})
    assert "| Inflation | 1.7 | 2.1 |" in trocada.markdown
    fora = original.replace("A paragraph outside the frame.", "{{TABELA:123456}}")
    _extracao(monkeypatch, fora)
    # Um marcador produzido pelo texto do documento não indica uma tabela que exista.
    assert Leitor().preparar(arq)


def test_separa_duas_grades_de_mesmo_cabecalho_e_recupera_colunas(tmp_path, monkeypatch):
    arq = tmp_path / "indices.pdf"
    c = canvas.Canvas(str(arq), pagesize=(320, 570))
    c.setFont("Helvetica", 8)
    titulos = ("Evolução do índice geral", "Evolução do índice de alimentos")
    for topo, titulo, valores in (
        (500, titulos[0], (10, 20, 30, 40, 50, 60)),
        (265, titulos[1], (11, 21, 31, 41, 51, 61)),
    ):
        fundo = topo - 133
        c.drawString(58, topo + 10, titulo)
        for y in (topo, topo - 22, fundo):
            c.line(35, y, 270, y)
        for x in (85, 130, 180):
            c.line(x, topo - 4, x, fundo)
        _desenhar(c, ["Ano", "Jan", "Fev", "No Ano"], topo - 16, [46, 96, 145, 194])
        for i, valor in enumerate(valores):
            c.setFillColorRGB(int(i == 0), 0, 0)
            _desenhar(
                c,
                [str(1994 + i), str(valor), str(valor + 2), str(2 * valor + 2)],
                topo - 36 - 17 * i,
                [46, 96, 145, 194],
            )
    c.save()
    original = "\n\n".join(
        (
            f"**{titulos[0]}**",
            "\n".join(
                [
                    "| Ano | Jan Fev | No Ano |",
                    "| --- | --- | --- |",
                    "| 1994 | 10 12 | 22 |",
                    "| 1995 | 20 22 | 42 |",
                    "| 1996 | 30 32 | 62 |",
                    "| 1997 | 40 42 | 82 |",
                    "| 1998 | 50 52 | 102 |",
                    f"| 1999 | 60 62 {titulos[1]} | 122 |",
                    "| Ano | Jan Fev | No Ano |",
                    "| 1994 | 11 13 | 24 |",
                    "| 1995 | 21 23 | 44 |",
                    "| 1996 | 31 33 | 64 |",
                    "| 1997 | 41 43 | 84 |",
                    "| 1998 | 51 53 | 104 |",
                    "| 1999 | 61 63 | 124 |",
                ]
            ),
        )
    )
    _extracao(monkeypatch, original)
    leitor = Leitor()
    plano = leitor.preparar(arq)
    geral, alimentos = plano.tarefas
    assert geral.tipo == alimentos.tipo == "tabela"
    assert geral.caixa[1] > alimentos.caixa[3]
    assert "| 1994 | 10 | 12 | 22 |" in geral.instrucao and "| 1999 | 60 | 62 | 122 |" in geral.instrucao
    assert "| 1994 | 11 | 13 | 24 |" in alimentos.instrucao and "| 1999 | 61 | 63 | 124 |" in alimentos.instrucao
    md = leitor.concluir(plano, {}).markdown
    assert md.count("| Ano | Jan | Fev | No Ano |") == 2
    assert md.count(titulos[0]) == md.count(titulos[1]) == 1
    leitura_geral = {
        geral.id: (
            "| Ano | Jan | Fev | No Ano |\n| --- | --- | --- | --- |\n| 1994 | 10 | 12 | 22 |\n\n"
            "A linha de 1994 está em vermelho."
        )
    }
    parcial = leitor.concluir(plano, leitura_geral)
    assert "A linha de 1994 está em vermelho." in parcial.markdown
    assert "| 1999 | 60 | 62 | 122 |" in parcial.markdown and "| 1999 | 61 | 63 | 124 |" in parcial.markdown
    assert tamanho_da_saida(plano.estado, leitura_geral) >= len(parcial.markdown)


def test_tabela_com_titulo_e_fonte_nao_absorve_eixo_de_grafico(tmp_path, monkeypatch):
    arq = tmp_path / "contas.pdf"
    c = canvas.Canvas(str(arq), pagesize=(380, 480))
    c.setFont("Helvetica", 8)
    c.drawString(57, 430, "Tabela 1.2.6 – Contas externas")
    c.drawString(57, 407, "Acumulado no período")
    c.drawString(283, 407, "US$ bilhões")
    for y in (402, 370, 280):
        c.line(53, y, 335, y)
    xs = [56, 195, 248, 296]
    _desenhar(c, ["Período", "2022", "2023", "2024"], 382, xs)
    _desenhar(c, ["Receita", "15", "21", "32"], 353, xs)
    _desenhar(c, ["Despesa", "-21", "-22", "-24"], 329, xs)
    _desenhar(c, ["Saldo", "-6", "-1", "8"], 304, xs)
    c.drawString(55, 269, "Fonte: estimativas oficiais.")
    c.drawString(55, 233, "Gráfico 1.2.7 – Séries históricas")
    c.drawString(55, 220, "100 200 300")
    c.save()
    _extracao(
        monkeypatch,
        (
            "**Tabela 1.2.6 – Contas externas**\n\n"
            "| Acumulado no período | | US$ bilhões |\n| --- | --- | --- |\n"
            "| Período | 2022 2023 | 2024 |\n| Receita | 15 21 | 32 |\n| Despesa | -21-22 | -24 |\n"
            "| Saldo Fonte: estimativas oficiais. | -6-1 | 8 |\n"
            "| Gráfico 1.2.7 – Séries históricas | 100 200 300 | |"
        ),
    )
    leitor = Leitor()
    plano = leitor.preparar(arq)
    (t,) = plano.tarefas
    assert t.tipo == "tabela" and t.caixa[1] > 240 and t.caixa[3] > 436
    assert "Tabela 1.2.6 – Contas externas" in t.instrucao
    assert "| Saldo | -6 | -1 | 8 |" in t.instrucao and "Fonte: estimativas oficiais." in t.instrucao
    assert "100 200 300" not in t.instrucao and "Gráfico 1.2.7" not in t.instrucao
    assert "Gráfico 1.2.7 – Séries históricas 100 200 300" in leitor.concluir(plano, {}).markdown


def test_indice_em_paineis_cobre_primeira_linha_e_chave_de_simbolos(tmp_path, monkeypatch):
    arq = tmp_path / "indice.pdf"
    c = canvas.Canvas(str(arq), pagesize=(680, 510))
    c.setFont("Helvetica", 9)
    c.drawString(34, 475, "Índice de estações")
    c.setFont("Helvetica", 7)
    for x in (180, 325, 470):
        c.line(x, 42, x, 461)
    for painel, x in enumerate((34, 195, 340, 485), 1):
        _desenhar(c, ["Estação e serviços", "Zona"], 458, (x, x + 106))
        for n in range(21):
            _desenhar(c, [f"Ponto {painel}-{n:02d}", str((painel + n) % 6)], 440 - 17 * n, (x, x + 106))
    c.drawString(340, 62, "Legenda de serviços")
    c.drawString(340, 47, "Estacionamento de bicicletas")
    c.save()
    primeira = ["Ponto 1-00 1 | Ponto 2-00 2 | Ponto 3-00 3 | Ponto 4-00 4"]
    linhas = primeira + ["--- | --- | --- | ---"]
    linhas += [" 1 2 | | |"]  # zonas de estações vizinhas fundidas na saída do extrator
    linhas += [" | ".join(f"Ponto {j}-{i:02d}" for j in range(1, 5)) for i in range(1, 21)]
    linhas += ["Legenda de serviços | Estacionamento de bicicletas | |"]
    _extracao(monkeypatch, "**Índice de estações**\n\n" + "\n".join(f"| {ln} |" for ln in linhas))
    leitor = Leitor()
    plano = leitor.preparar(arq)
    (t,) = plano.tarefas
    assert t.tipo == "tabela" and t.caixa[1] < 47 and t.caixa[3] > 475
    assert "Índice de estações" in t.instrucao and "Ponto 1-00" in t.instrucao
    nativo = leitor.concluir(plano, {}).markdown
    assert "Estacionamento de bicicletas" in nativo
    visual = "\n".join(
        (
            "| Estação | Zona |",
            "| --- | --- |",
            *(f"| Ponto {j}-{i:02d} | {(j + i) % 6} |" for j in range(1, 5) for i in range(21)),
            "",
            "Legenda de serviços: Estacionamento de bicicletas",
        )
    )
    respostas = {t.id: visual}
    resultado = leitor.concluir(plano, respostas)
    assert resultado.cobertura.pendentes == [f"{t.id}: integralidade da transcrição da tabela não verificada"]
    assert "| Ponto 2-05 | 1 |" in resultado.markdown and "| Ponto 4-20 | 0 |" in resultado.markdown
    assert primeira[0] in resultado.markdown
    assert resultado.markdown.index("Leitura visual complementar") < resultado.markdown.index(
        "Texto nativo de referência"
    )
    estimado = tamanho_da_saida(plano.estado, respostas)
    assert estimado >= len(resultado.markdown)
    monkeypatch.setattr(leitor_mod, "SAIDA_MAX", estimado)
    assert leitor.concluir(plano, respostas).markdown == resultado.markdown

    limite = max(len(nativo), len(visual)) + 100
    assert limite < len(resultado.markdown)
    monkeypatch.setattr(leitor_mod, "SAIDA_MAX", limite)
    limitado = leitor.concluir(plano, respostas)
    assert primeira[0] in limitado.markdown and "Leitura visual complementar" not in limitado.markdown
    assert any("resultado grande demais" in p for p in limitado.cobertura.pendentes)


def test_titulo_interno_notas_e_subtitulos_de_ano_dentro_da_grade(tmp_path, monkeypatch):
    arq = tmp_path / "produto-regional.pdf"
    c = canvas.Canvas(str(arq), pagesize=(420, 540))
    c.setFont("Helvetica", 8)
    c.drawString(57, 461, "Parágrafo fora do quadro.")
    c.drawString(57, 420, "Tabela 1 – Produto Regional")
    c.drawString(57, 408, "Acumulado no ano")
    c.drawString(285, 396, "Variação %")
    for y in (389, 352, 262):
        c.line(54, y, 330, y)
    c.line(247, 370, 330, 370)
    _desenhar(c, ["Discriminação", "2023", "2024¹"], 375, (57, 207, 250))
    _desenhar(c, ["Anterior", "Atual"], 358, (253, 305))
    xs = (57, 210, 258, 305)
    _desenhar(c, ["Setor A", "16,3", "-1,6", "-2,0"], 333, xs)
    _desenhar(c, ["Serviços de transporte", "5,1", "6,1", "7,1"], 320, xs)
    _desenhar(c, ["Setor B", "3,8", "2,7", "1,9"], 307, xs)
    _desenhar(c, ["Movimentação portuária", "5,2", "6,2", "7,2"], 294, xs)
    _desenhar(c, ["Importação", "-1,2", "11,3", "13,7"], 280, xs)
    c.drawString(57, 249, "Fontes: IBGE e BC")
    c.drawString(57, 238, "1/ Estimativa.")
    c.drawString(57, 204, "Seção externa começa aqui.")
    c.save()
    _extracao(
        monkeypatch,
        (
            "Parágrafo fora do quadro.\n\n"
            "| Tabela 1 – Produto Regional Acumulado no ano | |\n| --- | --- |\n"
            "| | Variação % |\n"
            "| Discriminação 2023 | 2024¹ |\n"
            "| Setor A 16,3 Serviços de transporte 5,1 Setor B 3,8 Movimentação portuária 5,2 "
            "Importação -1,2 Fontes: IBGE e BC 1/ Estimativa. | "
            "Anterior Atual -1,6 -2,0 6,1 7,1 2,7 1,9 6,2 7,2 11,3 13,7 |\n\n"
            "Seção externa começa aqui."
        ),
    )
    leitor = Leitor()
    plano = leitor.preparar(arq)
    (t,) = plano.tarefas
    l, b, r, alto = t.caixa
    assert t.tipo == "tabela" and l < 54 and 330 < r < 345 and 204 + 8 < b < 238 and 429 < alto < 461
    assert "Parágrafo fora" not in t.instrucao and "Seção externa" not in t.instrucao
    nativo = leitor.concluir(plano, {}).markdown
    for texto in (
        "Tabela 1 – Produto Regional",
        "Acumulado no ano",
        "Variação %",
        "| Discriminação | 2023 | 2024¹ / Anterior | 2024¹ / Atual |",
        "| Setor A | 16,3 | -1,6 | -2,0 |",
        "| Importação | -1,2 | 11,3 | 13,7 |",
        "Fontes: IBGE e BC",
        "1/ Estimativa.",
    ):
        assert texto in t.instrucao
        assert texto in nativo


@pytest.mark.parametrize("deslocamento", [0, 4])
def test_meses_conservam_ano_e_notas_fora_da_ultima_celula(tmp_path, monkeypatch, deslocamento):
    arq = tmp_path / "projecoes-mensais.pdf"
    c = canvas.Canvas(str(arq), pagesize=(420, 540))
    c.setFont("Helvetica", 8)
    c.drawString(56, 432, "Tabela 7 – Projeções locais")
    c.drawString(298, 418, "Variação %")
    for y in (410, 368, 310):
        c.line(50, y, 346, y)
    c.line(168, 393, 346, 393)
    _desenhar(c, ["2024", "2025"], 398, (170, 216 + deslocamento))
    _desenhar(c, ["Dez", "Jan", "Fev", "Mar"], 383, (170, 216, 262, 308))
    xs = (56, 174, 220, 266, 312)
    mensal = ["Variação mensal", "0,58", "-0,08", "1,17", "0,42"]
    trimestre = ["Variação trimestral", "1,54", "0,89", "1,68", "1,51"]
    ano = ["Variação em 12 meses", "4,89", "4,37", "4,72", "5,00"]
    for y, linha in zip((351, 334, 318), (mensal, trimestre, ano), strict=True):
        _desenhar(c, linha, y, xs)
    c.drawString(56, 296, "Fonte: série oficial.")
    c.drawString(56, 285, "1/ Cenário revisado.")
    c.save()
    _extracao(
        monkeypatch,
        (
            "**Tabela 7 – Projeções locais**\n\n"
            "Variação % <u>2024 2025</u>\n\n"
            "||2024|2025|||\n|---|---|---|---|---|\n"
            "||Dez|Jan|Fev|Mar|\n"
            "|Variação mensal|0,58|-0,08|1,17|0,42|\n"
            "|Variação trimestral|1,54|0,89|1,68|1,51|\n"
            "|Variação em 12 meses Fonte: série oficial. 1/ Cenário revisado.|4,89|4,37|4,72|5,00|"
        ),
    )
    leitor = Leitor()
    plano = leitor.preparar(arq)
    md = leitor.concluir(plano, {}).markdown
    if deslocamento:
        (tarefa,) = plano.tarefas
        assert tarefa.tipo == "tabela" and "Associação entre anos e colunas a conferir" in tarefa.instrucao
        assert "|  | 2024 / Dez |" not in md
    else:
        assert not plano.tarefas and plano.estado["paginas"][0]["rota"] == "nativa"
        assert "|  | 2024 / Dez | 2025 / Jan | 2025 / Fev | 2025 / Mar |" in md
        for linha in (mensal, trimestre, ano):
            assert "| " + " | ".join(linha) + " |" in md
        assert "\n\nFonte: série oficial. 1/ Cenário revisado." in md
