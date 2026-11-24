"""TIFF com várias páginas precisa informar todas, inclusive sob teto."""

import io

import pytest
from PIL import Image

import lince


@pytest.mark.parametrize("ext", ["tif", "tiff"])
def test_tiff_multipagina_tem_ordem_pagina_e_pendencia(tmp_path, ext):
    caminho = tmp_path / f"cores.{ext}"
    Image.new("RGB", (600, 200), "red").save(
        caminho, save_all=True, append_images=[Image.new("RGB", (600, 200), "blue")]
    )
    cores = []

    def ver(data, instrucao):
        with Image.open(io.BytesIO(data)) as im:
            cor = "vermelho" if im.convert("RGB").getpixel((0, 0))[0] else "azul"
        cores.append(cor)
        return cor

    leitor = lince.Leitor(ia=lince.FuncaoLeitor(ver), concorrencia=1)
    direto = leitor.ler(caminho)
    assert cores == ["vermelho", "azul"] and direto.cobertura.paginas == 2
    plano = leitor.preparar(caminho)
    assert [t.pagina for t in plano.tarefas] == [1, 2]
    assert (
        leitor.concluir(plano, dict(zip((t.id for t in plano.tarefas), cores, strict=True))).markdown == direto.markdown
    )
    teto = lince.Leitor(max_leituras=1)
    parcial = teto.preparar(caminho)
    r = teto.concluir(parcial, {parcial.tarefas[0].id: "vermelho"})
    assert r.cobertura.paginas == 2 and any("página 2" in s for s in r.cobertura.pendentes)


def test_tiff_alto_tem_numero_de_faixa_por_pagina(tmp_path):
    arq = tmp_path / "alto.tif"
    Image.new("RGB", (800, 1800)).save(arq, save_all=True, append_images=[Image.new("RGB", (250, 200))])
    leitor = lince.Leitor()
    plano = leitor.preparar(arq)
    assert [t.pagina for t in plano.tarefas] == [1, 1, 1, 2]
    assert "faixa 3 de 3" in plano.tarefas[2].instrucao and "faixa 1 de 1" in plano.tarefas[3].instrucao


def test_tiff_com_limite_preserva_pagina_lida_e_informa_restante(tmp_path, monkeypatch):
    arq = tmp_path / "limitado.tif"
    Image.new("RGB", (500, 200)).save(arq, save_all=True, append_images=[Image.new("RGB", (500, 200))])
    monkeypatch.setattr(lince.leitor, "PIXELS_IMAGEM_MAX", 100000)
    leitor = lince.Leitor()
    plano = leitor.preparar(arq)
    assert len(plano.tarefas) == 1
    r = leitor.concluir(plano, {plano.tarefas[0].id: "Conteúdo preservado."})
    assert "Conteúdo preservado." in r.markdown and r.cobertura.paginas == 2
    assert len(r.cobertura.pendentes) == 1 and "página 2:" in r.cobertura.pendentes[0]


@pytest.mark.parametrize("erro", [OSError, EOFError])
def test_falha_na_segunda_pagina_do_tiff_conserva_a_primeira(tmp_path, monkeypatch, erro):
    arq = tmp_path / "defeito.tif"
    Image.new("RGB", (600, 200), "red").save(
        arq, save_all=True, append_images=[Image.new("RGB", (600, 200), "blue"), Image.new("RGB", (600, 200), "green")]
    )
    converter = Image.Image.convert

    def falhar(im, *args, **kwargs):
        if im.format == "TIFF" and im.tell() == 1:
            raise erro("erro de decodificação")
        return converter(im, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "convert", falhar)
    r = lince.Leitor(ia=lince.FuncaoLeitor(lambda i, s: "vermelho")).ler(arq)
    assert "vermelho" in r.markdown and "página 1" in r.markdown
    assert any("página 2: quadro TIFF ilegível" in p for p in r.cobertura.pendentes)
    assert r.cobertura.paginas == 3 and "página 3" in r.markdown


def test_teto_de_saida_do_tiff_preserva_as_leituras_que_cabem(tmp_path, monkeypatch):
    arq = tmp_path / "cores.tif"
    cores = [Image.new("RGB", (600, 200), c) for c in ("red", "blue", "green")]
    cores[0].save(arq, save_all=True, append_images=cores[1:])
    leitor = lince.Leitor()
    plano = leitor.preparar(arq)
    textos = {t.id: str(n) * 10 for n, t in enumerate(plano.tarefas, 1)}
    primeira = plano.tarefas[0].id
    tamanho = len(leitor.concluir(plano, {primeira: textos[primeira]}).markdown)
    monkeypatch.setattr(lince.leitor, "SAIDA_MAX", tamanho)
    r = leitor.concluir(plano, textos)
    assert textos[primeira] in r.markdown and len(r.markdown) == tamanho
    assert "página 3" in r.markdown and r.cobertura.leituras_ia == 1
