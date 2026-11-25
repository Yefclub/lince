"""Títulos do RTF pela folha de estilos do próprio arquivo: o leitor do corpo entregava o parágrafo de título como
texto comum, e o documento ficava sem seções (issue 150)."""

from pathlib import Path

import lince

REAIS = Path(__file__).parent / "fixtures" / "reais"


def test_titulos_do_rtf_saem_com_o_nivel_da_folha_de_estilos():
    r = lince.ler(REAIS / "lince_titulos.rtf")

    titulos = [ln for ln in r.markdown.splitlines() if ln.startswith("#")]
    assert titulos == ["# Manual de recebimento", "## Conferência da carga", "## Registro no sistema"]
    assert [s["titulo"] for s in r.estrutura["secoes"]] == [
        "Manual de recebimento",
        "Conferência da carga",
        "Registro no sistema",
    ]
    assert "Confira o lacre antes de abrir as portas." in r.markdown


def test_rtf_sem_estilo_de_titulo_continua_igual():
    md = lince.ler(REAIS / "testRTF.rtf").markdown

    assert not [ln for ln in md.splitlines() if ln.startswith("#")]
