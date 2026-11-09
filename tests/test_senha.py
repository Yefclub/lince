"""Arquivo protegido por senha (issue #56): reconhecido antes de qualquer leitura, recusado com código próprio sem a
senha, lido com ela como o mesmo arquivo sem senha, e a senha não fica em lugar nenhum: log, resposta, plano, cache,
metadados nem disco. Senhas erradas demais travam o arquivo e o token por um tempo."""

import base64
import io
import json
import logging
import os
import zipfile
import zlib
from pathlib import Path

import pytest
from msoffcrypto.format.ooxml import OOXMLFile
from reportlab.lib import pdfencrypt
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from starlette.testclient import TestClient
from test_servidor import ESPERA, A, B, _provedor, conforme, enviar

import lince
import lince.servidor as srv
from lince import protecao

SENHA = "Girafa-Azul-26"  # a dos fixtures do LibreOffice; o Word 97 aceita até 15 caracteres
REAIS = Path(__file__).parent / "fixtures" / "reais"
TEXTO = "Relatório protegido do Lince: a leitura só sai com a senha."
SESSOES = "/v1/sessoes"
POI_PPT = "poi_Password_Protected-hello.ppt"


def _pdf(senha=None, dono=None, figura=False):
    """O mesmo PDF, com senha de abertura (`senha`), só com a de permissões (`dono`) ou sem nenhuma."""
    buf = io.BytesIO()
    cifra = pdfencrypt.StandardEncryption(senha or "", dono or "dono-do-pdf", strength=128) if senha or dono else None
    c = canvas.Canvas(buf, pagesize=A4, encrypt=cifra, invariant=1)
    c.drawString(60, 780, "Contrato de fornecimento: valor total de R$ 1.234,56.")
    c.drawString(60, 760, "Vigência de 12 meses a partir da assinatura.")
    if figura:
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (900, 500), "white")
        ImageDraw.Draw(img).rectangle([100, 100, 300, 450], fill="navy")
        png = io.BytesIO()
        img.save(png, "PNG")
        from reportlab.lib.utils import ImageReader

        c.drawImage(ImageReader(io.BytesIO(png.getvalue())), 60, 400, width=450, height=250)
    c.save()
    return buf.getvalue()


def _cifrado(dados, senha=SENHA):
    """O pacote OOXML gravado com senha (Agile, como o Office 2010 em diante) pelo msoffcrypto."""
    saida = io.BytesIO()
    OOXMLFile(io.BytesIO(dados)).encrypt(senha, saida)
    return saida.getvalue()


def _docx():
    import docx

    d = docx.Document()
    d.add_paragraph(TEXTO)
    d.add_paragraph("Segunda parte do relatório, com o valor 42.")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _xlsx():
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Item", "Valor"])
    ws.append([TEXTO, 42])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _gravar(pasta, nome, dados):
    pasta.mkdir(parents=True, exist_ok=True)
    p = pasta / nome
    p.write_bytes(dados)
    return p


# ------------------------------------------------------------------ biblioteca
@pytest.mark.parametrize(
    ("nome", "cifrado", "claro"),
    [
        ("contrato.pdf", lambda: _pdf(senha=SENHA), lambda: _pdf()),
        ("relatorio.docx", lambda: _cifrado(_docx()), _docx),
        ("planilha.xlsx", lambda: _cifrado(_xlsx()), _xlsx),
    ],
)
def test_protegido_sem_senha_com_a_errada_e_com_a_certa(tmp_path, nome, cifrado, claro):
    arquivo = _gravar(tmp_path / "c", nome, cifrado())
    formato = nome.rsplit(".", 1)[1]
    with pytest.raises(lince.DocumentoProtegido) as sem:
        lince.ler(arquivo)
    assert type(sem.value) is lince.DocumentoProtegido and sem.value.formato == formato
    with pytest.raises(lince.SenhaIncorreta) as errada:
        lince.ler(arquivo, senha="Senha-Errada-1")
    assert errada.value.formato == formato and "Senha-Errada-1" not in str(errada.value)
    lido = lince.ler(arquivo, senha=SENHA)
    original = lince.ler(_gravar(tmp_path, nome, claro()))
    assert lido.markdown == original.markdown and lido.estrutura == original.estrutura
    assert "1.234,56" in lido.markdown or "42" in lido.markdown


def test_pdf_so_com_senha_de_permissoes_e_lido_sem_pedir_senha(tmp_path):
    so_dono = _gravar(tmp_path, "permissoes.pdf", _pdf(dono="so-o-dono-altera"))
    assert b"/Encrypt" in so_dono.read_bytes()  # cifrado, mas abre sem senha (como num visualizador)
    assert protecao.formato_protegido(so_dono) is None
    claro = lince.ler(_gravar(tmp_path, "claro.pdf", _pdf()))
    assert lince.ler(so_dono).markdown == claro.markdown


@pytest.mark.parametrize(
    ("nome", "senha", "esperado"),
    [
        ("lince_protegido.odt", SENHA, TEXTO),
        ("lince_protegido.ods", SENHA, TEXTO),
        ("lince_protegido.doc", SENHA, TEXTO),
        ("lince_protegido.xls", SENHA, TEXTO),
        (POI_PPT, "hello", "This is a protected document"),
    ],
)
def test_odf_e_office_legado_gravados_pelos_programas(nome, senha, esperado):
    formato = nome.rsplit(".", 1)[1]
    with pytest.raises(lince.DocumentoProtegido) as sem:
        lince.ler(REAIS / nome)
    assert type(sem.value) is lince.DocumentoProtegido and sem.value.formato == formato
    with pytest.raises(lince.SenhaIncorreta):
        lince.ler(REAIS / nome, senha="Senha-Errada-1")
    assert esperado in lince.ler(REAIS / nome, senha=senha).markdown


def test_cifra_que_o_lince_nao_decifra_fica_protegida_com_o_motivo(tmp_path):
    # Blowfish do OpenOffice antigo: o manifesto diz a cifra, e ela fica de fora com o motivo, sem quebrar nada
    antigo = tmp_path / "antigo.odt"
    with zipfile.ZipFile(REAIS / "lince_protegido.odt") as z, zipfile.ZipFile(antigo, "w") as saida:
        for item in z.infolist():
            dados = z.read(item)
            if item.filename == "META-INF/manifest.xml":
                dados = dados.replace(
                    b"http://www.w3.org/2001/04/xmlenc#aes256-cbc",
                    b"urn:oasis:names:tc:opendocument:xmlns:manifest:1.0#blowfish",
                )
            saida.writestr(item, dados)
    with pytest.raises(lince.DocumentoProtegido) as e:
        lince.ler(antigo, senha=SENHA)
    assert type(e.value) is lince.DocumentoProtegido and "ODF 1.2" in e.value.motivo


def test_derivacao_de_chave_hostil_nao_prende_a_leitura(tmp_path, monkeypatch):
    # o teto vale antes de derivar: um spinCount de milhões de voltas não chega a rodar
    monkeypatch.setattr(protecao, "VOLTAS_MAX", 1000)
    docx = _gravar(tmp_path, "r.docx", _cifrado(_docx()))
    with pytest.raises(lince.DocumentoProtegido) as e:
        lince.ler(docx, senha=SENHA)
    assert "voltas" in e.value.motivo
    with pytest.raises(lince.DocumentoProtegido) as e:
        lince.ler(REAIS / "lince_protegido.odt", senha=SENHA)
    assert "voltas" in e.value.motivo


def test_arquivo_sem_senha_ignora_a_senha_e_nao_e_falso_positivo(tmp_path):
    claro = _gravar(tmp_path, "claro.pdf", _pdf())
    assert lince.ler(claro, senha="qualquer").markdown == lince.ler(claro).markdown
    achados = sorted(p.name for p in REAIS.iterdir() if protecao.formato_protegido(p))  # os outros são abertos
    assert achados == sorted([*(f"lince_protegido.{e}" for e in ("doc", "ods", "odt", "xls")), POI_PPT])


def test_preparar_com_senha_e_concluir_sem_ela(tmp_path):
    arquivo = _gravar(tmp_path, "g.pdf", _pdf(senha=SENHA, figura=True))
    with pytest.raises(lince.DocumentoProtegido):
        lince.Leitor().preparar(arquivo)
    plano = lince.Leitor().preparar(arquivo, senha=SENHA)
    assert plano.tarefas and SENHA not in json.dumps(plano.estado, ensure_ascii=False)
    r = lince.Leitor().concluir(plano, {t.id: "Gráfico: Lapa 3500." for t in plano.tarefas})
    assert "Lapa 3500" in r.markdown and "1.234,56" in r.markdown


def test_leitura_do_protegido_nao_entra_no_cache(tmp_path):
    cache, chamadas = lince.CacheMemoria(), []
    ia = lince.FuncaoLeitor(lambda img, instr: chamadas.append(1) or "Gráfico: Lapa 3500.", "m")
    leitor = lince.Leitor(ia=ia, cache=cache)
    protegido = _gravar(tmp_path / "p", "g.pdf", _pdf(SENHA, figura=True))
    for _ in range(2):
        assert "Lapa 3500" in leitor.ler(protegido, senha=SENHA).markdown
    assert cache._d == {} and len(chamadas) == 2  # nada guardado: a segunda leitura paga de novo
    leitor.ler(_gravar(tmp_path, "g.pdf", _pdf(figura=True)))
    assert cache._d  # o mesmo documento sem senha usa o cache como sempre


# ------------------------------------------------------------------ API
@pytest.fixture
def api():
    return TestClient(srv.criar_app(["token-a", "token-b"]))


def _problema(r, codigo, status=422):
    conforme(r, SESSOES, "post")
    assert r.status_code == status and r.json()["type"] == f"urn:lince:erro:{codigo}", r.json()
    return r.json()


def test_api_recusa_o_protegido_sem_ler_nem_chamar_a_ia(tmp_path):
    servidor, provedor, url = _provedor("não devia chegar aqui")
    api = TestClient(srv.criar_app(["token-a"]))
    cab = {**A, **ESPERA, "X-IA-URL": url, "X-IA-Modelo": "m"}
    lote = [("claro.pdf", _pdf()), ("contrato.pdf", _pdf(senha=SENHA, figura=True))]
    corpo = _problema(enviar(api, lote, cab), "arquivo-protegido")
    servidor.shutdown()
    assert corpo["title"] == "Arquivo protegido por senha"
    assert corpo["formato"] == "pdf" and corpo["arquivo"] == "contrato.pdf"
    assert provedor.chamadas == 0 and api.app.state.servico.sessoes == {} and api.app.state.servico.vagas == {}


@pytest.mark.parametrize(
    ("nome", "cifrado", "claro"),
    [
        ("contrato.pdf", lambda: _pdf(senha=SENHA), lambda: _pdf()),
        ("relatorio.docx", lambda: _cifrado(_docx()), _docx),
        ("planilha.xlsx", lambda: _cifrado(_xlsx()), _xlsx),
        ("lince_protegido.odt", lambda: (REAIS / "lince_protegido.odt").read_bytes(), None),
    ],
)
def test_api_senha_errada_e_certa(api, nome, cifrado, claro):
    formato = nome.rsplit(".", 1)[1]
    assert _problema(enviar(api, (nome, cifrado()), {**A, **ESPERA}), "arquivo-protegido")["formato"] == formato
    errada = _problema(enviar(api, (nome, cifrado()), {**A, **ESPERA}, senha="Senha-Errada-1"), "senha-incorreta")
    assert errada["formato"] == formato and errada["arquivo"] == nome
    r = conforme(enviar(api, (nome, cifrado()), {**A, **ESPERA}, senha=SENHA), SESSOES, "post")
    assert r.status_code == 201
    item = r.json()["itens"][0]
    assert item["estado"] == "pronto" and item["nome"] == nome
    if claro is not None:
        original = enviar(api, (nome, claro()), {**A, **ESPERA}).json()["itens"][0]["resultado"]
        assert item["resultado"]["markdown"] == original["markdown"]
    else:
        assert TEXTO in item["resultado"]["markdown"]


def test_api_modo_tarefas_codigo_e_montagem_com_o_arquivo_decifrado(api):
    import docx
    from PIL import Image

    d = docx.Document()
    d.add_paragraph(TEXTO)
    png = io.BytesIO()
    Image.new("RGB", (400, 300), "navy").save(png, "PNG")
    d.add_picture(io.BytesIO(png.getvalue()))
    buf = io.BytesIO()
    d.save(buf)
    cifrado = _cifrado(buf.getvalue())
    r = enviar(api, ("r.docx", cifrado), {**A, **ESPERA}, modo="tarefas", codigo="sim", senha=SENHA)
    preparo = conforme(r, SESSOES, "post").json()["itens"][0]["resultado"]
    assert preparo["tarefas"] and preparo["codigo"]["formato"] == "docx"
    assert any(TEXTO in str(b) for b in preparo["codigo"]["blocos"])
    respostas = {t["id"]: "Retângulo azul." for t in preparo["tarefas"]}
    montado = api.post("/v1/montagens", json={"plano": preparo["plano"], "respostas": respostas}, headers=A)
    conforme(montado, "/v1/montagens", "post")
    assert TEXTO in montado.json()["markdown"] and "Retângulo azul." in montado.json()["markdown"]


def test_api_releitura_ampliada_trabalha_sobre_o_decifrado():
    servidor, provedor, url = _provedor("Valores: [ilegível] e ≈ 3 ≈ 4.")  # pede a segunda passada
    api = TestClient(srv.criar_app(["token-a"]))
    cab = {**A, **ESPERA, "X-IA-URL": url, "X-IA-Modelo": "m"}
    r = enviar(api, ("g.pdf", _pdf(senha=SENHA, figura=True)), cab, esforco="max", senha=SENHA)
    servidor.shutdown()
    resultado = conforme(r, SESSOES, "post").json()["itens"][0]["resultado"]
    assert resultado["cobertura"]["rotas"]["segunda_passada"] == 1
    assert not any("zoom" in p for p in resultado["cobertura"]["pendentes"])  # a releitura abriu o arquivo


def test_api_senhas_erradas_demais_travam_o_arquivo_e_o_token(api, monkeypatch):
    cifrado = _pdf(senha=SENHA)
    for _ in range(srv.SENHAS_ERRADAS_POR_ARQUIVO):
        _problema(enviar(api, ("c.pdf", cifrado), A, senha="Senha-Errada-1"), "senha-incorreta")
    travado = _problema(enviar(api, ("c.pdf", cifrado), A, senha=SENHA), "tentativas-de-senha", 429)
    assert travado["arquivo"] == "c.pdf"
    espera = int(api.post(SESSOES, files=[("arquivo", ("c.pdf", cifrado))], data={"senha": SENHA}, headers=A)
                 .headers["Retry-After"])  # fmt: skip
    assert 1 <= espera <= srv.SENHAS_JANELA  # quando a senha errada mais antiga sai da janela
    # outro token e outro arquivo do mesmo token seguem tentando
    assert enviar(api, ("c.pdf", cifrado), B, senha=SENHA).status_code == 201
    outro = _pdf(senha=SENHA, dono="outro-dono")
    _problema(enviar(api, ("o.pdf", outro), A, senha="Senha-Errada-1"), "senha-incorreta")
    # a janela passou: a senha certa abre e zera a conta do arquivo
    s = api.app.state.servico
    for erros in s.senhas_erradas.values():
        for k in range(len(erros)):
            erros[k] -= srv.SENHAS_JANELA + 1
    assert enviar(api, ("c.pdf", cifrado), A, senha=SENHA).status_code == 201
    s.limpar()
    assert s.senhas_erradas == {}
    # o mesmo arquivo com um byte a mais é outro hash: o teto do token segura a troca
    monkeypatch.setattr(srv, "SENHAS_ERRADAS_POR_TOKEN", 3)
    for k in range(3):
        _problema(enviar(api, (f"v{k}.pdf", cifrado + b"\n" * (k + 1)), A, senha="Senha-Errada-1"), "senha-incorreta")
    _problema(enviar(api, ("v9.pdf", cifrado + b"\n" * 9), A, senha="Senha-Errada-1"), "tentativas-de-senha", 429)


def test_api_tentativas_em_paralelo_nao_furam_o_teto(api, monkeypatch):
    import time
    from concurrent.futures import ThreadPoolExecutor

    def devagar(caminho, senha, pasta):  # a decifração demora: todas as tentativas chegam antes da primeira terminar
        time.sleep(0.5)
        raise protecao.SenhaIncorreta("pdf")

    monkeypatch.setattr(protecao, "aberto", devagar)
    cifrado = _pdf(senha=SENHA)
    with ThreadPoolExecutor(12) as ex:
        status = list(ex.map(lambda _: enviar(api, ("c.pdf", cifrado), A, senha="Errada").status_code, range(12)))
    assert sorted(status) == [422] * srv.SENHAS_ERRADAS_POR_ARQUIVO + [429] * (12 - srv.SENHAS_ERRADAS_POR_ARQUIVO)


def test_api_senha_invalida_e_ignorada_no_arquivo_sem_senha(api):
    longa = enviar(api, ("c.pdf", _pdf(senha=SENHA)), A, senha="x" * (srv.SENHA_MAX + 1))
    _problema(longa, "senha-invalida", 400)
    r = conforme(enviar(api, ("claro.pdf", _pdf()), {**A, **ESPERA}, senha=SENHA), SESSOES, "post")
    assert r.status_code == 201 and "1.234,56" in r.json()["itens"][0]["resultado"]["markdown"]


def test_api_o_decifrado_tambem_passa_pelo_antivirus(monkeypatch):
    from lince import antivirus

    varridos = []

    async def varrer(alvo, arquivo):
        varridos.append(Path(arquivo).read_bytes())

    monkeypatch.setattr(antivirus, "varrer", varrer)
    api = TestClient(srv.criar_app(["token-a"], clamav=("clamav", 3310)))
    assert enviar(api, ("c.pdf", _pdf(senha=SENHA)), {**A, **ESPERA}, senha=SENHA).status_code == 201
    assert len(varridos) == 2 and b"/Encrypt" in varridos[0] and b"/Encrypt" not in varridos[1]


def test_documento_protegido_nao_entra_no_cache_do_servidor():
    cache = lince.CacheMemoria()
    servidor, provedor, url = _provedor("Gráfico de barras: Lapa 3500.")
    api = TestClient(srv.criar_app(["token-a"], cache=cache))
    cab = {**A, **ESPERA, "X-IA-URL": url, "X-IA-Modelo": "m"}
    for _ in range(2):
        r = enviar(api, ("g.pdf", _pdf(senha=SENHA, figura=True)), cab, senha=SENHA).json()["itens"][0]["resultado"]
        assert not r["uso"][0]["cache"] and "Lapa 3500" in r["markdown"]
    servidor.shutdown()
    assert provedor.chamadas == 2 and cache._d == {}


def test_a_senha_nao_aparece_em_lugar_nenhum(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    cache = lince.CacheMemoria()
    servidor, provedor, url = _provedor("Gráfico de barras: Lapa 3500.")
    api = TestClient(srv.criar_app(["token-a"], cache=cache))
    s = api.app.state.servico
    ia = {"X-IA-URL": url, "X-IA-Modelo": "m"}
    docx, pdf = _cifrado(_docx()), _pdf(senha=SENHA, figura=True)
    respostas = [
        enviar(api, ("c.pdf", pdf), {**A, **ESPERA, **ia}),
        enviar(api, ("c.pdf", pdf), {**A, **ESPERA, **ia}, senha="Outra-Senha-9"),
        enviar(api, ("c.pdf", pdf), {**A, **ESPERA, **ia}, senha=SENHA, codigo="sim"),
        enviar(api, ("r.docx", docx), {**A, **ESPERA}, senha=SENHA, modo="tarefas", codigo="sim"),
        enviar(api, ("r.docx", docx), {**A, **ESPERA}, senha="x" * (srv.SENHA_MAX + 1)),
    ]
    plano = respostas[3].json()["itens"][0]["resultado"]["plano"]
    respostas.append(api.post("/v1/montagens", json={"plano": plano, "respostas": {}}, headers=A))
    # o que ficaria em disco e em memória enquanto o documento espera a leitura
    monkeypatch.setattr(srv.Servico, "_ler", lambda self, doc: None)
    sid = enviar(api, ("c.pdf", pdf), A, senha=SENHA).json()["id"]
    sessao = s.sessoes[sid]
    na_pasta = [p.read_bytes() for p in Path(sessao["pasta"]).rglob("*") if p.is_file()]
    em_disco = na_pasta + [p.read_bytes() for p in Path(s.guardados).rglob("*") if p.is_file()]  # os resultados
    servidor.shutdown()
    # o msoffcrypto registra em DEBUG a chave derivada da senha (cifra Standard): nada dele chega ao log
    logging.getLogger("msoffcrypto.method.ecma376_standard").debug("chave derivada de %s", SENHA)
    selado = zlib.decompress(base64.urlsafe_b64decode(plano.partition(".")[0])).decode()
    textos = [r.text for r in respostas] + [str(dict(r.headers)) for r in respostas]
    textos += [caplog.text, selado, repr(s.sessoes), repr(s.senhas_erradas), json.dumps(cache._d, ensure_ascii=False)]
    for senha in (SENHA, "Outra-Senha-9"):
        assert not any(senha in t for t in textos)
        for codificada in (senha.encode(), senha.encode("utf-16-le"), base64.b64encode(senha.encode())):
            assert not any(codificada in b for b in em_disco)
    assert len(na_pasta) == 1 and b"/Encrypt" not in na_pasta[0]  # só o decifrado, esperando a leitura
    assert [r.status_code for r in respostas] == [422, 422, 201, 201, 400, 200]


def test_documento_lido_some_do_disco_com_a_sessao(api):
    r = enviar(api, ("c.pdf", _pdf(senha=SENHA)), {**A, **ESPERA}, senha=SENHA)
    sessao = api.app.state.servico.sessoes[r.json()["id"]]
    assert not os.path.exists(sessao["pasta"])  # o decifrado foi apagado com a pasta da sessão


def test_office_cifrado_e_reconhecido_pelo_conteiner_mesmo_sem_a_extensao(tmp_path):
    with pytest.raises(lince.DocumentoProtegido) as e:  # o contêiner OLE com EncryptionInfo, não a extensão
        lince.ler(_gravar(tmp_path, "sem_extensao.bin", _cifrado(_docx())))
    assert e.value.formato == "ooxml"
