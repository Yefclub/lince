"""Provedores de leitura visual: a plataforma pluga o modelo que já usa.

Qualquer objeto com `modelo: str` e `ler(imagem: bytes, instrucao: str) -> RespostaIA` serve.
"""

import base64
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Protocol

from ._prazo import prazo_restante
from .tipos import PrazoEsgotado, RespostaIA, Uso


class LeitorVisual(Protocol):
    modelo: str

    def ler(self, imagem: bytes, instrucao: str, extras: list[bytes] | None = None) -> RespostaIA: ...


class _SemRedirecionar(urllib.request.HTTPRedirectHandler):
    """Redirect do provedor não é seguido: levaria a chave para outro host e abriria caminho para a rede interna."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_ABRIDOR = urllib.request.build_opener(_SemRedirecionar)
ESPERA_MAX_PROVEDOR = 60  # Retry-After acima disso não prende a thread: o provedor é escolhido por quem chama


def _retry_after(valor: str | None) -> float:
    """Segundos do Retry-After (data HTTP ou inválido viram 0 = espera exponencial), com teto."""
    try:
        return min(ESPERA_MAX_PROVEDOR, max(0.0, float(valor or 0)))
    except ValueError:
        return 0.0


class OpenAICompativel:
    """Qualquer endpoint /v1/chat/completions (OpenAI, OpenRouter, vLLM, Ollama, LM Studio...).

    Repete 429 e 5xx com espera exponencial e respeita Retry-After: o limite do provedor
    não pode virar documento perdido.
    """

    def __init__(
        self,
        url: str,
        modelo: str,
        chave: str | None = None,
        timeout: float = 120,
        tentativas: int = 6,
        cabecalhos: dict | None = None,
    ):
        partes = urllib.parse.urlsplit(url)
        if partes.scheme not in ("http", "https") or not partes.netloc or partes.query or partes.fragment:
            raise ValueError("URL do provedor deve ser http(s)://host/caminho, sem ? nem #")
        self.url, self.modelo, self.chave = url.rstrip("/"), modelo, chave
        self.timeout, self.tentativas, self.cabecalhos = timeout, tentativas, cabecalhos or {}
        if not self.url.endswith("/chat/completions"):
            self.url += "/chat/completions"
        # o cache separa o mesmo nome de modelo servido por provedores diferentes
        self.identidade = f"{self.modelo}@{self.url}"

    def ler(self, imagem: bytes, instrucao: str, extras: list[bytes] | None = None) -> RespostaIA:
        imagens = [imagem] + list(extras or [])
        corpo = json.dumps(
            {
                "model": self.modelo,
                "temperature": 0,
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": instrucao}]
                        + [
                            {
                                "type": "image_url",
                                "image_url": {"url": "data:image/png;base64," + base64.b64encode(im).decode()},
                            }
                            for im in imagens
                        ],
                    }
                ],
            }
        ).encode()
        cab = {"Content-Type": "application/json", **self.cabecalhos}
        if self.chave:
            cab["Authorization"] = f"Bearer {self.chave}"
        for k in range(self.tentativas):
            restante = prazo_restante()
            if restante is not None and restante <= 0:
                raise PrazoEsgotado
            # com prazo, a chamada em curso é cortada quando ele vence: nada fica rodando depois da entrega
            timeout = self.timeout if restante is None else min(self.timeout, restante)
            try:
                with _ABRIDOR.open(urllib.request.Request(self.url, corpo, cab), timeout=timeout) as r:
                    d = json.loads(_ler_corpo(r))
                u = d.get("usage") or {}
                return RespostaIA(
                    d["choices"][0]["message"]["content"] or "",
                    Uso(d.get("model", self.modelo), u.get("prompt_tokens", 0), u.get("completion_tokens", 0)),
                )
            except urllib.error.HTTPError as e:
                if e.code != 429 and e.code < 500 or k == self.tentativas - 1:
                    raise
                _esperar(_retry_after(e.headers.get("Retry-After")) or min(30, 0.5 * 2**k), e)
            except (urllib.error.URLError, TimeoutError) as e:
                if (restante := prazo_restante()) is not None and restante <= 0:
                    raise PrazoEsgotado from e  # o timeout foi o do prazo, não falha do provedor
                if k == self.tentativas - 1:
                    raise
                _esperar(min(30, 0.5 * 2**k), e, sorteio=False)
        raise RuntimeError("inalcançável")


def _ler_corpo(r) -> bytes:
    """Corpo da resposta em blocos, conferindo o prazo a cada um: provedor que manda a resposta aos poucos não segura
    a leitura além do prazo (o timeout do socket vale por operação, não para o corpo inteiro)."""
    partes = []
    while bloco := r.read1(64 * 1024):
        partes.append(bloco)
        if (restante := prazo_restante()) is not None and restante <= 0:
            raise PrazoEsgotado
    return b"".join(partes)


def _esperar(segundos, erro, sorteio=True):
    """Espera antes de repetir, a menos que a espera passe do prazo: aí a nova tentativa não acontece."""
    if sorteio:  # só no 429/5xx, como antes: espalha as novas tentativas de quem levou limite ao mesmo tempo
        segundos += random.random() * 0.3
    if (restante := prazo_restante()) is not None and restante <= segundos:
        raise PrazoEsgotado from erro
    time.sleep(segundos)


class FuncaoLeitor:
    """Adapta uma função qualquer (SDK próprio, fila, outro serviço) ao Lince."""

    def __init__(self, funcao: Callable[[bytes, str], str], modelo: str = "funcao"):
        self.funcao, self.modelo = funcao, modelo

    def ler(self, imagem: bytes, instrucao: str, extras: list[bytes] | None = None) -> RespostaIA:
        return RespostaIA(self.funcao(imagem, instrucao), Uso(self.modelo))
