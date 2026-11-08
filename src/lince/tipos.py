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
    custo_usd: float | None = None  # o que o provedor informa em usage.cost (OpenRouter); None quando não informa


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


@dataclass
class TarefaVisual:
    """Uma leitura que a IA precisa fazer, para quem chama o modelo por conta própria (`Leitor.preparar`).

    `chave` é o hash da imagem, da instrução e da versão do prompt: junte o seu modelo para o cache. `tokens_estimados`
    é a conta aproximada da imagem (largura × altura / 750) mais a instrução (caracteres / 4). Num `lote`, a folha
    traz recortes pequenos numerados e a resposta esperada é o JSON pedido na instrução; `itens` diz o que é cada um."""

    id: str
    tipo: str  # imagem | pagina | figura | tabela | formula | lote
    imagem: bytes  # PNG
    instrucao: str
    chave: str
    tokens_estimados: int
    pagina: int | None = None  # PDF, a partir de 1
    caixa: list[float] | None = None  # PDF: x0, y0, x1, y1 em pontos, origem embaixo à esquerda
    onde: str = ""  # Office e HTML: "imagem 3", "aba Vendas, célula B4"...
    itens: list[dict] = field(default_factory=list)  # lote: {"n", "tarefa", "tipo", "onde"} de cada recorte


@dataclass
class Plano:
    """A primeira fase da leitura: o texto já extraído e as leituras que faltam. `Leitor.concluir(plano, respostas)`
    monta o resultado. `estado` é interno e serializável (JSON); `para_json`/`de_json` levam o plano entre processos."""

    tarefas: list[TarefaVisual]
    estado: dict[str, Any]
    pendentes: list[str] = field(default_factory=list)

    def para_json(self) -> dict:
        import base64
        from dataclasses import asdict

        tarefas = [{**asdict(t), "imagem": base64.b64encode(t.imagem).decode()} for t in self.tarefas]
        return {"tarefas": tarefas, "estado": self.estado, "pendentes": self.pendentes}

    @classmethod
    def de_json(cls, dados: dict) -> "Plano":
        import base64

        tarefas = [TarefaVisual(**{**t, "imagem": base64.b64decode(t["imagem"])}) for t in dados["tarefas"]]
        return cls(tarefas, dados["estado"], list(dados.get("pendentes", [])))
