"""API HTTP do Lince. O contrato é `openapi.json` (servido em /v1/openapi.json); este módulo o implementa.

Cada envio (um documento ou um lote) cria uma sessão, marcada pela `referencia` de quem chama, para o sistema de
origem saber de quem é cada resultado. O documento não é guardado: o arquivo é apagado assim que lido, e o resultado
fica num arquivo temporário (fora da memória do processo) só até ser entregue (a primeira resposta com a sessão
concluída, mais REENTREGA para repetir se a resposta se perder) ou até ENTREGA_PRAZO. Só com LINCE_REDIS, a
transcrição que a IA fez de cada recorte ou página fica no Redis por LINCE_CACHE_DIAS, separada por token. O modelo
de visão vem em cada envio (X-IA-URL, X-IA-Modelo, X-IA-Chave): o servidor não guarda chave de provedor. Fila justa
entre tokens.
"""

import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import math
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
import weakref
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

from . import antivirus, memoria, protecao
from . import codigo as codigo_doc
from .cache import CacheComEscopo, CacheRedis
from .leitor import FORMATOS, VERSAO, Leitor
from .limites import RETIDOS_POR_TOKEN, DocumentoRecusado
from .provedores import OpenAICompativel

CONTRATO = json.loads(files("lince").joinpath("openapi.json").read_text(encoding="utf-8"))

TAMANHO_MAX = 100 * 1024 * 1024  # por envio (soma do lote): acima disso, dividir em mais sessões
ARQUIVOS_MAX = 50  # por sessão
DOCS_SIMULTANEOS = 16  # medido na carga: o tempo é espera da IA, não CPU
# A montagem (POST /v1/montagens) é CPU: tem fila justa própria, para não esperar atrás de leituras presas na IA nem
# ocupar uma vaga delas. Duas: o GIL serializa o trabalho, então mais threads só disputariam a CPU com as leituras, e a
# segunda deixa uma montagem curta passar enquanto outra, grande, termina.
MONTAGENS_SIMULTANEAS = 2
# A decifração de um arquivo com senha deriva a chave (até milhões de voltas de hash) e segura algumas cópias dele na
# memória: tem fila justa própria, com poucas vagas, para não ocupar o executor que copia os envios dos outros tokens.
DECIFRACOES_SIMULTANEAS = 2
ESPERA_MAX = 300  # teto do Prefer: wait, abaixo do timeout comum de proxy reverso
ENTREGA_PRAZO = 15 * 60  # resultado não buscado em 15 min depois de concluído é descartado
# Depois da entrega, a sessão fica 60 s para o cliente repetir o pedido se a resposta se perdeu (rede, timeout);
# DELETE confirma o recebimento e apaga na hora.
REENTREGA = 60
PENDENTES_POR_CLIENTE = 64  # documentos na fila ou lendo por token; acima disso 429
# Resultado esperando entrega por token (padrão; o porquê em limites.py): acima disso, novos envios recebem 429 até o
# token buscar o que é dele. Um token não derruba os outros nem enche o disco da instância.
BYTES_RETIDOS_POR_CLIENTE = RETIDOS_POR_TOKEN
LIMPEZA_INTERVALO = 60
CABECALHOS_MAX = {"x-ia-url": 2048, "x-ia-modelo": 200, "x-ia-chave": 4096}
ERROS_HTTP = {400: "requisicao-invalida", 404: "rota-inexistente", 405: "metodo-nao-permitido"}
LISTA_MAX = 200
ESFORCOS = {"padrao", "max"}
MODOS = {"leitura", "tarefas"}  # tarefas: o Lince não chama a IA; devolve o que ela precisa ler e monta depois
PLANO_VALIDADE = 24 * 3600  # plano devolvido no modo tarefas vale um dia para a montagem
PLANO_MAX = 64 * 1024 * 1024  # plano descompactado (texto e estado do documento): acima disso é hostil
PRAZO_MIN, PRAZO_MAX = 1, 3600  # segundos por documento; o chat usa poucos, a base não manda prazo
# Modo tarefas: cada tarefa leva a imagem em base64 no resultado, que fica guardado até a entrega. Medido: cada metade
# de uma página escaneada densa sai com 0,6 MB de PNG (texto limpo) a 2 MB (com ruído), e 300 das limpas já somam
# 230 MB em base64, perto de um quarto dos BYTES_RETIDOS_POR_CLIENTE de um token. O que passa do teto fica nas
# pendências do preparo, com o motivo; `max_tarefas` só baixa o do documento.
TAREFAS_POR_DOCUMENTO = 200
TAREFAS_POR_SESSAO = 300
# Senha de arquivo protegido: o Office aceita até 255 caracteres e o PDF, 127 bytes; acima de 1024 não é senha.
SENHA_MAX = 1024
# Senhas erradas numa janela, para o Lince não servir de quebrador de senha. Por arquivo (o sha256): quem digita erra
# poucas vezes, e cinco erros em 15 min pedem uma pausa. Por token, um teto alto o bastante para os usuários de uma
# plataforma que errarem ao mesmo tempo: sem ele, o mesmo arquivo com um byte a mais no fim (outro hash, a mesma
# senha) recomeçaria a conta. Acima de um deles, 429 até o erro mais antigo da janela vencer; a senha certa zera a do
# arquivo.
SENHAS_ERRADAS_POR_ARQUIVO = 5
SENHAS_ERRADAS_POR_TOKEN = 50
SENHAS_JANELA = 15 * 60
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


def _limite_retencao(em_uso, teto, espera):
    return Problema(
        429,
        "limite-retencao",
        "Limite de resultados esperando entrega",
        f"Até {teto // 2**20} MB por token, com {em_uso // 2**20} MB em uso; busque ou apague sessões e tente de novo.",
        {"Retry-After": str(espera)},
        extras={"em_uso_bytes": em_uso, "teto_bytes": teto},
    )


def _iso(t):
    return datetime.fromtimestamp(t, UTC).isoformat().replace("+00:00", "Z") if t else None


class Servico:
    def __init__(self, tokens: list[str], tetos: dict[str, int] | None = None):
        self.tokens = [t for t in tokens if t]
        # teto de retenção de cada token que tem o seu (LINCE_TOKENS `nome:token:teto`), pela identidade do cliente
        self.tetos = {_identidade(t): n for t, n in (tetos or {}).items()}
        # o resultado de cada documento pronto fica aqui até ser entregue: a memória do processo é das leituras
        self.guardados = tempfile.mkdtemp(prefix="lince-resultados-")
        weakref.finalize(self, shutil.rmtree, self.guardados, True)  # sem o lifespan, sai na saída do processo
        self.sessoes: dict[str, dict] = {}
        self.idempotencia: dict[tuple[str, str], str] = {}
        self.clamav: tuple[str, int] | None = None  # LINCE_CLAMAV: todo arquivo recebido passa pelo antivírus
        self.cache = None  # LINCE_REDIS: transcrições da IA reaproveitadas entre envios do mesmo token
        self.edicoes: dict[str, dict] = {}  # arquivo editado esperando download (entrega única, como as sessões)
        self.vagas: dict[str, int] = {}  # documentos pendentes por cliente, reservados antes do upload
        self.retidos: dict[str, int] = {}  # bytes de resultado esperando entrega por cliente (em curso e concluídas)
        # instantes das senhas erradas na janela, por (cliente, sha256 do arquivo) e por (cliente, "") para o token
        self.senhas_erradas: dict[tuple[str, str], deque] = {}
        self.trava = threading.Lock()
        self.fila = Fila(self._ler, DOCS_SIMULTANEOS)
        self.montagens = Fila(lambda trabalho: trabalho(), MONTAGENS_SIMULTANEAS)
        self.decifracoes = Fila(lambda trabalho: trabalho(), DECIFRACOES_SIMULTANEAS)
        # segredo que assina o plano do modo tarefas: só o servidor conhece (LINCE_SEGREDO para várias réplicas)
        self.segredo_plano = os.urandom(32)

    # ---------------------------------------------------------------- identidade e limites
    def cliente(self, request: Request) -> str:
        """Identidade do cliente (hash do token). Sem tokens configurados, o serviço é aberto."""
        if not self.tokens:
            return "aberto"
        m = re.match(r"Bearer\s+(.+)", request.headers.get("authorization", ""))
        if m:
            dado = m.group(1).strip().encode()
            if any(hmac.compare_digest(dado, t.encode()) for t in self.tokens):
                return _identidade(m.group(1).strip())
        raise Problema(401, "nao-autorizado", "Token ausente ou inválido", cabecalhos={"WWW-Authenticate": "Bearer"})

    def segredo(self, cliente: str) -> bytes:
        """Chave que sela o plano do modo tarefas para um cliente, derivada do segredo do servidor: quem chama não a
        conhece, então não assina plano nenhum; só monta o que o servidor entregou a ele."""
        return hmac.digest(self.segredo_plano, b"lince-plano\0" + cliente.encode(), "sha256")

    def kid(self) -> str:
        """Identificador da chave que sela os planos: o mesmo em todas as réplicas com o mesmo LINCE_SEGREDO e outro a
        cada reinício sem ele. Um HMAC de rótulo fixo, truncado: não revela o segredo nem a chave de um cliente."""
        import base64

        return base64.urlsafe_b64encode(hmac.digest(self.segredo_plano, b"lince-kid", "sha256")[:9]).decode()

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

    # ---------------------------------------------------------------- tentativas de senha
    def espera_por_senha(self, cliente, sha256) -> int | None:
        """Segundos até o cliente poder tentar de novo a senha deste arquivo, ou None se pode já. Chamar com a trava."""
        agora, espera = time.time(), 0.0
        for chave, teto in (((cliente, sha256), SENHAS_ERRADAS_POR_ARQUIVO), ((cliente, ""), SENHAS_ERRADAS_POR_TOKEN)):
            erros = self._erros_na_janela(chave, agora)
            if len(erros) >= teto:  # libera quando o erro que completou o teto sai da janela
                espera = max(espera, erros[-teto] + SENHAS_JANELA - agora)
        return max(1, math.ceil(espera)) if espera else None

    def tentar_senha(self, cliente, sha256) -> float:
        """Conta a tentativa como errada já, antes de decifrar: tentativas em paralelo não furam o teto. Devolve a marca
        para `desfazer_tentativa`. Chamar com a trava."""
        agora = time.time()
        for chave in ((cliente, sha256), (cliente, "")):
            self.senhas_erradas.setdefault(chave, deque()).append(agora)
        return agora

    def desfazer_tentativa(self, cliente, sha256, marca, certa):
        """A tentativa não foi senha errada: sai da conta. Com a senha certa, a conta do arquivo recomeça (a do token,
        não). Chamar com a trava."""
        for chave in ((cliente, sha256), (cliente, "")):
            if marca in (erros := self.senhas_erradas.get(chave, ())):
                erros.remove(marca)
            self._erros_na_janela(chave, time.time())
        if certa:
            self.senhas_erradas.pop((cliente, sha256), None)

    def _erros_na_janela(self, chave, agora) -> deque:
        """Chamar com a trava."""
        erros = self.senhas_erradas.get(chave, deque())
        while erros and agora - erros[0] > SENHAS_JANELA:
            erros.popleft()
        if not erros:
            self.senhas_erradas.pop(chave, None)
        return erros

    # ---------------------------------------------------------------- leitura
    def _ler(self, doc):
        sessao = doc["sessao"]
        estado, reservadas = "falhou", 0
        try:
            if sessao.get("apagada"):
                estado = "cancelado"
                return
            doc["estado"], doc["inicio"] = "lendo", time.time()
            # o documento protegido não entra no cache: a transcrição dele não fica guardada nem serve a quem não tem
            # a senha
            cache = CacheComEscopo(self.cache, sessao["cliente"]) if self.cache and not doc.get("protegido") else None
            if sessao.get("modo") == "tarefas":
                teto = min(TAREFAS_POR_DOCUMENTO, sessao.get("max_tarefas") or TAREFAS_POR_DOCUMENTO)
                plano = Leitor(max_leituras=teto).preparar(doc["arquivo"], prazo=sessao.get("prazo"))
                reservadas = self._teto_da_sessao(sessao, plano)
                doc["resultado"] = {
                    "tarefas": plano.para_json()["tarefas"],  # imagem em base64
                    "pendentes": plano.estado.get("pendentes", []) + plano.pendentes,
                    "plano": selar(sessao["segredo"], sessao["cliente"], plano, sessao["kid"]),
                }
                if sessao.get("codigo"):
                    doc["resultado"].update(_codigo_do_documento(doc))
                estado = "pronto"
                return
            leitor = Leitor(ia=sessao["ia"], esforco=sessao["esforco"], cache=cache)
            r = leitor.ler(doc["arquivo"], prazo=sessao.get("prazo"))
            doc["resultado"] = _resultado_api(r)
            if sessao.get("codigo"):
                doc["resultado"].update(_codigo_do_documento(doc))
            estado = "pronto"
        except Exception as e:  # noqa: BLE001 — o erro vira estado do documento, nunca derruba o serviço
            doc["erro"] = f"{type(e).__name__}: {str(e)[:300]}"
            if reservadas:  # o documento não entregou as tarefas (plano grande demais, por exemplo): a cota volta
                with self.trava:
                    sessao["tarefas"] -= reservadas
                reservadas = 0  # devolvida: o finally não devolve de novo
        finally:
            Path(doc["arquivo"]).unlink(missing_ok=True)
            doc["fim"] = time.time()
            self.liberar(sessao["cliente"])
            guardado, real = None, 0
            if doc.get("resultado") is not None:  # para o disco, fora da trava: a memória fica para as leituras
                guardado = os.path.join(self.guardados, f"{doc['id']}.json")
                try:
                    dados = json.dumps(doc.pop("resultado"), ensure_ascii=False).encode()
                    Path(guardado).write_bytes(dados)
                    real = len(dados)
                    del dados
                except Exception as e:  # noqa: BLE001 — disco cheio ou sem memória: o documento falha, nunca fica preso
                    Path(guardado).unlink(missing_ok=True)
                    guardado, estado = None, "falhou"
                    doc["erro"] = f"o resultado não pôde ser guardado: {type(e).__name__}: {str(e)[:200]}"
                    if reservadas:  # as tarefas não foram entregues: a cota da sessão volta
                        with self.trava:
                            sessao["tarefas"] -= reservadas
            with self.trava:
                if sessao["id"] in self.sessoes:
                    # troca a reserva feita na entrada (bytes do arquivo) pelo tamanho real do resultado
                    self._reter(sessao["cliente"], real - doc.get("bytes_retidos", 0))
                    doc["bytes_retidos"], doc["guardado"] = real, guardado
                elif guardado:  # sessão apagada no meio da leitura: o resultado não fica
                    Path(guardado).unlink(missing_ok=True)
                doc["estado"] = estado  # por último: quem lê "pronto" já encontra resultado e fim
                self._fechar_se_terminou(sessao)

    def _teto_da_sessao(self, sessao, plano):
        """Tira do plano as tarefas além do que sobra de TAREFAS_POR_SESSAO, com o motivo nas pendências. Os documentos
        do lote são preparados em paralelo: a conta da sessão é feita sob a trava. Devolve quantas tarefas reservou,
        para o documento que falhar depois devolver a cota."""
        with self.trava:
            cabem = max(0, min(len(plano.tarefas), TAREFAS_POR_SESSAO - sessao.get("tarefas", 0)))
            sessao["tarefas"] = sessao.get("tarefas", 0) + cabem
        if cabem < len(plano.tarefas):
            plano.pendentes += [f"{t.id}: teto de tarefas da sessão" for t in plano.tarefas[cabem:]]
            plano.tarefas = plano.tarefas[:cabem]
            plano.estado["tarefas"] = plano.estado.get("tarefas", [])[:cabem]
        return cabem

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
                # entregue: os bytes deixam de contar no teto já; o arquivo fica só os REENTREGA segundos de repetição
                for d in sessao["itens"]:
                    self._reter(sessao["cliente"], -d.get("bytes_retidos", 0))
                    d["bytes_retidos"] = 0
        return corpo

    def _reter(self, cliente, delta):
        """Chamar com a trava."""
        self.retidos[cliente] = self.retidos.get(cliente, 0) + delta
        if not self.retidos[cliente]:
            del self.retidos[cliente]

    def teto(self, cliente) -> int:
        return self.tetos.get(cliente, BYTES_RETIDOS_POR_CLIENTE)

    def sem_espaco(self, cliente, entrada=0):
        """O 429 de retenção se `entrada` bytes a mais passam do teto do cliente, ou None. Chamar com a trava.
        Retry-After é quando o próximo resultado dele que ninguém buscou vence (a limpeza o descarta); com tudo ainda
        em leitura, 30 s."""
        agora = time.time()
        self._sem_vencidas(agora)  # o que já venceu não conta nem dá Retry-After no passado
        em_uso, teto = self.retidos.get(cliente, 0), self.teto(cliente)
        if em_uso + entrada <= teto:
            return None
        vencem = [  # só o que libera bytes ao vencer: a sessão em que tudo falhou não retém nada
            x["concluida_em"] + ENTREGA_PRAZO - agora
            for x in [*self.sessoes.values(), *self.edicoes.values()]
            if x["cliente"] == cliente and x.get("concluida_em") and not x.get("entregue_em") and _retem(x)
        ]
        espera = max(1, math.ceil(min(vencem))) if vencem else 30
        return _limite_retencao(em_uso, teto, min(espera, ENTREGA_PRAZO))

    def limpar(self):
        agora = time.time()
        with self.trava:
            self._sem_vencidas(agora)
            for chave in list(self.senhas_erradas):
                self._erros_na_janela(chave, agora)

    def _sem_vencidas(self, agora):
        """Remove as sessões e edições vencidas. Chamar com a trava."""
        for sid in [k for k, x in self.sessoes.items() if _vencida(x, agora)]:
            self._remover(sid)
        for eid in [k for k, x in self.edicoes.items() if _vencida(x, agora)]:
            self._remover_edicao(eid)

    def _remover_edicao(self, eid):
        """Chamar com a trava."""
        x = self.edicoes.pop(eid)
        self._reter(x["cliente"], -x.get("bytes_retidos", 0))
        Path(x["guardado"]).unlink(missing_ok=True)

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
        for d in x["itens"]:
            if d.get("guardado"):
                Path(d["guardado"]).unlink(missing_ok=True)
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
def selar(segredo: bytes, cliente: str, plano, kid: str = "") -> str:
    """O plano (texto e estado do documento, sem as imagens) compactado e assinado com a chave do servidor para o
    cliente: a montagem não guarda nada no servidor, só o mesmo token monta, dentro de PLANO_VALIDADE, e ninguém (nem
    o próprio cliente) altera ou forja o plano. A forma e os tetos ainda são conferidos, como rede. `kid` (de
    `Servico.kid`) vai à parte, depois da assinatura: com ele, a montagem distingue o plano selado por outra chave
    (reinício sem LINCE_SEGREDO, réplica com outro segredo) do plano alterado."""
    import base64
    import zlib

    corpo = {"v": 1, "c": cliente, "t": time.time(), "estado": plano.estado, "pendentes": plano.pendentes,
             "tarefas": [t.id for t in plano.tarefas]}  # fmt: skip
    texto = json.dumps(corpo, ensure_ascii=False).encode()
    if len(texto) > PLANO_MAX:  # a montagem recusaria: falha já aqui, antes de quem chama pagar as leituras
        raise ValueError(f"plano de {len(texto) // 2**20} MB, acima do teto de {PLANO_MAX // 2**20} MB da montagem")
    dados = base64.urlsafe_b64encode(zlib.compress(texto, 6)).decode()
    assinado = dados + "." + base64.urlsafe_b64encode(hmac.digest(segredo, dados.encode(), "sha256")).decode()
    return assinado + "." + kid if kid else assinado


def abrir(segredo: bytes, cliente: str, selado: str, kid: str = ""):
    """O plano de `selar`, conferido: assinatura, dono, validade e tamanho. Levanta Problema. O `kid` só diz por que a
    assinatura não conferiu (outra chave ou plano alterado); o plano sem ele (selado antes dele existir) é recusado
    como inválido."""
    import base64
    import binascii
    import zlib

    from .tipos import Plano, TarefaVisual

    dados, _, assinatura = (selado or "").partition(".")
    assinatura, _, do_plano = assinatura.partition(".")
    esperada = base64.urlsafe_b64encode(hmac.digest(segredo, dados.encode("utf-8", "replace"), "sha256")).decode()
    if not dados or not hmac.compare_digest(assinatura.encode("utf-8", "replace"), esperada.encode()):
        # a assinatura vem primeiro: o `kid` não é assinado e só escolhe o motivo da recusa, nunca a decide
        if do_plano and kid and not hmac.compare_digest(do_plano.encode("utf-8", "replace"), kid.encode()):
            raise Problema(
                422,
                "plano-de-outra-chave",
                "Plano de outra chave",
                "Selado por uma chave que este servidor não tem (reinício sem LINCE_SEGREDO ou réplica com outro"
                " segredo): refaça a fase 1.",
            )
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
# no documento: regiões x caracteres de cada página, o que a montagem percorre ao achar o lugar de cada leitura
# (~1 s); um PDF de mil páginas com dez figuras por página de 5 KB usa 50 milhões
PLANO_TRABALHO_MAX = 500_000_000
RESPOSTAS_MAX = 64 * 1024 * 1024  # caracteres somados das respostas de uma montagem


def _montar_pedido(segredo, cliente, corpo, kid=""):
    """O corpo de POST /v1/montagens até o resultado. O plano foi assinado pelo servidor; a forma e os tetos ainda são
    conferidos antes da montagem, como rede, e o que ela não entende vira 422, nunca erro interno."""
    try:
        pedido = json.loads(corpo)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise Problema(400, "montagem-invalida", "O corpo precisa ser JSON", str(e)[:200]) from None
    respostas = pedido.get("respostas") if isinstance(pedido, dict) else None
    if not isinstance(respostas, dict) or not all(isinstance(v, str) for v in respostas.values()):
        raise Problema(400, "montagem-invalida", 'Envie {"plano": ..., "respostas": {id: texto}}')
    if len(respostas) > PLANO_TAREFAS_MAX:
        raise Problema(
            413, "envio-grande", "Respostas grandes demais", f"Até {PLANO_TAREFAS_MAX} respostas por montagem."
        )
    if sum(len(v) for v in respostas.values()) > RESPOSTAS_MAX:
        raise Problema(413, "envio-grande", "Respostas grandes demais", f"Até {RESPOSTAS_MAX // 2**20} M caracteres.")
    plano = abrir(segredo, cliente, str(pedido.get("plano") or ""), kid)
    try:
        _conferir_estado(plano.estado)
        # acima do teto de saída, a montagem sai com o texto nativo e as leituras que não couberem nas pendências
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
    trabalho = 0
    for p in e.get("paginas", []):
        if not isinstance(p.get("md", ""), str) or p.get("rota") not in ("nativa", "regioes", "pagina"):
            raise ValueError("página")
        lista(p.get("regioes", []), dict, PLANO_TAREFAS_MAX, "regiões")
        lista(p.get("tabelas", []), str, PLANO_TAREFAS_MAX, "tabelas")
        lista(p.get("formulas", []), str, PLANO_TAREFAS_MAX, "fórmulas")
        trabalho += len(p.get("regioes", [])) * (len(p.get("md", "")) + 1)
        if trabalho > PLANO_TRABALHO_MAX:
            raise ValueError("regiões e texto demais no documento")


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


def _retem(x) -> bool:
    """Se a sessão ou a edição ainda ocupa bytes do teto do cliente."""
    return x.get("bytes_retidos", 0) > 0 or any(d.get("bytes_retidos", 0) > 0 for d in x.get("itens", ()))


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


_TAMANHO = re.compile(r"(\d+)([KMGT]?)(i?B)?", re.I)


def tokens_do_ambiente(valor: str) -> tuple[list[str], dict[str, int]]:
    """LINCE_TOKENS: tokens separados por vírgula. Cada um é só o token (teto padrão) ou `nome:token:teto`, com o teto
    em bytes ou com K, M, G ou T (base 1024: `2G`, `512M`, `1536MiB`). O nome só ajuda quem lê a configuração. Um token
    com `:` no meio continua valendo sozinho, desde que o último pedaço não seja um tamanho."""
    tokens, tetos = [], {}
    for entrada in (e.strip() for e in valor.split(",")):
        if not entrada:
            continue
        partes = entrada.split(":")
        if len(partes) >= 3 and (m := _TAMANHO.fullmatch(partes[-1].strip())):
            token = ":".join(partes[1:-1]).strip()
            teto = int(m[1]) * 1024 ** " KMGT".index(m[2].upper() or " ")
            if not token or teto <= 0:
                raise ValueError(f"LINCE_TOKENS: entrada inválida (nome:token:teto) para {partes[0].strip()!r}")
            tokens.append(token)
            tetos[token] = teto
        else:
            tokens.append(entrada)
    return tokens, tetos


def _identidade(token: str) -> str:
    """O cliente de um token: um hash curto, que é o que fica em memória e nos logs, nunca o token."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


class _Guardado:
    """O resultado de um documento no disco, lido só na resposta (`_com_resultados`), fora da trava."""

    def __init__(self, caminho):
        self.caminho = caminho


def _com_resultados(corpo):
    """A representação da sessão com o resultado de cada documento lido do disco. Chamar fora da trava."""
    for item in corpo["itens"]:
        if isinstance(guardado := item["resultado"], _Guardado):
            try:
                item["resultado"] = json.loads(Path(guardado.caminho).read_bytes())
            except FileNotFoundError:  # apagada (DELETE ou prazo) entre a trava e a leitura
                raise Problema(404, "sessao-nao-encontrada", "Sessão não encontrada", "Foi apagada.") from None
    return corpo


async def _lidos(corpo):
    """`_com_resultados` numa thread: ler e decodificar o resultado do disco não para o laço de eventos."""
    return await asyncio.to_thread(_com_resultados, corpo)


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
            "resultado": _Guardado(d["guardado"]) if d["estado"] == "pronto" and d.get("guardado") else None,
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
def criar_app(
    tokens: list[str],
    clamav: tuple[str, int] | None = None,
    cache=None,
    segredo: bytes | None = None,
    tetos: dict[str, int] | None = None,
) -> Starlette:
    """`tetos`: o teto de retenção dos tokens que têm um próprio ({token: bytes}); os outros ficam no padrão."""
    s = Servico(tokens, tetos)
    s.clamav, s.cache = clamav, cache
    if segredo:  # o mesmo em todas as réplicas: o plano de uma monta em qualquer outra
        s.segredo_plano = segredo

    async def saude(request):
        corpo = {"status": "ok", "versao": VERSAO, "contrato": CONTRATO["info"]["version"]}
        if (residente := memoria.rss()) is not None:
            corpo["memoria"] = {"rss_bytes": residente}
        return JSONResponse(corpo)

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
            cheio = s.sem_espaco(cliente)
        if cheio and chave_idem is None:  # a repetição idempotente passa: ela entrega e libera o teto
            raise cheio
        if not s.reservar(cliente, 1):
            raise _limite_pendentes()
        reservadas, sessao, form = 1, None, None
        try:
            form = await request.form(max_files=ARQUIVOS_MAX, max_fields=ARQUIVOS_MAX + 6)
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
            max_tarefas = form.get("max_tarefas")
            if max_tarefas is not None and max_tarefas != "":
                digitos = str(max_tarefas).lstrip("0")
                if not re.fullmatch(r"\d+", str(max_tarefas)) or not digitos or modo != "tarefas":
                    raise Problema(
                        400, "max-tarefas-invalido", "max_tarefas deve ser um inteiro maior que zero, no modo tarefas"
                    )
                # qualquer inteiro maior que zero, e o acima do teto vale o teto (contrato); dez algarismos já passam
                # dele, então o resto nem é convertido (int() de milhares de algarismos é lento ou recusado)
                max_tarefas = min(TAREFAS_POR_DOCUMENTO, int(digitos[:10]))
            else:
                max_tarefas = None
            pedir_codigo = str(form.get("codigo") or "nao")
            if pedir_codigo not in ("sim", "nao"):
                raise Problema(400, "codigo-invalido", "codigo deve ser 'sim' ou 'nao'")
            senha = form.get("senha") or None  # vazia é sem senha, como os outros campos
            if senha is not None and (not isinstance(senha, str) or len(senha) > SENHA_MAX):
                raise Problema(400, "senha-invalida", f"senha deve ser texto de até {SENHA_MAX} caracteres")
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
                modo=modo, segredo=s.segredo(cliente), kid=s.kid(), max_tarefas=max_tarefas, senha=senha,
            )  # fmt: skip
            senha = None  # a senha só abriu os arquivos: não fica com o pedido
        finally:
            if form is not None:
                await form.close()  # o spool do multipart sai do disco já: o documento foi copiado
            if sessao is None or sessao.get("repetida"):
                s.liberar(cliente, reservadas)  # não entrou na fila (erro, exceção ou repetição idempotente)
        if sessao.get("repetida"):
            return JSONResponse(await _lidos(s.entregar(sessao["original"])), status_code=200)
        if (t := _espera(request)) > 0:
            await _aguardar(sessao["pronta"], t)
        local = str(request.url_for("sessao", id=sessao["id"]))
        # cliente que desistiu da espera não recebe nada: a sessão não conta como entregue
        entregue = await _lidos(s.entregar(sessao, marcar=not await request.is_disconnected()))
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
            with s.trava:
                corpo = _sessao(sessao)
            return JSONResponse(await _lidos(corpo))
        return JSONResponse(await _lidos(s.entregar(sessao, marcar=not await request.is_disconnected())))

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
            cheio = s.sem_espaco(cliente)
        if cheio:
            raise cheio
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
        guardado = os.path.join(s.guardados, f"edicao-{eid}")  # no disco, como os resultados: fora da memória
        edicao = {"id": eid, "cliente": cliente, "nome": nome, "guardado": guardado, "tamanho": len(novo),
                  "criada_em": agora, "concluida_em": agora, "bytes_retidos": len(novo)}  # fmt: skip
        with s.trava:
            if cheio := s.sem_espaco(cliente, len(novo)):
                raise cheio
            s._reter(cliente, len(novo))
        try:
            await asyncio.to_thread(Path(guardado).write_bytes, novo)
        except Exception:
            with s.trava:
                s._reter(cliente, -len(novo))
            Path(guardado).unlink(missing_ok=True)
            raise
        del novo
        with s.trava:
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
                s._reter(cliente, -x["bytes_retidos"])  # e deixa de contar no teto já
                x["bytes_retidos"] = 0
        tipo = MIDIA.get(Path(x["nome"]).suffix.lower(), "application/octet-stream")
        nome_seguro = re.sub(r"[^A-Za-z0-9._-]", "_", x["nome"]) or "documento"
        try:
            dados = await asyncio.to_thread(Path(x["guardado"]).read_bytes) if request.method == "GET" else b""
        except FileNotFoundError:  # apagada (DELETE ou prazo) entre a trava e a leitura
            raise Problema(404, "edicao-nao-encontrada", "Edição não encontrada", "Foi apagada.") from None
        return Response(
            dados,
            media_type=tipo,
            headers={
                "Content-Disposition": f'attachment; filename="{nome_seguro}"',
                "Content-Length": str(x["tamanho"]),
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
            cheio = s.sem_espaco(cliente)
        if cheio:
            raise cheio
        if not s.reservar(cliente, 1):  # a vaga antes do corpo: pedidos em paralelo não somam memória sem teto
            raise _limite_pendentes()
        na_fila = False
        try:
            corpo = await request.body()
            segredo, kid = s.segredo(cliente), s.kid()
            # leitura do JSON, abertura do plano e montagem na fila justa das montagens, fora do laço de eventos; a
            # vaga volta quando a montagem termina, mesmo que quem pediu desista antes
            na_fila = True
            r = await _na_fila(
                s.montagens, cliente, lambda: _montar_pedido(segredo, cliente, corpo, kid), lambda: s.liberar(cliente)
            )
        finally:
            if not na_fila:
                s.liberar(cliente)
        return JSONResponse(_resultado_api(r))

    async def limpeza(app):
        async def laco():
            while True:
                await asyncio.sleep(LIMPEZA_INTERVALO)
                s.limpar()
                # a memória que as leituras liberaram volta ao sistema: uma vez por intervalo, fora de trava e do laço
                await asyncio.to_thread(memoria.devolver)

        tarefa = asyncio.create_task(laco())
        yield
        tarefa.cancel()
        shutil.rmtree(s.guardados, ignore_errors=True)  # os resultados guardados não sobrevivem ao serviço

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


async def _na_fila(fila: Fila, cliente, trabalho, ao_terminar):
    """O resultado de `trabalho()`, executado na `fila` na vez do cliente. A conexão que cai não interrompe a espera: o
    Starlette não cancela o handler, então o trabalho roda na vez dele e a resposta se perde no envio. Só a espera
    cancelada (a tarefa do pedido cancelada, como no desligamento do servidor) pula o que ainda não começou.
    `ao_terminar` roda sempre, ao fim ou ao pular."""
    laco, desistiu = asyncio.get_running_loop(), threading.Event()
    futuro = laco.create_future()

    def entregar(valor, erro):
        if not futuro.done():  # quem desistiu já cancelou o futuro
            futuro.set_exception(erro) if erro else futuro.set_result(valor)

    def executar():
        valor = erro = None
        try:
            if not desistiu.is_set():
                valor = trabalho()
        except Exception as e:  # noqa: BLE001 — volta para quem espera, que trata como trataria em linha
            erro = e
        finally:
            ao_terminar()
        laco.call_soon_threadsafe(entregar, valor, erro)

    fila.por(cliente, executar)
    try:
        return await futuro
    except asyncio.CancelledError:
        desistiu.set()
        raise


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


async def _abrir(s: Servico, cliente, d, senha, pasta) -> bool:
    """Arquivo protegido por senha: sem senha, 422 `arquivo-protegido`; com a senha errada, 422 `senha-incorreta` (e,
    errando demais, 429 `tentativas-de-senha`); com a certa, a cópia decifrada toma o lugar do arquivo recebido, na
    pasta da sessão (apagada com ela), e passa pelo antivírus como ele. Devolve se o arquivo estava protegido. A
    detecção e a decifração rodam fora do laço de eventos; a senha não vai para a sessão, o log nem o erro."""
    if senha is None:
        if formato := await asyncio.to_thread(protecao.formato_protegido, d["arquivo"]):
            raise _protegido(d["nome"], formato)
        return False
    with s.trava:
        espera = s.espera_por_senha(cliente, d["sha256"])
        marca = None if espera else s.tentar_senha(cliente, d["sha256"])
    if espera:
        raise Problema(
            429,
            "tentativas-de-senha",
            "Tentativas de senha demais",
            f"{d['nome']!r}: até {SENHAS_ERRADAS_POR_ARQUIVO} senhas erradas por arquivo e {SENHAS_ERRADAS_POR_TOKEN}"
            f" por token em {SENHAS_JANELA // 60} min.",
            {"Retry-After": str(espera)},
            extras={"arquivo": d["nome"]},
        )
    errada, lido = False, None
    try:
        # CPU e memória de várias vezes o arquivo: na fila justa das decifrações, com poucas vagas
        lido = await _na_fila(s.decifracoes, cliente, lambda: protecao.aberto(d["arquivo"], senha, pasta), lambda: None)
    except protecao.SenhaIncorreta as e:
        errada = True  # fica na conta
        raise Problema(
            422, "senha-incorreta", "Senha incorreta", f"{d['nome']!r}: a senha não abre o arquivo.",
            extras={"arquivo": d["nome"], "formato": e.formato},
        ) from None  # fmt: skip
    except protecao.DocumentoProtegido as e:
        raise _protegido(d["nome"], e.formato, e.motivo) from None
    except DocumentoRecusado as e:  # o decifrado passa dos tetos (zip bomb dentro do ODF, por exemplo)
        raise Problema(422, "documento-recusado", "Documento recusado", f"{d['nome']!r}: {e}") from None
    finally:
        if not errada:
            with s.trava:
                s.desfazer_tentativa(cliente, d["sha256"], marca, certa=lido is not None and lido != d["arquivo"])
    if lido == d["arquivo"]:  # não pedia senha: a enviada não serve para nada
        return False
    os.replace(lido, d["arquivo"])  # o cifrado sai já; o decifrado é apagado assim que lido, como todo arquivo
    os.rmdir(pasta)
    await _varrer(s, d["nome"], d["arquivo"])  # o que chega aos leitores é o decifrado: ele também passa pelo antivírus
    return True


def _protegido(nome, formato, motivo=""):
    detalhe = f"{nome!r}: envie a senha no campo 'senha'." if not motivo else f"{nome!r}: {motivo}."
    return Problema(
        422, "arquivo-protegido", "Arquivo protegido por senha", detalhe, extras={"arquivo": nome, "formato": formato}
    )


async def _receber(
    s: Servico, cliente, arquivos, referencia, esforco, ia, chave_idem, codigo=False, prazo=None, modo="leitura",
    segredo=b"", kid="", max_tarefas=None, senha=None,
):  # fmt: skip
    """Grava o lote, calcula hash e tamanho, aplica a idempotência, abre o que é protegido por senha e põe os documentos
    na fila."""
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
        if max_tarefas is not None:  # só entra na impressão quando enviado: a de um envio sem ele não muda
            conteudo.append(max_tarefas)
        if senha is not None:  # só que veio senha, nunca ela nem um hash dela (a impressão fica com a sessão)
            conteudo.append("com senha")
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
        for k, d in enumerate(itens):  # protegido: recusado aqui, no pedido, ou decifrado para a leitura
            d["protegido"] = await _abrir(s, cliente, d, senha, os.path.join(pasta, f"aberto-{k}"))
        with s.trava:
            if chave_idem and (sid := s.idempotencia.get((cliente, chave_idem))) and sid in s.sessoes:
                original = s.sessoes[sid]
                if original["impressao"] != impressao:
                    raise Problema(422, "idempotency-key-reutilizada", "Idempotency-Key já usada com outro conteúdo")
                shutil.rmtree(pasta, ignore_errors=True)
                return {"repetida": True, "original": original}
            entrada = sum(d["bytes"] for d in itens)
            if cheio := s.sem_espaco(cliente, entrada):
                # conferido de novo sob a trava, junto com a reserva: envios simultâneos não furam o teto
                raise cheio
            sessao = {
                "id": uuid.uuid4().hex,
                "cliente": cliente,
                "referencia": referencia,
                "esforco": esforco,
                "prazo": prazo,
                "codigo": codigo,
                "modo": modo,
                "segredo": segredo,
                "kid": kid,
                "max_tarefas": max_tarefas,
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
    try:
        tokens, tetos = tokens_do_ambiente(os.environ.get("LINCE_TOKENS", ""))
    except ValueError as e:
        raise SystemExit(str(e)) from None
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
    segredo = os.environ.get("LINCE_SEGREDO", "")
    if segredo and len(segredo) < 32:
        raise SystemExit("LINCE_SEGREDO precisa de pelo menos 32 caracteres aleatórios.")
    import uvicorn

    memoria.ajustar_alocador()  # uma vez, antes das threads do serviço (memoria.py); quem embute o app decide o seu
    app = criar_app(tokens, clamav, cache, segredo.encode() or None, tetos)
    uvicorn.run(app, host=a.host, port=a.porta, proxy_headers=True, log_level="info")


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
