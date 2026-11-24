import base64
import io

from PIL import Image, ImageDraw

import lince


def _png(imagem):
    buf = io.BytesIO()
    imagem.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def test_alfa_sobre_fundo_css_conserva_leitura(tmp_path):
    im = Image.new("RGBA", (200, 80), (255, 255, 255, 0))
    ImageDraw.Draw(im).rectangle((50, 30, 120, 50), fill="white")
    p = tmp_path / "painel.html"
    p.write_text(
        f'<div style="background:#123452 !important;background-color:white"><img alt="painel" src="{_png(im)}"></div>',
        encoding="utf-8",
    )
    (t,) = lince.Leitor().preparar(p).tarefas
    with Image.open(io.BytesIO(t.imagem)) as vista:
        assert vista.getpixel((10, 10))[:3] == (18, 52, 82)
        assert vista.getpixel((60, 40))[:3] == (255, 255, 255)


def test_icone_com_alt_tem_valor_entre_os_rotulos(tmp_path):
    uri = _png(Image.new("RGB", (24, 24), "green"))
    p = tmp_path / "estado.htm"
    p.write_text(
        f'<p>Parecer <img alt="Aprovado sem ressalvas" src="{uri}"> para expedição.</p><img alt="" src="{uri}">',
        encoding="utf-8",
    )
    l = lince.Leitor()
    assert not l.preparar(p).tarefas
    m = l.ler(p).markdown
    assert m.index("Parecer") < m.index("Aprovado sem ressalvas") < m.index("para expedição")
    assert m.count("Aprovado sem ressalvas") == 1


def test_fundo_complexo_conserva_os_pixels_e_o_alfa(tmp_path):
    im = Image.new("RGBA", (160, 90), (123, 45, 6, 120))
    p = tmp_path / "gradiente.html"
    p.write_text(
        f'<div style="background-image:linear-gradient(red,blue);background-color:white"><img src="{_png(im)}"></div>',
        encoding="utf-8",
    )
    (t,) = lince.Leitor().preparar(p).tarefas
    with Image.open(io.BytesIO(t.imagem)) as recorte:
        assert recorte.convert("RGBA").tobytes() == im.tobytes()


def test_cor_moderna_e_alfa_real_no_lote(tmp_path):
    p = tmp_path / "misto.html"
    ims = [Image.new("RGBA", (140, 80), (255, i, 100, 0)) for i in range(4)]
    for im in ims:
        ImageDraw.Draw(im).rectangle((40, 20, 90, 55), fill="white")
    p.write_text(
        "<body style='background:red'><img style='background-color:rgb(0 0 255)' src='"
        + _png(ims[0])
        + "'>"
        + "".join(f'<img style="background:var(--painel)" src="{_png(im)}">' for im in ims[1:])
        + "</body>",
        encoding="utf-8",
    )
    tarefas = lince.Leitor().preparar(p).tarefas
    assert len(tarefas) == 4
    with Image.open(io.BytesIO(tarefas[0].imagem)) as im:
        assert im.getpixel((0, 0))[:3] == (0, 0, 255)
    for t in tarefas[1:]:
        with Image.open(io.BytesIO(t.imagem)) as im:
            assert im.convert("RGBA").getpixel((0, 0))[3] == 0


def test_cor_desconhecida_nao_fica_com_a_declaracao_anterior(tmp_path):
    uri = _png(Image.new("RGBA", (100, 50), (10, 20, 30, 0)))
    for valor in ("inherit", "color-mix(in srgb, red, white)", "initial"):
        p = tmp_path / "cor.html"
        p.write_text(
            '<body style="background:red">'
            f'<img style="background-color:blue;background-color:{valor}" src="{uri}"></body>',
            encoding="utf-8",
        )
        (tarefa,) = lince.Leitor().preparar(p).tarefas
        with Image.open(io.BytesIO(tarefa.imagem)) as vista:
            pixel = vista.convert("RGBA").getpixel((2, 2))
            assert pixel == (255, 0, 0, 255) if valor == "initial" else pixel[3] == 0
