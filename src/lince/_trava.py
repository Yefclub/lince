"""A PDFium não é segura entre threads: todo uso dela no processo passa por esta trava.

Só o que usa a PDFium (extração e renderização do PDF) fica em série dentro do processo; Office, HTML, texto e as
chamadas à IA seguem em paralelo.
Para paralelismo de CPU, use vários processos.
"""

import threading

PDFIUM = threading.RLock()
