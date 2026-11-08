"""A PDFium não é segura entre threads: todo uso dela no processo passa por esta trava.

Renderização e extração ficam em série dentro do processo; as chamadas à IA continuam paralelas.
Para paralelismo de CPU, use vários processos.
"""

import contextlib
import threading

PDFIUM = threading.RLock()


@contextlib.contextmanager
def sem_pdfium():
    """Solta a trava (todas as voltas do RLock desta thread) durante um trabalho longo que não usa a PDFium, como o
    desenho de SVG em outro processo, e a pega de volta no fim. Fora da trava, não faz nada."""
    voltas = 0
    while True:
        try:
            PDFIUM.release()
        except RuntimeError:  # a thread não segura (mais) a trava
            break
        voltas += 1
    try:
        yield
    finally:
        for _ in range(voltas):
            PDFIUM.acquire()
