"""A PDFium não é segura entre threads: todo uso dela no processo passa por esta trava.

Renderização e extração ficam em série dentro do processo; as chamadas à IA continuam paralelas.
Para paralelismo de CPU, use vários processos.
"""

import threading

PDFIUM = threading.RLock()
