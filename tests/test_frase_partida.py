"""A frase de abertura em negrito que o extrator do PDF parte em título e continuação volta a ser um parágrafo só."""

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

import lince
from lince.texto import juntar_frases_partidas


def _secao(c, titulo, y):
    """Um título de seção de verdade na página (o documento com seções é o que tem cabeçalho corrido a tirar)."""
    if titulo:
        c.setFont("Helvetica-Bold", 14)
        c.drawString(60, y, titulo)
        y -= 24
    return y


def test_titulo_seguido_da_continuacao_em_negrito_volta_a_ser_a_frase():
    blocos = [
        "## A atividade econômica doméstica expandiu-se em ritmo moderado, mas o mercado de trabalho",
        "**seguiu aquecido.** O PIB cresceu 0,5% no trimestre.",
        "Outro parágrafo.",
    ]

    assert juntar_frases_partidas(blocos) == [
        "**A atividade econômica doméstica expandiu-se em ritmo moderado, mas o mercado de trabalho seguiu aquecido.**"
        " O PIB cresceu 0,5% no trimestre.",
        "Outro parágrafo.",
    ]


def test_frase_partida_em_varios_titulos_vira_uma_frase_em_negrito():
    blocos = [
        "## Em contexto de política monetária restritiva, o mercado de crédito continua exibindo sinais de",
        "## desaceleração e o endividamento segue pressionando famílias e empresas",
        "O endividamento permaneceu alto.",
    ]

    assert juntar_frases_partidas(blocos) == [
        "**Em contexto de política monetária restritiva, o mercado de crédito continua exibindo sinais de"
        " desaceleração e o endividamento segue pressionando famílias e empresas**",
        "O endividamento permaneceu alto.",
    ]


def test_titulo_de_verdade_fica_como_esta():
    blocos = [
        "## 4.2.1 – Panorama da evolução fiscal brasileira nos últimos anos",  # longo, mas seguido de frase nova
        "O resultado primário melhorou.",
        "## Atividade econômica",  # curto
        "**destaque** do trimestre.",
        "## Riscos para a inflação acima do cenário de referência:",  # rótulo
        "**i. desancoragem das expectativas** por período prolongado.",
        "## Os reajustes salariais negociados seguem indicando ganhos reais.",  # frase completa
        "**depois** do acordo.",
    ]

    assert juntar_frases_partidas(blocos) == blocos


def test_item_de_lista_em_negrito_nao_e_continuacao():
    blocos = [
        "## Riscos para a inflação abaixo do cenário de referência",
        "**i. eventual desaceleração** da atividade.",
    ]

    assert juntar_frases_partidas(blocos) == blocos


def test_titulo_seguido_de_subtitulo_nota_ou_negrito_sem_fim_de_frase_fica_como_esta():
    blocos = [
        "## Como usar o comando de rebase no dia a dia",
        "### git rebase --onto",  # subtítulo, mesmo em minúscula
        "## Distribuição da população por faixa etária em 2024",
        "**nota:** dados do censo.",  # nota, não continuação
        "## Os principais indicadores de condições financeiras mantiveram",
        "**comportamentos variados** em relação ao trimestre anterior.",  # o negrito não termina a frase
        "## Panorama da evolução fiscal brasileira nos últimos anos",
        "**em milhões de reais**",  # legenda: negrito sem pontuação
        "## Exemplo de configuração do serviço de leitura em lote",
        "**veja o arquivo de exemplo.**\n```yaml\nservico: lince\n```",  # código na continuação
    ]

    assert juntar_frases_partidas(blocos) == blocos


def test_dentro_de_bloco_de_codigo_nada_e_juntado():
    blocos = [
        "```python",
        "# calcula a média ponderada dos valores da amostra",
        "# depois normaliza pelo total de observações",
        "```",
    ]

    assert juntar_frases_partidas(blocos) == blocos


def test_no_pdf_a_frase_volta_inteira_sem_o_cabecalho_corrido_e_a_secao_real_fica(tmp_path):
    p = tmp_path / "carta.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    for n in range(4):
        c.setFont("Helvetica-Bold", 16)  # o cabeçalho corrido, que o leitor marca como título pela fonte
        c.drawString(60, 800, "Carta do Trimestre | 69 | Nota 22 | Terceiro trimestre")
        y = 740
        if n == 1:  # a frase de abertura em negrito, partida pelo leitor em título e continuação
            c.setFont("Helvetica-Bold", 13)
            c.drawString(60, y, "A atividade econômica doméstica expandiu-se em ritmo mais moderado, mas o mercado de")
            c.setFont("Helvetica-Bold", 11)
            c.drawString(60, y - 18, "trabalho seguiu aquecido.")
            c.setFont("Helvetica", 11)
            c.drawString(200, y - 18, "O PIB cresceu 0,5% no segundo trimestre de 2026.")
            y -= 36
        y = _secao(c, {2: "Inflação", 3: "Crédito"}.get(n), y)
        c.setFont("Helvetica", 11)
        for k in range(10):
            c.drawString(60, y - 18 * k, f"Texto corrido {k} da página {n + 1} com a análise do trimestre.")
        c.showPage()
    c.save()

    r = lince.ler(p)

    frase = (
        "A atividade econômica doméstica expandiu-se em ritmo mais moderado, mas o mercado de trabalho seguiu aquecido."
    )
    assert f"**{frase}** O PIB cresceu 0,5%" in r.markdown
    assert "Carta do Trimestre" not in r.markdown
    assert r.metadados["cabeçalho/rodapé"] == ["Carta do Trimestre | 69 | Nota 22 | Terceiro trimestre"]
    assert [s["titulo"] for s in r.estrutura["secoes"]] == ["Inflação", "Crédito"]


def test_continuacao_com_o_ponto_fora_do_negrito_ou_em_trechos_tambem_junta():
    titulo = "## O preço do petróleo oscilou ao longo do trimestre, assim como sua trajetória"
    for continuacao, fim in [
        (
            "**prospectiva no quarto trimestre**. Logo depois, recuou.",
            "trajetória prospectiva no quarto trimestre**. Logo",
        ),
        (
            "**prospectiva e** **perspectivas da economia.** Mais adiante.",
            "trajetória prospectiva e** **perspectivas da",
        ),
    ]:
        (juntado,) = juntar_frases_partidas([titulo, continuacao])
        assert juntado.startswith("**O preço do petróleo") and fim in juntado


def test_cabecalho_corrido_no_topo_nao_entra_na_continuacao_da_frase(tmp_path):
    p = tmp_path / "carta.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    for n in range(4):
        c.setFont("Helvetica-Bold", 16)
        c.drawString(60, 800, "Carta do Trimestre | 69 | Nota 22 | Terceiro trimestre")
        y = 740
        if n == 2:  # a frase em negrito que começou na página anterior continua logo abaixo do cabeçalho
            c.setFont("Helvetica-Bold", 11)
            c.drawString(60, y, "trabalho seguiu aquecido.")
            c.setFont("Helvetica", 11)
            c.drawString(200, y, "O PIB cresceu 0,5% no segundo trimestre de 2026.")
            y -= 18
        y = _secao(c, {1: "Atividade", 3: "Inflação"}.get(n), y)
        c.setFont("Helvetica", 11)
        for k in range(10):
            c.drawString(60, y - 18 * k, f"Texto corrido {k} da página {n + 1} com a análise do trimestre.")
        c.showPage()
    c.save()

    r = lince.ler(p)

    assert "Carta do Trimestre" not in r.markdown
    assert "**trabalho seguiu aquecido.** O PIB cresceu" in r.markdown


def test_figura_logo_abaixo_da_frase_juntada_entra_depois_do_paragrafo(tmp_path):
    from PIL import Image

    Image.new("RGB", (600, 300), "navy").save(tmp_path / "g.png")
    p = tmp_path / "carta.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(60, 760, "A atividade econômica doméstica expandiu-se em ritmo mais moderado, mas o mercado de")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(60, 742, "trabalho seguiu aquecido.")
    c.setFont("Helvetica", 11)
    c.drawString(200, 742, "O PIB cresceu 0,5% no segundo trimestre de 2026.")
    c.drawImage(str(tmp_path / "g.png"), 60, 480, width=400, height=200)  # logo abaixo da continuação
    for k in range(6):
        c.drawString(60, 440 - 18 * k, f"Texto corrido {k} depois da figura.")
    c.showPage()
    c.save()

    md = lince.ler(p).markdown

    assert "**A atividade econômica" in md
    assert md.index("**A atividade econômica") < md.index("> [figura: pendente]") < md.index("Texto corrido 0")
