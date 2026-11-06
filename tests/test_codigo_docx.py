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
            b'<w:t>Fim.</w:t></w:r><w:hyperlink w:anchor="x"><w:r><w:t xml:space="preserve"> Ver anexo</w:t></w:r>'
            b"</w:hyperlink>",
        )

    dados = _trocar_xml(contrato, com_link)
    novo, _ = codigo.editar(  # dentro do link: troca e o link continua
        dados, "c.docx", [{"op": "substituir", "buscar": "anexo", "trocar": "apêndice", "revisao": False}]
    )
    assert '<w:hyperlink w:anchor="x">' in _xml(novo) and "apêndice" in _xml(novo)
    with pytest.raises(OperacaoInvalida) as e:  # cruza a borda do link: não troca
        codigo.editar(dados, "c.docx", [{"op": "substituir", "buscar": "Fim. Ver", "trocar": "x"}])
    assert e.value.codigo == "conflito"
