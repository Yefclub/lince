"""Teto de resultados esperando entrega por token (issue #55): 1 GiB por padrão, um por token na LINCE_TOKENS, em
disco, liberado na entrega e com um 429 que diz quanto esperar."""

import os

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


def _pronta(api, sid):
    assert api.app.state.servico.sessoes[sid]["pronta"].wait(60)


def test_teto_padrao_cabe_seis_documentos_pesados_e_o_setimo_espera(monkeypatch):
    monkeypatch.setattr(srv, "Leitor", _Pesado)
    api = TestClient(srv.criar_app(["token-a", "token-b"]))
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
    api = TestClient(srv.criar_app(["token-a", "token-b"], tetos={"token-a": 5 * MB}))
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
