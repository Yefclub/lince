"""Prazo do documento em leitura, visível para o provedor de IA na thread de cada leitura."""

import contextvars
import time

LIMITE: contextvars.ContextVar[float | None] = contextvars.ContextVar("lince_limite", default=None)


def prazo_restante() -> float | None:
    """Segundos até o prazo do documento em leitura, ou None sem prazo. Provedor próprio usa isso como timeout."""
    limite = LIMITE.get()
    return None if limite is None else limite - time.monotonic()
