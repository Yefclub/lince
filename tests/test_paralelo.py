"""Fase 1 do PDF em processos (issue #59): o plano sai igual ao da leitura em série (texto, tarefas, recortes e as
imagens repetidas entre páginas), documentos diferentes dividem os processos de forma justa, um processo que cai não
derruba o documento, e sem processos a leitura segue em série."""

import concurrent.futures as cf
import hashlib
import io
import json
import logging
import multiprocessing
import os
import threading
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
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


class _SemProcessos(type(multiprocessing.get_context("spawn"))):
    """Contêiner no teto de processos: o processo do pool não sobe (ele só sobe no primeiro trabalho)."""

    class Process(multiprocessing.get_context("spawn").Process):
        def start(self):
            raise OSError(11, "Resource temporarily unavailable")


def test_sem_processos_a_leitura_segue_em_serie(relatorio, monkeypatch):
    def executor_sem_processos(self):
        return ProcessPoolExecutor(self.processos, mp_context=_SemProcessos())

    monkeypatch.setattr(paralelo.Distribuidor, "_novo_executor", executor_sem_processos)
    assert paralelo.ativar(4) is False and paralelo.pool() is None
    assert paralelo.ativar(1) is False  # uma CPU: nada a paralelizar
    serie = _despejo(lince.Leitor().preparar(relatorio))
    monkeypatch.undo()
    assert paralelo.ativar(2)
    try:
        assert _despejo(lince.Leitor().preparar(relatorio)) == serie
    finally:
        paralelo.desativar()


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
    assert len(plano.estado["paginas"]) == 50 and not plano.tarefas  # a primeira faixa sempre entra, só com o texto
    pendentes = plano.estado["pendentes"]
    assert any(p.startswith("páginas 1–50: só o texto") and "prazo esgotado" in p for p in pendentes)
    assert "páginas 51–60: não lidas (prazo esgotado)" in pendentes


def test_prazo_vale_com_os_processos_ocupados_por_outro_documento(relatorio, em_processos):
    """Passado o prazo, as páginas que ainda esperam a vez entram só com o texto na hora, sem esperar a rodada."""
    carga = [em_processos.submeter("outro", time.sleep, 0.5) for _ in range(40)]
    inicio = time.monotonic()
    plano = lince.Leitor().preparar(relatorio, prazo=1.5)
    assert time.monotonic() - inicio < 8  # sem cortar a fila: ~25 s, uma página por vaga a cada 0,5 s
    assert any("prazo esgotado" in p for p in plano.estado["pendentes"])
    em_processos.cancelar("outro")
    cf.wait(carga)


def test_cota_de_cpu_do_conteiner(tmp_path):
    (tmp_path / "cpu.max").write_text("150000 100000\n")
    assert paralelo.cota_de_cpu(tmp_path) == 2  # 1,5 CPU: dois processos
    (tmp_path / "cpu.max").write_text("max 100000\n")
    assert paralelo.cota_de_cpu(tmp_path) is None
    (tmp_path / "cpu.max").unlink()
    (tmp_path / "cpu").mkdir()
    (tmp_path / "cpu" / "cpu.cfs_quota_us").write_text("400000")
    (tmp_path / "cpu" / "cpu.cfs_period_us").write_text("100000")
    assert paralelo.cota_de_cpu(tmp_path) == 4  # cgroup v1
    assert paralelo.cota_de_cpu(tmp_path / "nada") is None


def test_processos_ociosos_sao_trocados(relatorio, em_processos, monkeypatch):
    """Sem trabalho, o pool é trocado: os documentos abertos e a memória dos processos voltam ao sistema."""
    monkeypatch.setattr(paralelo, "OCIOSO_S", 0.3)
    lince.Leitor().preparar(relatorio)
    usado = em_processos._executor
    with em_processos._cv:
        em_processos._cv.notify()  # o despacho passa a esperar com o tempo novo
    limite = time.monotonic() + 10
    while em_processos._executor is usado and time.monotonic() < limite:
        time.sleep(0.1)
    assert em_processos._executor is not usado
    assert len(lince.Leitor().preparar(relatorio).estado["paginas"]) == 60  # o pool novo sobe no próximo PDF


def test_documento_da_leitura_que_terminou_e_fechado_no_processo(relatorio, em_processos):
    lince.Leitor().preparar(relatorio)
    (leitura,) = em_processos.encerradas()[-1:]
    assert len(leitura) == 16
    try:
        pdf._doc_do_processo(str(relatorio), "a", ())
        pdf._doc_do_processo(str(relatorio), "b", ("a",))  # o próximo trabalho fecha o da leitura encerrada
        assert list(pdf._DOCS) == ["b"]
    finally:
        while pdf._DOCS:
            pdf._DOCS.popitem()[1].close()


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


def test_pool_que_nao_pode_ser_recriado_nao_prende_ninguem(em_processos, monkeypatch):
    """Logo depois de um processo morrer, criar o pool novo pode falhar (sem descritores, sem memória): os trabalhos
    recebem o erro, em vez de ficar esperando para sempre."""

    def sem_descritores(self):
        raise OSError(24, "Too many open files")

    def erro(futuro):
        try:
            futuro.result(timeout=30)
        except TimeoutError:  # antes de OSError: TimeoutError é um OSError
            pytest.fail("o trabalho ficou esperando para sempre")
        except (BrokenProcessPool, OSError) as e:
            return e
        pytest.fail("o trabalho devia ter falhado")

    monkeypatch.setattr(paralelo.Distribuidor, "_novo_executor", sem_descritores)
    erro(em_processos.submeter("hostil", os._exit, 1))
    erro(em_processos.submeter("outro", time.sleep, 0))


class _SoInspecao:
    """Distribuidor que roda a inspeção da faixa aqui e devolve toda página cancelada (o prazo venceu na fila)."""

    processos = 1

    def submeter(self, dono, funcao, *args):
        futuro = cf.Future()
        if funcao is pdf._faixa_no_processo:
            futuro.set_result(funcao(*args))
        else:
            futuro.cancel()
        return futuro

    def cancelar(self, dono):
        pass

    def encerradas(self):
        return ()

    def encerrada(self, leitura):
        pass


def test_pagina_cancelada_acima_do_teto_tem_o_motivo_do_teto(relatorio, tmp_path, monkeypatch):
    monkeypatch.setattr(pdf, "PAGINAS_ANALISE_MAX", 5)
    paginas, _, _, pendentes, so_texto, _ = pdf._distribuir(
        _SoInspecao(), str(relatorio), str(tmp_path), 60, 60, time.monotonic(), defaultdict(float)
    )
    assert len(paginas) == 60
    assert so_texto == [[0, 5, "prazo esgotado"], [5, 60, "teto de 5 páginas"]]


def _docs_do_processo(espera):
    time.sleep(espera)  # os dois processos recebem um trabalho
    return os.getpid(), list(pdf._DOCS)


def test_processos_fecham_o_documento_da_leitura_encerrada(relatorio, em_processos):
    lince.Leitor().preparar(relatorio)
    primeira = em_processos.encerradas()[-1]
    lince.Leitor().preparar(relatorio)  # os trabalhos desta leitura fecham o documento da anterior
    vistos = dict(f.result(timeout=60) for f in [em_processos.submeter(k, _docs_do_processo, 0.5) for k in range(2)])
    assert len(vistos) == 2 and all(primeira not in docs for docs in vistos.values())


def test_leitura_com_erro_espera_as_paginas_ja_despachadas(relatorio, em_processos, monkeypatch):
    paginas, submeter = [], paralelo.Distribuidor.submeter

    def guardar(self, dono, funcao, *args):
        futuro = submeter(self, dono, funcao, *args)
        if funcao is pdf._pagina_no_processo:
            paginas.append(futuro)
        return futuro

    def falha(*_):
        raise RuntimeError("erro ao juntar a página")

    monkeypatch.setattr(paralelo.Distribuidor, "submeter", guardar)
    monkeypatch.setattr(pdf, "_juntar", falha)
    with pytest.raises(RuntimeError, match="juntar"):
        lince.Leitor().preparar(relatorio)
    assert paginas and all(f.done() for f in paginas)  # nenhuma ainda gravando recortes numa pasta já apagada
