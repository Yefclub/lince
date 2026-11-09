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

    if sys.argv[1] == "sem":  # o serviço de antes: nem o limite de mmap, nem a devolução periódica
        memoria.ajustar_alocador = lambda: False
        memoria.devolver = lambda: False
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


def _medir(modo):
    import json

    comando = [sys.executable, "-c", _CARGA, modo]  # o próprio interpretador, com o roteiro deste arquivo
    saida = subprocess.run(comando, capture_output=True, text=True, timeout=600, check=True)  # noqa: S603
    return json.loads(saida.stdout.strip().splitlines()[-1])


def test_memoria_das_leituras_volta_ao_sistema_em_um_intervalo():
    m = _medir("com")
    # sem o ajuste (modo "sem"), a mesma carga deixava o processo em ~2,4× (medido: 100 → ~240 MB)
    assert m["depois"] < 2 * m["antes"], m


def test_saude_informa_a_memoria_e_as_funcoes_sao_seguras_fora_do_glibc(monkeypatch):
    assert memoria.rss() > 0
    monkeypatch.setattr(memoria, "_GLIBC", None)  # outra libc: nada muda, nada quebra
    assert memoria.devolver() is False and memoria.ajustar_alocador() is False
