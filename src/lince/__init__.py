"""Lince — leitor único de documentos para bases de conhecimento e IA.

Extração nativa quando o texto é bom; o modelo de visão da plataforma só onde precisa
(imagem, gráfico, diagrama, tabela duvidosa, página escaneada), com cache, custo por leitura,
metadados corporativos e proteção contra texto oculto e prompt injection.

    import lince
    r = lince.ler("relatorio.pdf", ia=lince.OpenAICompativel(url, modelo, chave))
    r.markdown, r.metadados, r.cobertura, r.alertas_injecao
"""

from ._prazo import prazo_restante
from .cache import CacheDisco, CacheMemoria, CacheRedis
from .leitor import VERSAO as __version__
from .leitor import FormatoNaoSuportado, Leitor, ler
from .provedores import FuncaoLeitor, LeitorVisual, OpenAICompativel
from .tipos import Cobertura, PrazoEsgotado, RespostaIA, Resultado, Uso

__all__ = [
    "Leitor",
    "FormatoNaoSuportado",
    "ler",
    "OpenAICompativel",
    "FuncaoLeitor",
    "LeitorVisual",
    "CacheMemoria",
    "CacheDisco",
    "CacheRedis",
    "Resultado",
    "Cobertura",
    "Uso",
    "RespostaIA",
    "PrazoEsgotado",
    "prazo_restante",
    "__version__",
]
