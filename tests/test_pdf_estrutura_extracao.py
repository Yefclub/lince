"""Casos em que a saída do extrator de texto precisa ser fixada para reproduzir o defeito (rodapé colado no parágrafo,
tabela com células fundidas, bloco de fórmula misturado com a coluna vizinha): o PDF é gerado e o Markdown do extrator
é o que ele entregou no documento real (issue 150)."""

from reportlab.pdfgen import canvas

import lince
from lince import pdf


def _extracao(monkeypatch, por_pagina):
    extrair = pdf._extrair

    def com_blocos(dados, inicio):
        mds, ocr, itens = extrair(dados, inicio)
        for i in mds:
            mds[i] = por_pagina(i)
        return mds, ocr, itens

    monkeypatch.setattr(pdf, "_extrair", com_blocos)


def test_rodape_colado_pelo_extrator_no_ultimo_paragrafo_sai_do_corpo(tmp_path, monkeypatch):
    """No contrato exportado pelo LibreOffice o extrator emendava o rodapé na frase que terminava a página: a linha
    não era vista como rodapé corrido e ficava no meio do texto."""
    p = tmp_path / "contrato.pdf"
    c = canvas.Canvas(str(p), pagesize=(595, 842))
    for n in range(1, 4):
        c.setFont("Helvetica", 11)
        for k in range(4):
            c.drawString(90, 748 - 16 * k, f"Considerando {k + 1} do contrato de número {n}.")
        c.drawString(90, 684, f"Cláusula {n}: a contratada responde pela carga até a entrega,")
        c.drawString(90, 668, "calculados pro rata die.")
        c.setFont("Helvetica", 9)
        c.drawString(90, 39, f"Aurora Logística S.A. · Página {n} de 3")
        c.showPage()
    c.save()
    _extracao(
        monkeypatch,
        lambda i: (
            ""
            + " ".join(f"Considerando {k + 1} do contrato de número {i + 1}." for k in range(4))
            + f"\n\nCláusula {i + 1}: a contratada responde pela carga até a entrega, calculados pro rata die. "
            f"Aurora Logística S.A. · Página {i + 1} de 3\n"
        ),
    )

    r = lince.ler(p)

    assert "Aurora Logística" not in r.markdown and "Página" not in r.markdown
    assert r.metadados["cabeçalho/rodapé"] == ["Aurora Logística S.A. · Página # de 3"]
    assert r.markdown.count("calculados pro rata die.") == 3


def test_ultima_linha_de_texto_corrido_na_borda_nao_e_tratada_como_rodape(tmp_path, monkeypatch):
    """A última linha do corpo, na mesma entrelinha das outras, é corpo mesmo perto da borda: fica no parágrafo."""
    p = tmp_path / "relatorio.pdf"
    c = canvas.Canvas(str(p), pagesize=(595, 842))
    for n in range(1, 4):
        c.setFont("Helvetica", 11)
        for k in range(50):
            c.drawString(60, 790 - 15 * k, f"Linha {k + 1} da página {n}: texto corrido.")
        c.showPage()
    c.save()
    _extracao(monkeypatch, lambda i: " ".join(f"Linha {k + 1} da página {i + 1}: texto corrido." for k in range(50)))

    r = lince.ler(p)

    assert r.markdown.count("texto corrido.") == 150 and "cabeçalho/rodapé" not in r.metadados


def _contas(p):
    c = canvas.Canvas(str(p), pagesize=(380, 480))
    c.setFont("Helvetica", 8)
    c.drawString(57, 430, "Tabela 1 – Contas externas")
    for x, t in zip((56, 195, 248, 296), ("Período", "2022", "2023", "2024"), strict=True):
        c.drawString(x, 400, t)
    for k, linha in enumerate(
        (("Receita", "150", "210", "320"), ("Despesa", "211", "222", "243"), ("Saldo", "61", "12", "77"))
    ):
        for x, t in zip((56, 195, 248, 296), linha, strict=True):
            c.drawString(x, 380 - 20 * k, t)
    c.save()
    return (
        "**Tabela 1 – Contas externas**\n\n| Período | 2022 2023 | 2024 |\n| --- | --- | --- |\n"
        "| Receita | 150 210 | 320 |\n| Despesa | 211 222 | 243 |\n| Saldo | 61 12 | 77 |"
    )


def test_leitura_de_tabela_que_perde_uma_linha_nao_substitui_a_nativa(tmp_path, monkeypatch):
    """O recorte que cortava a última linha fazia a linha sumir: a leitura trocava a tabela nativa sem conferência."""
    p = tmp_path / "contas.pdf"
    md = _contas(p)
    _extracao(monkeypatch, lambda i: md)
    leitor = lince.Leitor()
    plano = leitor.preparar(p)
    (t,) = plano.tarefas
    assert t.tipo == "tabela"
    cabecalho = "| Período | 2022 | 2023 | 2024 |\n| --- | --- | --- | --- |\n"
    completa = cabecalho + "| Receita | 150 | 210 | 320 |\n| Despesa | 211 | 222 | 243 |\n| Saldo | 61 | 12 | 77 |"
    cortada = cabecalho + "| Receita | 150 | 210 | 320 |\n| Despesa | 211 | 222 | 243 |"

    boa = leitor.concluir(plano, {t.id: completa})
    ruim = leitor.concluir(plano, {t.id: cortada})

    assert "| Saldo | 61 | 12 | 77 |" in boa.markdown and "| Saldo | 61 12 | 77 |" not in boa.markdown
    assert boa.cobertura.pendentes == []
    assert "| Saldo | 61 12 | 77 |" in ruim.markdown  # a linha que a leitura não trouxe continua, pela tabela nativa
    assert ruim.cobertura.pendentes == [f"{t.id}: integralidade da transcrição da tabela não verificada"]


def test_tabela_de_colunas_afastadas_vai_inteira_no_recorte(tmp_path, monkeypatch):
    p = tmp_path / "contas.pdf"
    md = _contas(p)
    _extracao(monkeypatch, lambda i: md)

    (t,) = lince.Leitor().preparar(p).tarefas

    assert t.caixa[0] <= 56 and t.caixa[2] >= 310  # da primeira à última coluna
    assert t.caixa[1] <= 340 and t.caixa[3] >= 408  # do cabeçalho à linha do saldo


def test_bloco_de_formula_misturado_com_a_coluna_vizinha_nao_vira_recorte(tmp_path, monkeypatch):
    """O extrator fundia a fração com o título da outra coluna; a caixa que une os pedaços levava o texto vizinho,
    cortado. Sem uma caixa só da fórmula, o texto nativo fica e a pendência diz por quê."""
    p = tmp_path / "nota.pdf"
    c = canvas.Canvas(str(p), pagesize=(595, 842))
    c.setFont("Helvetica", 10)
    for k in range(6):
        c.drawString(60, 520 - 13 * k, f"linha {k + 1} do parágrafo da coluna da esquerda, antes")
        c.drawString(320, 520 - 13 * k, f"linha {k + 1} do parágrafo da coluna da direita, depois")
    c.drawString(120, 420, "E = × 100 (1)")
    c.setFont("Helvetica-Bold", 12)
    c.drawString(320, 422, "4. Conclusão")
    c.setFont("Helvetica", 10)
    for k in range(4):
        c.drawString(320, 400 - 13 * k, f"linha {k + 1} da conclusão, na coluna da direita")
        c.drawString(60, 400 - 13 * k, f"linha {k + 1} depois da fórmula, na coluna da esquerda")
    c.save()
    corpo = " ".join(
        f"linha {k + 1} do parágrafo da coluna da esquerda, antes "
        f"linha {k + 1} do parágrafo da coluna da direita, depois"
        for k in range(6)
    )
    resto = " ".join(
        f"linha {k + 1} depois da fórmula, na coluna da esquerda linha {k + 1} da conclusão, na coluna da direita"
        for k in range(4)
    )
    _extracao(monkeypatch, lambda i: f"{corpo}\n\n### E = × 100 (1) 4. Conclusão\n\n{resto}")

    plano = lince.Leitor().preparar(p)
    r = lince.Leitor().concluir(plano, {})

    assert [t.tipo for t in plano.tarefas] == []
    assert "E = × 100 (1)" in r.markdown and "4. Conclusão" in r.markdown
    assert r.markdown.count("na coluna da esquerda") == 4 and r.markdown.count("na coluna da direita") == 4
