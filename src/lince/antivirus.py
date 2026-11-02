"""Antivírus opcional: todo arquivo recebido passa pelo ClamAV (clamd, protocolo INSTREAM) antes de ser lido.

Liga com LINCE_CLAMAV=host:porta. Sem a variável, nada muda. Ligado, falha fechado: se o clamd não responde, o envio
é recusado (503), nunca lido sem varredura. O ClamAV roda como serviço à parte; o Lince só fala o protocolo pela rede
(não há biblioteca nem código do ClamAV no Lince).
"""

import asyncio
import socket
import struct
import time
from concurrent.futures import ThreadPoolExecutor

BLOCO = 64 * 1024
TIMEOUT = 120  # segundos por arquivo; 100 MB num clamd comum leva poucos segundos
SIMULTANEAS = 8  # varreduras ao mesmo tempo; abaixo do MaxThreads padrão do clamd (10), que enfileira o excedente
ESPERA_MAX = 2 * TIMEOUT  # fila mais a varredura; passou disso, o envio é recusado em vez de esperar sem fim
# executor próprio: quem espera vaga do antivírus não ocupa as threads do executor padrão (cópia dos uploads)
_EXECUTOR = ThreadPoolExecutor(SIMULTANEAS, thread_name_prefix="clamav")


class Infectado(Exception):
    def __init__(self, assinatura: str):
        super().__init__(assinatura)
        self.assinatura = assinatura


class Indisponivel(Exception):
    pass


def endereco(valor: str | None) -> tuple[str, int] | None:
    """`host:porta` (ou `tcp://host:porta`) da variável LINCE_CLAMAV; None desliga."""
    if not valor:
        return None
    valor = valor.removeprefix("tcp://").strip()
    host, _, porta = valor.rpartition(":")
    host = host[1:-1] if host.startswith("[") and host.endswith("]") else host  # IPv6: [::1]:3310
    if not host or not porta.isdigit():
        raise ValueError("LINCE_CLAMAV deve ser host:porta, por exemplo clamav:3310")
    return host, int(porta)


async def varrer(alvo: tuple[str, int], caminho: str) -> None:
    """Envia o arquivo ao clamd. Limpo: retorna. Vírus: Infectado. Sem resposta válida: Indisponivel."""
    tarefa = asyncio.get_running_loop().run_in_executor(_EXECUTOR, _varrer, alvo, caminho)
    try:
        await asyncio.wait_for(tarefa, ESPERA_MAX)
    except TimeoutError:
        raise Indisponivel("fila do antivírus cheia") from None


def _varrer(alvo, caminho):
    prazo = time.monotonic() + TIMEOUT  # total, não por operação: clamd lento não segura uma thread do pool sem fim

    def resta(s):
        restante = prazo - time.monotonic()
        if restante <= 0:
            raise TimeoutError
        s.settimeout(restante)
        return s

    try:
        with socket.create_connection(alvo, timeout=TIMEOUT) as s, open(caminho, "rb") as f:
            resta(s).sendall(b"zINSTREAM\0")
            while bloco := f.read(BLOCO):
                resta(s).sendall(struct.pack("!L", len(bloco)) + bloco)
            resta(s).sendall(struct.pack("!L", 0))
            resposta = b""
            while not resposta.endswith(b"\0"):
                parte = resta(s).recv(4096)
                if not parte:
                    break
                resposta += parte
    except OSError as e:
        raise Indisponivel(f"clamd não respondeu: {type(e).__name__}") from None
    texto = resposta.rstrip(b"\0").decode("utf-8", "replace").strip()
    if texto == "stream: OK":
        return
    if texto.startswith("stream:") and texto.endswith(" FOUND"):
        raise Infectado(texto.removeprefix("stream:").removesuffix("FOUND").strip())
    # "INSTREAM size limit exceeded", erro de leitura etc.: sem veredito não há leitura
    raise Indisponivel(f"clamd sem veredito: {texto[:120]}")
