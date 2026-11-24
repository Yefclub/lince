"""Cobertura das condições da hachura nas linhas, colunas e medidas de uma tabela."""

from lince.conferencia_visual import avisos_transcricao

LEGENDA = "Os valores em fundo branco são efetivos e os em fundo hachurado são projeções."


def pendente(tabela):
    return "condicao_visual" in avisos_transcricao(tabela, LEGENDA, {})


def test_linha_de_quinze_colunas_tem_que_qualificar_todas_as_medidas():
    # Tabela 2.2.1, Relatório de Inflação do BCB de dezembro de 2024, página 62.
    medidas = "4,6 3,9 4,2 4,4 4,9 5,0 5,0 5,1 4,5 4,2 4,0 3,8 3,6 3,4 3,2".split()

    def tabela(qualificados):
        celulas = []
        for i, medida in enumerate(medidas):
            estado = " (efetivo)" if i < 4 else " (projeção)"
            celulas.append("<td>" + medida + (estado if i in qualificados else "") + "</td>")
        return "<table><tbody><tr><th>IPCA</th>" + "".join(celulas) + "</tr></tbody></table>"

    assert pendente(tabela([3, 4]))
    assert pendente(tabela(range(14)))
    assert not pendente(tabela(range(15)))


def test_colspan_rowspan_e_rodape_nao_transferem_estado_a_outra_medida():
    tabela = (
        "<table><thead><tr><th rowspan='2'>Indicador</th><th colspan='2'>Efetivo</th>"
        "<th>Projeção</th></tr><tr><th>I</th><th>II</th><th>III</th></tr></thead>"
        "<tbody><tr><th>Produção</th><td>alto</td><td>baixo</td><td>estável</td></tr></tbody>"
        "<tfoot><tr><td>Fundo branco: efetivo</td><td>Hachurado: projeção</td></tr></tfoot></table>"
    )
    assert not pendente(tabela)
    assert pendente(tabela.replace("<th>Projeção</th>", "<th>Estimativa anual</th>"))
    assert pendente(tabela.replace("<td>baixo</td><td>estável</td>", "<td colspan='2'>estável</td>"))


def test_cabecalho_de_linha_mesclado_e_medida_textual_sem_qualificacao():
    tabela = (
        "<table><tbody><tr><th rowspan='2'>Efetivo</th><td>aberto</td></tr><tr><td>fechado</td></tr>"
        "<tr><th>Projeção</th><td>parcial</td></tr></tbody></table>"
    )
    assert not pendente(tabela)
    assert pendente(tabela.replace("rowspan='2'", "rowspan='1'"))
    grade = "| Período | Estado | Situação operacional |\n|---|---|---|\n| II | A (efetivo) | B (projeção) |"
    assert not pendente(grade)
    assert pendente(grade + "\n| III | C (projeção) | indisponível |")


def test_tabelas_e_secoes_nao_herdam_cabecalhos_da_tabela_anterior():
    tabela = "<table><tr><th>Efetivo</th><th>Projeção</th></tr><tr><td>sim</td><td>não</td></tr></table>"
    assert not pendente(tabela)
    assert pendente(tabela + "<table><tr><td>sem dados confirmados</td><td>em análise</td></tr></table>")
    assert pendente("<table><tr><td>Fundo branco: efetivo</td><td>Hachurado: projeção</td></tr></table>")


def test_quebras_no_cabecalho_e_conflito_de_classificacao():
    tabela = (
        "<table><tr><th>Coluna A<br>Efetivo</th><th>Coluna B<br>Projeção</th></tr>"
        "<tr><td>admitido</td><td>em espera</td></tr></table>"
    )
    assert not pendente(tabela)
    assert pendente(tabela.replace("admitido", "admitido (projeção)"))


def test_totais_no_rodape_exigem_as_condicoes():
    inicio = (
        "<table><thead><tr><th>Indicador</th><th>2025</th><th>2026</th></tr></thead>"
        "<tbody><tr><th>IPCA</th><td>7,0 (efetivo)</td><td>8,0 (projeção)</td></tr></tbody>"
    )
    fim = "<tfoot><tr><th>Total</th><td>7,1</td><td>8,2</td></tr></tfoot></table>"
    assert pendente(inicio + fim)
    assert not pendente(inicio + fim.replace("7,1", "7,1 (efetivo)").replace("8,2", "8,2 (projeção)"))
