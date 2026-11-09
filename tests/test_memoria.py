"""A memória que as leituras liberaram volta ao sistema no serviço (issue #52)."""

import subprocess
import sys
import textwrap

import pytest

from lince import memoria

pytestmark = pytest.mark.skipif(memoria.rss() is None or not memoria.devolver(), reason="só no Linux com glibc")

# um processo novo: o RSS do pytest, com tudo o que os outros testes alocaram, não serve de referência
_CARGA = textwrap.dedent(
    """
    import gc, json, sys, tempfile, time
    from concurrent.futures import ThreadPoolExecutor

    from PIL import Image
    from reportlab.pdfgen import canvas
    from starlette.testclient import TestClient

    import lince
    import lince.servidor as srv
    from lince import memoria

    memoria.ajustar_alocador()  # como o main do servidor, antes das threads
    srv.LIMPEZA_INTERVALO = 0.3
    d = tempfile.mkdtemp()
    Image.effect_noise((1600, 1200), 80).convert("RGB").save(f"{d}/f.jpg", quality=90)
    p = f"{d}/doc.pdf"
    c = canvas.Canvas(p, pagesize=(612, 792))
    for k in range(4):  # páginas com uma imagem grande: a leitura decodifica e renderiza buffers de megabytes
        c.drawString(60, 760, f"Página {k}")
        c.drawImage(f"{d}/f.jpg", 60, 200, width=480, height=360)
        c.showPage()
    c.save()
    with TestClient(srv.criar_app(["t"])) as api:
        lince.Leitor().ler(p)  # o 1º documento carrega as bibliotecas, que ficam
        gc.collect()
        time.sleep(1)
        antes = api.get("/saude").json()["memoria"]["rss_bytes"]
        with ThreadPoolExecutor(8) as ex:  # leituras ao mesmo tempo, como no serviço
            list(ex.map(lambda _: lince.Leitor().ler(p), range(8)))
        gc.collect()
        time.sleep(1)  # mais que um intervalo de limpeza
        depois = api.get("/saude").json()["memoria"]["rss_bytes"]
    print(json.dumps({"antes": antes, "depois": depois}))
    """
)


def _medir():
    import json

    comando = [sys.executable, "-c", _CARGA]  # o próprio interpretador, com o roteiro deste arquivo
    saida = subprocess.run(comando, capture_output=True, text=True, timeout=600)  # noqa: S603
    assert saida.returncode == 0, saida.stderr[-3000:]
    return json.loads(saida.stdout.strip().splitlines()[-1])


def test_memoria_das_leituras_volta_ao_sistema_em_um_intervalo():
    m = _medir()
    # sem o ajuste e sem a devolução, a mesma carga deixava o processo em ~2,3× (medido: 100 → ~234 MB)
    assert m["depois"] < 2 * m["antes"], m


def test_saude_informa_a_memoria_e_as_funcoes_sao_seguras_fora_do_glibc(monkeypatch):
    assert memoria.rss() > 0
    monkeypatch.setattr(memoria, "_GLIBC", None)  # outra libc: nada muda, nada quebra
    assert memoria.devolver() is False and memoria.ajustar_alocador() is False


def test_limpeza_periodica_devolve_a_memoria_em_um_intervalo_fora_do_laco(monkeypatch):
    """Independe do glibc: a limpeza do serviço chama a devolução a cada intervalo, depois de limpar as sessões e
    numa thread que não é a do laço de eventos."""
    import threading
    import time

    from starlette.testclient import TestClient

    import lince.servidor as srv

    chamadas, limpezas = [], []
    monkeypatch.setattr(srv, "LIMPEZA_INTERVALO", 0.2)
    monkeypatch.setattr(memoria, "devolver", lambda: chamadas.append((time.monotonic(), threading.get_ident())))
    monkeypatch.setattr(srv.Servico, "limpar", lambda self: limpezas.append(time.monotonic()))
    with TestClient(srv.criar_app(["t"])) as api:
        laco = api.portal.call(threading.get_ident)  # a thread do laço de eventos do app
        inicio = time.monotonic()
        while not chamadas and time.monotonic() - inicio < 5:
            time.sleep(0.02)
    assert chamadas and chamadas[0][0] - inicio < 2 * 0.2 + 0.3  # dentro do primeiro intervalo, com folga de agenda
    assert limpezas and limpezas[0] <= chamadas[0][0]  # depois de limpar as sessões
    assert all(fio != laco for _, fio in chamadas)  # fora do laço de eventos
