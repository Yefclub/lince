"""Cache de leituras: a mesma imagem com a mesma instrução e o mesmo modelo não é paga duas vezes.

Guarda só o texto que a IA leu de cada recorte ou página, pela chave (hash); nunca o documento. A plataforma usa o
Redis que já tem (`CacheRedis(cliente)`) ou qualquer objeto com `obter` e `guardar`.
"""

import hashlib
import json
import math
import os
import tempfile
import time
from pathlib import Path
from typing import Protocol


def chave(imagem: bytes, instrucao: str, modelo: str, versao: str) -> str:
    h = hashlib.sha256()
    for parte in (imagem, instrucao.encode(), modelo.encode(), versao.encode()):
        h.update(hashlib.sha256(parte).digest())
    return h.hexdigest()


class Cache(Protocol):
    def obter(self, k: str) -> str | None: ...

    def guardar(self, k: str, texto: str) -> None: ...


DIAS_MAX = 3650  # dez anos: acima disso o EX do Redis estoura e o cache ficaria desligado em silêncio


class CacheRedis:
    """Cache no Redis da plataforma (passe o cliente que ela já usa) ou num próprio (passe a URL). Cada leitura vence
    em `dias`; a chave leva o prefixo `lince:leitura:` para não colidir com o resto do Redis."""

    PREFIXO = "lince:leitura:"
    PAUSA = 30  # segundos sem tentar o Redis depois de uma falha: Redis lento não soma 2 s em cada leitura

    def __init__(self, redis, dias: float = 30):
        """`redis`: URL (o Lince cria o cliente com timeout de 2 s) ou o cliente síncrono da plataforma, que precisa
        ter `socket_timeout`: sem ele, um Redis travado prende a leitura."""
        if not (math.isfinite(dias) and 1 <= dias * 86400 <= DIAS_MAX * 86400):
            raise ValueError(f"dias precisa estar entre um segundo e {DIAS_MAX} dias")
        if isinstance(redis, str):
            import redis as redis_py

            redis = redis_py.Redis.from_url(redis, socket_timeout=2, socket_connect_timeout=2)
        self.redis, self.segundos, self._fora_ate = redis, int(dias * 86400), 0.0

    def _chamar(self, funcao, *args, **kw):
        if time.monotonic() < self._fora_ate:
            return None  # em pausa: segue sem cache, sem esperar o timeout de novo
        try:
            return funcao(*args, **kw)
        except Exception:
            self._fora_ate = time.monotonic() + self.PAUSA
            raise

    def obter(self, k):
        v = self._chamar(self.redis.get, self.PREFIXO + k)
        if isinstance(v, bytes):
            return v.decode("utf-8")
        return v if isinstance(v, str) else None  # tipo inesperado (cliente assíncrono, por exemplo) não é acerto

    def guardar(self, k, texto):
        self._chamar(self.redis.set, self.PREFIXO + k, texto.encode("utf-8"), ex=self.segundos)


class CacheComEscopo:
    """O mesmo cache separado por dono (o token, na API): um cliente não descobre o que outro leu nem grava resposta
    que outro vai receber, mas continua aproveitando as próprias leituras entre envios e versões."""

    def __init__(self, cache, escopo: str):
        self.cache, self._escopo = cache, hashlib.sha256(escopo.encode()).hexdigest()[:32]

    def _k(self, k):
        return hashlib.sha256(f"{self._escopo}:{k}".encode()).hexdigest()

    def obter(self, k):
        return self.cache.obter(self._k(k))

    def guardar(self, k, texto):
        self.cache.guardar(self._k(k), texto)


class CacheMemoria:
    def __init__(self):
        self._d: dict[str, str] = {}

    def obter(self, k):
        return self._d.get(k)

    def guardar(self, k, texto):
        self._d[k] = texto


class CacheDisco:
    def __init__(self, pasta: str | Path):
        self.pasta = Path(pasta)
        self.pasta.mkdir(parents=True, exist_ok=True)

    def obter(self, k):
        p = self.pasta / f"{k}.json"
        return json.loads(p.read_text(encoding="utf-8"))["texto"] if p.exists() else None

    def guardar(self, k, texto):
        # grava ao lado e troca de uma vez: leitura concorrente nunca vê arquivo pela metade
        fd, tmp = tempfile.mkstemp(dir=self.pasta, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"texto": texto}, f, ensure_ascii=False)
        os.replace(tmp, self.pasta / f"{k}.json")
