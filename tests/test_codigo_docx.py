"""Documento como código no DOCX: estrutura com ids, edição cirúrgica e controle de alterações por padrão."""

import io
import re
import zipfile

import pytest
from docx import Document
from docx.shared import Inches
from PIL import Image

from lince import codigo
from lince.codigo import OperacaoInvalida, docx

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _salvar(d) -> bytes:
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


@pytest.fixture
def contrato():
    d = Document()
    d.add_heading("Contrato de fornecimento", 1)
    p = d.add_paragraph("O prazo de entrega é de ")
    p.add_run("30 dias").bold = True
    p.add_run(" corridos, a contar da assinatura.")
    d.add_paragraph("Cláusula a remover.")
    t = d.add_table(2, 2)
    t.cell(0, 0).text, t.cell(0, 1).text, t.cell(1, 0).text, t.cell(1, 1).text = "Item", "Valor", "Soja", "30 dias"
    d.add_paragraph("Fim.")
    return _salvar(d)


def _xml(dados, parte="word/document.xml") -> str:
    return zipfile.ZipFile(io.BytesIO(dados)).read(parte).decode()


def _paragrafos(c):
    out = []
    for b in c["blocos"]:
        if b["tipo"] == "paragrafo":
            out.append(b)
        else:
            out += [p for linha in b["linhas"] for celula in linha for p in celula]
    return out


def _texto(c, pid):
    return next(p["texto"] for p in _paragrafos(c) if p["id"] == pid)


def _trocar_xml(dados, troca, parte="word/document.xml"):
    saida = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(dados)) as o, zipfile.ZipFile(saida, "w") as z:
        for i in o.infolist():
            z.writestr(i, troca(o.read(i)) if i.filename == parte else o.read(i))
    return saida.getvalue()


def test_codigo_expoe_paragrafos_tabelas_e_estilos(contrato):
    c = docx.extrair(contrato)
    assert c["formato"] == "docx" and re.fullmatch(r"[0-9a-f]{64}", c["sha256"])
    tipos = [b["tipo"] for b in c["blocos"]]
    assert tipos == ["paragrafo", "paragrafo", "paragrafo", "tabela", "paragrafo"]
    assert c["blocos"][0]["estilo"] == "Heading1"
    assert c["blocos"][1]["texto"] == "O prazo de entrega é de 30 dias corridos, a contar da assinatura."
    assert c["blocos"][3]["linhas"][1][1][0]["texto"] == "30 dias"
    assert {"id": "Heading2", "nome": "heading 2"} in c["estilos"]


def test_ida_e_volta_sem_operacoes_preserva_todas_as_partes(contrato):
    novo, _, _ = docx.aplicar(contrato, [])
    assert codigo.comparar(contrato, novo)["partes_alteradas"] == []


def test_edicao_entra_como_revisao_e_preserva_a_formatacao_em_volta(contrato):
    alvo = _paragrafos(docx.extrair(contrato))[1]["id"]
    novo, rel = codigo.editar(contrato, "c.docx", [{"op": "substituir", "alvo": alvo, "buscar": "30", "trocar": "45"}])
    xml = _xml(novo)
    deletado = re.search(r"<w:del [^>]*>(.*?)</w:del>", xml).group(1)
    inserido = re.search(r"<w:ins [^>]*>(.*?)</w:ins>", xml).group(1)
    assert "<w:delText>30</w:delText>" in deletado and "<w:b/>" in deletado
    assert ">45</w:t>" in inserido and "<w:b/>" in inserido  # o texto novo herda o negrito do trecho trocado
    assert 'w:author="Lince"' in xml
    assert re.search(r"<w:r><w:rPr><w:b/></w:rPr><w:t[^>]*> dias</w:t>", xml)  # o resto do run continua negrito
    assert _texto(rel["codigo"], alvo) == "O prazo de entrega é de 45 dias corridos, a contar da assinatura."
    assert docx.integridade(novo) == []
    Document(io.BytesIO(novo))  # reabre


def test_sem_revisao_aplica_direto(contrato):
    c = docx.extrair(contrato)
    ids = [p["id"] for p in _paragrafos(c)]
    ops = [
        {"op": "substituir", "buscar": "30 dias", "trocar": "45 dias", "revisao": False},  # documento inteiro
        {"op": "texto", "alvo": ids[0], "texto": "Contrato de soja", "revisao": False},
        {"op": "remover_paragrafo", "alvo": ids[2], "revisao": False},
    ]
    novo, rel = codigo.editar(contrato, "c.docx", ops)
    xml = _xml(novo)
    assert "<w:ins " not in xml and "<w:del " not in xml
    textos = [p["texto"] for p in _paragrafos(rel["codigo"])]
    assert textos == [
        "Contrato de soja",
        "O prazo de entrega é de 45 dias corridos, a contar da assinatura.",
        "Item",
        "Valor",
        "Soja",
        "45 dias",
        "Fim.",
    ]
    assert rel["aplicadas"][0]["trocas"] == 2  # corpo e célula da tabela


def test_alvos_por_posicao_valem_para_o_arquivo_original(contrato):
    ids = [p["id"] for p in _paragrafos(docx.extrair(contrato))]
    ops = [
        {"op": "remover_paragrafo", "alvo": ids[2], "revisao": False},
        {"op": "inserir_paragrafo_apos", "alvo": ids[0], "texto": "Partes: A e B.", "revisao": False},
        {"op": "texto", "alvo": ids[-1], "texto": "Assinaturas.", "revisao": False},  # o "Fim." do original
    ]
    _, rel = codigo.editar(contrato, "c.docx", ops)
    textos = [p["texto"] for p in _paragrafos(rel["codigo"])]
    assert textos[:2] == ["Contrato de fornecimento", "Partes: A e B."] and textos[-1] == "Assinaturas."
    assert "Cláusula a remover." not in textos


def test_inserir_e_remover_com_revisao_marcam_o_paragrafo(contrato):
    ids = [p["id"] for p in _paragrafos(docx.extrair(contrato))]
    novo, rel = codigo.editar(
        contrato,
        "c.docx",
        [
            {"op": "inserir_paragrafo_apos", "alvo": ids[1], "texto": "Multa de 2%.", "revisao": {"autor": "Yef"}},
            {"op": "remover_paragrafo", "alvo": ids[2]},
        ],
    )
    xml = _xml(novo)
    assert re.search(r'<w:pPr><w:rPr><w:ins [^>]*w:author="Yef"', xml)  # marca do parágrafo inserido
    assert re.search(r"<w:rPr><w:del [^>]*/></w:rPr>", xml)  # marca do parágrafo removido
    assert "<w:delText>Cláusula a remover.</w:delText>" in xml
    removido = [p for p in _paragrafos(rel["codigo"]) if p["texto"] == "" and p.get("revisoes")]
    assert len(removido) == 1  # continua lá, vazio na visão final, até alguém aceitar no Word
    assert docx.integridade(novo) == []


def test_estilo_com_revisao_guarda_o_anterior(contrato):
    alvo = _paragrafos(docx.extrair(contrato))[-1]["id"]
    novo, rel = codigo.editar(contrato, "c.docx", [{"op": "estilo_paragrafo", "alvo": alvo, "estilo": "Heading 2"}])
    assert rel["aplicadas"][0]["depois"] == "Heading2"
    assert re.search(r'<w:pPr><w:pStyle w:val="Heading2"/><w:pPrChange [^>]*><w:pPr/></w:pPrChange>', _xml(novo))
    with pytest.raises(OperacaoInvalida) as e:
        codigo.editar(contrato, "c.docx", [{"op": "estilo_paragrafo", "alvo": alvo, "estilo": "Inexistente"}])
    assert e.value.codigo == "alvo-inexistente"


def test_comentario_cria_a_parte_e_o_segundo_reusa(contrato):
    ids = [p["id"] for p in _paragrafos(docx.extrair(contrato))]
    novo, rel = codigo.editar(
        contrato,
        "c.docx",
        [
            {"op": "comentar", "alvo": ids[1], "texto": "Conferir com o jurídico", "autor": "Ana Lima"},
            {"op": "comentar", "alvo": ids[0], "texto": "Título ok?"},
        ],
    )
    assert rel["diff"]["partes_novas"] == ["/word/comments.xml"]
    assert "comments+xml" in _xml(novo, "[Content_Types].xml")
    assert "relationships/comments" in _xml(novo, "word/_rels/document.xml.rels")
    assert [(c["autor"], c["texto"]) for c in rel["codigo"]["comentarios"]] == [
        ("Ana Lima", "Conferir com o jurídico"),
        ("Lince", "Título ok?"),
    ]
    assert 'w:initials="AL"' in _xml(novo, "word/comments.xml")
    assert docx.integridade(novo) == []


def test_paraid_do_word_vira_o_id_e_o_paragrafo_novo_ganha_um(contrato):
    contador = iter(range(1, 100))

    def com_para_id(x):
        return re.sub(
            rb"<w:p>|<w:p ", lambda m: m.group(0)[:-1] + b' w14:paraId="%08X"' % next(contador) + m.group(0)[-1:], x
        )

    dados = _trocar_xml(contrato, com_para_id)
    c = docx.extrair(dados)
    ids = [p["id"] for p in _paragrafos(c)]
    assert all(re.fullmatch(r"p:[0-9A-F]{8}", i) for i in ids)
    _, rel = codigo.editar(dados, "c.docx", [{"op": "inserir_paragrafo_apos", "alvo": ids[0], "texto": "Novo."}])
    novo_id = rel["aplicadas"][0]["id"]
    depois = [p["id"] for p in _paragrafos(rel["codigo"])]
    assert novo_id in depois and len(set(depois)) == len(depois) and depois[0] == ids[0]


def test_recusas_explicam_o_motivo(contrato, tmp_path):
    imagem = tmp_path / "logo.png"
    Image.new("RGB", (20, 20), "red").save(imagem)
    d = Document(io.BytesIO(contrato))
    d.paragraphs[2].add_run().add_picture(str(imagem), width=Inches(0.3))
    com_imagem = _salvar(d)
    ids = [p["id"] for p in _paragrafos(docx.extrair(com_imagem))]

    def erro(ops, dados=com_imagem):
        with pytest.raises(OperacaoInvalida) as e:
            codigo.editar(dados, "c.docx", ops)
        return e.value.codigo

    assert erro([{"op": "texto", "alvo": ids[2], "texto": "x"}]) == "operacao-invalida"  # apagaria a imagem
    assert erro([{"op": "texto", "alvo": ids[1], "texto": "x", "se_texto": "outro"}]) == "conflito"
    assert erro([{"op": "texto", "alvo": ids[0], "texto": "a"}, {"op": "texto", "alvo": ids[0], "texto": "b"}]) == (
        "conflito"
    )
    assert erro([{"op": "remover_paragrafo", "alvo": ids[3]}]) == "operacao-invalida"  # único da célula
    assert erro([{"op": "texto", "alvo": "p:n999", "texto": "x"}]) == "alvo-inexistente"
    assert erro([{"op": "texto", "alvo": "sld:256", "texto": "x"}]) == "alvo-invalido"
    revisado, _ = codigo.editar(contrato, "c.docx", [{"op": "texto", "alvo": ids[0], "texto": "Novo título"}])
    assert erro([{"op": "texto", "alvo": ids[0], "texto": "x"}], revisado) == "revisoes-pendentes"


def test_substituir_contorna_imagem_e_link(contrato):
    def com_link(x):
        return x.replace(
            b"<w:t>Fim.</w:t></w:r>",
            b'<w:t>Fim.</w:t></w:r><w:r><w:drawing/></w:r><w:r><w:t xml:space="preserve"> anexo</w:t></w:r>'
            b'<w:hyperlink w:anchor="x"><w:r><w:t xml:space="preserve"> Ver anexo</w:t></w:r></w:hyperlink>',
        )

    dados = _trocar_xml(contrato, com_link)
    novo, _ = codigo.editar(  # dentro do link: troca e o link continua
        dados, "c.docx", [{"op": "substituir", "buscar": "anexo", "trocar": "apêndice", "revisao": False}]
    )
    xml = _xml(novo)
    assert '<w:hyperlink w:anchor="x">' in xml and xml.count("apêndice") == 2 and "<w:drawing/>" in xml
    with pytest.raises(OperacaoInvalida) as e:  # atravessa a imagem: não troca
        codigo.editar(dados, "c.docx", [{"op": "substituir", "buscar": "Fim. anexo", "trocar": "x"}])
    assert e.value.codigo == "conflito"
    with pytest.raises(OperacaoInvalida) as e:  # cruza a borda do link: não troca
        codigo.editar(dados, "c.docx", [{"op": "substituir", "buscar": "Fim. Ver", "trocar": "x"}])
    assert e.value.codigo == "conflito"


def _com_paragrafos(dados, *paragrafos: bytes) -> bytes:
    """Acrescenta parágrafos em XML cru antes do sectPr do corpo (o último elemento do documento)."""

    def troca(x):
        k = x.rindex(b"<w:sectPr")
        return x[:k] + b"".join(paragrafos) + x[k:]

    return _trocar_xml(dados, troca)


CAMPO_SIMPLES = (
    '<w:p><w:r><w:t xml:space="preserve">Página </w:t></w:r><w:fldSimple w:instr="PAGE"><w:r><w:t>3</w:t></w:r>'
    "</w:fldSimple></w:p>"
).encode()
CAMPO_COMPLEXO = (
    b'<w:p><w:r><w:t xml:space="preserve">Data: </w:t></w:r><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
    b'<w:r><w:instrText xml:space="preserve"> DATE </w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r>'
    b'<w:r><w:t>06/11/2026</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
)
CAIXA = (
    b'<w:p><w:r><w:t xml:space="preserve">Veja </w:t></w:r><w:r><mc:AlternateContent><mc:Choice Requires="wps">'
    b"<w:drawing/></mc:Choice><mc:Fallback><w:pict/></mc:Fallback></mc:AlternateContent></w:r></w:p>"
)
CONTROLE = (
    b'<w:p><w:r><w:t xml:space="preserve">Nome: </w:t></w:r><w:sdt><w:sdtPr><w:tag w:val="nome"/></w:sdtPr>'
    b"<w:sdtContent><w:r><w:t>Fulano</w:t></w:r></w:sdtContent></w:sdt></w:p>"
)


def test_campos_caixas_e_controles_nunca_sao_apagados(contrato):
    dados = _com_paragrafos(contrato, CAMPO_SIMPLES, CAMPO_COMPLEXO, CAIXA, CONTROLE)
    ps = _paragrafos(docx.extrair(dados))[-4:]
    assert [p["texto"] for p in ps] == ["Página ", "Data: ", "Veja ", "Nome: Fulano"]
    assert [bool(p.get("objetos")) for p in ps] == [True, True, True, False] and ps[3]["controles"]
    for p in ps:
        for revisao in (True, False):
            with pytest.raises(OperacaoInvalida) as e:
                codigo.editar(dados, "c.docx", [{"op": "texto", "alvo": p["id"], "texto": "x", "revisao": revisao}])
            assert e.value.codigo == "operacao-invalida"
    with pytest.raises(OperacaoInvalida):  # o resultado do campo é do Word: trocar ali não duraria
        codigo.editar(dados, "c.docx", [{"op": "substituir", "alvo": ps[1]["id"], "buscar": "2026", "trocar": "x"}])
    novo, rel = codigo.editar(
        dados,
        "c.docx",
        [
            {"op": "substituir", "alvo": ps[3]["id"], "buscar": "Fulano", "trocar": "Beltrano", "revisao": False},
            {"op": "substituir", "alvo": ps[0]["id"], "buscar": "Página", "trocar": "Folha", "revisao": False},
        ],
    )
    xml = _xml(novo)
    assert '<w:sdtContent><w:r><w:t xml:space="preserve">Beltrano</w:t></w:r></w:sdtContent>' in xml
    assert '<w:fldSimple w:instr="PAGE"><w:r><w:t>3</w:t></w:r></w:fldSimple>' in xml
    assert "<mc:AlternateContent>" in xml and 'fldCharType="begin"' in xml


def test_remover_com_revisao_mantem_o_campo_simples_marcado(contrato):
    dados = _com_paragrafos(contrato, CAMPO_SIMPLES)
    alvo = _paragrafos(docx.extrair(dados))[-1]["id"]
    novo, _ = codigo.editar(dados, "c.docx", [{"op": "remover_paragrafo", "alvo": alvo}])
    assert re.search(r'<w:fldSimple w:instr="PAGE"><w:del [^>]*><w:r><w:delText>3</w:delText>', _xml(novo))
    assert docx.integridade(novo) == []


def test_formatacao_pendente_tambem_e_revisao(contrato):
    pendente = (
        b'<w:p><w:r><w:rPr><w:b/><w:rPrChange w:id="900" w:author="Ana" w:date="2026-01-01T00:00:00Z"><w:rPr/>'
        b"</w:rPrChange></w:rPr><w:t>Negrito proposto</w:t></w:r></w:p>"
    )
    dados = _com_paragrafos(contrato, pendente)
    c = docx.extrair(dados)
    p = _paragrafos(c)[-1]
    assert p["revisoes"] and c["revisoes"] == 1
    for op in (
        {"op": "texto", "alvo": p["id"], "texto": "x", "revisao": False},
        {"op": "substituir", "alvo": p["id"], "buscar": "Negrito", "trocar": "x"},
    ):
        with pytest.raises(OperacaoInvalida) as e:
            codigo.editar(dados, "c.docx", [op])
        assert e.value.codigo == "revisoes-pendentes"


def test_corte_no_meio_de_run_com_varios_filhos_preserva_o_resto(contrato):
    misto = (
        b'<w:p><w:r><w:rPr><w:i/></w:rPr><w:t>Prazo</w:t><w:tab/><w:t xml:space="preserve">30 dias </w:t>'
        b"<w:lastRenderedPageBreak/><w:t>fim</w:t></w:r></w:p>"
    )
    dados = _com_paragrafos(contrato, misto)
    alvo = _paragrafos(docx.extrair(dados))[-1]["id"]
    for revisao in (False, True):
        novo, rel = codigo.editar(
            dados, "c.docx", [{"op": "substituir", "alvo": alvo, "buscar": "30", "trocar": "45", "revisao": revisao}]
        )
        assert _texto(rel["codigo"], alvo) == "Prazo\t45 dias fim"
        xml = _xml(novo)
        assert "<w:tab/>" in xml and "<w:lastRenderedPageBreak/>" in xml
        assert re.search(r"<w:r><w:rPr><w:i/></w:rPr><w:t[^>]*> dias </w:t>", xml)  # o resto continua itálico


def test_substituir_muitas_vezes_e_linear_e_tem_teto(contrato):
    import time

    d = Document()
    d.add_paragraph("a " * 9_000)
    dados = _salvar(d)
    alvo = _paragrafos(docx.extrair(dados))[0]["id"]
    inicio = time.monotonic()
    _, rel = codigo.editar(dados, "c.docx", [{"op": "substituir", "alvo": alvo, "buscar": "a", "trocar": "b"}])
    assert rel["aplicadas"][0]["trocas"] == 9_000 and time.monotonic() - inicio < 10
    d = Document()
    d.add_paragraph("a " * 11_000)
    with pytest.raises(OperacaoInvalida) as e:
        codigo.editar(_salvar(d), "c.docx", [{"op": "substituir", "buscar": "a", "trocar": "b"}])
    assert "ocorrências" in str(e.value)
    with pytest.raises(OperacaoInvalida) as e:
        codigo.editar(dados, "c.docx", [{"op": "texto", "alvo": alvo, "texto": "x" * 100_001}])
    assert e.value.codigo == "texto-grande"


def test_remover_recusa_o_que_quebraria_o_documento(contrato):
    d = Document(io.BytesIO(contrato))
    d.add_section()  # o parágrafo antes da quebra de seção carrega o sectPr
    celula = d.tables[0].cell(0, 0)
    celula.add_table(1, 1)
    celula.paragraphs[-1].text = "Depois da tabela"  # o parágrafo que o python-docx põe depois da tabela
    dados = _salvar(d)
    c = docx.extrair(dados)
    ids = [p["id"] for p in _paragrafos(c)]
    comentado, _ = codigo.editar(dados, "c.docx", [{"op": "comentar", "alvo": ids[1], "texto": "ok"}])

    def erro(op, base=dados):
        with pytest.raises(OperacaoInvalida) as e:
            codigo.editar(base, "c.docx", [op])
        return str(e.value)

    secao = c["blocos"][-1]["id"]  # o parágrafo vazio que o python-docx cria com o sectPr
    assert "seção" in erro({"op": "remover_paragrafo", "alvo": secao, "revisao": False})
    assert "comentário" in erro({"op": "remover_paragrafo", "alvo": ids[1]}, comentado)
    fecha = next(p["id"] for p in _paragrafos(c) if p["texto"] == "Depois da tabela")
    assert "fecha uma tabela" in erro({"op": "remover_paragrafo", "alvo": fecha, "revisao": False})


def test_pacote_que_nao_e_docx_e_recusado_sem_erro_interno(contrato):
    saida = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(contrato)) as o, zipfile.ZipFile(saida, "w") as z:
        for i in o.infolist():
            if i.filename != "_rels/.rels":
                z.writestr(i, o.read(i))
    with pytest.raises(OperacaoInvalida) as e:
        codigo.editar(saida.getvalue(), "c.docx", [{"op": "texto", "alvo": "p:n0", "texto": "x"}])
    assert e.value.codigo == "operacao-invalida"


def test_paragrafo_dentro_de_customxml_aparece_na_leitura(contrato):
    def envolver(x):
        return x.replace(
            b"<w:p><w:r><w:t>Fim.</w:t></w:r></w:p>",
            b'<w:customXml w:element="x"><w:p><w:r><w:t>Fim.</w:t></w:r></w:p></w:customXml>',
        )

    c = docx.extrair(_trocar_xml(contrato, envolver))
    assert c["blocos"][-1]["texto"] == "Fim."


def test_troca_que_cruza_runs_corta_as_duas_pontas(contrato):
    misto = (
        b'<w:p><w:r><w:t>Pra</w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">zo de </w:t></w:r>'
        b"<w:r><w:t>30</w:t></w:r></w:p>"
    )
    dados = _com_paragrafos(contrato, misto)
    alvo = _paragrafos(docx.extrair(dados))[-1]["id"]
    novo, rel = codigo.editar(
        dados, "c.docx", [{"op": "substituir", "alvo": alvo, "buscar": "azo d", "trocar": "AZO D", "revisao": False}]
    )
    assert _texto(rel["codigo"], alvo) == "PrAZO De 30"
    xml = _xml(novo)
    assert re.search(r"<w:r><w:t>Pr</w:t></w:r><w:r><w:t[^>]*>AZO D</w:t></w:r>", xml)  # herda do 1º trocado
    assert re.search(r"<w:r><w:rPr><w:b/></w:rPr><w:t[^>]*>e </w:t></w:r><w:r><w:t>30</w:t>", xml)


def test_substituir_num_run_enorme_e_linear():
    import time

    d = Document()
    d.add_paragraph(("a" + "x" * 19) * 10_000)  # 200 mil caracteres num run só, 10 mil ocorrências
    dados = _salvar(d)
    alvo = _paragrafos(docx.extrair(dados))[0]["id"]
    inicio = time.monotonic()
    _, rel = codigo.editar(
        dados, "c.docx", [{"op": "substituir", "alvo": alvo, "buscar": "a", "trocar": "b", "revisao": False}]
    )
    assert time.monotonic() - inicio < 5
    assert _texto(rel["codigo"], alvo) == ("b" + "x" * 19) * 10_000


SUMARIO = (
    b'<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> TOC \\o </w:instrText>'
    b'</w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>Cap 1</w:t></w:r></w:p>'
    b"<w:p><w:r><w:t>Cap 2</w:t></w:r></w:p>"
    b'<w:p><w:r><w:t>Cap 3</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
    b"<w:p><w:r><w:t>Depois do sumario</w:t></w:r></w:p>"
)


def test_sumario_de_varios_paragrafos_e_campo_inteiro(contrato):
    dados = _com_paragrafos(contrato, SUMARIO)
    ps = _paragrafos(docx.extrair(dados))[-4:]
    assert [bool(p.get("objetos")) for p in ps] == [True, True, True, False]
    with pytest.raises(OperacaoInvalida):
        codigo.editar(dados, "c.docx", [{"op": "texto", "alvo": ps[1]["id"], "texto": "x"}])
    with pytest.raises(OperacaoInvalida) as e:  # o Word refaz o sumário: trocar ali não duraria
        codigo.editar(dados, "c.docx", [{"op": "substituir", "buscar": "Cap 2", "trocar": "x"}])
    assert e.value.codigo == "conflito"


@pytest.mark.parametrize(
    "conteudo",
    [
        b'<w:r><w:t>Antes</w:t><w:br w:type="page"/><w:t>Depois</w:t></w:r>',
        b'<w:r><w:t>Antes</w:t></w:r><m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
        b"<m:r><m:t>x</m:t></m:r></m:oMath><w:r><w:t>Depois</w:t></w:r>",
    ],
    ids=["quebra-de-pagina", "equacao"],
)
def test_quebra_de_pagina_e_equacao_sao_objeto(contrato, conteudo):
    dados = _com_paragrafos(contrato, b"<w:p>" + conteudo + b"</w:p>")
    p = _paragrafos(docx.extrair(dados))[-1]
    assert p["objetos"] and p["texto"] == "AntesDepois"
    with pytest.raises(OperacaoInvalida):
        codigo.editar(dados, "c.docx", [{"op": "texto", "alvo": p["id"], "texto": "x"}])
    with pytest.raises(OperacaoInvalida):  # não atravessa
        codigo.editar(dados, "c.docx", [{"op": "substituir", "alvo": p["id"], "buscar": "AntesDepois", "trocar": "x"}])


def test_texto_novo_fica_dentro_do_bookmark(contrato):
    titulo = (
        b'<w:p><w:bookmarkStart w:id="7" w:name="_Toc1"/><w:r><w:t>Objeto</w:t></w:r><w:bookmarkEnd w:id="7"/></w:p>'
    )
    dados = _com_paragrafos(contrato, titulo)
    alvo = _paragrafos(docx.extrair(dados))[-1]["id"]
    for revisao in (False, True):
        novo, _ = codigo.editar(dados, "c.docx", [{"op": "texto", "alvo": alvo, "texto": "Escopo", "revisao": revisao}])
        assert re.search(r'w:name="_Toc1"/>.*Escopo.*<w:bookmarkEnd w:id="7"/>', _xml(novo))


def test_linhas_dentro_de_controle_de_conteudo_aparecem_na_tabela(contrato):
    def repetida(x):
        k = x.index(b"<w:tr>", x.index(b"<w:tbl>"))
        k = x.index(b"<w:tr>", k + 1)  # a segunda linha vira seção repetida
        fim = x.index(b"</w:tr>", k) + len(b"</w:tr>")
        return x[:k] + b"<w:sdt><w:sdtContent>" + x[k:fim] + b"</w:sdtContent></w:sdt>" + x[fim:]

    dados = _trocar_xml(contrato, repetida)
    tabela = next(b for b in docx.extrair(dados)["blocos"] if b["tipo"] == "tabela")
    assert [[c[0]["texto"] for c in linha] for linha in tabela["linhas"]] == [["Item", "Valor"], ["Soja", "30 dias"]]


def test_paragrafo_sem_troca_valida_nao_fica_marcado(contrato):
    def com_link(x):  # "dias" aqui atravessa a borda do link: não dá para trocar
        return x.replace(
            b"<w:t>Fim.</w:t></w:r>",
            b'<w:t xml:space="preserve">Fim. di</w:t></w:r><w:hyperlink w:anchor="x"><w:r><w:t>as</w:t></w:r>'
            b"</w:hyperlink>",
        )

    dados = _trocar_xml(contrato, com_link)
    ids = [p["id"] for p in _paragrafos(docx.extrair(dados))]
    _, rel = codigo.editar(
        dados,
        "c.docx",
        [
            {"op": "substituir", "buscar": "dias", "trocar": "DIAS"},
            {"op": "texto", "alvo": ids[-1], "texto": "Encerramento"},  # o parágrafo sem troca segue livre
        ],
    )
    assert rel["aplicadas"][0]["trocas"] == 2 and _texto(rel["codigo"], ids[-1]) == "Encerramento"
    assert any("1 ocorrência" in a for a in rel["avisos"])


def test_run_com_muitos_textos_e_paragrafo_alternado_sao_lineares(contrato):
    import time

    picado = b"<w:p><w:r>" + b"<w:t>a</w:t><w:t>x</w:t>" * 10_000 + b"</w:r></w:p>"  # 20 mil w:t num run
    alternado = b"<w:p>" + b"<w:r><w:rPr><w:b/></w:rPr><w:t>a</w:t></w:r><w:r><w:t>b</w:t></w:r>" * 5_000 + b"</w:p>"
    dados = _com_paragrafos(contrato, picado, alternado)
    ps = _paragrafos(docx.extrair(dados))[-2:]
    inicio = time.monotonic()
    _, rel = codigo.editar(
        dados,
        "c.docx",
        [
            {"op": "substituir", "alvo": ps[0]["id"], "buscar": "a", "trocar": "b"},
            {"op": "texto", "alvo": ps[1]["id"], "texto": "Resumo"},  # revisão: 10 mil runs viram w:del
        ],
    )
    assert time.monotonic() - inicio < 10
    assert _texto(rel["codigo"], ps[0]["id"]) == "bx" * 10_000 and _texto(rel["codigo"], ps[1]["id"]) == "Resumo"


def test_conteudo_de_run_desconhecido_e_objeto(contrato):
    ruby = (
        b"<w:p><w:r><w:t>Nome </w:t></w:r><w:r><w:ruby><w:rubyBase><w:r><w:t>X</w:t></w:r></w:rubyBase></w:ruby>"
        b"</w:r><w:r><w:t> fim</w:t></w:r></w:p>"
    )
    dados = _com_paragrafos(contrato, ruby)
    p = _paragrafos(docx.extrair(dados))[-1]
    assert p["objetos"]
    with pytest.raises(OperacaoInvalida):
        codigo.editar(dados, "c.docx", [{"op": "texto", "alvo": p["id"], "texto": "x", "revisao": False}])


def test_texto_com_revisao_nao_vira_link(contrato):
    def com_link(x):
        return x.replace(
            b"<w:t>Fim.</w:t></w:r>",
            b'<w:t xml:space="preserve">Fim. </w:t></w:r><w:hyperlink w:anchor="x"><w:r><w:t>ver</w:t></w:r>'
            b"</w:hyperlink>",
        )

    dados = _trocar_xml(contrato, com_link)
    alvo = _paragrafos(docx.extrair(dados))[-1]["id"]
    novo, _ = codigo.editar(dados, "c.docx", [{"op": "texto", "alvo": alvo, "texto": "Encerramento"}])
    assert re.search(r"</w:hyperlink><w:ins [^>]*><w:r><w:t[^>]*>Encerramento", _xml(novo))


def test_edicao_que_passa_do_prazo_nao_aplica_nada(contrato, monkeypatch):
    monkeypatch.setattr(docx, "EDICAO_PRAZO", -1)
    with pytest.raises(OperacaoInvalida) as e:
        codigo.editar(contrato, "c.docx", [{"op": "comentar", "alvo": "p:n0", "texto": "x"}])
    assert "divida" in str(e.value)


def test_paragrafo_complexo_demais_e_recusado(contrato):
    enorme = b"<w:p>" + b"<w:r><w:rPr><w:b/></w:rPr><w:t>a</w:t></w:r>" * 15_000 + b"</w:p>"
    dados = _com_paragrafos(contrato, enorme)
    alvo = _paragrafos(docx.extrair(dados))[-1]["id"]
    with pytest.raises(OperacaoInvalida) as e:
        codigo.editar(dados, "c.docx", [{"op": "texto", "alvo": alvo, "texto": "x"}])
    assert e.value.codigo == "texto-grande"


def test_texto_longo_de_mesma_formatacao_com_revisao_e_linear(contrato):
    import time

    longo = b"<w:p>" + (b"<w:r><w:t>" + b"x" * 40 + b"</w:t></w:r>") * 4_000 + b"</w:p>"  # 160 mil caracteres
    dados = _com_paragrafos(contrato, longo)
    alvo = _paragrafos(docx.extrair(dados))[-1]["id"]
    for op in ({"op": "texto", "alvo": alvo, "texto": "Curto"}, {"op": "remover_paragrafo", "alvo": alvo}):
        inicio = time.monotonic()
        novo, _ = codigo.editar(dados, "c.docx", [op])
        assert time.monotonic() - inicio < 5
        assert "<w:delText>" + "x" * 160_000 + "</w:delText>" in _xml(novo)  # um delText só para o grupo


def test_paraid_repetido_e_ambiguo(contrato):
    def repetir(x):
        return x.replace(b"<w:p>", b'<w:p w14:paraId="0000ABCD">', 2)

    dados = _trocar_xml(contrato, repetir)
    with pytest.raises(OperacaoInvalida) as e:
        codigo.editar(dados, "c.docx", [{"op": "texto", "alvo": "p:0000ABCD", "texto": "x", "revisao": False}])
    assert e.value.codigo == "alvo-ambiguo"


def test_paragrafo_com_texto_demais_e_recusado_e_op_malformada_nao_vira_erro_interno(contrato):
    longo = b"<w:p><w:r><w:t>" + b"x" * 200_001 + b"</w:t></w:r></w:p>"  # poucos elementos, texto acima do teto
    dados = _com_paragrafos(contrato, longo)
    alvo = _paragrafos(docx.extrair(dados))[-1]["id"]
    with pytest.raises(OperacaoInvalida) as e:
        codigo.editar(dados, "c.docx", [{"op": "texto", "alvo": alvo, "texto": "x"}])
    assert e.value.codigo == "texto-grande"
    for nome in ("c.docx", "d.pptx"):
        with pytest.raises(OperacaoInvalida):
            codigo.editar(contrato if nome == "c.docx" else _deck(), nome, [{"op": []}])


def _deck():
    from pptx import Presentation

    buf = io.BytesIO()
    Presentation().save(buf)
    return buf.getvalue()
