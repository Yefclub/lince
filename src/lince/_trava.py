"""A PDFium não é segura entre threads: todo uso dela no processo passa por esta trava.

Só o que usa a PDFium (extração e renderização do PDF) fica em série dentro do processo; Office, HTML, texto e as
chamadas à IA seguem em paralelo. Para o PDF em paralelo, o pool de processos de paralelo.py: cada processo tem a
PDFium e esta trava dele.
"""

import threading
from collections import deque


class TravaJusta:
    """Trava reentrante que atende por ordem de chegada.

    Um PDF grande toma a trava em vezes curtas (`VEZ_S` em pdf.py) e a solta entre elas. Com `threading.Lock`, quem
    solta pode pegá-la de volta na hora, antes de quem está esperando acordar, e um PDF de uma página espera o grande
    inteiro. Aqui a trava passa direto para o primeiro da fila: quem solta e pede de novo vai para o fim dela."""

    def __init__(self):
        self._estado = threading.Lock()
        self._dono = None
        self._nivel = 0
        self._fila = deque()  # (thread, evento) de quem espera, na ordem de chegada

    def acquire(self, blocking=True, timeout=-1):
        eu = threading.get_ident()
        ja_era, nivel, vez = self._dono == eu, self._nivel, None  # só esta thread muda o nível da trava que é dela
        try:
            with self._estado:
                if ja_era:
                    self._nivel += 1
                    return True
                if self._dono is None and not self._fila:
                    self._dono, self._nivel = eu, 1
                    return True
                if not blocking:
                    return False
                vez = (eu, threading.Event())
                self._fila.append(vez)
            vez[1].wait(None if timeout is None or timeout < 0 else timeout)
            with self._estado:
                if self._dono == eu:  # recebeu a trava, mesmo que no limite do timeout
                    return True
                self._fila.remove(vez)
                return False
        except BaseException:  # interrompida (KeyboardInterrupt, alarme) em qualquer ponto: nunca deixa a trava presa
            with self._estado:
                if ja_era:  # devolve só o nível que esta chamada somou
                    self._nivel = nivel
                elif self._dono == eu:  # a vez chegou junto com a interrupção: passa para o próximo
                    self._passar()
                elif vez in self._fila:
                    self._fila.remove(vez)
            raise

    def release(self):
        with self._estado:
            if self._dono != threading.get_ident():
                raise RuntimeError("a trava da PDFium não é desta thread")
            self._nivel -= 1
            if not self._nivel:
                self._passar()

    def _passar(self):
        """Dá a trava ao primeiro da fila, ou a deixa livre. Chamar com `_estado`."""
        if self._fila:
            self._dono, vez = self._fila.popleft()
            self._nivel = 1
            vez.set()
        else:
            self._dono, self._nivel = None, 0

    # Como em `threading.Lock`, a entrada do `with` é a própria `acquire`: nenhuma interrupção cai entre pegar a trava
    # e o `with` saber que precisa soltá-la.
    #
    # Limite da saída: `__exit__`, `release` e `_passar` são Python, e não C como em `threading.Lock`. Uma exceção
    # assíncrona nas poucas instruções entre a entrada de `__exit__` e o fim de `vez.set()` (o KeyboardInterrupt, que só
    # chega à thread principal, ou uma `PyThreadState_SetAsyncExc`) pode deixar a trava com quem saía, ou passada a
    # quem espera sem acordá-lo. Em Python puro, sem consultar o estado em intervalos, não há como tornar essa passagem
    # atômica; o servidor lê os documentos em threads de trabalho, que não recebem sinais.
    __enter__ = acquire

    def __exit__(self, *_):
        self.release()


PDFIUM = TravaJusta()
