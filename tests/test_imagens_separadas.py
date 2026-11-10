"""Modo tarefas com as imagens à parte (issue #62): as tarefas vêm sem a imagem, cada uma é buscada sozinha e deixa de
contar no teto do token ao ser buscada, o teto de tarefas sobe para o documento grande, e o que passa do teto fica nas
pendências pelas páginas. Quem não pede o novo recebe o mesmo de antes."""

import hashlib
import io
import os
import time

import pytest
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from starlette.testclient import TestClient
from test_servidor import ESPERA, A, B, _docx_com_imagens, _montar, conforme, enviar

import lince
import lince.servidor as srv
from lince.leitor import pendencias_do_teto

SESSOES = "/v1/sessoes"


def _escaneado(paginas):
    """PDF de páginas escaneadas: cada uma é uma imagem de texto, que vai inteira para a IA."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    for k in range(paginas):
        im = Image.new("RGB", (620, 877), "white")
        d = ImageDraw.Draw(im)
        for j in range(30):
            d.text((40, 30 + 27 * j), f"Laudo {k + 1}, linha {j + 1}: medição {(k * 31 + j) % 997}.", fill="black")
        png = io.BytesIO()
        im.save(png, "PNG")
        c.drawImage(ImageReader(io.BytesIO(png.getvalue())), 0, 0, *A4)
        c.showPage()
    c.save()
    return buf.getvalue()


def _docx(cores):
    from docx import Document
    from docx.shared import Inches

    d = Document()
    d.add_paragraph("Relatório de moagem.")
    for cor in cores:
        im = Image.new("RGB", (600, 300), "white")
        ImageDraw.Draw(im).rectangle([50, 50, 400, 250], fill=cor)
        b = io.BytesIO()
        im.save(b, "PNG")
        d.add_picture(io.BytesIO(b.getvalue()), width=Inches(3))
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


@pytest.fixture
def api():
    with TestClient(srv.criar_app(["token-a", "token-b"])) as cliente:
        yield cliente


def _preparo(api, arquivo, **campos):
    r = conforme(enviar(api, arquivo, {**A, **ESPERA}, modo="tarefas", **campos), SESSOES, "post")
    assert r.status_code == 201, r.json()
    return r.json()


ROTA = "/v1/sessoes/{id}/itens/{item}/tarefas/{tarefa}/imagem"


def _imagem(api, sessao, tid, cab=A, item=0):
    doc = sessao["itens"][item]["id"] if isinstance(item, int) else item
    return api.get(f"/v1/sessoes/{sessao['id']}/itens/{doc}/tarefas/{tid}/imagem", headers=cab)


def test_imagens_separadas_sao_as_mesmas_das_embutidas(api):
    docx = ("r.docx", _docx_com_imagens())
    embutido = _preparo(api, docx)["itens"][0]["resultado"]
    sessao = _preparo(api, docx, imagens="separadas")
    separado = sessao["itens"][0]["resultado"]
    assert len(separado["tarefas"]) == len(embutido["tarefas"]) == 2
    for sep, emb in zip(separado["tarefas"], embutido["tarefas"], strict=True):
        assert "imagem" not in sep and {k: v for k, v in sep.items() if k != "imagem_bytes"} == {
            k: v for k, v in emb.items() if k != "imagem"
        }  # mesma instrução, chave, página e tokens
        r = conforme(_imagem(api, sessao, sep["id"]), ROTA, "get")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert len(r.content) == sep["imagem_bytes"]
        import base64

        assert hashlib.sha256(r.content).digest() == hashlib.sha256(base64.b64decode(emb["imagem"])).digest()
    respostas = {t["id"]: "Figura." for t in separado["tarefas"]}
    assert _montar(api, {"plano": separado["plano"], "respostas": respostas}).status_code == 200


def test_cada_imagem_sai_do_teto_ao_ser_buscada_e_a_sessao_espera_por_elas(api):
    s = api.app.state.servico
    sessao = _preparo(api, ("r.docx", _docx_com_imagens()), imagens="separadas")  # entregue: o POST esperou
    sid, tarefas = sessao["id"], sessao["itens"][0]["resultado"]["tarefas"]
    assert sum(s.retidos.values()) == sum(t["imagem_bytes"] for t in tarefas)  # o JSON já foi entregue; as imagens não
    x = s.sessoes[sid]
    x["entregue_em"] -= srv.REENTREGA + 5  # a repetição da entrega já venceu, mas há imagem não buscada
    assert api.get(f"/v1/sessoes/{sid}", headers=A).status_code == 200
    pasta = x["itens"][0]["pasta_imagens"]
    assert _imagem(api, sessao, tarefas[0]["id"]).status_code == 200
    assert sum(s.retidos.values()) == tarefas[1]["imagem_bytes"]
    item = sessao["itens"][0]["id"]
    assert api.head(f"/v1/sessoes/{sid}/itens/{item}/tarefas/{tarefas[1]['id']}/imagem", headers=A).status_code == 200
    assert sum(s.retidos.values()) == tarefas[1]["imagem_bytes"]  # HEAD não conta como busca
    assert _imagem(api, sessao, tarefas[1]["id"]).status_code == 200 and s.retidos == {}
    assert _imagem(api, sessao, tarefas[1]["id"]).status_code == 200  # repetir a busca dentro da janela
    x["ultima_busca"] -= srv.REENTREGA + 5  # tudo buscado e a janela de repetição passou: a sessão some
    assert api.get(f"/v1/sessoes/{sid}", headers=A).status_code == 404
    assert not os.path.exists(pasta)


def test_imagem_de_outro_token_inexistente_ou_embutida_e_404(api):
    separada = _preparo(api, ("r.docx", _docx_com_imagens()), imagens="separadas")
    tid = separada["itens"][0]["resultado"]["tarefas"][0]["id"]
    embutida = _preparo(api, ("r.docx", _docx_com_imagens()))
    for sessao, t, cab, item in [(separada, tid, B, 0), (separada, "nao-existe", A, 0), (embutida, tid, A, 0),
                                 (separada, "..%2F..%2Fx", A, 0), (separada, tid, A, "outro-documento")]:  # fmt: skip
        r = conforme(_imagem(api, sessao, t, cab, item), ROTA, "get")
        assert r.status_code == 404, (sessao["id"], t, item)


def test_delete_apaga_as_imagens_e_libera_o_teto(api):
    s = api.app.state.servico
    sessao = _preparo(api, ("r.docx", _docx_com_imagens()), imagens="separadas")
    pasta = s.sessoes[sessao["id"]]["itens"][0]["pasta_imagens"]
    assert os.listdir(pasta) and s.retidos
    assert api.delete(f"/v1/sessoes/{sessao['id']}", headers=A).status_code == 204
    assert not os.path.exists(pasta) and s.retidos == {}


def test_campo_imagens_invalido(api):
    for campos in ({"modo": "tarefas", "imagens": "base64"}, {"imagens": "separadas"}):
        r = conforme(enviar(api, ("r.docx", b"x"), A, **campos), SESSOES, "post")
        assert r.status_code == 400 and r.json()["type"] == "urn:lince:erro:imagens-invalido"


def test_documento_grande_cabe_inteiro_com_as_imagens_separadas(api, monkeypatch):
    """O PDF que passava do teto embutido entra inteiro à parte, e o que passa do teto diz as páginas."""
    monkeypatch.setattr(srv, "TAREFAS_EMBUTIDAS_POR_DOCUMENTO", 3)
    pdf = ("laudo.pdf", _escaneado(6))
    embutido = _preparo(api, pdf)["itens"][0]["resultado"]
    assert len(embutido["tarefas"]) == 3
    assert embutido["pendentes"] == ["páginas 4–6: teto de 3 tarefas por documento"]
    separado = _preparo(api, pdf, imagens="separadas")["itens"][0]["resultado"]
    assert {t["pagina"] for t in separado["tarefas"]} == set(range(1, 7)) and separado["pendentes"] == []
    corte = _preparo(api, pdf, imagens="separadas", max_tarefas="2")["itens"][0]["resultado"]
    assert corte["pendentes"] == ["páginas 3–6: teto de 2 tarefas por documento"]


def test_teto_da_sessao_apaga_as_imagens_cortadas(api, monkeypatch):
    monkeypatch.setattr(srv, "TAREFAS_POR_SESSAO", 3)
    s = api.app.state.servico
    sessao = _preparo(api, [("r.docx", _docx_com_imagens()), ("s.docx", _docx_com_imagens())], imagens="separadas")
    itens = sessao["itens"]
    assert sum(len(i["resultado"]["tarefas"]) for i in itens) == 3
    cortadas = [p for i in itens for p in i["resultado"]["pendentes"] if p.endswith("teto de 3 tarefas da sessão")]
    assert len(cortadas) == 1
    pastas = [d["pasta_imagens"] for d in s.sessoes[sessao["id"]]["itens"]]
    assert sum(len(os.listdir(p)) for p in pastas) == 3  # só as que ficaram no plano


def test_resultado_que_nao_fica_nao_deixa_imagens(api, monkeypatch):
    def falha(*_a, **_k):
        raise ValueError("plano grande demais")

    monkeypatch.setattr(srv, "selar", falha)
    s = api.app.state.servico
    sessao = _preparo(api, ("r.docx", _docx_com_imagens()), imagens="separadas")
    assert sessao["itens"][0]["estado"] == "falhou"
    assert os.listdir(s.guardados) == [] and s.retidos == {}


def test_biblioteca_grava_as_imagens_na_pasta(tmp_path):
    arq = tmp_path / "r.docx"
    arq.write_bytes(_docx_com_imagens())
    na_memoria = lince.Leitor().preparar(arq)
    pasta = tmp_path / "imagens"
    pasta.mkdir()
    em_disco = lince.Leitor().preparar(arq, imagens_em=pasta)
    for m, d in zip(na_memoria.tarefas, em_disco.tarefas, strict=True):
        assert d.imagem == b"" and (pasta / f"{d.id}.png").read_bytes() == m.imagem
        assert (d.chave, d.tokens_estimados, d.instrucao) == (m.chave, m.tokens_estimados, m.instrucao)


def test_pendencias_do_teto_pelas_paginas():
    plano = {"tarefas_originais": [{"id": "p003-r1", "pagina": 2}, {"id": "p009-r1", "pagina": 8}]}
    cortadas = [
        {"id": "p005a", "modo": "pagina", "pagina": 4},
        {"id": "p005b", "modo": "pagina", "pagina": 4},
        {"id": "p006", "modo": "pagina", "pagina": 5},
        {"id": "lote01", "modo": "lote", "itens": [{"tarefa": "p003-r1"}, {"tarefa": "p009-r1"}]},
        {"id": "img2", "modo": "imagem"},
    ]
    assert pendencias_do_teto(cortadas, plano, "teto") == [
        "página 3: teto",
        "páginas 5–6: teto",
        "página 9: teto",
        "img2: teto",
    ]


def test_o_teto_espera_pelas_imagens_nao_buscadas(api, monkeypatch):
    """O Retry-After do 429 de retenção conta a sessão entregue que ainda guarda imagens."""
    sessao = _preparo(api, ("r.docx", _docx_com_imagens()), imagens="separadas")
    s = api.app.state.servico
    monkeypatch.setitem(s.tetos, srv._identidade("token-a"), 1)
    r = conforme(enviar(api, ("r.docx", _docx_com_imagens()), A, modo="tarefas"), SESSOES, "post")
    assert r.status_code == 429
    restante = s.sessoes[sessao["id"]]["concluida_em"] + srv.ENTREGA_PRAZO - time.time()
    assert abs(int(r.headers["Retry-After"]) - restante) <= 2


def test_cada_documento_do_lote_tem_as_suas_imagens(api):
    """Os ids das tarefas se repetem entre documentos (img01 nos dois): a imagem é a do documento pedido."""
    r_docx, s_docx = _docx(("navy", "darkgreen")), _docx(("darkred", "gold"))
    s = api.app.state.servico
    lote = _preparo(api, [("r.docx", r_docx), ("s.docx", s_docx)], imagens="separadas")
    for k, (nome, dados) in enumerate([("r.docx", r_docx), ("s.docx", s_docx)]):
        import base64

        emb = {t["id"]: t["imagem"] for t in _preparo(api, (nome, dados))["itens"][0]["resultado"]["tarefas"]}
        for t in lote["itens"][k]["resultado"]["tarefas"]:
            assert _imagem(api, lote, t["id"], item=k).content == base64.b64decode(emb[t["id"]])
    assert s.retidos == {}


def test_imagem_do_documento_pronto_antes_do_fim_do_lote(api, monkeypatch):
    """O documento que ficou pronto já entrega as imagens, com o outro do lote ainda em leitura."""
    import threading

    solta, original, chamadas = threading.Event(), srv.Leitor.preparar, []

    def preparar(self, caminho, *a, **k):
        chamadas.append(caminho)
        if len(chamadas) == 2:  # o segundo documento a começar fica preso até o fim do teste
            solta.wait(30)
        return original(self, caminho, *a, **k)

    monkeypatch.setattr(srv.Leitor, "preparar", preparar)
    try:
        r = enviar(api, [("r.docx", _docx_com_imagens()), ("s.docx", _docx_com_imagens())], A, modo="tarefas",
                   imagens="separadas")  # fmt: skip
        sid = r.json()["id"]
        limite = time.time() + 30
        while True:
            sessao = api.get(f"/v1/sessoes/{sid}", headers=A).json()
            if any(i["estado"] == "pronto" for i in sessao["itens"]):
                break
            assert time.time() < limite
            time.sleep(0.1)
        (k, pronto), *_ = [(k, i) for k, i in enumerate(sessao["itens"]) if i["estado"] == "pronto"]
        assert sessao["estado"] == "processando"
        assert _imagem(api, sessao, pronto["resultado"]["tarefas"][0]["id"], item=k).status_code == 200
    finally:
        solta.set()


def test_entregue_perto_do_prazo_ainda_tem_a_janela_de_repeticao(api):
    sessao = _preparo(api, ("r.docx", _docx_com_imagens()), imagens="separadas")
    x = api.app.state.servico.sessoes[sessao["id"]]
    x["concluida_em"] -= srv.ENTREGA_PRAZO - 5  # entregue a 5 s do prazo, com imagens não buscadas
    x["entregue_em"] = time.time()
    assert srv._expira(x, x["concluida_em"]) >= x["entregue_em"] + srv.REENTREGA


def test_montagem_passa_com_o_teto_cheio_de_imagens_ainda_nao_buscadas(api, monkeypatch):
    """Quem chama monta um documento do lote enquanto as imagens dos outros ainda ocupam o teto: a montagem não guarda
    nada, e o 429 de retenção fica para os envios novos."""
    lote = _preparo(api, [("r.docx", _docx_com_imagens()), ("s.docx", _docx_com_imagens())], imagens="separadas")
    s = api.app.state.servico
    monkeypatch.setitem(s.tetos, srv._identidade("token-a"), 1)
    um = lote["itens"][0]["resultado"]
    respostas = {t["id"]: "Figura." for t in um["tarefas"]}
    assert _montar(api, {"plano": um["plano"], "respostas": respostas}).status_code == 200
    novo = conforme(enviar(api, ("r.docx", _docx_com_imagens()), A, modo="tarefas"), SESSOES, "post")
    assert novo.status_code == 429
