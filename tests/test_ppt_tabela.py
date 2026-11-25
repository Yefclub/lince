"""Tabela do PowerPoint 97-2003: o formato a guarda como um grupo de formas, uma por célula, e o leitor do corpo
entregava cada célula como um parágrafo solto. Agora sai como tabela, igual à do .pptx (issue 150)."""

from pathlib import Path

import lince

REAIS = Path(__file__).parent / "fixtures" / "reais"


def test_tabela_do_ppt_sai_como_tabela_no_lugar_das_celulas_soltas():
    md = lince.ler(REAIS / "lince_tabela.ppt").markdown

    assert (
        "| Etapa | Início | Fim |\n| --- | --- | --- |\n| Piloto | 11/01/2027 | 15/03/2027 |\n"
        "| Expansão | 22/02/2027 | 26/04/2027 |"
    ) in md
    assert md.count("Piloto") == 1  # as células não ficam repetidas como parágrafos
    assert md.index("Cronograma de implantação") < md.index("| Etapa |") < md.index("Datas sujeitas à aprovação")
