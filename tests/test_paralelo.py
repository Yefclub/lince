"""Fase 1 do PDF em processos (issue #59): o plano sai igual ao da leitura em série (texto, tarefas, recortes e as
imagens repetidas entre páginas), documentos diferentes dividem os processos de forma justa, um processo que cai não
derruba o documento, e sem processos a leitura segue em série."""

import hashlib
import io
import json
import logging
import os
import threading
import time
from concurrent.futures.process import BrokenProcessPool

import pytest
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import lince
from lince import paralelo, pdf


def _imagem(cor, tamanho=(500, 300), ruido=None):
    im = Image.effect_noise(tamanho, ruido).convert("RGB") if ruido else Image.new("RGB", tamanho, "white")
    ImageDraw.Draw(im).rectangle([30, 30, tamanho[0] - 30, tamanho[1] - 30], outline=cor, width=12)
    b = io.BytesIO()
    im.save(b, "PNG")
    return ImageReader(io.BytesIO(b.getvalue()))


def _pagina_escaneada():
    im = Image.new("RGB", (1240, 1754), "white")
    d = ImageDraw.Draw(im)
    for k in range(60):
        d.text((80, 60 + 27 * k), f"Linha {k + 1} do laudo escaneado, medição {k * 37 % 997}.", fill="black")
    b = io.BytesIO()
    im.save(b, "PNG")
    return ImageReader(io.BytesIO(b.getvalue()))


@pytest.fixture(scope="module")
def relatorio(tmp_path_factory):
    """60 páginas (duas faixas do pdf_inspector): fotos, gráfico vetorial, o mesmo logo em várias páginas (e duas vezes
    numa delas, e de novo na segunda faixa), uma página escaneada e uma girada."""
    p = tmp_path_factory.mktemp("paralelo") / "relatorio.pdf"
    c = canvas.Canvas(str(p), pagesize=A4, invariant=1)
    logo = _imagem("navy", (300, 120))
    for k in range(60):
        c.setFont("Helvetica", 10)
        c.drawString(50, 800, f"Relatório de inspeção, seção {k + 1}: equipamento {k * 13 % 101}.")
        if k in (0, 1, 2, 55):
            c.drawImage(logo, 400, 760, 150, 60)
        if k == 2:
            c.drawImage(logo, 50, 60, 150, 60)
        if k in (0, 3, 56):
            c.drawImage(_imagem("darkred", ruido=40 + k), 60, 450, 300, 180)
        if k in (1, 57):
            for b in range(6):
                c.rect(80 + 30 * b, 200, 18, 20 + 25 * b, fill=1)
                c.drawString(80 + 30 * b, 188, str(20 + 25 * b))
        if k == 4:
            c.drawImage(_pagina_escaneada(), 0, 0, *A4)
        if k == 58:
            c.setPageRotation(90)
            c.drawImage(_imagem("darkgreen", ruido=70), 60, 450, 300, 180)
        c.showPage()
    c.save()
    return p


def _despejo(plano):
    """O plano inteiro, com o hash de cada imagem no lugar dela."""
    tarefas = [(t.id, t.tipo, hashlib.sha256(t.imagem).hexdigest(), t.instrucao, t.pagina, t.caixa, t.onde, t.itens)
               for t in plano.tarefas]  # fmt: skip
    return tarefas, json.dumps(plano.estado, sort_keys=True, ensure_ascii=False), plano.pendentes


@pytest.fixture
def em_processos():
    assert paralelo.ativar(2)
    try:
        for f in [paralelo.pool().submeter(k, time.sleep, 0.3) for k in range(2)]:  # os dois processos de pé
            f.result(timeout=120)
        yield paralelo.pool()
    finally:
        paralelo.desativar()


def test_plano_em_processos_e_igual_ao_da_leitura_em_serie(relatorio, em_processos):
    paralelo_ = _despejo(lince.Leitor().preparar(relatorio))
    paralelo.desativar()
    serie = _despejo(lince.Leitor().preparar(relatorio))
    assert paralelo_ == serie
    tarefas, estado, _ = serie
    regioes = [r for p in json.loads(estado)["paginas"] for r in p["regioes"]]
    repetidas = [r for r in regioes if r.get("repetida")]
    # o logo na 2ª e na 3ª página (duas vezes) e na 56ª, e o gráfico da 2ª de novo na 58ª, já na outra faixa
    assert len(repetidas) >= 4 and {r["tarefa"] for r in repetidas} == {"p001-r1", "p002-r2"}
    assert any(t[1] == "pagina" for t in tarefas)  # a página escaneada vai inteira


def test_leitura_completa_em_processos_e_igual(relatorio, em_processos):
    com = lince.ler(relatorio)
    paralelo.desativar()
    sem = lince.ler(relatorio)
    assert (com.markdown, com.metadados, com.cobertura.pendentes) == (sem.markdown, sem.metadados,
                                                                      sem.cobertura.pendentes)  # fmt: skip


def test_sem_processos_a_leitura_segue_em_serie(relatorio, monkeypatch):
    def sem_processos(*_):
        raise OSError("sem processos neste ambiente")

    monkeypatch.setattr(paralelo, "Distribuidor", sem_processos)
    assert paralelo.ativar(4) is False and paralelo.pool() is None
    assert paralelo.ativar(1) is False  # uma CPU: nada a paralelizar
    plano = lince.Leitor().preparar(relatorio)
    assert plano.tarefas and len(plano.estado["paginas"]) == 60


def test_processo_que_cai_e_trocado_e_o_documento_seguinte_e_lido(relatorio, em_processos):
    # com outros trabalhos sendo despachados enquanto o pool quebra: nada fica preso esperando
    outros = [em_processos.submeter("outro", time.sleep, 0.05) for _ in range(20)]
    caiu = em_processos.submeter("hostil", os._exit, 1)
    with pytest.raises(BrokenProcessPool):
        caiu.result(timeout=60)
    for f in outros:
        try:
            f.result(timeout=60)
        except BrokenProcessPool:  # estava no pool que quebrou
            pass
    assert len(lince.Leitor().preparar(relatorio).estado["paginas"]) == 60


def test_documento_e_lido_de_novo_se_os_processos_caem_no_meio(relatorio, em_processos, monkeypatch):
    real, chamadas = pdf._distribuir, []

    def cai_uma_vez(*args):
        chamadas.append(1)
        if len(chamadas) == 1:
            raise BrokenProcessPool("um processo morreu")
        return real(*args)

    monkeypatch.setattr(pdf, "_distribuir", cai_uma_vez)
    assert len(lince.Leitor().preparar(relatorio).estado["paginas"]) == 60 and len(chamadas) == 2

    def sempre_cai(*_):
        raise BrokenProcessPool("um processo morreu")

    monkeypatch.setattr(pdf, "_distribuir", sempre_cai)
    with pytest.raises(RuntimeError, match="caíram duas vezes"):
        lince.Leitor().preparar(relatorio)


def test_documento_grande_nao_segura_os_outros(em_processos):
    """Um documento com 12 trabalhos na fila e outro que chega depois com 1: o segundo entra na próxima vaga."""
    grande = [em_processos.submeter("grande", time.sleep, 0.3) for _ in range(12)]
    time.sleep(0.05)
    inicio = time.monotonic()
    em_processos.submeter("pequeno", time.sleep, 0.01).result(timeout=60)
    esperou = time.monotonic() - inicio
    assert esperou < 1.0 and sum(f.done() for f in grande) <= 4  # não esperou o grande inteiro (~1,8 s)
    for f in grande:
        f.result(timeout=60)


def test_documento_que_desiste_tira_o_que_falta_da_fila(em_processos):
    trabalhos = [em_processos.submeter("doc", time.sleep, 0.2) for _ in range(10)]
    em_processos.cancelar("doc")
    assert sum(f.cancelled() for f in trabalhos) >= 6


def test_prazo_vale_nos_processos(relatorio, em_processos):
    plano = lince.Leitor().preparar(relatorio, prazo=0.001)
    assert len(plano.estado["paginas"]) == 50  # a primeira faixa sempre entra, só com o texto
    assert any("prazo esgotado" in p for p in plano.estado["pendentes"])


def test_tempo_de_cada_etapa_no_log(relatorio, em_processos, caplog):
    with caplog.at_level(logging.INFO, logger="lince"):
        lince.Leitor().preparar(relatorio)
        paralelo.desativar()
        lince.Leitor().preparar(relatorio)
    linhas = [r.getMessage() for r in caplog.records if "fase 1 do PDF" in r.getMessage()]
    assert len(linhas) == 2 and "2 processos" in linhas[0] and "em série" in linhas[1]
    for linha in linhas:
        assert "60 páginas" in linha and all(e in linha for e in ("inspecionar", "coletar", "renderizar", "recortar"))


def test_varios_documentos_ao_mesmo_tempo_saem_iguais(relatorio, em_processos):
    resultados = []

    def um():
        resultados.append(_despejo(lince.Leitor().preparar(relatorio)))

    threads = [threading.Thread(target=um) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(resultados) == 3 and resultados[0] == resultados[1] == resultados[2]


def test_servidor_le_nos_processos_e_registra_a_sessao_no_log(relatorio, em_processos, caplog):
    from starlette.testclient import TestClient
    from test_servidor import ESPERA, A, enviar

    import lince.servidor as srv

    with caplog.at_level(logging.INFO, logger="lince"), TestClient(srv.criar_app(["token-a"])) as api:
        r = enviar(api, ("relatorio.pdf", relatorio.read_bytes()), {**A, **ESPERA}, modo="tarefas")
        assert r.status_code == 201, r.json()
        item = r.json()["itens"][0]
        assert item["estado"] == "pronto" and item["resultado"]["tarefas"]
    (linha,) = [r.getMessage() for r in caplog.records if "fase 1 do PDF" in r.getMessage()]
    assert f"sessão {r.json()['id']}, documento {item['id']}" in linha and "2 processos" in linha
