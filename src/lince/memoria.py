"""Memória do processo do servidor: o que o alocador do glibc guarda depois de liberado volta ao sistema.

Medido com 8 leituras de PDF com imagens grandes em paralelo: o processo saía de ~63 MB e ficava em ~170 MB depois
delas, sem nada vivo no Python. Com as imagens decodificadas em várias threads, o limite dinâmico de mmap do glibc
sobe depois do primeiro buffer grande liberado, os buffers seguintes vêm das arenas das threads e as fragmentam;
`malloc_trim` sozinho devolvia só parte (~140 MB). Com o limite fixo em LIMIAR_MMAP, o buffer grande vem de mmap e
volta inteiro ao ser liberado (~70 MB), a ~7% de tempo nessa carga; o `malloc_trim` periódico devolve o resto.

Em outra libc (musl no Alpine, macOS, Windows) as duas chamadas não existem e nada muda.
"""

import ctypes
import os

_M_MMAP_THRESHOLD = -3  # mallopt(3): opção do limite de mmap
# 1 MiB: acima dele a alocação vem de mmap e volta ao sistema ao ser liberada. Uma página renderizada ou uma imagem
# decodificada passa disso; os objetos pequenos do Python ficam no heap, como antes.
LIMIAR_MMAP = 1024 * 1024


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
    """Fixa o limite de mmap em LIMIAR_MMAP. Chamar uma vez, no início do serviço."""
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
