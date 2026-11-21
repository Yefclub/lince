"""A frase de abertura em negrito que o extrator do PDF parte em título e continuação volta a ser um parágrafo só."""

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

import lince
from lince.texto import juntar_frases_partidas


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
        if n == 2:
            c.setFont("Helvetica-Bold", 14)
            c.drawString(60, y, "Inflação")
            y -= 24
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
    assert [s["titulo"] for s in r.estrutura["secoes"]] == ["Inflação"]


def test_continuacao_com_o_ponto_fora_do_negrito_em_trechos_ou_sem_ponto_tambem_junta():
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
        (
            "**prospectiva, que considera queda mais acentuada**",
            "trajetória prospectiva, que considera queda mais acentuada**",
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
        if n == 3:
            c.setFont("Helvetica-Bold", 14)
            c.drawString(60, y, "Inflação")
            y -= 24
        c.setFont("Helvetica", 11)
        for k in range(10):
            c.drawString(60, y - 18 * k, f"Texto corrido {k} da página {n + 1} com a análise do trimestre.")
        c.showPage()
    c.save()

    r = lince.ler(p)

    assert "Carta do Trimestre" not in r.markdown
    assert "**trabalho seguiu aquecido.** O PIB cresceu" in r.markdown
