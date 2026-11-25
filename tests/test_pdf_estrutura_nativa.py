"""Estrutura que o próprio PDF desenha ou posiciona: tabela com grade, gráfico vetorial com eixos, duas colunas,
cabeçalho e rodapé corridos em documento curto, marca d'água e rótulo de campo de formulário. Cada teste gera o PDF e
confere o que sai no Markdown, nas tarefas para a IA e nos metadados (issue 150)."""

import pytest
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    FrameBreak,
    PageTemplate,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

import lince

GRADE = TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 8)])
N = ParagraphStyle("n", parent=getSampleStyleSheet()["Normal"], fontSize=9, leading=12, spaceAfter=6)
H2 = ParagraphStyle("h2", parent=getSampleStyleSheet()["Heading2"], fontSize=12, spaceBefore=6, spaceAfter=4)
H1 = ParagraphStyle("h1", parent=getSampleStyleSheet()["Heading1"], fontSize=16, spaceAfter=6)


def _linha_do(md, trecho):
    return next(ln for ln in md.splitlines() if trecho in ln)


def test_tabela_com_grade_em_duas_paginas_sai_inteira_e_sem_ia(tmp_path):
    """O extrator junta o quadro de cima à tabela e ela ia para a IA num recorte que cortava a última linha."""
    p = tmp_path / "extrato.pdf"
    linhas = [[f"{d:02d}/10/2026", f"Lançamento {d}", f"{700000 + d}", f"{d * 137},50"] for d in range(1, 47)]
    quadro = Table([["Agência: 3021-4", "Conta: 48.776-2"], ["Período: outubro/2026", "Saldo anterior: 9.999,00"]])
    quadro.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.grey)]))
    tabela = Table([["Data", "Histórico", "Documento", "Valor"]] + linhas, repeatRows=1)
    tabela.setStyle(GRADE)
    SimpleDocTemplate(str(p), pagesize=A4).build([quadro, Spacer(1, 8), tabela])

    plano = lince.Leitor().preparar(p)
    md = lince.Leitor().concluir(plano, {}).markdown

    assert plano.tarefas == []  # as linhas da grade deram as células: nada para a IA ler
    for d in (1, 30, 46):  # a primeira, a última da página 1 e a última do documento
        assert f"| {d:02d}/10/2026 | Lançamento {d} | {700000 + d} | {d * 137},50 |" in md
    assert md.count("Agência: 3021-4") == 1 and md.count("Saldo anterior: 9.999,00") == 1  # o quadro não some


def test_tabela_com_celula_mesclada_sai_em_html_com_cada_valor_na_sua_celula(tmp_path):
    p = tmp_path / "dre.pdf"
    dados = [
        ["Conta", "Trimestre", "", "Acumulado", ""],
        ["", "3T26", "3T25", "9M26", "9M25"],
        ["Receita líquida", "184.600", "164.235", "531.870", "478.910"],
        ["Lucro líquido", "18.900", "15.340", "52.410", "43.980"],
    ]
    t = Table(dados)
    t.setStyle(
        TableStyle([*GRADE.getCommands(), ("SPAN", (0, 0), (0, 1)), ("SPAN", (1, 0), (2, 0)), ("SPAN", (3, 0), (4, 0))])
    )
    SimpleDocTemplate(str(p), pagesize=A4).build([Paragraph("Demonstração de resultados", H2), t])

    plano = lince.Leitor().preparar(p)
    md = lince.Leitor().concluir(plano, {}).markdown

    assert plano.tarefas == []
    assert '<th rowspan="2">Conta</th>' in md and '<th colspan="2">Trimestre</th>' in md
    assert "<td>Receita líquida</td>\n<td>184.600</td>\n<td>164.235</td>\n<td>531.870</td>\n<td>478.910</td>" in md


def _painel(p, com_grafico=True):
    W, H = landscape(A4)
    c = canvas.Canvas(str(p), pagesize=(W, H))
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, H - 90, "Tabela 2 – Indicadores por filial")
    t = Table(
        [
            ["Filial", "Entregas", "OTIF (%)"],
            ["Curitiba", "48.312", "96,4"],
            ["Recife", "21.106", "91,7"],
            ["Total", "69.418", "93,9"],
        ],
        colWidths=[110, 80, 80],
    )
    t.setStyle(GRADE)
    _, th = t.wrapOn(c, 300, 300)
    t.drawOn(c, 40, H - 100 - th)
    c.setFont("Helvetica", 7.5)
    c.drawString(40, H - 112 - th, "* Valores de Recife são projeção, sujeitos a revisão.")
    if com_grafico:  # gráfico de colunas vetorial: barras soltas sobre um eixo
        c.setFont("Helvetica", 9)
        c.drawString(480, H - 90, "Gráfico 3 – Entregas por mês (mil)")
        c.line(470, H - 260, 760, H - 260)
        c.line(470, H - 260, 470, H - 110)
        for k, (mes, v) in enumerate([("Jul", 62), ("Ago", 71), ("Set", 88), ("Out", 93)]):
            x = 495 + k * 68
            c.setFillColor(colors.HexColor("#2f6fad"))
            c.rect(x, H - 260, 30, v * 1.4, stroke=0, fill=1)
            c.setFillColor(colors.black)
            c.drawCentredString(x + 15, H - 257 + v * 1.4, str(v))
            c.drawCentredString(x + 15, H - 272, mes)
    c.save()
    return W, H


def test_tabela_ao_lado_de_grafico_nao_e_cortada_nem_repetida(tmp_path):
    """A tabela saía duas vezes, a segunda cortada na esquerda e com o gráfico dentro do recorte."""
    p = tmp_path / "painel.pdf"
    _painel(p)

    plano = lince.Leitor().preparar(p)
    md = lince.Leitor().concluir(plano, {}).markdown

    assert [t.tipo for t in plano.tarefas] == ["desenho"]  # só o gráfico vai para a IA
    grafico = plano.tarefas[0].caixa
    assert grafico[0] > 320  # o recorte começa depois da tabela, que termina em x=310
    assert grafico[0] <= 472 and grafico[2] >= 729  # e leva o gráfico inteiro: do eixo à última barra
    assert md.count("| Curitiba | 48.312 | 96,4 |") == 1 and md.count("| Total | 69.418 | 93,9 |") == 1
    assert md.count("* Valores de Recife são projeção, sujeitos a revisão.") == 1  # a nota da tabela fica, inteira


def test_tabela_sem_grafico_ao_lado_continua_sem_tarefa(tmp_path):
    p = tmp_path / "so_tabela.pdf"
    _painel(p, com_grafico=False)
    assert lince.Leitor().preparar(p).tarefas == []


def _nota_em_duas_colunas(p):
    W, H = A4
    m, sep = 51, 20
    lc = (W - 2 * m - sep) / 2
    doc = BaseDocTemplate(str(p), pagesize=A4, leftMargin=m, rightMargin=m, topMargin=m, bottomMargin=m)
    topo = Frame(m, H - m - 120, W - 2 * m, 120, id="topo", leftPadding=0, rightPadding=0)
    c1 = Frame(m, m, lc, H - 2 * m - 126, id="c1", leftPadding=0, rightPadding=0)
    c2 = Frame(m + lc + sep, m, lc, H - 2 * m - 126, id="c2", leftPadding=0, rightPadding=0)
    doc.addPageTemplates([PageTemplate(id="p", frames=[topo, c1, c2])])
    tabela = Table(
        [["Rota", "Antes", "Depois", "Ganho"], ["Sul", "2,41", "2,68", "11,2%"], ["Norte", "2,35", "2,57", "9,4%"]]
    )
    tabela.setStyle(
        TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8.5), ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black)])
    )
    doc.build(
        [
            Paragraph("Nota técnica 14 — Redução do consumo de diesel com telemetria embarcada", H1),
            Paragraph(
                "<b>Resumo.</b> Seis meses de operação do programa em 148 cavalos mecânicos da frota dedicada.", N
            ),
            FrameBreak(),
            Paragraph("1. Introdução", H2),
            Paragraph(
                "O combustível representa 37% do custo operacional do transporte rodoviário de cargas da companhia. A "
                "diretoria aprovou a instalação de módulos de telemetria com leitura da rede CAN em toda a frota.",
                N,
            ),
            Paragraph("2. Método", H2),
            Paragraph(
                "Foram descartadas as viagens com carga inferior a 12 t e os dias com interdição de pista, o que "
                "eliminou 6,8% dos registros brutos.",
                N,
            ),
            FrameBreak(),
            Paragraph("3. Resultados", H2),
            Paragraph(
                "O maior ganho ocorreu no trecho que concentra a Serra do Cafezal e, por isso, é mais sensível à "
                "forma de condução dos motoristas da frota.",
                N,
            ),
            tabela,
            Paragraph("4. Conclusão", H2),
            Paragraph(
                "O programa atingiu a meta de redução de consumo dois meses antes do previsto. Recomenda-se estender "
                "a telemetria aos 62 veículos de distribuição urbana.",
                N,
            ),
        ]
    )


def test_duas_colunas_lidas_como_tabela_voltam_a_ordem_de_leitura(tmp_path):
    """O extrator casava as linhas das duas colunas pela altura: títulos fundidos e frases emendadas."""
    p = tmp_path / "nota.pdf"
    _nota_em_duas_colunas(p)

    md = lince.ler(p).markdown

    titulos = [ln for ln in md.splitlines() if ln.startswith("#")]
    assert titulos == [
        "# Nota técnica 14 — Redução do consumo de diesel com telemetria embarcada",
        "## 1. Introdução",
        "## 2. Método",
        "## 3. Resultados",
        "## 4. Conclusão",
    ]
    # cada frase inteira, sem linha da outra coluna no meio, e a coluna 1 toda antes da 2
    assert "Foram descartadas as viagens com carga inferior a 12 t e os dias com interdição de pista" in md
    assert "estender a telemetria aos 62 veículos de distribuição urbana" in md
    assert md.index("eliminou 6,8% dos registros brutos") < md.index("3. Resultados")
    assert "Resumo. Seis meses de operação" in md  # o item estreito na linha de largura total não some
    assert "| Sul | 2,41 | 2,68 | 11,2% |" in md and "| Norte | 2,35 | 2,57 | 9,4% |" in md


def _relatorio(p, paginas, rodape=True, cabecalho="POP-LOG-017 — Recebimento de carga — Versão 3.2"):
    c = canvas.Canvas(str(p), pagesize=A4)
    for n in range(1, paginas + 1):
        c.setFont("Helvetica", 8)
        c.drawString(57, 805, cabecalho)
        if rodape:
            c.drawString(57, 34, "Cópia não controlada quando impressa")
            c.drawRightString(538, 34, f"Página {n} de {paginas}")
        c.setFont("Helvetica", 11)
        for k in range(6):
            c.drawString(57, 740 - 18 * k, f"Linha {k + 1} do conteúdo da página {n}, com texto próprio.")
        c.showPage()
    c.save()


def test_cabecalho_e_rodape_de_documento_de_duas_paginas_vao_para_os_metadados(tmp_path):
    p = tmp_path / "pop.pdf"
    _relatorio(p, 2)

    r = lince.ler(p)

    assert "Cópia não controlada" not in r.markdown and "POP-LOG-017" not in r.markdown
    assert sorted(r.metadados["cabeçalho/rodapé"]) == [
        "Cópia não controlada quando impressa Página # de 2",
        "POP-LOG-017 — Recebimento de carga — Versão 3.2",
    ]
    assert r.markdown.count("com texto próprio") == 12  # o conteúdo fica inteiro


def test_linha_igual_no_topo_de_duas_paginas_sem_paginacao_e_conteudo(tmp_path):
    """Sem "Página 1 de 2" não há prova de que é um documento paginado: podem ser dois documentos de uma página."""
    p = tmp_path / "duas_fichas.pdf"
    _relatorio(p, 2, rodape=False, cabecalho="Ficha de inspeção de veículo")

    r = lince.ler(p)

    assert r.markdown.count("Ficha de inspeção de veículo") == 2 and "cabeçalho/rodapé" not in r.metadados


def test_rodape_colado_no_ultimo_paragrafo_sai_do_corpo(tmp_path):
    """O extrator emenda o rodapé no parágrafo que termina a página; a frase não pode levar o rodapé junto."""
    p = tmp_path / "contrato.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    for n in range(1, 4):
        c.setFont("Helvetica", 11)
        y = 780
        for k in range(38):
            c.drawString(60, y, f"Cláusula {n}.{k + 1}: obrigação da contratada número {n * 100 + k}, sem ressalvas.")
            y -= 18
        c.setFont("Helvetica", 9)
        c.drawString(60, 40, f"Aurora Logística S.A. · Página {n} de 3")
        c.showPage()
    c.save()

    r = lince.ler(p)

    assert "Aurora Logística" not in r.markdown
    assert r.metadados["cabeçalho/rodapé"] == ["Aurora Logística S.A. · Página # de 3"]
    assert r.markdown.count("sem ressalvas") == 114


def test_marca_dagua_em_diagonal_vai_para_os_metadados(tmp_path):
    p = tmp_path / "minuta.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    c.saveState()
    c.setFillColor(colors.Color(0.85, 0.85, 0.85))
    c.setFont("Helvetica-Bold", 90)
    c.translate(300, 420)
    c.rotate(30)
    c.drawCentredString(0, 0, "MINUTA")
    c.restoreState()
    c.setFont("Helvetica", 11)
    c.drawString(60, 760, "O OTIF consolidado ficou em 93,9%, abaixo da meta de 94,0%.")
    c.drawString(60, 740, "A filial de Recife segue como o principal ponto de atenção.")
    c.save()

    r = lince.ler(p)

    assert "MINUTA" not in r.markdown and r.metadados["marca d'água"] == ["MINUTA"]
    assert "O OTIF consolidado ficou em 93,9%" in r.markdown


def test_campo_de_formulario_sai_com_o_rotulo_impresso(tmp_path):
    """O nome interno do campo ("ie") não diz nada a quem lê; o rótulo ao lado dele diz."""
    p = tmp_path / "ficha.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    c.setFont("Helvetica", 10)
    form = c.acroForm
    c.drawString(57, 705, "Inscrição estadual:")
    form.textfield(name="ie", value="908.44217-60", x=200, y=700, width=300, height=18, forceBorder=True)
    c.drawString(57, 675, "Prazo de pagamento (dias):")
    form.textfield(name="prazo", value="28", x=200, y=670, width=300, height=18, forceBorder=True)
    form.checkbox(name="simples", checked=True, x=57, y=640, size=13, forceBorder=True)
    c.drawString(77, 643, "Optante pelo Simples Nacional")
    form.checkbox(name="iso", checked=False, x=57, y=618, size=13, forceBorder=True)
    c.drawString(77, 621, "Possui certificação ISO 9001")
    form.textfield(
        name="obs", tooltip="Observações do comprador", value="sem pendências", x=200, y=580, width=300, height=18
    )
    form.textfield(name="solto", value="valor sem rótulo", x=200, y=300, width=300, height=18)
    c.save()

    md = lince.ler(p).markdown

    assert "- Inscrição estadual: 908.44217-60" in md and "- Prazo de pagamento (dias): 28" in md
    assert "- Optante pelo Simples Nacional: [x]" in md and "- Possui certificação ISO 9001: [ ]" in md
    assert "- Observações do comprador: sem pendências" in md  # o nome de exibição do próprio PDF vence
    assert "- solto: valor sem rótulo" in md  # sem rótulo por perto, fica o nome do campo


@pytest.mark.parametrize("completa", [True, False], ids=["leitura-completa", "leitura-sem-um-valor"])
def test_rotulos_de_um_grafico_lido_nao_se_repetem_embaralhados(tmp_path, completa):
    """Com a leitura da IA completa, as linhas nativas dos rótulos ("88 93", "Jul Ago Set Out") só sujam; se a
    leitura deixou um valor de fora, o texto nativo fica: nada some."""
    p = tmp_path / "painel.pdf"
    _painel(p)
    leitor = lince.Leitor()
    plano = leitor.preparar(p)
    leitura = (
        "Gráfico (colunas): Gráfico 3 – Entregas por mês (mil)\n\n"
        "| Mês | Entregas |\n| --- | --- |\n| Jul | 62 |\n| Ago | 71 |\n| Set | 88 |\n| Out | 93 |"
    )
    if not completa:
        leitura = leitura.replace("| Set | 88 |", "| Set | [ilegível] |")

    md = leitor.concluir(plano, {plano.tarefas[0].id: leitura}).markdown

    assert "| Jul | 62 |" in md and "| Curitiba | 48.312 | 96,4 |" in md
    if completa:
        assert md.count("Jul") == 1 and md.count("93") == 2  # 93 do gráfico e 93,9 da tabela: nenhum rótulo repetido
        assert md.count("Gráfico 3 – Entregas por mês (mil)") == 1
    else:
        assert "88" in md  # o valor que a leitura não trouxe continua no texto nativo
