"""Cache de leituras: a mesma imagem com a mesma instrução e o mesmo modelo não é paga duas vezes.

A plataforma pode trocar por Redis/Postgres implementando `obter` e `guardar`.
"""

import hashlib
import json
import os
import tempfile
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
