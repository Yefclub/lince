"""Antivírus opcional: todo arquivo recebido passa pelo ClamAV (clamd, protocolo INSTREAM) antes de ser lido.

Liga com LINCE_CLAMAV=host:porta. Sem a variável, nada muda. Ligado, falha fechado: se o clamd não responde, o envio
é recusado (503), nunca lido sem varredura. O ClamAV roda como serviço à parte; o Lince só fala o protocolo pela rede
(não há biblioteca nem código do ClamAV no Lince).
"""

import socket
import struct
import threading

BLOCO = 64 * 1024
TIMEOUT = 120  # segundos por arquivo; 100 MB num clamd comum leva poucos segundos
SIMULTANEAS = 8  # varreduras ao mesmo tempo; abaixo do MaxThreads padrão do clamd (10), que enfileira o excedente
_VAGAS = threading.BoundedSemaphore(SIMULTANEAS)


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


def varrer(alvo: tuple[str, int], caminho: str) -> None:
    """Envia o arquivo ao clamd. Limpo: retorna. Vírus: Infectado. Sem resposta válida: Indisponivel."""
    if not _VAGAS.acquire(timeout=TIMEOUT):
        raise Indisponivel("fila do antivírus cheia")
    try:
        return _varrer(alvo, caminho)
    finally:
        _VAGAS.release()


def _varrer(alvo, caminho):
    try:
        with socket.create_connection(alvo, timeout=TIMEOUT) as s, open(caminho, "rb") as f:
            s.sendall(b"zINSTREAM\0")
            while bloco := f.read(BLOCO):
                s.sendall(struct.pack("!L", len(bloco)) + bloco)
            s.sendall(struct.pack("!L", 0))
            resposta = b""
            while not resposta.endswith(b"\0"):
                parte = s.recv(4096)
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
