"""Antivírus (clamd INSTREAM): limpo passa, infectado é recusado antes de ser lido, clamd fora recusa."""

import socket
import struct
import threading

import pytest
from starlette.testclient import TestClient

from lince import antivirus
from lince.servidor import criar_app

MARCA = b"ASSINATURA-DE-TESTE"
H = {"Authorization": "Bearer t", "Prefer": "wait=30"}


@pytest.fixture
def clamd_falso():
    """Fala o protocolo INSTREAM: responde FOUND se o conteúdo tiver MARCA, OK se não. Guarda o que recebeu."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    recebidos = []

    def atender():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            with c:
                buf = b""
                while not buf.startswith(b"zINSTREAM\0") or len(buf) < 10:
                    buf += c.recv(65536)
                dados, buf = b"", buf[10:]
                while True:
                    while len(buf) < 4:
                        buf += c.recv(65536)
                    n = struct.unpack("!L", buf[:4])[0]
                    buf = buf[4:]
                    if n == 0:
                        break
                    while len(buf) < n:
                        buf += c.recv(65536)
                    dados, buf = dados + buf[:n], buf[n:]
                recebidos.append(dados)
                c.sendall(b"stream: Teste-Assinatura FOUND\0" if MARCA in dados else b"stream: OK\0")

    threading.Thread(target=atender, daemon=True).start()
    yield srv.getsockname(), recebidos
    srv.close()


def test_arquivo_limpo_e_varrido_e_lido(clamd_falso):
    alvo, recebidos = clamd_falso
    api = TestClient(criar_app(["t"], alvo))
    r = api.post("/v1/sessoes", files=[("arquivo", ("nota.txt", b"Texto limpo."))], headers=H)
    assert r.status_code == 201 and r.json()["estado"] == "concluida"
    assert recebidos == [b"Texto limpo."]  # o conteúdo inteiro passou pelo antivírus


def test_arquivo_infectado_e_recusado_e_o_lote_nao_entra(clamd_falso):
    alvo, _ = clamd_falso
    api = TestClient(criar_app(["t"], alvo))
    lote = [("arquivo", ("bom.txt", b"ok")), ("arquivo", ("mau.txt", b"xx" + MARCA))]
    r = api.post("/v1/sessoes", files=lote, headers=H)
    assert r.status_code == 422 and r.json()["type"] == "urn:lince:erro:arquivo-infectado"
    assert "Teste-Assinatura" in r.json()["detail"] and "mau.txt" in r.json()["detail"]
    assert api.app.state.servico.sessoes == {} and api.app.state.servico.vagas == {}


def test_antivirus_fora_do_ar_recusa_em_vez_de_ler_sem_varrer():
    livre = socket.socket()
    livre.bind(("127.0.0.1", 0))
    porta = livre.getsockname()[1]
    livre.close()
    api = TestClient(criar_app(["t"], ("127.0.0.1", porta)))
    r = api.post("/v1/sessoes", files=[("arquivo", ("a.txt", b"oi"))], headers=H)
    assert r.status_code == 503 and r.json()["type"] == "urn:lince:erro:antivirus-indisponivel"
    assert r.headers["retry-after"] == "30"


def test_sem_antivirus_configurado_nada_muda():
    api = TestClient(criar_app(["t"]))
    assert api.post("/v1/sessoes", files=[("arquivo", ("a.txt", b"oi"))], headers=H).status_code == 201


def test_endereco_do_clamav():
    assert antivirus.endereco(None) is None
    assert antivirus.endereco("clamav:3310") == ("clamav", 3310)
    assert antivirus.endereco("tcp://10.0.0.5:3310") == ("10.0.0.5", 3310)
    with pytest.raises(ValueError):
        antivirus.endereco("clamav")
