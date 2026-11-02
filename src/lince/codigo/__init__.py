"""Documento como código: representação estruturada e editável (ids estáveis) e edição cirúrgica por operações.

    extrair(dados, nome)               -> dict com o código do documento
    editar(dados, nome, operacoes)     -> (arquivo novo, relatório com o aplicado, diff, avisos e o código novo)

Leitura e edição usam o mesmo extrator, então os ids que a IA lê são os que ela endereça.
"""

import os

from . import pptx
from .pacote import comparar, sha
from .pptx import OperacaoInvalida

EXTRATORES = {".pptx": pptx, ".pptm": pptx, ".ppsx": pptx}
EDITAVEIS = {".pptx": pptx, ".pptm": pptx}
OPERACOES_MAX = 500  # por pedido de edição
CRESCIMENTO_AVISO = 1.3  # texto 30% maior que o original pode transbordar a caixa (autoajuste não é recalculado)


class FormatoNaoSuportado(ValueError):
    pass


def _extensao(nome: str) -> str:
    return os.path.splitext(nome or "")[1].lower()


def extrair(dados: bytes, nome: str) -> dict:
    modulo = EXTRATORES.get(_extensao(nome))
    if modulo is None:
        raise FormatoNaoSuportado(f"código disponível para: {', '.join(sorted(EXTRATORES))}")
    return modulo.extrair(dados)


def editar(dados: bytes, nome: str, operacoes: list, base_sha256: str | None = None) -> tuple[bytes, dict]:
    modulo = EDITAVEIS.get(_extensao(nome))
    if modulo is None:
        raise FormatoNaoSuportado(f"edição disponível para: {', '.join(sorted(EDITAVEIS))}")
    if not isinstance(operacoes, list) or not operacoes:
        raise OperacaoInvalida("operacao-invalida", "operacoes precisa ser uma lista com ao menos uma operação")
    if len(operacoes) > OPERACOES_MAX:
        raise OperacaoInvalida("operacao-invalida", f"no máximo {OPERACOES_MAX} operações por pedido")
    original = sha(dados)
    if base_sha256 and base_sha256 != original:
        raise OperacaoInvalida(
            "base-divergente", "o arquivo enviado não é o que foi lido (sha256 diferente): leia de novo antes de editar"
        )
    novo, aplicadas = modulo.aplicar(dados, operacoes)
    problemas = modulo.integridade(novo)
    if problemas:  # nunca devolve pacote quebrado
        raise OperacaoInvalida(
            "validacao-falhou", "o arquivo resultante ficaria inconsistente: " + "; ".join(problemas[:5])
        )
    avisos = []
    for a in aplicadas:
        if a["op"] in ("texto", "celula_tabela"):
            razao = len(str(a.get("depois", ""))) / max(len(str(a.get("antes", ""))), 1)
            if razao > CRESCIMENTO_AVISO:
                avisos.append(f"{a['alvo']}: texto {razao:.1f}x maior, pode transbordar a caixa")
    relatorio = {
        "base_sha256": original,
        "sha256": sha(novo),
        "bytes": len(novo),
        "aplicadas": aplicadas,
        "diff": comparar(dados, novo),
        "avisos": avisos,
        "codigo": modulo.extrair(novo),
    }
    return novo, relatorio
