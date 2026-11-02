"""API HTTP do Lince. O contrato é `openapi.json` (servido em /v1/openapi.json); este módulo o implementa.

Cada envio (um documento ou um lote) cria uma sessão, marcada pela `referencia` de quem chama, para o sistema de
origem saber de quem é cada resultado. Nada é guardado: o arquivo é apagado assim que lido, e o resultado fica em
memória só até ser entregue (a primeira resposta com a sessão concluída, mais REENTREGA para repetir se a resposta
se perder) ou até ENTREGA_PRAZO. O modelo de visão vem
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
from .leitor import VERSAO, Leitor
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

    def __init__(self, status, codigo, titulo, detalhe=None, cabecalhos=None):
        super().__init__(titulo)
        self.status, self.codigo, self.titulo, self.detalhe, self.cabecalhos = (
            status,
            codigo,
            titulo,
            detalhe,
            cabecalhos,
        )


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
        self.vagas: dict[str, int] = {}  # documentos pendentes por cliente, reservados antes do upload
        self.retidos: dict[str, int] = {}  # bytes de resultado em memória por cliente (sessões em curso e concluídas)
        self.trava = threading.Lock()
        self.fila = Fila(self._ler, DOCS_SIMULTANEOS)

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
            r = Leitor(ia=sessao["ia"], esforco=sessao["esforco"]).ler(doc["arquivo"])
            doc["resultado"] = {
                "markdown": r.markdown,
                "metadados": r.metadados,
                "cobertura": asdict(r.cobertura),
                "alertas_injecao": r.alertas_injecao,
                "uso": [asdict(u) for u in r.uso],
            }
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


# -------------------------------------------------------------------- representação (contrato)
def _contagem(x):
    estados = [d["estado"] for d in x["itens"]]
    return {"total": len(estados), "prontos": estados.count("pronto"), "falhos": estados.count("falhou")}


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
def criar_app(tokens: list[str], clamav: tuple[str, int] | None = None) -> Starlette:
    s = Servico(tokens)
    s.clamav = clamav

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
            form = await request.form(max_files=ARQUIVOS_MAX, max_fields=ARQUIVOS_MAX + 4)
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
            for a in arquivos:
                if not re.fullmatch(r"\.[a-z0-9]{1,8}", Path(a.filename or "").suffix.lower()):
                    raise Problema(
                        400, "extensao-ausente", "Todo arquivo precisa de nome com extensão", a.filename or ""
                    )
            sessao = await _receber(s, cliente, arquivos, referencia, esforco, ia, chave_idem)
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
        ],
        exception_handlers={Problema: erro_de_contrato, HTTPException: erro_http, Exception: erro_interno},
        lifespan=_vida(limpeza),
    )
    app.add_middleware(_Envelope)
    app.state.servico = s
    return app


async def _receber(s: Servico, cliente, arquivos, referencia, esforco, ia, chave_idem):
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
        if s.clamav:  # antes de qualquer leitura: arquivo infectado nunca chega aos parsers
            for d, a in zip(itens, arquivos, strict=True):
                try:
                    await asyncio.to_thread(antivirus.varrer, s.clamav, d["arquivo"])
                except antivirus.Infectado as e:
                    raise Problema(
                        422, "arquivo-infectado", "Arquivo recusado pelo antivírus", f"{a.filename!r}: {e.assinatura}"
                    ) from None
                except antivirus.Indisponivel as e:
                    raise Problema(
                        503, "antivirus-indisponivel", "Antivírus indisponível", str(e), {"Retry-After": "30"}
                    ) from None
        if sum(d["bytes"] for d in itens) > TAMANHO_MAX:
            raise Problema(413, "envio-grande", "Envio grande demais", f"Teto de {TAMANHO_MAX // 2**20} MB por envio.")
        provedor = getattr(ia, "identidade", None)
        conteudo = [referencia, esforco, provedor, [(d["nome"], d["sha256"]) for d in itens]]
        impressao = hashlib.sha256(json.dumps(conteudo).encode()).hexdigest()
        s.limpar()
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
    clamav = antivirus.endereco(os.environ.get("LINCE_CLAMAV"))
    if not tokens and not a.sem_autenticacao:
        raise SystemExit("Defina LINCE_TOKENS (tokens aceitos, separados por vírgula) ou use --sem-autenticacao.")
    import uvicorn

    uvicorn.run(criar_app(tokens, clamav), host=a.host, port=a.porta, proxy_headers=True, log_level="info")


if __name__ == "__main__":
    main()
