"""Tipos públicos do Lince."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Uso:
    """Consumo de uma leitura pela IA, para o ledger de custo da plataforma."""

    modelo: str
    tokens_entrada: int = 0
    tokens_saida: int = 0
    cache: bool = False  # True quando a resposta veio do cache (custo zero)
    tarefa: str = ""


class PrazoEsgotado(Exception):
    """A leitura pela IA não coube no prazo do documento. Provedor próprio pode levantar quando
    `lince.prazo_restante()` acabar; o Lince registra a pendência `prazo esgotado`."""


@dataclass
class RespostaIA:
    texto: str
    uso: Uso


@dataclass
class Cobertura:
    """O que foi lido de onde, e o que ficou de fora e por quê."""

    paginas: int = 0
    rotas: dict = field(default_factory=dict)  # página → nativa | regioes | pagina
    leituras_ia: int = 0
    leituras_cache: int = 0
    pendentes: list = field(default_factory=list)  # tarefas que a IA não leu (sem IA, erro, teto)


@dataclass
class Resultado:
    markdown: str  # corpo pronto para a base / para a IA
    metadados: dict[str, Any]  # versão, rodapé, marca d'água, propriedades, alertas
    cobertura: Cobertura
    uso: list[Uso] = field(default_factory=list)
    # páginas (PDF), slides, abas (nome e se é oculta), seções do Markdown com posição e início de cada página
    estrutura: dict[str, Any] = field(default_factory=dict)

    @property
    def alertas_injecao(self) -> list[str]:
        return list(self.metadados.get("alertas_injecao", []))
