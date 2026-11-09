"""Teto de resultados esperando entrega por token (issue #55): 1 GiB por padrão, um por token na LINCE_TOKENS, em
disco, liberado na entrega e com um 429 que diz quanto esperar."""

import json
import os
import threading
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from test_servidor import A, B, conforme, enviar

import lince.servidor as srv
from lince.tipos import Cobertura, Resultado

MB = 1024 * 1024


class _Pesado:
    """Leitor de mentira: devolve um resultado do tamanho de um documento pesado (imagens da 1ª fase e o texto)."""

    tamanho = 170 * MB

    def __init__(self, **_):
        pass

    def ler(self, caminho, prazo=None):
        return Resultado("x" * self.tamanho, {}, Cobertura(paginas=1))


def _editar(api):
    pedido = {"operacoes": json.dumps({"operacoes": []})}
    return api.post("/v1/edicoes", files=[("arquivo", ("d.pptx", b"PK"))], data=pedido, headers=A)


def _pronta(api, sid):
    assert api.app.state.servico.sessoes[sid]["pronta"].wait(60)


def test_teto_padrao_cabe_seis_documentos_pesados_e_o_setimo_espera(monkeypatch):
    monkeypatch.setattr(srv, "Leitor", _Pesado)
    with TestClient(srv.criar_app(["token-a", "token-b"])) as api:
        _seis_e_o_setimo(api)


def _seis_e_o_setimo(api):
    ids = []
    for k in range(6):  # 6 x 170 MiB = 1020 MiB, dentro de 1 GiB: em sessões simultâneas, nenhuma buscada
        r = enviar(api, (f"d{k}.txt", b"documento pesado"), A)
        assert r.status_code == 201, r.json()
        ids.append(r.json()["id"])
        _pronta(api, ids[-1])
    s = api.app.state.servico
    assert sum(s.retidos.values()) > 6 * 170 * MB
    assert all(os.path.getsize(d["guardado"]) > 170 * MB for x in s.sessoes.values() for d in x["itens"])  # no disco
    setimo = conforme(enviar(api, ("d7.txt", b"x" * (60 * MB)), A), "/v1/sessoes", "post")  # mais um pesado: passa
    assert setimo.status_code == 429 and setimo.json()["type"] == "urn:lince:erro:limite-retencao"
    corpo, espera = setimo.json(), int(setimo.headers["Retry-After"])
    assert corpo["teto_bytes"] == 1024 * MB and corpo["em_uso_bytes"] > 6 * 170 * MB
    assert 1 <= espera <= srv.ENTREGA_PRAZO  # quando o primeiro resultado não buscado vence, não um palpite fixo
    assert enviar(api, ("b.txt", b"outro token"), B).status_code == 201  # o teto é de cada token
    entregue = api.get(f"/v1/sessoes/{ids[0]}", headers=A)  # buscar: os bytes deixam de contar na hora
    assert entregue.status_code == 200 and len(entregue.json()["itens"][0]["resultado"]["markdown"]) == 170 * MB
    assert enviar(api, ("d7.txt", b"x" * (60 * MB)), A).status_code == 201


def test_teto_de_cada_token_na_lince_tokens():
    tokens, tetos = srv.tokens_do_ambiente(" qa:token-a:2G , token-b, prod:tok:en:c:512MiB, d:e")
    assert tokens == ["token-a", "token-b", "tok:en:c", "d:e"]  # sem tamanho no fim, a entrada inteira é o token
    assert tetos == {"token-a": 2 * 1024 * MB, "tok:en:c": 512 * MB}
    with pytest.raises(ValueError):
        srv.tokens_do_ambiente("qa::1G")


def test_token_com_teto_proprio_recebe_429_no_dele(monkeypatch):
    monkeypatch.setattr(srv, "Leitor", _Pesado)
    monkeypatch.setattr(_Pesado, "tamanho", 3 * MB)
    with TestClient(srv.criar_app(["token-a", "token-b"], tetos={"token-a": 5 * MB})) as api:
        _pronta(api, enviar(api, ("a.txt", b"a"), A).json()["id"])
        r = enviar(api, ("a.txt", b"a" * (3 * MB)), A)  # 3 + 3 passa dos 5 MiB do token-a
        assert r.status_code == 429 and r.json()["teto_bytes"] == 5 * MB
        _pronta(api, enviar(api, ("b.txt", b"b"), B).json()["id"])  # o token-b fica no padrão
        assert enviar(api, ("b.txt", b"b" * (3 * MB)), B).status_code == 201


def test_resultado_some_do_disco_quando_a_sessao_some(monkeypatch):
    monkeypatch.setattr(srv, "Leitor", _Pesado)
    monkeypatch.setattr(_Pesado, "tamanho", 1000)
    with TestClient(srv.criar_app(["token-a"])) as api:
        s = api.app.state.servico
        sid = enviar(api, ("a.txt", b"a"), A).json()["id"]
        _pronta(api, sid)
        guardado = s.sessoes[sid]["itens"][0]["guardado"]
        assert os.path.exists(guardado) and s.retidos
        assert api.delete(f"/v1/sessoes/{sid}", headers=A).status_code == 204
        assert not os.path.exists(guardado) and s.retidos == {}
        pasta = s.guardados
    assert not os.path.exists(pasta)  # o serviço parou: a pasta dos resultados sai junto


def test_disco_que_falha_ao_guardar_falha_o_documento_e_nao_prende_o_teto(monkeypatch):
    monkeypatch.setattr(srv, "Leitor", _Pesado)
    monkeypatch.setattr(_Pesado, "tamanho", 1000)
    gravar = Path.write_bytes

    def cheio(self, dados):
        if self.suffix == ".json":
            raise OSError(28, "No space left on device")
        return gravar(self, dados)

    monkeypatch.setattr(Path, "write_bytes", cheio)
    with TestClient(srv.criar_app(["token-a"])) as api:
        s = api.app.state.servico
        sid = enviar(api, ("a.txt", b"a"), A).json()["id"]
        _pronta(api, sid)
        item = api.get(f"/v1/sessoes/{sid}", headers=A).json()["itens"][0]
        assert item["estado"] == "falhou" and "não pôde ser guardado" in item["erro"]
        assert s.retidos == {} and os.listdir(s.guardados) == []


def test_sessao_vencida_nao_conta_nem_vira_retry_after_no_passado(monkeypatch):
    monkeypatch.setattr(srv, "Leitor", _Pesado)
    monkeypatch.setattr(_Pesado, "tamanho", 3 * MB)
    monkeypatch.setattr(srv.codigo_doc, "editar", lambda *_: (b"E" * (3 * MB), {"diff": {}}))
    with TestClient(srv.criar_app(["token-a"], tetos={"token-a": 5 * MB})) as api:
        sid = enviar(api, ("a.txt", b"a"), A).json()["id"]
        _pronta(api, sid)
        api.app.state.servico.sessoes[sid]["concluida_em"] -= srv.ENTREGA_PRAZO + 60  # venceu sem a limpeza passar
        assert _editar(api).status_code == 201  # a edição não passa pela limpeza do POST de sessão


def test_com_tudo_ainda_em_leitura_o_retry_after_e_30(monkeypatch):
    solta = threading.Event()

    class _Lento(_Pesado):
        def ler(self, caminho, prazo=None):
            solta.wait(60)
            return super().ler(caminho, prazo)

    monkeypatch.setattr(srv, "Leitor", _Lento)
    monkeypatch.setattr(_Pesado, "tamanho", 10)
    with TestClient(srv.criar_app(["token-a"], tetos={"token-a": 3 * MB})) as api:
        try:
            assert enviar(api, ("a.txt", b"a" * (2 * MB)), A).status_code == 201  # o envio reserva os bytes dele
            r = enviar(api, ("b.txt", b"b" * (2 * MB)), A)
            assert r.status_code == 429 and r.headers["Retry-After"] == "30"
        finally:
            solta.set()


def test_edicao_fica_no_disco_e_libera_o_teto_quando_baixada(monkeypatch):
    novo = b"E" * (3 * MB)
    monkeypatch.setattr(srv.codigo_doc, "editar", lambda *_: (novo, {"diff": {}}))
    with TestClient(srv.criar_app(["token-a"], tetos={"token-a": 5 * MB})) as api:
        s = api.app.state.servico

        r = _editar(api)
        assert r.status_code == 201
        (x,) = s.edicoes.values()
        assert os.path.getsize(x["guardado"]) == len(novo) and list(s.retidos.values()) == [len(novo)]
        assert _editar(api).status_code == 429  # 3 + 3 passa dos 5 MiB
        baixado = api.get(r.headers["location"], headers=A)
        assert baixado.content == novo and s.retidos == {}  # baixou: deixa de contar
        assert _editar(api).status_code == 201
        assert api.delete(r.headers["location"], headers=A).status_code == 204
        assert not os.path.exists(x["guardado"])
