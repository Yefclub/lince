"""Fluxograma de formas do Impress: as ligações e os rótulos Sim/Não saem como no .pptx (Mermaid e lista), em vez de
só as caixas soltas (issue 150)."""

from pathlib import Path

import lince

REAIS = Path(__file__).parent / "fixtures" / "reais"


def test_fluxograma_do_odp_sai_com_as_ligacoes_e_os_rotulos():
    md = lince.ler(REAIS / "lince_fluxo.odp").markdown

    assert "- Pedido de reembolso → Valor acima do limite?" in md
    assert "- Valor acima do limite? → Aprovação da diretoria (Sim)" in md
    assert "- Valor acima do limite? → Aprovação do gestor (Não)" in md
    assert "```mermaid" in md and "-->|Sim|" in md and "-->|Não|" in md


def test_odp_sem_conectores_nao_ganha_diagrama():
    md = lince.ler(REAIS / "lince_grafico.odp").markdown

    assert "Diagrama do slide" not in md
