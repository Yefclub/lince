"""Processos para a PDFium: a fase 1 do PDF (extração, análise, renderização e recortes) em paralelo de verdade.

A PDFium não é segura entre threads, e o pdf_inspector segura a GIL: dentro de um processo, só um PDF anda por vez.
Com `ativar()`, as páginas de um mesmo documento e os documentos diferentes vão para um pool de processos. Cada
processo abre o próprio documento; o processo principal só distribui o trabalho e junta os resultados na ordem das
páginas, então o plano sai igual ao da leitura em série.

O servidor ativa o pool ao subir. Na biblioteca, quem quer o paralelo chama `lince.paralelo.ativar()` no início do
programa, dentro do `if __name__ == "__main__":` (os processos são iniciados por `spawn`, que importa o módulo
principal). Sem o pool, ou se ele não pôde ser criado, a leitura segue em série, como antes.
"""

import concurrent.futures as cf
import multiprocessing
import os
import threading
from collections import OrderedDict, deque
from concurrent.futures.process import BrokenProcessPool

from .limites import PROCESSOS_PDF_MAX

# Trabalhos por processo antes de ele ser trocado por um novo: a memória que a PDFium e o Pillow fragmentam volta ao
# sistema, e os documentos que o processo deixou abertos são fechados. Uma página pesada custa ~0,1 s; 200 trabalhos
# são alguns documentos grandes, e iniciar um processo custa ~1 s.
TRABALHOS_POR_PROCESSO = 200


class Distribuidor:
    """Pool de processos com fila justa: até `processos` trabalhos em curso, um documento de cada vez na rodada
    (`dono`), como a `Fila` do servidor. Um PDF de 2.000 páginas não segura os outros: cada documento que chega entra
    na rodada seguinte, com a mesma vez dele.

    Uma thread só despacha, e nada chama o pool com a trava daqui tomada: quando um processo morre, o pool avisa os
    trabalhos dele com a trava de encerramento dele tomada, e o aviso pega a trava daqui; o despacho, com a trava daqui
    tomada, pediria a dele."""

    def __init__(self, processos):
        self.processos = processos
        self._executor = self._novo_executor()
        self._cv = threading.Condition()
        self._fila: OrderedDict[object, deque] = OrderedDict()
        self._em_curso = 0
        self._encerrado = False
        threading.Thread(target=self._despachar, name="lince-pdf-despacho", daemon=True).start()

    def _novo_executor(self):
        return cf.ProcessPoolExecutor(
            self.processos,
            mp_context=multiprocessing.get_context("spawn"),  # sem herdar threads e travas do servidor
            max_tasks_per_child=TRABALHOS_POR_PROCESSO,
        )

    def submeter(self, dono, funcao, *args) -> cf.Future:
        futuro = cf.Future()
        with self._cv:
            if self._encerrado:
                raise RuntimeError("o pool de processos foi encerrado")
            self._fila.setdefault(dono, deque()).append((funcao, args, futuro))
            self._cv.notify()
        return futuro

    def cancelar(self, dono):
        """Tira da fila o que o documento ainda não começou (ele terminou, desistiu ou falhou)."""
        with self._cv:
            trabalhos = self._fila.pop(dono, ())
        for _funcao, _args, futuro in trabalhos:
            futuro.cancel()

    def encerrar(self):
        with self._cv:
            self._encerrado = True
            donos = list(self._fila)
            self._cv.notify()
        for dono in donos:
            self.cancelar(dono)
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _despachar(self):
        while True:
            with self._cv:
                while not self._encerrado and not (self._fila and self._em_curso < self.processos):
                    self._cv.wait()
                if self._encerrado:
                    return
                dono, fila = self._fila.popitem(last=False)
                funcao, args, futuro = fila.popleft()
                if fila:
                    self._fila[dono] = fila  # volta para o fim da rodada
                if not futuro.set_running_or_notify_cancel():
                    continue
                self._em_curso += 1
                executor = self._executor
            try:
                interno = executor.submit(funcao, *args)
            except BrokenProcessPool:  # quebrou entre um despacho e outro: o trabalho vai para o novo
                executor = self._renovar(executor)
                try:
                    interno = executor.submit(funcao, *args)
                except Exception as e:  # noqa: BLE001 — o erro volta para quem esperava
                    self._falhou(futuro, e)
                    continue
            except Exception as e:  # noqa: BLE001 — pool encerrado: o erro volta para quem esperava
                self._falhou(futuro, e)
                continue
            interno.add_done_callback(lambda f, futuro=futuro, executor=executor: self._terminou(f, futuro, executor))

    def _renovar(self, quebrado):
        """O pool no lugar do que quebrou (um processo morreu: falta de memória, PDF hostil que derruba a PDFium). Os
        trabalhos que estavam nele recebem `BrokenProcessPool`; os da fila seguem no novo. O quebrado já encerra os
        processos dele."""
        with self._cv:
            if self._executor is quebrado:
                self._executor = self._novo_executor()
            return self._executor

    def _falhou(self, futuro, erro):
        with self._cv:
            self._em_curso -= 1
            self._cv.notify()
        futuro.set_exception(erro)

    def _terminou(self, interno, futuro, executor):
        erro = cf.CancelledError() if interno.cancelled() else interno.exception()
        if isinstance(erro, BrokenProcessPool):
            self._renovar(executor)
        with self._cv:
            self._em_curso -= 1
            self._cv.notify()
        futuro.set_exception(erro) if erro else futuro.set_result(interno.result())


_POOL: Distribuidor | None = None
_TRAVA = threading.Lock()


def processos_disponiveis() -> int:
    """As CPUs que este processo pode usar (no contêiner, as do cpuset), com o teto de `PROCESSOS_PDF_MAX`."""
    try:
        cpus = len(os.sched_getaffinity(0))
    except AttributeError:  # macOS e Windows
        cpus = os.cpu_count() or 1
    return min(cpus, PROCESSOS_PDF_MAX)


def ativar(processos: int | None = None) -> bool:
    """Liga o pool com `processos` (padrão: as CPUs disponíveis, até `PROCESSOS_PDF_MAX`). Com menos de 2, não há o
    que paralelizar e a leitura segue em série. Devolve se o pool ficou ativo; se ele não pôde ser criado (ambiente
    sem processos, por exemplo), a leitura também segue em série."""
    global _POOL
    n = processos or processos_disponiveis()
    with _TRAVA:
        if _POOL is not None:
            return True
        if n < 2:
            return False
        try:
            _POOL = Distribuidor(n)
        except Exception:  # noqa: BLE001 — sem processos: a leitura segue em série
            _POOL = None
            return False
        return True


def desativar():
    global _POOL
    with _TRAVA:
        pool, _POOL = _POOL, None
    if pool is not None:
        pool.encerrar()


def pool() -> Distribuidor | None:
    return _POOL
