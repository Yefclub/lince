"""Cache das leituras da IA: a mesma imagem não é paga duas vezes, nem entre versões do Lince, e cache fora do ar
nunca derruba a leitura."""

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

import lince
import lince.leitor
from lince.servidor import cache_do_ambiente


class RedisFalso:
    """O que o CacheRedis usa do cliente redis-py: get e set com ex (expiração em segundos)."""

    def __init__(self):
        self.dados, self.expira = {}, {}

    def get(self, k):
        return self.dados.get(k)

    def set(self, k, v, ex=None):
        self.dados[k], self.expira[k] = v, ex


@pytest.fixture
def pdf_com_figura(tmp_path):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (800, 400), "white")
    ImageDraw.Draw(img).rectangle([100, 100, 300, 350], fill="navy")
    img.save(tmp_path / "f.png")
    pdf = tmp_path / "f.pdf"
    c = canvas.Canvas(str(pdf), pagesize=A4)
    c.drawString(60, 780, "Figura de teste.")
    c.drawImage(str(tmp_path / "f.png"), 60, 400, width=400, height=200)
    c.save()
    return pdf


def _leitor(cache, chamadas):
    return lince.Leitor(
        ia=lince.FuncaoLeitor(lambda i, s: chamadas.append(1) or "lido", "m"), cache=cache, agrupar=False
    )


def test_redis_da_plataforma_guarda_so_o_texto_lido_com_prefixo_e_validade(pdf_com_figura):
    redis, chamadas = RedisFalso(), []
    _leitor(lince.CacheRedis(redis, dias=2), chamadas).ler(pdf_com_figura)
    r = _leitor(lince.CacheRedis(redis, dias=2), chamadas).ler(pdf_com_figura)
    assert len(chamadas) == 1 and r.cobertura.leituras_cache == 1 and r.uso[0].cache
    assert all(k.startswith("lince:leitura:") for k in redis.dados)
    assert set(redis.dados.values()) == {b"lido"}  # só o que a IA leu, nunca o documento
    assert set(redis.expira.values()) == {2 * 86400}


def test_versao_nova_do_lince_nao_paga_de_novo_o_que_ja_foi_lido(pdf_com_figura, monkeypatch):
    cache, chamadas = lince.CacheMemoria(), []
    _leitor(cache, chamadas).ler(pdf_com_figura)
    monkeypatch.setattr(lince.leitor, "VERSAO", "99.0.0")
    _leitor(cache, chamadas).ler(pdf_com_figura)
    assert len(chamadas) == 1


def test_redis_fora_do_ar_nao_derruba_a_leitura(pdf_com_figura):
    chamadas = []
    fora = lince.CacheRedis("redis://127.0.0.1:1/0")  # porta fechada: conexão recusada
    r = _leitor(fora, chamadas).ler(pdf_com_figura)
    assert chamadas and "lido" in r.markdown and not r.cobertura.pendentes


def test_cache_do_servidor_vem_do_ambiente():
    assert cache_do_ambiente({}) is None
    c = cache_do_ambiente({"LINCE_REDIS": "redis://redis:6379/0", "LINCE_CACHE_DIAS": "7"})
    assert isinstance(c, lince.CacheRedis) and c.segundos == 7 * 86400
    assert cache_do_ambiente({"LINCE_REDIS": "redis://redis:6379/0"}).segundos == 30 * 86400
    for env in ({"LINCE_REDIS": "http://redis"}, {"LINCE_REDIS": "redis://r", "LINCE_CACHE_DIAS": "abc"}):
        with pytest.raises(ValueError):
            cache_do_ambiente(env)
    with pytest.raises(ValueError):
        cache_do_ambiente({"LINCE_REDIS": "redis://r", "LINCE_CACHE_DIAS": "0"})


def test_redis_lento_nao_cobra_o_timeout_em_cada_leitura(tmp_path):
    from PIL import Image

    class RedisLento:
        tentativas = 0

        def get(self, k):
            type(self).tentativas += 1
            raise TimeoutError("timeout")

        set = get

    pdf = tmp_path / "varias.pdf"
    c = canvas.Canvas(str(pdf), pagesize=A4)
    for n in range(4):  # quatro páginas, cada uma com uma figura diferente: quatro leituras pela IA
        Image.new("RGB", (800, 400), (40 * n, 0, 120)).save(tmp_path / f"f{n}.png")
        c.drawString(60, 780, f"Página {n}.")
        c.drawImage(str(tmp_path / f"f{n}.png"), 60, 400, width=400, height=200)
        c.showPage()
    c.save()
    chamadas = []
    leitor = lince.Leitor(
        ia=lince.FuncaoLeitor(lambda i, s: chamadas.append(1) or "lido", "m"),
        cache=lince.CacheRedis(RedisLento()),
        agrupar=False,
        concorrencia=1,
    )
    leitor.ler(pdf)
    assert len(chamadas) == 4 and RedisLento.tentativas == 1  # depois da 1ª falha, o cache fica em pausa
