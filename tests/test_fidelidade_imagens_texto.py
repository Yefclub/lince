import io

import pytest
from PIL import Image, ImageOps

import lince


@pytest.mark.parametrize("cod,bom", [("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff")])
@pytest.mark.parametrize("ext", ["txt", "xml"])
def test_texto_com_assinatura_unicode(tmp_path, cod, bom, ext):
    s = "<registro>Vazão 18,3 m³/s</registro>"
    p = tmp_path / f"texto.{ext}"
    p.write_bytes(bom + s.encode(cod))
    assert lince.ler(p).markdown == s


@pytest.mark.parametrize("ext", ["gif", "webp"])
def test_animacao_entrega_os_estados_distintos_em_ordem(tmp_path, ext, monkeypatch):
    monkeypatch.setattr(lince.leitor, "TAREFAS_POR_DOCUMENTO", 2)
    p = tmp_path / f"estado.{ext}"
    Image.new("RGB", (200, 96), "red").save(
        p, save_all=True, append_images=[Image.new("RGB", (200, 96), "blue")], lossless=True, duration=120
    )
    leitor = lince.Leitor()
    plano = leitor.preparar(p)
    assert [t.pagina for t in plano.tarefas] == [1, 2]
    cores = [Image.open(io.BytesIO(t.imagem)).convert("RGB").getpixel((5, 5)) for t in plano.tarefas]
    assert cores == [(255, 0, 0), (0, 0, 255)]
    r = leitor.concluir(plano, {t.id: f"Estado {t.pagina}" for t in plano.tarefas})
    assert r.cobertura.paginas == 2 and not r.cobertura.pendentes
    assert r.markdown.index("Estado 1") < r.markdown.index("Estado 2")


@pytest.mark.parametrize("orientacao", [2, 6, 8])
def test_orientacao_aplicada_antes_da_entrega(tmp_path, orientacao):
    p = tmp_path / "foto.jpg"
    im = Image.new("RGB", (240, 110), "navy")
    im.paste("red", (0, 0, 100, 50))
    exif = Image.Exif()
    exif[274] = orientacao
    im.save(p, exif=exif)
    with Image.open(p) as original:
        esperado = ImageOps.exif_transpose(original).convert("RGB")
    (tarefa,) = lince.Leitor().preparar(p).tarefas
    with Image.open(io.BytesIO(tarefa.imagem)) as vista:
        assert vista.size == esperado.size and vista.convert("RGB").tobytes() == esperado.tobytes()
