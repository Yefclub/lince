"""Memória do processo do servidor: o que o alocador do glibc guarda depois de liberado volta ao sistema.

Medido com 8 leituras de PDF com imagens grandes em paralelo: o processo saía de ~63 MB e ficava em ~160 MB depois
delas, sem nada vivo no Python. O glibc sobe sozinho o limite de mmap depois do primeiro buffer grande liberado e,
junto, o limite de devolução do heap; os buffers seguintes ficam nas arenas das threads e as fragmentam, e
`malloc_trim` sozinho devolvia só parte (~130 MB). Com o limite fixo em LIMIAR_MMAP o ajuste dinâmico fica desligado,
e o `malloc_trim` periódico devolve quase tudo (~67 MB), sem mudar o tempo dessa carga (30 s nos dois casos, em três
rodadas alternadas; com 1 a 4 MiB custava ~7%, pelos buffers de imagem que passavam a ser mapeados um a um).

Em outra libc (musl no Alpine, macOS, Windows) as duas chamadas não existem e nada muda.
"""

import ctypes
import os

_M_MMAP_THRESHOLD = -3  # mallopt(3): opção do limite de mmap
# 16 MiB, fixo (o ajuste dinâmico do glibc vai até 32 MiB em 64 bits). Os buffers de imagem (alguns MiB) ficam no
# heap, sem o custo de mapear um a um, e o heap volta a ser devolvido; só o buffer enorme vem de mmap.
LIMIAR_MMAP = 16 * 1024 * 1024


def _libc():
    try:
        libc = ctypes.CDLL(None)
        trim, mallopt = libc.malloc_trim, libc.mallopt
    except (OSError, AttributeError, TypeError):  # outra libc, ou Windows (CDLL sem nome)
        return None
    trim.argtypes, trim.restype = [ctypes.c_size_t], ctypes.c_int
    mallopt.argtypes, mallopt.restype = [ctypes.c_int, ctypes.c_int], ctypes.c_int
    return trim, mallopt


_GLIBC = _libc()


def ajustar_alocador() -> bool:
    """Fixa o limite de mmap em LIMIAR_MMAP. Muda o alocador do processo inteiro (o mallopt não é seguro entre threads):
    chamar uma vez, no início do serviço, antes de criar as threads; o `main` do servidor chama."""
    return bool(_GLIBC and _GLIBC[1](_M_MMAP_THRESHOLD, LIMIAR_MMAP))


def devolver() -> bool:
    """Devolve ao sistema a memória livre que o alocador guarda (`malloc_trim(0)`). Pode levar alguns milissegundos:
    chamar fora de trava e fora do laço de eventos."""
    if _GLIBC is None:
        return False
    _GLIBC[0](0)
    return True


def rss() -> int | None:
    """Memória residente do processo, em bytes; None onde não há /proc."""
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError, AttributeError):
        return None
