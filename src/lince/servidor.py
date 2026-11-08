"""API HTTP do Lince. O contrato é `openapi.json` (servido em /v1/openapi.json); este módulo o implementa.

Cada envio (um documento ou um lote) cria uma sessão, marcada pela `referencia` de quem chama, para o sistema de
origem saber de quem é cada resultado. O documento não é guardado: o arquivo é apagado assim que lido, e o resultado
fica em memória só até ser entregue (a primeira resposta com a sessão concluída, mais REENTREGA para repetir se a
resposta se perder) ou até ENTREGA_PRAZO. Só com LINCE_REDIS, a transcrição que a IA fez de cada recorte ou página
fica no Redis por LINCE_CACHE_DIAS, separada por token. O modelo de visão vem
em cada envio (X-IA-URL, X-IA-Modelo, X-IA-Chave): o servidor não guarda chave de provedor. Fila justa entre tokens.
"""

import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import antivirus
from . import codigo as codigo_doc
from .cache import CacheComEscopo, CacheRedis
from .leitor import FORMATOS, VERSAO, Leitor
from .limites import DocumentoRecusado
from .provedores import OpenAICompativel

CONTRATO = json.loads(files("lince").joinpath("openapi.json").read_text(encoding="utf-8"))

TAMANHO_MAX = 100 * 1024 * 1024  # por envio (soma do lote): acima disso, dividir em mais sessões
ARQUIVOS_MAX = 50  # por sessão
DOCS_SIMULTANEOS = 16  # medido na carga: o tempo é espera da IA, não CPU
ESPERA_MAX = 300  # teto do Prefer: wait, abaixo do timeout comum de proxy reverso
ENTREGA_PRAZO = 15 * 60  # resultado não buscado em 15 min depois de concluído é descartado
# Depois da entrega, a sessão fica 60 s para o cliente repetir o pedido se a resposta se perdeu (rede, timeout);
# DELETE confirma o recebimento e apaga na hora.
REENTREGA = 60
PENDENTES_POR_CLIENTE = 64  # documentos na fila ou lendo por token; acima disso 429
# Resultado esperando entrega por token: acima disso, novos envios recebem 429 até o token buscar o que é dele.
# Protege a memória da instância sem deixar um token derrubar os outros.
BYTES_RETIDOS_POR_CLIENTE = 256 * 1024 * 1024
LIMPEZA_INTERVALO = 60
CABECALHOS_MAX = {"x-ia-url": 2048, "x-ia-modelo": 200, "x-ia-chave": 4096}
ERROS_HTTP = {400: "requisicao-invalida", 404: "rota-inexistente", 405: "metodo-nao-permitido"}
LISTA_MAX = 200
ESFORCOS = {"padrao", "max"}
MODOS = {"leitura", "tarefas"}  # tarefas: o Lince não chama a IA; devolve o que ela precisa ler e monta depois
PLANO_VALIDADE = 24 * 3600  # plano devolvido no modo tarefas vale um dia para a montagem
PLANO_MAX = 64 * 1024 * 1024  # plano descompactado (texto e estado do documento): acima disso é hostil
PRAZO_MIN, PRAZO_MAX = 1, 3600  # segundos por documento; o chat usa poucos, a base não manda prazo
MIDIA = {
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".pptm": "application/vnd.ms-powerpoint.presentation.macroEnabled.12",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
REFERENCIA = re.compile(r"[A-Za-z0-9._:@/-]{1,200}")
REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
log = logging.getLogger("lince")


class Fila:
    """Fila justa: atende um documento de cada cliente por vez (round-robin)."""

    def __init__(self, executar, simultaneos):
        self._por_cliente: OrderedDict[str, deque] = OrderedDict()
        self._cv = threading.Condition()
        self._executar = executar
        for _ in range(simultaneos):
            threading.Thread(target=self._trabalhar, daemon=True).start()

    def por(self, cliente, trabalho):
        with self._cv:
            self._por_cliente.setdefault(cliente, deque()).append(trabalho)
            self._cv.notify()

    def _proximo(self):
        with self._cv:
            while not self._por_cliente:
                self._cv.wait()
            cliente, fila = self._por_cliente.popitem(last=False)
            trabalho = fila.popleft()
            if fila:
                self._por_cliente[cliente] = fila  # volta para o fim da rodada
            return trabalho

    def _trabalhar(self):
        while True:
            try:
                self._executar(self._proximo())
            except Exception:  # noqa: BLE001 — uma falha inesperada não pode custar uma thread do pool
                log.exception("falha fora do tratamento do documento")


class Problema(Exception):
    """Erro de contrato: vira application/problem+json (RFC 9457)."""

    def __init__(self, status, codigo, titulo, detalhe=None, cabecalhos=None, extras=None):
        super().__init__(titulo)
        self.status, self.codigo, self.titulo, self.detalhe, self.cabecalhos = (
            status,
            codigo,
            titulo,
            detalhe,
            cabecalhos,
        )
        self.extras = extras or {}


def _limite_pendentes():
    return Problema(
        429,
        "limite-pendentes",
        "Limite de documentos pendentes",
        f"Até {PENDENTES_POR_CLIENTE} por token, contando os que estão em envio.",
        {"Retry-After": "30"},
    )


def _vida(gerador):
    from contextlib import asynccontextmanager

    return asynccontextmanager(gerador)


def _limite_retencao():
    return Problema(
        429,
        "limite-retencao",
        "Limite de resultados em memória",
        f"Até {BYTES_RETIDOS_POR_CLIENTE // 2**20} MB por token; busque ou apague sessões e tente de novo.",
        {"Retry-After": "60"},
    )


def _iso(t):
    return datetime.fromtimestamp(t, UTC).isoformat().replace("+00:00", "Z") if t else None


class Servico:
    def __init__(self, tokens: list[str]):
        self.tokens = [t for t in tokens if t]
        self.sessoes: dict[str, dict] = {}
        self.idempotencia: dict[tuple[str, str], str] = {}
        self.clamav: tuple[str, int] | None = None  # LINCE_CLAMAV: todo arquivo recebido passa pelo antivírus
        self.cache = None  # LINCE_REDIS: transcrições da IA reaproveitadas entre envios do mesmo token
        self.edicoes: dict[str, dict] = {}  # arquivo editado esperando download (entrega única, como as sessões)
        self.vagas: dict[str, int] = {}  # documentos pendentes por cliente, reservados antes do upload
        self.retidos: dict[str, int] = {}  # bytes de resultado em memória por cliente (sessões em curso e concluídas)
        self.trava = threading.Lock()
        self.fila = Fila(self._ler, DOCS_SIMULTANEOS)
        self.segredo_aberto = os.urandom(32)  # sem tokens: o plano só vale nesta instância

    # ---------------------------------------------------------------- identidade e limites
    def cliente(self, request: Request) -> str:
        """Identidade do cliente (hash do token). Sem tokens configurados, o serviço é aberto."""
        if not self.tokens:
            return "aberto"
        m = re.match(r"Bearer\s+(.+)", request.headers.get("authorization", ""))
        if m:
            dado = m.group(1).strip().encode()
            if any(hmac.compare_digest(dado, t.encode()) for t in self.tokens):
                return hashlib.sha256(dado).hexdigest()[:16]
        raise Problema(401, "nao-autorizado", "Token ausente ou inválido", cabecalhos={"WWW-Authenticate": "Bearer"})

    def segredo(self, request: Request) -> bytes:
        """Chave que sela o plano do modo tarefas: derivada do token, vale em qualquer instância que o aceite."""
        if not self.tokens:
            return self.segredo_aberto
        m = re.match(r"Bearer\s+(.+)", request.headers.get("authorization", ""))
        return hashlib.sha256(b"lince-plano\0" + m.group(1).strip().encode()).digest()

    def reservar(self, cliente, n=1) -> bool:
        """Conferir e ocupar as vagas numa só operação: envios paralelos não furam o teto."""
        with self.trava:
            if self.vagas.get(cliente, 0) + n > PENDENTES_POR_CLIENTE:
                return False
            self.vagas[cliente] = self.vagas.get(cliente, 0) + n
            return True

    def liberar(self, cliente, n=1):
        with self.trava:
            self.vagas[cliente] -= n
            if not self.vagas[cliente]:
                del self.vagas[cliente]

    # ---------------------------------------------------------------- leitura
    def _ler(self, doc):
        sessao = doc["sessao"]
        estado = "falhou"
        try:
            if sessao.get("apagada"):
                estado = "cancelado"
                return
            doc["estado"], doc["inicio"] = "lendo", time.time()
            cache = CacheComEscopo(self.cache, sessao["cliente"]) if self.cache else None
            leitor = Leitor(ia=sessao["ia"], esforco=sessao["esforco"], cache=cache)
            if sessao.get("modo") == "tarefas":
                plano = leitor.preparar(doc["arquivo"], prazo=sessao.get("prazo"))
                doc["resultado"] = {
                    "tarefas": plano.para_json()["tarefas"],  # imagem em base64
                    "pendentes": plano.estado.get("pendentes", []) + plano.pendentes,
                    "plano": selar(sessao["segredo"], sessao["cliente"], plano),
                }
                if sessao.get("codigo"):
                    doc["resultado"].update(_codigo_do_documento(doc))
                estado = "pronto"
                return
            r = leitor.ler(doc["arquivo"], prazo=sessao.get("prazo"))
            doc["resultado"] = _resultado_api(r)
            if sessao.get("codigo"):
                doc["resultado"].update(_codigo_do_documento(doc))
            estado = "pronto"
        except Exception as e:  # noqa: BLE001 — o erro vira estado do documento, nunca derruba o serviço
            doc["erro"] = f"{type(e).__name__}: {str(e)[:300]}"
        finally:
            Path(doc["arquivo"]).unlink(missing_ok=True)
            doc["fim"] = time.time()
            self.liberar(sessao["cliente"])
            with self.trava:
                if sessao["id"] in self.sessoes:
                    # troca a reserva feita na entrada (bytes do arquivo) pelo tamanho real do resultado
                    real = len(json.dumps(doc["resultado"], ensure_ascii=False)) if doc.get("resultado") else 0
                    self._reter(sessao["cliente"], real - doc.get("bytes_retidos", 0))
                    doc["bytes_retidos"] = real
                else:
                    doc["resultado"] = None  # sessão apagada no meio da leitura: o resultado não fica
                doc["estado"] = estado  # por último: quem lê "pronto" já encontra resultado e fim
                self._fechar_se_terminou(sessao)

    def _fechar_se_terminou(self, sessao):
        """Chamar com a trava."""
        terminou = all(d["estado"] in ("pronto", "falhou", "cancelado") for d in sessao["itens"])
        if sessao["concluida_em"] is None and terminou:
            sessao["concluida_em"] = time.time()
            sessao["ia"] = None  # a chave do provedor sai da memória junto com o último documento
            shutil.rmtree(sessao["pasta"], ignore_errors=True)
            sessao["pronta"].set()

    def entregar(self, sessao, marcar=True) -> dict:
        """Representação da sessão. Concluída, é a entrega: a sessão some depois de REENTREGA (ou no DELETE)."""
        with self.trava:
            if sessao["id"] not in self.sessoes:  # apagada enquanto o pedido esperava
                raise Problema(404, "sessao-nao-encontrada", "Sessão não encontrada", "Foi apagada.")
            corpo = _sessao(sessao)
            if marcar and corpo["estado"] == "concluida" and not sessao.get("entregue_em"):
                sessao["entregue_em"] = time.time()
        return corpo

    def _reter(self, cliente, delta):
        """Chamar com a trava."""
        self.retidos[cliente] = self.retidos.get(cliente, 0) + delta
        if not self.retidos[cliente]:
            del self.retidos[cliente]

    def acima_do_teto(self, cliente) -> bool:
        return self.retidos.get(cliente, 0) > BYTES_RETIDOS_POR_CLIENTE

    def limpar(self):
        agora = time.time()
        with self.trava:
            vencidas = [k for k, x in self.sessoes.items() if _vencida(x, agora)]
            for sid in vencidas:
                self._remover(sid)
            for eid in [k for k, x in self.edicoes.items() if _vencida(x, agora)]:
                self._remover_edicao(eid)

    def _remover_edicao(self, eid):
        """Chamar com a trava."""
        x = self.edicoes.pop(eid)
        self._reter(x["cliente"], -len(x["dados"]))

    def da_edicao(self, cliente, eid) -> dict:
        """Chamar com a trava."""
        x = self.edicoes.get(eid)
        if x is not None and _vencida(x, time.time()):
            self._remover_edicao(eid)
            x = None
        if x is None or x["cliente"] != cliente:
            raise Problema(
                404, "edicao-nao-encontrada", "Edição não encontrada", "Não existe, expirou ou é de outro token."
            )
        return x

    def _remover(self, sid):
        """Chamar com a trava."""
        x = self.sessoes.pop(sid)
        liberados = sum(d.get("bytes_retidos", 0) for d in x["itens"])
        if liberados:
            self._reter(x["cliente"], -liberados)
        if x.get("idempotencia"):
            self.idempotencia.pop((x["cliente"], x["idempotencia"]), None)

    def da_sessao(self, cliente, sid) -> dict:
        """Chamar com a trava."""
        x = self.sessoes.get(sid)
        if x is not None and _vencida(x, time.time()):
            self._remover(sid)  # entregue ou fora do prazo: some na hora, mesmo antes da limpeza periódica
            x = None
        if x is None or x["cliente"] != cliente:  # sessão de outro token é tratada como inexistente
            raise Problema(
                404, "sessao-nao-encontrada", "Sessão não encontrada", "Não existe, expirou ou é de outro token."
            )
        return x


# -------------------------------------------------------------------- plano do modo tarefas
def selar(segredo: bytes, cliente: str, plano) -> str:
    """O plano (texto e estado do documento, sem as imagens) compactado e assinado com uma chave derivada do token:
    a montagem não guarda nada no servidor, só o mesmo token monta, dentro de PLANO_VALIDADE, e ninguém altera o
    plano no caminho. Quem tem o token pode assinar um estado próprio: por isso a montagem o confere como entrada."""
    import base64
    import zlib

    corpo = {"v": 1, "c": cliente, "t": time.time(), "estado": plano.estado, "pendentes": plano.pendentes,
             "tarefas": [t.id for t in plano.tarefas]}  # fmt: skip
    texto = json.dumps(corpo, ensure_ascii=False).encode()
    if len(texto) > PLANO_MAX:  # a montagem recusaria: falha já aqui, antes de quem chama pagar as leituras
        raise ValueError(f"plano de {len(texto) // 2**20} MB, acima do teto de {PLANO_MAX // 2**20} MB da montagem")
    dados = base64.urlsafe_b64encode(zlib.compress(texto, 6)).decode()
    return dados + "." + base64.urlsafe_b64encode(hmac.digest(segredo, dados.encode(), "sha256")).decode()


def abrir(segredo: bytes, cliente: str, selado: str):
    """O plano de `selar`, conferido: assinatura, dono, validade e tamanho. Levanta Problema."""
    import base64
    import binascii
    import zlib

    from .tipos import Plano, TarefaVisual

    dados, _, assinatura = (selado or "").partition(".")
    esperada = base64.urlsafe_b64encode(hmac.digest(segredo, dados.encode(), "sha256")).decode()
    if not dados or not hmac.compare_digest(assinatura.encode("utf-8", "replace"), esperada.encode()):
        raise Problema(422, "plano-invalido", "Plano inválido", "Não foi assinado para este token, ou foi alterado.")
    try:
        d = zlib.decompressobj()
        corpo = json.loads(d.decompress(base64.urlsafe_b64decode(dados), PLANO_MAX))
        if d.unconsumed_tail:
            raise ValueError("grande demais")
    except (binascii.Error, zlib.error, ValueError) as e:
        raise Problema(422, "plano-invalido", "Plano inválido", str(e)[:200]) from None
    if corpo.get("c") != cliente:
        raise Problema(422, "plano-invalido", "Plano inválido", "É de outro token.")
    if time.time() - corpo.get("t", 0) > PLANO_VALIDADE:
        raise Problema(422, "plano-expirado", "Plano expirado", f"Vale {PLANO_VALIDADE // 3600} h depois da leitura.")
    tarefas = [TarefaVisual(i, "", b"", "", "", 0) for i in corpo["tarefas"]]
    return Plano(tarefas, corpo["estado"], corpo["pendentes"])


PLANO_PAGINAS_MAX = 100_000
PLANO_TAREFAS_MAX = 20_000
PLANO_TRABALHO_MAX = 2_000_000  # por página: regiões x blocos, o que a montagem compara uma com a outra


def _montar_pedido(segredo, cliente, corpo):
    """O corpo de POST /v1/montagens até o resultado. O plano é entrada de quem tem o token (a assinatura só prova
    que é dele e que não mudou no caminho): a forma e os tetos são conferidos antes da montagem, e o que ela não
    entende vira 422, nunca erro interno."""
    try:
        pedido = json.loads(corpo)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise Problema(400, "montagem-invalida", "O corpo precisa ser JSON", str(e)[:200]) from None
    respostas = pedido.get("respostas") if isinstance(pedido, dict) else None
    if not isinstance(respostas, dict) or not all(isinstance(v, str) for v in respostas.values()):
        raise Problema(400, "montagem-invalida", 'Envie {"plano": ..., "respostas": {id: texto}}')
    plano = abrir(segredo, cliente, str(pedido.get("plano") or ""))
    try:
        _conferir_estado(plano.estado)
        return Leitor().concluir(plano, respostas)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError) as e:
        raise Problema(422, "plano-invalido", "Plano inválido", f"{type(e).__name__}: {str(e)[:200]}") from None


def _conferir_estado(e):
    """Forma e tetos do estado do plano, para a montagem não ter custo sem limite com um estado montado à mão."""

    def lista(x, tipo, maximo, nome):
        if not isinstance(x, list) or len(x) > maximo or not all(isinstance(i, tipo) for i in x):
            raise ValueError(f"{nome} fora da forma ou acima de {maximo}")

    if not isinstance(e, dict) or e.get("tipo") not in ("pdf", "office", "imagem", "texto"):
        raise ValueError("tipo do plano")
    lista(e.get("tarefas", []), dict, PLANO_TAREFAS_MAX, "tarefas")
    lista(e.get("tarefas_originais", []), dict, PLANO_TAREFAS_MAX, "tarefas_originais")
    lista(e.get("extras", []), str, PLANO_TAREFAS_MAX, "extras")
    if not isinstance(e.get("md", ""), str) or not isinstance(e.get("iguais", {}), dict):
        raise ValueError("md ou iguais")
    lotes = e.get("lotes") or {}
    if (
        not isinstance(lotes, dict)
        or sum(len(v) if isinstance(v, list) else 0 for v in lotes.values()) > PLANO_TAREFAS_MAX
    ):
        raise ValueError("lotes")
    lista(e.get("paginas", []), dict, PLANO_PAGINAS_MAX, "paginas")
    for p in e.get("paginas", []):
        if not isinstance(p.get("md", ""), str) or p.get("rota") not in ("nativa", "regioes", "pagina"):
            raise ValueError("página")
        lista(p.get("regioes", []), dict, PLANO_TAREFAS_MAX, "regiões")
        lista(p.get("tabelas", []), str, PLANO_TAREFAS_MAX, "tabelas")
        lista(p.get("formulas", []), str, PLANO_TAREFAS_MAX, "fórmulas")
        if len(p.get("regioes", [])) * (p.get("md", "").count("\n\n") + 1) > PLANO_TRABALHO_MAX:
            raise ValueError("página com regiões e blocos demais")


def _resultado_api(r) -> dict:
    return {
        "markdown": r.markdown,
        "metadados": r.metadados,
        "cobertura": asdict(r.cobertura),
        "alertas_injecao": r.alertas_injecao,
        "uso": [asdict(u) for u in r.uso],
        "estrutura": r.estrutura,
    }


# -------------------------------------------------------------------- representação (contrato)
def _contagem(x):
    estados = [d["estado"] for d in x["itens"]]
    return {"total": len(estados), "prontos": estados.count("pronto"), "falhos": estados.count("falhou")}


def _codigo_do_documento(doc) -> dict:
    """O código (estrutura editável) do documento, quando o formato tem; senão, o motivo."""
    try:
        return {"codigo": codigo_doc.extrair(Path(doc["arquivo"]).read_bytes(), doc["nome"]), "codigo_motivo": None}
    except codigo_doc.FormatoNaoSuportado as e:
        return {"codigo": None, "codigo_motivo": str(e)}
    except Exception as e:  # noqa: BLE001 — a leitura já saiu; o código é um extra
        return {"codigo": None, "codigo_motivo": f"{type(e).__name__}: {str(e)[:200]}"}


def _vencida(x, agora):
    if x.get("entregue_em"):
        return agora - x["entregue_em"] > REENTREGA
    return bool(x["concluida_em"]) and agora - x["concluida_em"] > ENTREGA_PRAZO


def _expira(x, concluida):
    if x.get("entregue_em"):
        return x["entregue_em"] + REENTREGA
    return concluida + ENTREGA_PRAZO if concluida else None


def _resumo(x):
    concluida = x["concluida_em"]  # lido uma vez: o worker pode concluir a sessão no meio
    return {
        "id": x["id"],
        "estado": "concluida" if concluida else "processando",
        "referencia": x["referencia"],
        "criada_em": _iso(x["criada_em"]),
        "concluida_em": _iso(concluida),
        "expira_em": _iso(_expira(x, concluida)),
        "documentos": _contagem(x),
    }


def _sessao(x):
    itens = [
        {
            "id": d["id"],
            "nome": d["nome"],
            "bytes": d["bytes"],
            "sha256": d["sha256"],
            "estado": d["estado"],
            "segundos": round(d["fim"] - d["inicio"], 2) if d.get("fim") and d.get("inicio") else None,
            "erro": d.get("erro"),
            "resultado": d.get("resultado") if d["estado"] == "pronto" else None,
        }
        for d in x["itens"]
    ]
    return {**_resumo(x), "esforco": x["esforco"], "itens": itens}


async def _aguardar(evento: threading.Event, segundos):
    """Espera sem prender thread: cliente parado em Prefer: wait não esgota o executor."""
    limite = time.monotonic() + segundos
    while not evento.is_set() and time.monotonic() < limite:
        await asyncio.sleep(0.2)


def _espera(request: Request) -> float:
    """Só a preferência `wait` exata (RFC 7240): `x-nowait=3` ou `wait=abc` não contam."""
    for pref in request.headers.get("prefer", "").replace(";", ",").split(","):
        nome, _, valor = pref.strip().partition("=")
        if nome.strip().lower() == "wait" and valor.strip().isdigit():
            return min(int(valor.strip()), ESPERA_MAX)
    return 0


class _Envelope:
    """Middleware ASGI: X-Request-Id em toda resposta e nenhum resultado em cache intermediário."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        recebido = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        rid = recebido if REQUEST_ID.fullmatch(recebido) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = rid

        async def enviar(msg):
            if msg["type"] == "http.response.start":
                msg["headers"] = [
                    *msg.get("headers", []),
                    (b"x-request-id", rid.encode()),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                ]
            await send(msg)

        await self.app(scope, receive, enviar)


# -------------------------------------------------------------------- rotas
def criar_app(tokens: list[str], clamav: tuple[str, int] | None = None, cache=None) -> Starlette:
    s = Servico(tokens)
    s.clamav, s.cache = clamav, cache

    async def saude(request):
        return JSONResponse({"status": "ok", "versao": VERSAO, "contrato": CONTRATO["info"]["version"]})

    async def contrato(request):
        return JSONResponse(CONTRATO)

    async def criar(request: Request):
        cliente = s.cliente(request)
        tamanho = request.headers.get("content-length")
        if not tamanho or not tamanho.isdigit():
            raise Problema(411, "tamanho-ausente", "Content-Length obrigatório", "Upload chunked não é aceito.")
        if int(tamanho) > TAMANHO_MAX + 1024 * 1024:
            raise Problema(413, "envio-grande", "Envio grande demais", f"Teto de {TAMANHO_MAX // 2**20} MB por envio.")
        chave_idem = request.headers.get("idempotency-key")
        if chave_idem is not None and not 1 <= len(chave_idem) <= 200:
            raise Problema(400, "idempotency-key-invalida", "Idempotency-Key deve ter de 1 a 200 caracteres")
        for nome, maximo in CABECALHOS_MAX.items():
            if len(request.headers.get(nome, "")) > maximo:
                raise Problema(400, "cabecalho-grande", f"{nome} acima de {maximo} caracteres")
        url, modelo = request.headers.get("x-ia-url"), request.headers.get("x-ia-modelo")
        if bool(url) != bool(modelo):
            raise Problema(400, "provedor-incompleto", "Envie X-IA-URL e X-IA-Modelo juntos", "Ou nenhum: só nativo.")
        try:
            ia = OpenAICompativel(url, modelo, request.headers.get("x-ia-chave")) if url else None
        except ValueError as e:
            raise Problema(400, "provedor-invalido", "URL do provedor inválida", str(e)) from None
        # uma vaga antes do upload (envios paralelos não furam o teto); as do resto do lote, depois de ler o form
        with s.trava:
            cheio = s.acima_do_teto(cliente)
        if cheio and chave_idem is None:  # a repetição idempotente passa: ela entrega e libera memória
            raise _limite_retencao()
        if not s.reservar(cliente, 1):
            raise _limite_pendentes()
        reservadas, sessao, form = 1, None, None
        try:
            form = await request.form(max_files=ARQUIVOS_MAX, max_fields=ARQUIVOS_MAX + 5)
            arquivos = [a for a in form.getlist("arquivo") if hasattr(a, "read")]
            if not arquivos:
                raise Problema(400, "arquivo-ausente", "Envie ao menos um campo multipart 'arquivo'")
            if len(arquivos) > 1:
                if not s.reservar(cliente, len(arquivos) - 1):
                    raise _limite_pendentes()
                reservadas = len(arquivos)
            referencia = form.get("referencia") or None
            if referencia is not None and not REFERENCIA.fullmatch(str(referencia)):
                raise Problema(
                    400, "referencia-invalida", "Referência inválida", "Até 200 de: letras, números e . _ : @ / -"
                )
            esforco = str(form.get("esforco") or "padrao")
            if esforco not in ESFORCOS:
                raise Problema(400, "esforco-invalido", "esforco deve ser 'padrao' ou 'max'")
            prazo = form.get("prazo")
            if prazo is not None and prazo != "":
                if not re.fullmatch(r"\d{1,4}", str(prazo)) or not PRAZO_MIN <= int(prazo) <= PRAZO_MAX:
                    raise Problema(
                        400, "prazo-invalido", f"prazo deve ser um inteiro de {PRAZO_MIN} a {PRAZO_MAX} segundos"
                    )
                prazo = int(prazo)
            else:
                prazo = None
            modo = str(form.get("modo") or "leitura")
            if modo not in MODOS:
                raise Problema(400, "modo-invalido", "modo deve ser 'leitura' ou 'tarefas'")
            if modo == "tarefas" and (ia is not None or esforco == "max"):
                raise Problema(
                    400, "modo-invalido", "No modo tarefas a IA é de quem chama", "Sem X-IA-* e sem esforco=max."
                )
            pedir_codigo = str(form.get("codigo") or "nao")
            if pedir_codigo not in ("sim", "nao"):
                raise Problema(400, "codigo-invalido", "codigo deve ser 'sim' ou 'nao'")
            for a in arquivos:
                ext = Path(a.filename or "").suffix.lower()
                if not re.fullmatch(r"\.[a-z0-9]{1,8}", ext):
                    raise Problema(
                        400, "extensao-ausente", "Todo arquivo precisa de nome com extensão", a.filename or ""
                    )
                if ext not in FORMATOS:  # recusa antes da fila: nada é copiado, varrido nem lido, e as vagas voltam
                    raise Problema(
                        415,
                        "formato-nao-suportado",
                        "Formato não suportado",
                        f"{a.filename!r}: aceitos {', '.join(sorted(FORMATOS))}",
                    )
            sessao = await _receber(
                s, cliente, arquivos, referencia, esforco, ia, chave_idem, codigo=pedir_codigo == "sim", prazo=prazo,
                modo=modo, segredo=s.segredo(request),
            )  # fmt: skip
        finally:
            if form is not None:
                await form.close()  # o spool do multipart sai do disco já: o documento foi copiado
            if sessao is None or sessao.get("repetida"):
                s.liberar(cliente, reservadas)  # não entrou na fila (erro, exceção ou repetição idempotente)
        if sessao.get("repetida"):
            return JSONResponse(s.entregar(sessao["original"]), status_code=200)
        if (t := _espera(request)) > 0:
            await _aguardar(sessao["pronta"], t)
        local = str(request.url_for("sessao", id=sessao["id"]))
        # cliente que desistiu da espera não recebe nada: a sessão não conta como entregue
        entregue = s.entregar(sessao, marcar=not await request.is_disconnected())
        return JSONResponse(entregue, status_code=201, headers={"Location": local})

    async def listar(request: Request):
        cliente = s.cliente(request)
        referencia = request.query_params.get("referencia")
        if referencia is not None and not REFERENCIA.fullmatch(referencia):
            raise Problema(400, "referencia-invalida", "Referência inválida")
        limite = request.query_params.get("limite", "50")
        if not limite.isdigit() or not 1 <= int(limite) <= LISTA_MAX:
            raise Problema(400, "limite-invalido", f"limite deve estar entre 1 e {LISTA_MAX}")
        s.limpar()
        with s.trava:
            achadas = [
                x
                for x in s.sessoes.values()
                if x["cliente"] == cliente
                and not x.get("entregue_em")
                and (referencia is None or x["referencia"] == referencia)
            ]
        achadas.sort(key=lambda x: x["criada_em"], reverse=True)
        return JSONResponse({"sessoes": [_resumo(x) for x in achadas[: int(limite)]]})

    async def obter(request: Request):
        cliente = s.cliente(request)
        with s.trava:
            sessao = s.da_sessao(cliente, request.path_params["id"])
        if (t := _espera(request)) > 0:
            await _aguardar(sessao["pronta"], t)
        if request.method == "HEAD":  # HEAD não leva corpo, então não conta como entrega
            return JSONResponse(_sessao(sessao))
        return JSONResponse(s.entregar(sessao, marcar=not await request.is_disconnected()))

    async def apagar(request: Request):
        cliente = s.cliente(request)
        with s.trava:
            sessao = s.da_sessao(cliente, request.path_params["id"])
            sessao["apagada"] = True  # o que ainda está na fila é cancelado ao chegar a vez
            s._remover(sessao["id"])
        return Response(status_code=204)

    async def erro_de_contrato(request, p: Problema):
        corpo = {"type": f"urn:lince:erro:{p.codigo}", "title": p.titulo, "status": p.status}
        if p.detalhe:
            corpo["detail"] = p.detalhe
        corpo["instance"] = request.scope.get("state", {}).get("request_id", "")
        corpo.update(p.extras)
        return JSONResponse(corpo, status_code=p.status, media_type="application/problem+json", headers=p.cabecalhos)

    async def erro_http(request, exc: HTTPException):
        # rota inexistente, método errado, multipart malformado: o mesmo formato de erro do resto da API
        codigo = ERROS_HTTP.get(exc.status_code, "requisicao-invalida")
        p = Problema(exc.status_code, codigo, str(exc.detail), cabecalhos=exc.headers)
        return await erro_de_contrato(request, p)

    async def erro_interno(request, exc: Exception):
        # fica fora do _Envelope (ServerErrorMiddleware): os cabeçalhos do envelope vão aqui
        log.exception("erro interno")
        rid = request.scope.get("state", {}).get("request_id") or uuid.uuid4().hex
        corpo = {"type": "urn:lince:erro:interno", "title": "Erro interno", "status": 500, "instance": rid}
        cab = {"X-Request-Id": rid, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        return JSONResponse(corpo, status_code=500, media_type="application/problem+json", headers=cab)

    async def sessoes(request: Request):
        return await {"POST": criar, "GET": listar, "HEAD": listar}[request.method](request)

    async def sessao(request: Request):
        # despacho explícito: HEAD (que o Starlette aceita junto com GET) nunca pode cair em apagar
        return await {"GET": obter, "HEAD": obter, "DELETE": apagar}[request.method](request)

    async def editar(request: Request):
        cliente = s.cliente(request)
        tamanho = request.headers.get("content-length")
        if not tamanho or not tamanho.isdigit():
            raise Problema(411, "tamanho-ausente", "Content-Length obrigatório", "Upload chunked não é aceito.")
        if int(tamanho) > TAMANHO_MAX + 1024 * 1024:
            raise Problema(413, "envio-grande", "Envio grande demais", f"Teto de {TAMANHO_MAX // 2**20} MB por envio.")
        with s.trava:
            cheio = s.acima_do_teto(cliente)
        if cheio:
            raise _limite_retencao()
        if not s.reservar(cliente, 1):  # edição em andamento conta como documento pendente
            raise _limite_pendentes()
        form = None
        try:
            form = await request.form(max_files=1, max_fields=3)
            arquivo = form.get("arquivo")
            if arquivo is None or not hasattr(arquivo, "read"):
                raise Problema(400, "arquivo-ausente", "Envie o documento no campo multipart 'arquivo'")
            nome = Path(arquivo.filename or "").name[:255]
            try:
                pedido = json.loads(str(form.get("operacoes") or ""))
            except json.JSONDecodeError as e:
                raise Problema(400, "operacoes-invalidas", "operacoes precisa ser JSON", str(e)) from None
            if not isinstance(pedido, dict):
                raise Problema(400, "operacoes-invalidas", "operacoes precisa ser um objeto JSON")
            dados = await arquivo.read(TAMANHO_MAX + 1)
            if len(dados) > TAMANHO_MAX:
                raise Problema(413, "envio-grande", "Envio grande demais")
            await _varrer(s, arquivo.filename, dados)
            try:
                novo, relatorio = await asyncio.to_thread(
                    codigo_doc.editar, dados, nome, pedido.get("operacoes"), pedido.get("base_sha256")
                )
            except codigo_doc.FormatoNaoSuportado as e:
                raise Problema(415, "formato-nao-editavel", "Formato não editável", str(e)) from None
            except codigo_doc.OperacaoInvalida as e:
                status = 409 if e.codigo in ("conflito", "base-divergente", "revisoes-pendentes") else 422
                extras = {k: v for k, v in (("operacao", e.indice), ("alvo", e.alvo)) if v is not None}
                raise Problema(status, e.codigo, "Operação não aplicada", str(e), extras=extras) from None
            except DocumentoRecusado as e:
                raise Problema(422, "documento-recusado", "Documento recusado", str(e)) from None
        finally:
            if form is not None:
                await form.close()
            s.liberar(cliente)
        eid = uuid.uuid4().hex
        agora = time.time()
        edicao = {"id": eid, "cliente": cliente, "nome": nome, "dados": novo, "criada_em": agora, "concluida_em": agora}
        with s.trava:
            if s.retidos.get(cliente, 0) + len(novo) > BYTES_RETIDOS_POR_CLIENTE:
                raise _limite_retencao()
            s._reter(cliente, len(novo))
            s.edicoes[eid] = edicao
        url = str(request.url_for("edicao_arquivo", id=eid))
        corpo = {"id": eid, "nome": nome, "arquivo_url": url, "expira_em": _iso(agora + ENTREGA_PRAZO), **relatorio}
        return JSONResponse(corpo, status_code=201, headers={"Location": url})

    async def edicao_arquivo(request: Request):
        cliente = s.cliente(request)
        with s.trava:
            x = s.da_edicao(cliente, request.path_params["id"])
            if request.method == "GET" and not x.get("entregue_em"):
                x["entregue_em"] = time.time()  # entregue: some depois de REENTREGA (ou no DELETE)
        tipo = MIDIA.get(Path(x["nome"]).suffix.lower(), "application/octet-stream")
        nome_seguro = re.sub(r"[^A-Za-z0-9._-]", "_", x["nome"]) or "documento"
        return Response(
            x["dados"] if request.method == "GET" else b"",
            media_type=tipo,
            headers={
                "Content-Disposition": f'attachment; filename="{nome_seguro}"',
                "Content-Length": str(len(x["dados"])),
            },
        )

    async def edicao_apagar(request: Request):
        cliente = s.cliente(request)
        with s.trava:
            x = s.da_edicao(cliente, request.path_params["id"])
            s._remover_edicao(x["id"])
        return Response(status_code=204)

    async def edicao(request: Request):
        return await {"GET": edicao_arquivo, "HEAD": edicao_arquivo, "DELETE": edicao_apagar}[request.method](request)

    async def montar(request: Request):
        cliente = s.cliente(request)
        tamanho = request.headers.get("content-length")
        if not tamanho or not tamanho.isdigit():
            raise Problema(411, "tamanho-ausente", "Content-Length obrigatório", "Upload chunked não é aceito.")
        if int(tamanho) > TAMANHO_MAX:
            raise Problema(413, "envio-grande", "Envio grande demais", f"Teto de {TAMANHO_MAX // 2**20} MB por envio.")
        with s.trava:
            cheio = s.acima_do_teto(cliente)
        if cheio:
            raise _limite_retencao()
        if not s.reservar(cliente, 1):  # a vaga antes do corpo: pedidos em paralelo não somam memória sem teto
            raise _limite_pendentes()
        try:
            corpo = await request.body()
            segredo = s.segredo(request)
            # leitura do JSON, abertura do plano e montagem fora do laço de eventos: os outros clientes seguem
            r = await asyncio.to_thread(_montar_pedido, segredo, cliente, corpo)
        finally:
            s.liberar(cliente)
        return JSONResponse(_resultado_api(r))

    async def limpeza(app):
        async def laco():
            while True:
                await asyncio.sleep(LIMPEZA_INTERVALO)
                s.limpar()

        tarefa = asyncio.create_task(laco())
        yield
        tarefa.cancel()

    app = Starlette(
        routes=[
            Route("/saude", saude),
            Route("/v1/openapi.json", contrato),
            # uma rota por caminho: o 405 informa no Allow todos os métodos aceitos
            Route("/v1/sessoes", sessoes, methods=["GET", "POST"]),
            Route("/v1/sessoes/{id:str}", sessao, methods=["GET", "DELETE"], name="sessao"),
            Route("/v1/edicoes", editar, methods=["POST"]),
            Route("/v1/montagens", montar, methods=["POST"]),
            Route("/v1/edicoes/{id:str}/arquivo", edicao, methods=["GET", "DELETE"], name="edicao_arquivo"),
        ],
        exception_handlers={Problema: erro_de_contrato, HTTPException: erro_http, Exception: erro_interno},
        lifespan=_vida(limpeza),
    )
    app.add_middleware(_Envelope)
    app.state.servico = s
    return app


async def _varrer(s: Servico, nome, arquivo):
    """Com ClamAV configurado, arquivo infectado nunca chega aos parsers; sem veredito, nada é lido."""
    if not s.clamav:
        return
    try:
        await antivirus.varrer(s.clamav, arquivo)
    except antivirus.Infectado as e:
        raise Problema(
            422, "arquivo-infectado", "Arquivo recusado pelo antivírus", f"{nome!r}: {e.assinatura}"
        ) from None
    except antivirus.Indisponivel as e:
        log.warning("antivírus sem veredito: %s", e)  # detalhe interno fica no log, não na resposta
        raise Problema(
            503,
            "antivirus-indisponivel",
            "Antivírus indisponível",
            "Tente de novo em instantes.",
            {"Retry-After": "30"},
        ) from None


async def _receber(
    s: Servico, cliente, arquivos, referencia, esforco, ia, chave_idem, codigo=False, prazo=None, modo="leitura",
    segredo=b"",
):  # fmt: skip
    """Grava o lote, calcula hash e tamanho, aplica a idempotência e põe os documentos na fila."""
    pasta = tempfile.mkdtemp(prefix="lince-api-")
    try:
        itens = []
        for k, a in enumerate(arquivos):
            caminho = os.path.join(pasta, f"{k}{Path(a.filename).suffix.lower()}")
            h = hashlib.sha256()

            def copiar(origem=a.file, destino_caminho=caminho, h=h):
                with open(destino_caminho, "wb") as destino:  # do spool para o disco, sem o arquivo inteiro na RAM
                    while bloco := origem.read(1024 * 1024):
                        h.update(bloco)
                        destino.write(bloco)

            await asyncio.to_thread(copiar)
            itens.append(
                {
                    "id": uuid.uuid4().hex,
                    "nome": Path(a.filename).name[:255],
                    "bytes": os.path.getsize(caminho),
                    "sha256": h.hexdigest(),
                    "arquivo": caminho,
                    "estado": "na_fila",
                }
            )
        if sum(d["bytes"] for d in itens) > TAMANHO_MAX:
            raise Problema(413, "envio-grande", "Envio grande demais", f"Teto de {TAMANHO_MAX // 2**20} MB por envio.")
        provedor = getattr(ia, "identidade", None)
        conteudo = [referencia, esforco, provedor, codigo, [(d["nome"], d["sha256"]) for d in itens], prazo, modo]
        impressao = hashlib.sha256(json.dumps(conteudo).encode()).hexdigest()
        s.limpar()
        if chave_idem:  # repetição ou chave reusada: responde antes de varrer (o envio aceito já foi varrido)
            with s.trava:
                sid = s.idempotencia.get((cliente, chave_idem))
                if sid in s.sessoes:
                    if s.sessoes[sid]["impressao"] != impressao:
                        raise Problema(
                            422, "idempotency-key-reutilizada", "Idempotency-Key já usada com outro conteúdo"
                        )
                    shutil.rmtree(pasta, ignore_errors=True)
                    return {"repetida": True, "original": s.sessoes[sid]}
        for d, a in zip(itens, arquivos, strict=True):  # antes de qualquer leitura
            await _varrer(s, a.filename, d["arquivo"])
        with s.trava:
            if chave_idem and (sid := s.idempotencia.get((cliente, chave_idem))) and sid in s.sessoes:
                original = s.sessoes[sid]
                if original["impressao"] != impressao:
                    raise Problema(422, "idempotency-key-reutilizada", "Idempotency-Key já usada com outro conteúdo")
                shutil.rmtree(pasta, ignore_errors=True)
                return {"repetida": True, "original": original}
            entrada = sum(d["bytes"] for d in itens)
            if s.retidos.get(cliente, 0) + entrada > BYTES_RETIDOS_POR_CLIENTE:
                # conferido de novo sob a trava, junto com a reserva: envios simultâneos não furam o teto
                raise _limite_retencao()
            sessao = {
                "id": uuid.uuid4().hex,
                "cliente": cliente,
                "referencia": referencia,
                "esforco": esforco,
                "prazo": prazo,
                "codigo": codigo,
                "modo": modo,
                "segredo": segredo,
                "ia": ia,
                "criada_em": time.time(),
                "concluida_em": None,
                "pasta": pasta,
                "itens": itens,
                "impressao": impressao,
                "idempotencia": chave_idem,
                "pronta": threading.Event(),
            }
            for d in itens:
                d["sessao"] = sessao
                d["bytes_retidos"] = d["bytes"]  # reserva na entrada: uma rajada de envios não fura o teto
            s._reter(cliente, entrada)
            s.sessoes[sessao["id"]] = sessao
            if chave_idem:
                s.idempotencia[(cliente, chave_idem)] = sessao["id"]
        for d in itens:
            s.fila.por(cliente, d)
        return sessao
    except BaseException:
        shutil.rmtree(pasta, ignore_errors=True)
        raise


def main():
    ap = argparse.ArgumentParser(prog="lince-servidor", description="API HTTP do Lince")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--porta", type=int, default=8000)
    ap.add_argument("--sem-autenticacao", action="store_true", help="só para uso local: aceita pedido sem token")
    a = ap.parse_args()
    tokens = [t.strip() for t in os.environ.get("LINCE_TOKENS", "").split(",") if t.strip()]
    try:
        clamav = antivirus.endereco(os.environ.get("LINCE_CLAMAV"))
    except ValueError as e:
        raise SystemExit(str(e)) from None
    if not tokens and not a.sem_autenticacao:
        raise SystemExit("Defina LINCE_TOKENS (tokens aceitos, separados por vírgula) ou use --sem-autenticacao.")
    try:
        cache = cache_do_ambiente(os.environ)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    import uvicorn

    uvicorn.run(criar_app(tokens, clamav, cache), host=a.host, port=a.porta, proxy_headers=True, log_level="info")


def cache_do_ambiente(env) -> CacheRedis | None:
    """LINCE_REDIS liga o cache; LINCE_CACHE_DIAS diz por quanto tempo cada leitura fica (padrão 30)."""
    url = (env.get("LINCE_REDIS") or "").strip()
    if not url:
        return None
    try:
        dias = float(env.get("LINCE_CACHE_DIAS") or 30)
    except ValueError:
        raise ValueError("LINCE_CACHE_DIAS precisa ser um número de dias") from None
    if not url.startswith(("redis://", "rediss://", "unix://")):
        raise ValueError("LINCE_REDIS precisa ser uma URL redis://, rediss:// ou unix://")
    return CacheRedis(url, dias)


if __name__ == "__main__":
    main()
