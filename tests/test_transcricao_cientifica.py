from reportlab.pdfgen import canvas

import lince


def _arquivo(tmp_path):
    arq = tmp_path / "conversao.pdf"
    c = canvas.Canvas(str(arq))
    c.drawString(32, 700, "Equivalente: 2.083 661 912 x 10^10 Hz.")
    for i in range(8):
        c.drawString(50, 615 - i * 20, "x = 2 × 3 ± 5 ÷ 7")
    c.save()
    return arq


def _plano(tmp_path):
    return lince.Leitor().preparar(_arquivo(tmp_path))


def test_divergencia_de_expoente_registra_evidencia(tmp_path):
    plano = _plano(tmp_path)
    (t,) = plano.tarefas
    erro = "| Conversão | Valor |\n|---|---|\n| Kelvin–hertz | 2.083 661 912 × 10⁻⁴ Hz |"
    r = lince.Leitor().concluir(plano, {t.id: erro})
    assert "expoente_divergente" in r.estrutura["paginas_visuais"][0]["alertas"]
    assert any("10^-4" in p and "10^10" in p and t.id in p for p in r.cobertura.pendentes)
    assert erro in r.markdown


def test_expoente_equivalente_e_dado_sem_referencia_nao_disparam(tmp_path):
    plano = _plano(tmp_path)
    (t,) = plano.tarefas
    for resposta in (r"2.083661912 \times 10^{10} Hz", "7.089654 × 10⁻⁴ Hz"):
        r = lince.Leitor().concluir(plano, {t.id: resposta})
        assert not r.cobertura.pendentes


def test_legenda_hachurada_sem_correspondencia_na_tabela(tmp_path):
    plano = _plano(tmp_path)
    (t,) = plano.tarefas
    legenda = "Valores em fundo branco são efetivos e os em fundo hachurado são projeções."
    sem = "| Período | IPCA |\n|---|---|\n| III | 4,4 |\n| IV | 4,9 |\n\n" + legenda
    r = lince.Leitor().concluir(plano, {t.id: sem})
    assert "condicao_visual" in r.estrutura["paginas_visuais"][0]["alertas"]
    assert any(t.id in p and "condição visual" in p for p in r.cobertura.pendentes)
    com = sem.replace("| III |", "| III (efetivo) |").replace("| IV |", "| IV (projeção) |")
    assert not lince.Leitor().concluir(plano, {t.id: com}).cobertura.pendentes


def test_legenda_na_mesma_celula_nao_qualifica_dados():
    from lince.conferencia_visual import avisos_transcricao

    nota = "Fundo branco = efetivo e hachurado = projeção"
    for transcricao in ("<table><tr><td>" + nota + "</td></tr></table>", "<![desconhecido]>"):
        assert "condicao_visual" in avisos_transcricao(transcricao, nota, {})


def test_api_devolve_alertas_de_transcricao_conformes(tmp_path):
    from starlette.testclient import TestClient
    from test_servidor import ESPERA, A, conforme, enviar

    from lince.servidor import criar_app

    with TestClient(criar_app(["token-a"])) as api:
        prep = conforme(
            enviar(api, ("c.pdf", _arquivo(tmp_path).read_bytes()), {**A, **ESPERA}, modo="tarefas"),
            "/v1/sessoes",
            "post",
        ).json()["itens"][0]["resultado"]
        tarefa = prep["tarefas"][0]["id"]
        resposta = "2.083661912 × 10⁻⁴ Hz. Fundo branco = efetivo e fundo hachurado = projeção"
        montado = api.post("/v1/montagens", headers=A, json={"plano": prep["plano"], "respostas": {tarefa: resposta}})
        item = conforme(montado, "/v1/montagens", "post").json()
        assert {"expoente_divergente", "condicao_visual"} <= set(item["estrutura"]["paginas_visuais"][0]["alertas"])


def test_potencia_em_latex_e_condicao_so_no_rodape():
    from lince.conferencia_visual import avisos_transcricao

    latex = r"$2.083661912 \times 10^{-4}\,\mathrm{Hz}$"
    assert "expoente_divergente" in avisos_transcricao(latex, "", {"2.083661912 Hz": [10]})
    legenda = "Fundo branco = efetivo e hachurado = projeção"
    for nota in (
        "<tfoot><tr><td>Efetivo</td><td>Projeção</td></tr></tfoot>",
        "<tr><td>Fundo branco: efetivo</td><td>Hachurado: projeção</td></tr>",
    ):
        assert "condicao_visual" in avisos_transcricao(
            "<table><tr><td>2025</td><td>4,9</td></tr>" + nota + "</table>", legenda, {}
        )


def test_condicao_com_cor_nas_linhas_de_dados():
    from lince.conferencia_visual import avisos_transcricao

    resposta = (
        "| Período | IPCA |\n|---|---|\n| III (efetivo, fundo branco) | 4,4 |\n| IV (projeção, fundo hachurado) | 4,9 |"
    )
    assert not avisos_transcricao(resposta, "", {})


def test_condicoes_indicadas_por_cabecalhos_coloridos():
    from lince.conferencia_visual import avisos_transcricao

    for tabela in (
        "| Período | Efetivo (fundo branco) | Projeção (fundo hachurado) |\n|---|---|---|\n| III | 4,4 | 4,9 |",
        "<table><thead><tr><th>Efetivo (fundo branco)</th><th>Projeção (fundo hachurado)</th></tr></thead>"
        "<tbody><tr><td>4,4</td><td>4,9</td></tr></tbody></table>",
    ):
        assert not avisos_transcricao(tabela, "", {})
