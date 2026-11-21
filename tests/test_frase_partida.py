"""A frase de abertura em negrito que o extrator do PDF parte em título e continuação volta a ser um parágrafo só."""

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
