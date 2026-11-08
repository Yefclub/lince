"""API HTTP: cada resposta é conferida contra o contrato OpenAPI; os testes provam sessão, lote, referência,
entrega única (o documento não fica guardado), idempotência, isolamento entre tokens, limites e proteção do
provedor."""

import json
import threading
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate as validar_openapi
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from starlette.testclient import TestClient

from lince.servidor import PENDENTES_POR_CLIENTE, Fila, Servico, criar_app

CONTRATO = json.loads(files("lince").joinpath("openapi.json").read_text(encoding="utf-8"))
REGISTRO = Registry().with_resource("urn:contrato", Resource.from_contents(CONTRATO, default_specification=DRAFT202012))
A = {"Authorization": "Bearer token-a"}
B = {"Authorization": "Bearer token-b"}
ESPERA = {"Prefer": "wait=30"}


def conforme(resp, caminho, metodo):
    """A resposta tem de estar no contrato: status declarado, tipo de conteúdo e corpo válidos."""
    op = CONTRATO["paths"][caminho][metodo]
    status = str(resp.status_code)
    if status not in op["responses"]:
        assert resp.status_code >= 400, f"{metodo.upper()} {caminho} devolveu {status}, que não está no contrato"
        status = "default"  # erro não listado tem de seguir a resposta padrão (problem+json)
    declarada = op["responses"][status]
    ponteiro = ["paths", caminho, metodo, "responses", status]
    if "$ref" in declarada:
        ponteiro = declarada["$ref"].lstrip("#/").split("/")
        declarada = CONTRATO["components"]["responses"][ponteiro[-1]]
    assert resp.headers.get("x-request-id")
    for nome, h in declarada.get("headers", {}).items():
        assert not h.get("required") or nome.lower() in resp.headers, f"cabeçalho {nome} obrigatório ausente"
    if "content" not in declarada:
        assert not resp.content
        return resp
    tipo = resp.headers["content-type"].split(";")[0]
    assert tipo in declarada["content"], f"tipo {tipo} fora do contrato"
    # valida pelo ponteiro dentro do contrato: os $ref internos do esquema resolvem contra o documento inteiro
    partes = [*ponteiro, "content", tipo, "schema"]
    esquema = {"$ref": "urn:contrato#/" + "/".join(x.replace("~", "~0").replace("/", "~1") for x in partes)}
    Draft202012Validator(esquema, registry=REGISTRO).validate(resp.json())
    return resp


@pytest.fixture
def api():
    return TestClient(criar_app(["token-a", "token-b"]))


def _pdf_texto(tmp_path, texto, nome="aviso.pdf"):
    p = tmp_path / nome
    c = canvas.Canvas(str(p), pagesize=A4)
    c.drawString(60, 780, texto)
    c.save()
    return p.read_bytes()


@pytest.fixture
def pdf(tmp_path):
    return _pdf_texto(tmp_path, "Aviso de manutenção da balança rodoviária em 12/11.")


def parado(monkeypatch):
    """Documentos ficam na fila sem terminar: para testar o que acontece antes da entrega."""
    monkeypatch.setattr(Servico, "_ler", lambda self, doc: None)


def concluida(api, sid):
    """Espera a sessão terminar sem buscá-la (buscar é entregar)."""
    assert api.app.state.servico.sessoes[sid]["pronta"].wait(30)


def enviar(api, arquivos, cab, **campos):
    if not isinstance(arquivos, list):
        arquivos = [arquivos]
    return api.post("/v1/sessoes", files=[("arquivo", a) for a in arquivos], data=campos, headers=cab)


# ------------------------------------------------------------------ contrato
def test_contrato_e_openapi_valido_e_cobre_todas_as_rotas(api):
    validar_openapi(CONTRATO)
    servido = api.get("/v1/openapi.json")
    assert servido.status_code == 200 and servido.json() == CONTRATO
    rotas = {r.path.replace("{id:str}", "{id}") for r in api.app.routes}
    assert rotas == set(CONTRATO["paths"])


def test_saude_informa_versao_do_contrato(api):
    r = conforme(api.get("/saude"), "/saude", "get")
    assert r.json()["contrato"] == CONTRATO["info"]["version"]


# ------------------------------------------------------------------ sessão
def test_envio_com_espera_entrega_o_resultado_e_nao_guarda_nada(api, pdf):
    r = conforme(enviar(api, ("a.pdf", pdf), {**A, **ESPERA}, referencia="usuario:42"), "/v1/sessoes", "post")
    assert r.status_code == 201 and r.headers["location"].endswith(r.json()["id"])
    s = r.json()
    assert s["estado"] == "concluida" and s["referencia"] == "usuario:42"
    assert s["documentos"] == {"total": 1, "prontos": 1, "falhos": 0}
    assert "balança rodoviária em 12/11" in s["itens"][0]["resultado"]["markdown"]
    # entregue: dá para pedir de novo enquanto dura a janela; o DELETE confirma e apaga na hora
    assert api.get(f"/v1/sessoes/{s['id']}", headers=A).status_code == 200
    assert api.delete(f"/v1/sessoes/{s['id']}", headers=A).status_code == 204
    assert api.get(f"/v1/sessoes/{s['id']}", headers=A).status_code == 404
    assert api.app.state.servico.sessoes == {} and api.app.state.servico.retidos == {}


def test_lote_vira_uma_sessao_com_um_item_por_arquivo(api, tmp_path):
    lote = [(f"doc{k}.pdf", _pdf_texto(tmp_path, f"Documento número {k}.", f"d{k}.pdf")) for k in range(3)]
    s = conforme(enviar(api, lote, {**A, **ESPERA}), "/v1/sessoes", "post").json()
    assert [i["nome"] for i in s["itens"]] == ["doc0.pdf", "doc1.pdf", "doc2.pdf"]
    assert all(f"Documento número {k}" in i["resultado"]["markdown"] for k, i in enumerate(s["itens"]))
    assert len({i["sha256"] for i in s["itens"]}) == 3


def test_sem_espera_a_entrega_acontece_na_consulta(api, pdf):
    import lince.servidor as srv

    sid = conforme(enviar(api, ("a.pdf", pdf), A), "/v1/sessoes", "post").json()["id"]
    concluida(api, sid)
    final = conforme(api.get(f"/v1/sessoes/{sid}", headers=A), "/v1/sessoes/{id}", "get")
    assert final.json()["estado"] == "concluida" and final.json()["itens"][0]["resultado"]
    # resposta perdida: a repetição dentro da janela recebe o mesmo resultado
    repetida = conforme(api.get(f"/v1/sessoes/{sid}", headers=A), "/v1/sessoes/{id}", "get")
    assert repetida.json()["itens"] == final.json()["itens"]
    api.app.state.servico.sessoes[sid]["entregue_em"] -= srv.REENTREGA + 1  # passou a janela
    assert conforme(api.get(f"/v1/sessoes/{sid}", headers=A), "/v1/sessoes/{id}", "get").status_code == 404
    assert api.app.state.servico.retidos == {}


def test_sessoes_nao_entregues_sao_achadas_pela_referencia_do_sistema_de_origem(api, pdf):
    ids = [
        enviar(api, ("a.pdf", pdf), A, referencia=ref).json()["id"] for ref in ("usuario:1", "usuario:2", "usuario:1")
    ]
    for sid in ids:
        concluida(api, sid)
    r = conforme(api.get("/v1/sessoes", params={"referencia": "usuario:1"}, headers=A), "/v1/sessoes", "get")
    sessoes = r.json()["sessoes"]
    assert len(sessoes) == 2 and {x["referencia"] for x in sessoes} == {"usuario:1"}
    assert "itens" not in sessoes[0]  # a lista é resumo: não entrega nem apaga
    assert api.get(f"/v1/sessoes/{sessoes[0]['id']}", headers=A).status_code == 200


def test_apagar_cancela_a_sessao(api, pdf, monkeypatch):
    parado(monkeypatch)
    api = TestClient(criar_app(["token-a"]))
    sid = enviar(api, ("a.pdf", pdf), A).json()["id"]
    conforme(api.delete(f"/v1/sessoes/{sid}", headers=A), "/v1/sessoes/{id}", "delete")
    assert conforme(api.get(f"/v1/sessoes/{sid}", headers=A), "/v1/sessoes/{id}", "get").status_code == 404
    assert api.app.state.servico.retidos == {}


def test_documento_que_falha_vira_estado_e_o_lote_continua(api, pdf):
    s = enviar(api, [("ruim.pdf", b"isto nao e um pdf"), ("bom.pdf", pdf)], {**A, **ESPERA}).json()
    assert [i["estado"] for i in s["itens"]] == ["falhou", "pronto"]
    assert s["itens"][0]["erro"] and s["documentos"]["falhos"] == 1


# ------------------------------------------------------------------ idempotência
def test_repeticao_com_a_mesma_chave_devolve_a_mesma_sessao(pdf, monkeypatch):
    parado(monkeypatch)
    api = TestClient(criar_app(["token-a"]))
    cab = {**A, "Idempotency-Key": "envio-123"}
    primeira = conforme(enviar(api, ("a.pdf", pdf), cab), "/v1/sessoes", "post")
    segunda = conforme(enviar(api, ("a.pdf", pdf), cab), "/v1/sessoes", "post")
    assert primeira.status_code == 201 and segunda.status_code == 200
    assert segunda.json()["id"] == primeira.json()["id"]
    assert len(api.get("/v1/sessoes", headers=A).json()["sessoes"]) == 1


def test_mesma_chave_com_outro_conteudo_e_recusada(pdf, tmp_path, monkeypatch):
    parado(monkeypatch)
    api = TestClient(criar_app(["token-a"]))
    cab = {**A, "Idempotency-Key": "envio-123"}
    enviar(api, ("a.pdf", pdf), cab)
    r = conforme(enviar(api, ("b.pdf", _pdf_texto(tmp_path, "Outro.", "b.pdf")), cab), "/v1/sessoes", "post")
    assert r.status_code == 422 and r.json()["type"] == "urn:lince:erro:idempotency-key-reutilizada"


# ------------------------------------------------------------------ isolamento e erros
def test_sem_token_valido_nao_entra(api, pdf):
    r = conforme(enviar(api, ("a.pdf", pdf), {}), "/v1/sessoes", "post")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert (
        conforme(enviar(api, ("a.pdf", pdf), {"Authorization": "Bearer x"}), "/v1/sessoes", "post").status_code == 401
    )
    especial = {"Authorization": "Bearer é".encode("latin-1")}
    assert conforme(enviar(api, ("a.pdf", pdf), especial), "/v1/sessoes", "post").status_code == 401


def test_token_nao_ve_sessao_nem_referencia_de_outro(api, pdf):
    sid = enviar(api, ("a.pdf", pdf), A, referencia="usuario:1").json()["id"]
    concluida(api, sid)
    assert conforme(api.get(f"/v1/sessoes/{sid}", headers=B), "/v1/sessoes/{id}", "get").status_code == 404
    assert conforme(api.delete(f"/v1/sessoes/{sid}", headers=B), "/v1/sessoes/{id}", "delete").status_code == 404
    assert api.get("/v1/sessoes", params={"referencia": "usuario:1"}, headers=B).json()["sessoes"] == []
    assert api.get(f"/v1/sessoes/{sid}", headers=A).status_code == 200  # a tentativa de B não entregou nada


@pytest.mark.parametrize(
    ("arquivos", "campos", "cab", "codigo"),
    [
        ([("documento", b"x")], {}, {}, "extensao-ausente"),
        ([("a.pdf", b"x")], {"esforco": "turbo"}, {}, "esforco-invalido"),
        ([("a.pdf", b"x")], {"prazo": "0"}, {}, "prazo-invalido"),
        ([("a.pdf", b"x")], {"prazo": "3601"}, {}, "prazo-invalido"),
        ([("a.pdf", b"x")], {"prazo": "1.5"}, {}, "prazo-invalido"),
        ([("a.pdf", b"x")], {"referencia": "nome com espaço"}, {}, "referencia-invalida"),
        ([("a.pdf", b"x")], {}, {"X-IA-URL": "http://x/v1"}, "provedor-incompleto"),
        ([("a.pdf", b"x")], {}, {"X-IA-URL": "file:///etc/passwd", "X-IA-Modelo": "m"}, "provedor-invalido"),
        ([("a.pdf", b"x")], {}, {"X-IA-URL": "http://interno:8080/admin?x=", "X-IA-Modelo": "m"}, "provedor-invalido"),
    ],
)
def test_pedido_invalido_explica_o_motivo_no_formato_do_contrato(api, arquivos, campos, cab, codigo):
    r = conforme(enviar(api, arquivos, {**A, **cab}, **campos), "/v1/sessoes", "post")
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["type"] == f"urn:lince:erro:{codigo}" and r.json()["instance"] == r.headers["x-request-id"]


def test_request_id_enviado_volta_na_resposta(api):
    assert api.get("/saude", headers={"X-Request-Id": "orbital-abc.1"}).headers["x-request-id"] == "orbital-abc.1"
    assert api.get("/saude", headers={"X-Request-Id": "invalido com espaco"}).headers["x-request-id"] != ""


def test_resposta_nao_fica_em_cache_intermediario(api, pdf):
    r = enviar(api, ("a.pdf", pdf), {**A, **ESPERA})
    assert r.headers["cache-control"] == "no-store"


# ------------------------------------------------------------------ limites e fila
def test_envios_paralelos_nao_furam_o_teto_de_pendentes():
    from concurrent.futures import ThreadPoolExecutor

    s = Servico(["t"])
    with ThreadPoolExecutor(32) as ex:
        aceitos = sum(ex.map(lambda _: s.reservar("c"), range(PENDENTES_POR_CLIENTE * 3)))
    assert aceitos == PENDENTES_POR_CLIENTE
    assert s.reservar("outro")  # o teto é por token


def test_planilhas_em_texto_sao_lidas_com_separador_e_codificacao_detectados(api):
    csv = 'nome;valor;obs\nJoão;1,5;"a;b"\n'.encode("latin-1")
    s = conforme(
        enviar(api, [("dados.csv", csv), ("dados.tsv", b"a\tb\n1\t2\n")], {**A, **ESPERA}), "/v1/sessoes", "post"
    )
    s = s.json()
    assert [i["estado"] for i in s["itens"]] == ["pronto", "pronto"]
    assert "| João | 1,5 | a;b |" in s["itens"][0]["resultado"]["markdown"]
    assert "| 1 | 2 |" in s["itens"][1]["resultado"]["markdown"]
    perfil = s["itens"][0]["resultado"]["estrutura"]["perfil_tabular"]  # o contrato valida a resposta
    assert perfil[0]["aba"] is None and [c["tipo"] for c in perfil[0]["colunas"]] == ["texto", "numero", "texto"]


def test_formato_nao_suportado_recusa_o_lote_antes_de_entrar_na_fila(api, pdf):
    r = conforme(enviar(api, [("bom.pdf", pdf), ("mensagem.eml", b"From: a@b\n\noi")], A), "/v1/sessoes", "post")
    assert r.status_code == 415 and r.json()["type"] == "urn:lince:erro:formato-nao-suportado"
    assert "mensagem.eml" in r.json()["detail"] and ".pdf" in r.json()["detail"]
    assert api.app.state.servico.sessoes == {} and api.app.state.servico.vagas == {}


def test_pedido_recusado_devolve_a_vaga(api, pdf):
    for _ in range(PENDENTES_POR_CLIENTE + 10):  # se a vaga vazasse, viria 429
        assert enviar(api, ("sem_extensao", pdf), A).status_code == 400
    assert enviar(api, ("a.pdf", pdf), A).status_code == 201


def test_fila_justa_nao_deixa_um_cliente_travar_o_outro():
    ordem, liberar = [], threading.Event()

    def executar(t):
        if t == "primeiro":
            liberar.wait(5)
        ordem.append(t)

    fila = Fila(executar, 1)
    fila.por("a", "primeiro")
    for k in range(5):
        fila.por("a", f"a{k}")
    fila.por("b", "b0")
    liberar.set()
    for _ in range(100):
        if len(ordem) == 7:
            break
        threading.Event().wait(0.02)
    assert ordem.index("b0") <= 2  # b entra logo, não depois dos 5 de a


# ------------------------------------------------------------------ provedor de IA
def _provedor(texto, status=200, local=None, demora=0):
    """Provedor OpenAI-compatível falso que responde sempre o mesmo texto (ou um redirect)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        chamadas = 0

        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            type(self).chamadas += 1
            threading.Event().wait(demora)
            if status != 200:
                self.send_response(status)
                self.send_header("Location", local or "/")
                self.end_headers()
                return
            out = json.dumps({"choices": [{"message": {"content": texto}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, H, f"http://127.0.0.1:{srv.server_port}/v1"


@pytest.fixture
def pdf_com_figura(tmp_path):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (900, 500), "white")
    ImageDraw.Draw(img).rectangle([100, 100, 300, 450], fill="navy")
    img.save(tmp_path / "g.png")
    p = tmp_path / "grafico.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    c.drawString(60, 780, "Produção anual por unidade.")
    c.drawImage(str(tmp_path / "g.png"), 60, 400, width=450, height=250)
    c.save()
    return p.read_bytes()


def test_redirect_do_provedor_nao_e_seguido(pdf_com_figura):
    destino, chamado, url_destino = _provedor("não devia chegar aqui")
    origem, _, url_origem = _provedor("", status=302, local=url_destino + "/chat/completions")
    api = TestClient(criar_app(["token-a"]))
    r = enviar(api, ("g.pdf", pdf_com_figura), {**A, **ESPERA, "X-IA-URL": url_origem, "X-IA-Modelo": "m"})
    origem.shutdown()
    destino.shutdown()
    assert chamado.chamadas == 0
    assert r.json()["itens"][0]["resultado"]["cobertura"]["pendentes"]


def test_prazo_entrega_o_nativo_na_hora_e_marca_a_leitura_que_nao_coube(pdf_com_figura):
    import time

    srv, contador, url = _provedor("Gráfico: Lapa 3500.", demora=3)
    api = TestClient(criar_app(["token-a"]))
    inicio = time.monotonic()
    r = enviar(api, ("g.pdf", pdf_com_figura), {**A, **ESPERA, "X-IA-URL": url, "X-IA-Modelo": "m"}, prazo="1")
    gasto = time.monotonic() - inicio
    srv.shutdown()
    resultado = conforme(r, "/v1/sessoes", "post").json()["itens"][0]["resultado"]
    assert gasto < 2.5  # não esperou os 3 s da IA
    assert "Produção anual por unidade." in resultado["markdown"]  # o texto nativo saiu completo
    assert any("prazo esgotado" in p for p in resultado["cobertura"]["pendentes"])


def test_cache_do_servidor_vale_entre_envios(pdf_com_figura):
    from lince import CacheMemoria

    srv, contador, url = _provedor("Gráfico de barras: Lapa 3500.")
    api = TestClient(criar_app(["token-a"], cache=CacheMemoria()))
    cab = {**A, **ESPERA, "X-IA-URL": url, "X-IA-Modelo": "m"}
    primeiro = enviar(api, ("g.pdf", pdf_com_figura), cab).json()["itens"][0]["resultado"]
    segundo = enviar(api, ("g.pdf", pdf_com_figura), cab).json()["itens"][0]["resultado"]
    srv.shutdown()
    assert contador.chamadas == 1  # o segundo envio não paga a mesma imagem
    assert not primeiro["uso"][0]["cache"] and segundo["uso"][0]["cache"]
    assert "Lapa 3500" in segundo["markdown"]
    assert segundo["estrutura"]["paginas"] == 1  # a estrutura vem junto na API (e o contrato a valida)


def test_cache_do_servidor_e_separado_por_token(pdf_com_figura):
    from lince import CacheMemoria

    srv, contador, url = _provedor("Gráfico de barras: Lapa 3500.")
    api = TestClient(criar_app(["token-a", "token-b"], cache=CacheMemoria()))
    ia = {**ESPERA, "X-IA-URL": url, "X-IA-Modelo": "m"}
    enviar(api, ("g.pdf", pdf_com_figura), {**A, **ia})
    outro = enviar(api, ("g.pdf", pdf_com_figura), {**B, **ia}).json()["itens"][0]["resultado"]
    de_novo = enviar(api, ("g.pdf", pdf_com_figura), {**A, **ia}).json()["itens"][0]["resultado"]
    srv.shutdown()
    # o token B não aproveita (nem descobre) a leitura do A; o A aproveita a própria
    assert contador.chamadas == 2 and not outro["uso"][0]["cache"] and de_novo["uso"][0]["cache"]


def test_retry_after_do_provedor_tem_teto():
    from lince.provedores import ESPERA_MAX_PROVEDOR, _retry_after

    assert _retry_after("99999999") == ESPERA_MAX_PROVEDOR
    assert _retry_after("Wed, 21 Oct 2026 07:28:00 GMT") == 0
    assert _retry_after("2") == 2


# ------------------------------------------------------------------ retenção, expiração e erros fora das rotas


def test_resultado_nao_buscado_no_prazo_e_descartado(api, pdf):
    import lince.servidor as srv

    sid = enviar(api, ("a.pdf", pdf), A).json()["id"]
    concluida(api, sid)
    api.app.state.servico.sessoes[sid]["concluida_em"] -= srv.ENTREGA_PRAZO + 1
    assert conforme(api.get(f"/v1/sessoes/{sid}", headers=A), "/v1/sessoes/{id}", "get").status_code == 404
    assert api.app.state.servico.retidos == {}


def test_metodo_errado_informa_todos_os_metodos_aceitos(api):
    r = conforme(api.put("/v1/sessoes", headers=A), "/v1/sessoes", "get")
    assert r.status_code == 405 and {"GET", "POST"} <= set(r.headers["allow"].replace(" ", "").split(","))
    assert r.json()["type"] == "urn:lince:erro:metodo-nao-permitido"


def test_rota_inexistente_e_erro_interno_saem_no_formato_do_contrato(api, monkeypatch):
    r = api.get("/v1/nada")
    assert r.status_code == 404 and r.json()["type"] == "urn:lince:erro:rota-inexistente"
    assert r.headers["content-type"].startswith("application/problem+json")

    def quebra(self, request):
        raise RuntimeError("defeito")

    monkeypatch.setattr(Servico, "cliente", quebra)
    falha = TestClient(api.app, raise_server_exceptions=False).get("/v1/sessoes", headers={"X-Request-Id": "rid-9"})
    assert falha.status_code == 500 and falha.headers["x-request-id"] == "rid-9"
    assert falha.json() == {
        "type": "urn:lince:erro:interno",
        "title": "Erro interno",
        "status": 500,
        "instance": "rid-9",
    }
    assert "defeito" not in falha.text  # detalhe interno não vaza


def test_mesma_chave_com_outro_modelo_e_recusada(pdf, monkeypatch):
    parado(monkeypatch)
    srv_ia, _, url = _provedor("lido")
    api = TestClient(criar_app(["token-a"]))
    cab = {**A, "Idempotency-Key": "k1", "X-IA-URL": url}
    enviar(api, ("a.pdf", pdf), {**cab, "X-IA-Modelo": "modelo-1"})
    r = enviar(api, ("a.pdf", pdf), {**cab, "X-IA-Modelo": "modelo-2"})
    srv_ia.shutdown()
    assert r.status_code == 422


def test_prefer_so_aceita_a_preferencia_wait_exata(api, pdf):
    from lince.servidor import _espera

    class P:
        def __init__(self, v):
            self.headers = {"prefer": v}

    assert _espera(P("wait=10")) == 10
    assert _espera(P("respond-async, wait=5")) == 5
    assert _espera(P("x-nowait=3")) == 0
    assert _espera(P("wait=abc")) == 0


def test_cabecalho_do_provedor_grande_demais_e_recusado(api, pdf):
    r = enviar(api, ("a.pdf", pdf), {**A, "X-IA-URL": "http://x/v1", "X-IA-Modelo": "m" * 201})
    assert conforme(r, "/v1/sessoes", "post").status_code == 400


def test_head_nao_conta_como_entrega_nem_apaga(api, pdf):
    sid = enviar(api, ("a.pdf", pdf), A).json()["id"]
    concluida(api, sid)
    assert api.head(f"/v1/sessoes/{sid}", headers=A).status_code == 200
    assert api.get(f"/v1/sessoes/{sid}", headers=A).json()["itens"][0]["resultado"]


def test_resultado_esperando_entrega_acima_do_teto_trava_novos_envios_do_token(monkeypatch, pdf):
    import lince.servidor as srv

    api = TestClient(criar_app(["token-a", "token-b"]))
    sid = enviar(api, ("a.pdf", pdf), A).json()["id"]
    concluida(api, sid)
    monkeypatch.setattr(srv, "BYTES_RETIDOS_POR_CLIENTE", 1)  # o resultado não buscado passa a estar acima do teto
    recusada = conforme(enviar(api, ("a.pdf", pdf), A), "/v1/sessoes", "post")
    assert recusada.status_code == 429 and recusada.json()["type"] == "urn:lince:erro:limite-retencao"
    assert api.get("/v1/sessoes", headers=B).status_code == 200  # o outro token segue normal
    entrega = enviar(api, ("a.pdf", pdf), {**A, "Idempotency-Key": "k"})  # repetição idempotente não é barrada
    assert entrega.status_code == 429  # (esta chave é nova, então cai no teto conferido sob a trava)
    api.get(f"/v1/sessoes/{sid}", headers=A)
    api.delete(f"/v1/sessoes/{sid}", headers=A)  # buscou e confirmou: a memória voltou
    assert api.app.state.servico.retidos == {}


def test_rajada_de_envios_conta_no_teto_antes_de_terminar(monkeypatch, pdf):
    import lince.servidor as srv

    monkeypatch.setattr(srv, "BYTES_RETIDOS_POR_CLIENTE", len(pdf) * 3 // 2)
    monkeypatch.setattr(srv.Servico, "_ler", lambda self, doc: None)  # documentos que nunca terminam
    api = TestClient(criar_app(["token-a"]))
    assert enviar(api, ("a.pdf", pdf), A).status_code == 201
    # a checagem da entrada (antes do upload) ainda deixaria passar; a da reserva, sob a trava, recusa
    r = enviar(api, ("b.pdf", pdf), A)
    assert r.status_code == 429 and r.json()["type"] == "urn:lince:erro:limite-retencao"
    assert api.app.state.servico.retidos[next(iter(api.app.state.servico.retidos))] == len(pdf)
    assert api.app.state.servico.vagas == {next(iter(api.app.state.servico.vagas)): 1}  # a recusada devolveu a vaga


def test_contabilidade_de_retencao_volta_a_zero(api, tmp_path, pdf):
    ids = [enviar(api, ("a.pdf", pdf), {**A, **ESPERA}).json()["id"] for _ in range(3)]
    ids.append(enviar(api, [("r.pdf", b"nao e pdf"), ("b.pdf", pdf)], {**A, **ESPERA}).json()["id"])
    for sid in ids:
        api.delete(f"/v1/sessoes/{sid}", headers=A)
    assert api.app.state.servico.retidos == {}


def test_apagar_durante_a_espera_nao_entrega_depois(pdf, monkeypatch):
    parado(monkeypatch)
    api = TestClient(criar_app(["token-a"]))
    servico = api.app.state.servico
    sid = enviar(api, ("a.pdf", pdf), A).json()["id"]
    sessao = servico.sessoes[sid]
    api.delete(f"/v1/sessoes/{sid}", headers=A)
    with pytest.raises(Exception) as erro:
        servico.entregar(sessao)
    assert getattr(erro.value, "status", None) == 404


def test_repeticao_idempotente_passa_mesmo_acima_do_teto_e_entrega(monkeypatch, pdf):
    import lince.servidor as srv

    api = TestClient(criar_app(["token-a"]))
    cab = {**A, "Idempotency-Key": "envio-1"}
    sid = enviar(api, ("a.pdf", pdf), cab).json()["id"]
    concluida(api, sid)
    monkeypatch.setattr(srv, "BYTES_RETIDOS_POR_CLIENTE", 1)
    r = conforme(enviar(api, ("a.pdf", pdf), cab), "/v1/sessoes", "post")
    assert r.status_code == 200 and r.json()["id"] == sid and r.json()["itens"][0]["resultado"]


def test_lista_mostra_so_o_que_ainda_nao_foi_entregue(api, pdf):
    a, b = (enviar(api, ("a.pdf", pdf), A).json()["id"] for _ in range(2))
    concluida(api, a)
    concluida(api, b)
    api.get(f"/v1/sessoes/{a}", headers=A)
    assert [x["id"] for x in api.get("/v1/sessoes", headers=A).json()["sessoes"]] == [b]


# ------------------------------------------------------------------ documento como código
@pytest.fixture
def deck():
    from pptx import Presentation

    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = "Relatório trimestral"
    buf = __import__("io").BytesIO()
    prs.save(buf)
    return buf.getvalue()


def test_leitura_com_codigo_traz_a_estrutura_editavel(api, deck, pdf):
    r = enviar(api, [("d.pptx", deck), ("a.pdf", pdf)], {**A, **ESPERA}, codigo="sim")
    itens = conforme(r, "/v1/sessoes", "post").json()["itens"]
    cod = itens[0]["resultado"]["codigo"]
    assert (
        cod["formato"] == "pptx" and cod["slides"][0]["formas"][0]["paragrafos"][0]["texto"] == "Relatório trimestral"
    )
    assert itens[1]["resultado"]["codigo"] is None and "pptx" in itens[1]["resultado"]["codigo_motivo"]


def _editar(api, cab, arquivo, pedido):
    return api.post("/v1/edicoes", files=[("arquivo", arquivo)], data={"operacoes": json.dumps(pedido)}, headers=cab)


def test_edicao_devolve_relatorio_e_o_arquivo_uma_vez(api, deck):
    import io

    from pptx import Presentation

    cod = enviar(api, ("d.pptx", deck), {**A, **ESPERA}, codigo="sim").json()["itens"][0]["resultado"]["codigo"]
    titulo = cod["slides"][0]["formas"][0]["id"]
    pedido = {
        "base_sha256": cod["sha256"],
        "operacoes": [{"op": "texto", "alvo": titulo, "se_texto": "Relatório trimestral", "texto": "Q3 2026"}],
    }
    r = conforme(_editar(api, A, ("d.pptx", deck), pedido), "/v1/edicoes", "post")
    assert r.status_code == 201 and r.json()["diff"]["partes_alteradas"] == ["/ppt/slides/slide1.xml"]
    url = r.headers["location"]
    assert api.get(url, headers=B).status_code == 404  # outro token não baixa
    baixado = api.get(url, headers=A)
    assert baixado.status_code == 200 and baixado.headers["x-request-id"]
    assert Presentation(io.BytesIO(baixado.content)).slides[0].shapes.title.text == "Q3 2026"
    assert api.get(url, headers=A).status_code == 200  # repetir dentro da janela
    assert api.delete(url, headers=A).status_code == 204
    assert api.get(url, headers=A).status_code == 404
    assert api.app.state.servico.edicoes == {}  # o arquivo editado saiu da memória


def test_edicao_em_conflito_diz_qual_operacao_e_nao_aplica(api, deck):
    pedido = {"operacoes": [{"op": "texto", "alvo": "sld:256/sp:2", "se_texto": "outro texto", "texto": "x"}]}
    r = conforme(_editar(api, A, ("d.pptx", deck), pedido), "/v1/edicoes", "post")
    assert r.status_code == 409 and r.json()["type"] == "urn:lince:erro:conflito"
    assert r.json()["operacao"] == 0 and r.json()["alvo"] == "sld:256/sp:2"
    assert api.app.state.servico.edicoes == {}


def test_edicao_de_formato_sem_suporte_e_pedido_malformado(api, pdf, deck):
    r = conforme(_editar(api, A, ("a.pdf", pdf), {"operacoes": [{"op": "ordem", "slides": []}]}), "/v1/edicoes", "post")
    assert r.status_code == 415
    ruim = api.post("/v1/edicoes", files=[("arquivo", ("d.pptx", deck))], data={"operacoes": "{nao"}, headers=A)
    assert conforme(ruim, "/v1/edicoes", "post").status_code == 400
    assert conforme(_editar(api, {}, ("d.pptx", deck), {"operacoes": []}), "/v1/edicoes", "post").status_code == 401


def test_operacao_malformada_vira_422_e_nao_500(api, deck):
    pedido = {"operacoes": [{"op": "valor_grafico", "alvo": "sld:256/sp:2", "ponto": "a", "valor": "nan"}]}
    r = conforme(_editar(api, A, ("d.pptx", deck), pedido), "/v1/edicoes", "post")
    assert r.status_code == 422 and r.json()["operacao"] == 0


def test_docx_lido_como_codigo_e_editado_com_alteracoes_controladas(api):
    import io
    import zipfile

    from docx import Document

    d = Document()
    d.add_paragraph("Prazo de 30 dias.")
    buf = io.BytesIO()
    d.save(buf)
    cod = enviar(api, ("c.docx", buf.getvalue()), {**A, **ESPERA}, codigo="sim").json()["itens"][0]["resultado"]
    cod = cod["codigo"]
    alvo = cod["blocos"][0]["id"]
    pedido = {
        "base_sha256": cod["sha256"],
        "operacoes": [{"op": "substituir", "alvo": alvo, "buscar": "30", "trocar": "45"}],
    }
    r = conforme(_editar(api, A, ("c.docx", buf.getvalue()), pedido), "/v1/edicoes", "post")
    assert r.status_code == 201 and r.json()["codigo"]["blocos"][0]["texto"] == "Prazo de 45 dias."
    novo = api.get(r.headers["location"], headers=A).content
    assert b"<w:ins " in zipfile.ZipFile(io.BytesIO(novo)).read("word/document.xml")
    pendente = conforme(
        _editar(api, A, ("c.docx", novo), {"operacoes": [{"op": "texto", "alvo": alvo, "texto": "x"}]}),
        "/v1/edicoes",
        "post",
    )
    assert pendente.status_code == 409 and pendente.json()["type"] == "urn:lince:erro:revisoes-pendentes"


def test_xlsx_lido_como_codigo_e_editado(api):
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.title = "Fretes"
    wb.active["A1"], wb.active["A2"] = 10, "=A1*2"
    buf = io.BytesIO()
    wb.save(buf)
    cod = enviar(api, ("p.xlsx", buf.getvalue()), {**A, **ESPERA}, codigo="sim").json()["itens"][0]["resultado"]
    assert [c["id"] for c in cod["codigo"]["abas"][0]["celulas"]] == ["Fretes!A1", "Fretes!A2"]
    pedido = {"base_sha256": cod["codigo"]["sha256"], "operacoes": [{"op": "celula", "alvo": "Fretes!A1", "valor": 15}]}
    r = conforme(_editar(api, A, ("p.xlsx", buf.getvalue()), pedido), "/v1/edicoes", "post")
    assert r.status_code == 201 and r.json()["codigo"]["abas"][0]["celulas"][0]["valor"] == 15
    novo = openpyxl.load_workbook(io.BytesIO(api.get(r.headers["location"], headers=A).content))
    assert novo["Fretes"]["A1"].value == 15 and novo["Fretes"]["A2"].value == "=A1*2"


# ------------------------------------------------------------------ modo tarefas (a IA é de quem chama)
def _docx_com_imagens():
    import io

    from docx import Document
    from docx.shared import Inches
    from PIL import Image, ImageDraw

    d = Document()
    d.add_paragraph("Relatório de moagem.")
    for cor in ("navy", "darkgreen"):
        im = Image.new("RGB", (600, 300), "white")
        ImageDraw.Draw(im).rectangle([50, 50, 400, 250], fill=cor)
        b = io.BytesIO()
        im.save(b, "PNG")
        d.add_picture(io.BytesIO(b.getvalue()), width=Inches(3))
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


def _montar(api, corpo, cab=A):
    return conforme(api.post("/v1/montagens", json=corpo, headers=cab), "/v1/montagens", "post")


def test_modo_tarefas_devolve_o_que_ler_e_monta_com_as_respostas(api, tmp_path):
    import base64

    import lince

    docx = _docx_com_imagens()
    s = conforme(enviar(api, ("r.docx", docx), {**A, **ESPERA}, modo="tarefas"), "/v1/sessoes", "post").json()
    preparo = s["itens"][0]["resultado"]
    assert [t["onde"] for t in preparo["tarefas"]] == ["imagem 1", "imagem 2"]
    assert all(base64.b64decode(t["imagem"]).startswith(b"\x89PNG") for t in preparo["tarefas"])
    respostas = {t["id"]: f"Figura {k}." for k, t in enumerate(preparo["tarefas"], 1)}
    r = _montar(api, {"plano": preparo["plano"], "respostas": respostas}).json()
    (tmp_path / "r.docx").write_bytes(docx)
    ordem = iter(["Figura 1.", "Figura 2."])
    direto = lince.Leitor(ia=lince.FuncaoLeitor(lambda img, instr: next(ordem), "m"), concorrencia=1).ler(
        tmp_path / "r.docx"
    )
    assert r["markdown"] == direto.markdown and r["cobertura"]["pendentes"] == [] and r["uso"] == []


def test_plano_alterado_ou_de_outro_token_nao_monta(api):
    s = enviar(api, ("r.docx", _docx_com_imagens()), {**A, **ESPERA}, modo="tarefas").json()
    plano = s["itens"][0]["resultado"]["plano"]
    dados, _, assinatura = plano.partition(".")
    alterado = dados[:-4] + ("AAAA" if dados[-4:] != "AAAA" else "BBBB") + "." + assinatura
    for corpo, cab in (({"plano": alterado, "respostas": {}}, A), ({"plano": plano, "respostas": {}}, B)):
        r = _montar(api, corpo, cab)
        assert r.status_code == 422 and r.json()["type"] == "urn:lince:erro:plano-invalido"
    assert _montar(api, {"plano": plano}).status_code == 400  # sem respostas


def test_modo_tarefas_nao_aceita_provedor(api, pdf):
    cab = {**A, "X-IA-URL": "https://exemplo.invalid/v1", "X-IA-Modelo": "m"}
    r = conforme(enviar(api, ("a.pdf", pdf), cab, modo="tarefas"), "/v1/sessoes", "post")
    assert r.status_code == 400 and r.json()["type"] == "urn:lince:erro:modo-invalido"


def _pdf_com_desenho():
    import io

    c_buf = io.BytesIO()
    c = canvas.Canvas(c_buf, pagesize=A4)
    c.drawString(60, 780, "Moagem anual por unidade, em mil toneladas.")
    for k, h in enumerate((120, 200, 80)):  # gráfico de barras vetorial
        c.rect(100 + 60 * k, 400, 40, h, fill=1)
    c.save()
    return c_buf.getvalue()


def test_modo_tarefas_com_pdf_segue_o_contrato_e_respeita_o_prazo(api, monkeypatch):
    import lince.pdf

    s = conforme(enviar(api, ("g.pdf", _pdf_com_desenho()), {**A, **ESPERA}, modo="tarefas"), "/v1/sessoes", "post")
    preparo = s.json()["itens"][0]["resultado"]
    assert preparo["tarefas"] and {t["tipo"] for t in preparo["tarefas"]} <= {"desenho", "imagem", "lote"}
    monkeypatch.setattr(lince.pdf, "PAGINAS_ANALISE_MAX", 0)  # o teto (como o prazo) vem nas pendências do preparo
    s = enviar(api, ("g.pdf", _pdf_com_desenho()), {**A, **ESPERA}, modo="tarefas").json()
    assert any("teto de 0 páginas" in p for p in s["itens"][0]["resultado"]["pendentes"])


def test_plano_vencido_grande_ou_forjado_nao_monta(api, monkeypatch):
    import hashlib

    import lince.servidor as srv
    from lince.tipos import Plano

    s = enviar(api, ("r.docx", _docx_com_imagens()), {**A, **ESPERA}, modo="tarefas").json()
    plano = s["itens"][0]["resultado"]["plano"]
    monkeypatch.setattr(srv, "PLANO_VALIDADE", -1)
    r = _montar(api, {"plano": plano, "respostas": {}})
    assert r.status_code == 422 and r.json()["type"] == "urn:lince:erro:plano-expirado"
    monkeypatch.undo()
    cliente = hashlib.sha256(b"token-a").hexdigest()[:16]
    do_token = hashlib.sha256(b"lince-plano\0token-a").digest()  # quem tem o token não conhece a chave do servidor
    forjado = srv.selar(do_token, cliente, Plano([], {"tipo": "office", "md": "a"}, []))
    r = _montar(api, {"plano": forjado, "respostas": {}})
    assert r.status_code == 422 and r.json()["type"] == "urn:lince:erro:plano-invalido"
    servidor = api.app.state.servico.segredo(cliente)  # rede: mesmo assinado pelo servidor, forma errada é 422
    torto = srv.selar(servidor, cliente, Plano([], {"tipo": "pdf", "paginas": [{"rota": "x"}]}, []))
    r = _montar(api, {"plano": torto, "respostas": {}})
    assert r.status_code == 422 and r.json()["type"] == "urn:lince:erro:plano-invalido"  # 422, nunca 500
    monkeypatch.setattr(srv, "SAIDA_MAX", 100_000)  # a mesma leitura copiada em mil lugares passa do teto de saída
    copias = srv.selar(servidor, cliente, Plano([], {"tipo": "office", "md": "{{IA:x}}" * 1000}, []))
    r = _montar(api, {"plano": copias, "respostas": {"x": "y" * 1000}})
    assert r.status_code == 413 and r.json()["type"] == "urn:lince:erro:saida-grande"
    paginas = [{"i": k, "rota": "pagina", "md": "", "tarefas": [f"p{k:03d}"], "regioes": []} for k in range(200)]
    escaneado = {"tipo": "pdf", "paginas": paginas, "iguais": {f"p{k:03d}": "p000" for k in range(1, 200)}}
    plano = srv.selar(servidor, cliente, Plano([], escaneado, []))
    assert _montar(api, {"plano": plano, "respostas": {"p000": "z" * 1000}}).status_code == 413  # cada página conta
    monkeypatch.setattr(srv, "PLANO_MAX", 10)
    s = enviar(api, ("r.docx", _docx_com_imagens()), {**A, **ESPERA}, modo="tarefas").json()
    assert s["itens"][0]["estado"] == "falhou" and "acima do teto" in s["itens"][0]["erro"]


def test_montagem_tem_teto_de_trabalho_no_documento_e_nas_respostas(api, monkeypatch):
    import hashlib

    import lince.servidor as srv
    from lince.tipos import Plano

    cliente = hashlib.sha256(b"token-a").hexdigest()[:16]
    segredo = api.app.state.servico.segredo(cliente)  # o teto vale como rede, mesmo para plano do servidor
    pagina = {"rota": "regioes", "md": "x" * 5000, "regioes": [{"tarefa": "x"}] * 50}  # um bloco só, grande
    estado = {"tipo": "pdf", "paginas": [{**pagina, "i": k} for k in range(100)]}  # cada página cabe; o todo, não
    monkeypatch.setattr(srv, "PLANO_TRABALHO_MAX", 2_000_000)
    r = _montar(api, {"plano": srv.selar(segredo, cliente, Plano([], estado, [])), "respostas": {}})
    assert r.status_code == 422 and "no documento" in r.json()["detail"]
    monkeypatch.setattr(srv, "RESPOSTAS_MAX", 10)
    plano = srv.selar(segredo, cliente, Plano([], {"tipo": "office", "md": "a"}, []))
    assert _montar(api, {"plano": plano, "respostas": {"t": "x" * 11}}).status_code == 413


def test_texto_hostil_nao_custa_quadratico_na_montagem():
    import time

    from lince.agrupamento import _json_do_lote
    from lince.montagem import acabamento

    inicio = time.monotonic()
    acabamento("```x\n" * 200_000)  # cercas que nunca fecham
    _json_do_lote("{" * 2_000_000)  # chaves que nunca fecham
    assert time.monotonic() - inicio < 3
    assert acabamento("início\n```\ncod--x\n```\n\n\n\nfim--a") == "início\n```\ncod--x\n```\n\nfim—a"


def test_plano_monta_em_outra_replica_com_o_mesmo_segredo():
    from starlette.testclient import TestClient as Cliente

    segredo = b"s" * 32
    uma, outra = (Cliente(criar_app(["token-a"], segredo=segredo)) for _ in range(2))
    sem = Cliente(criar_app(["token-a"]))  # sem LINCE_SEGREDO: aleatório por processo
    s = enviar(uma, ("r.docx", _docx_com_imagens()), {**A, **ESPERA}, modo="tarefas").json()
    plano = s["itens"][0]["resultado"]["plano"]
    assert _montar(outra, {"plano": plano, "respostas": {}}).status_code == 200
    assert _montar(sem, {"plano": plano, "respostas": {}}).status_code == 422


def test_documento_com_marca_literal_no_texto_e_montado_com_o_texto_intacto(api):
    import io

    from docx import Document

    d = Document()
    d.add_paragraph("Modelo: {{IA:img01}} e {{IA:img01}} de novo, {{TABELA:itens}}.")
    out = io.BytesIO()
    d.save(out)
    s = enviar(api, ("modelo.docx", out.getvalue()), {**A, **ESPERA}, modo="tarefas").json()
    preparo = s["itens"][0]["resultado"]
    r = _montar(api, {"plano": preparo["plano"], "respostas": {}})
    assert (
        r.status_code == 200
        and r.json()["markdown"].count("{{IA:img01}}") == 2
        and "{{TABELA:itens}}" in r.json()["markdown"]
    )
    assert not r.json()["estrutura"].get("imagens")  # nem imagem fantasma na estrutura


def test_lince_segredo_curto_recusa_a_subida_e_o_certo_chega_ao_servico(monkeypatch):
    import sys

    import uvicorn

    import lince.servidor as srv

    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **k: apps.append(app))
    monkeypatch.setattr(sys, "argv", ["lince-servidor"])
    monkeypatch.setenv("LINCE_TOKENS", "token-a")
    monkeypatch.setenv("LINCE_SEGREDO", "curto")
    with pytest.raises(SystemExit):
        srv.main()
    monkeypatch.setenv("LINCE_SEGREDO", "x" * 40)
    srv.main()
    assert apps[0].state.servico.segredo_plano == b"x" * 40
