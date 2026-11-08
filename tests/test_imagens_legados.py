"""Imagens dos binários do Office 97-2003 (.doc, .xls, .ppt) saem do armazém OfficeArt, sem repetir e com teto."""

import io
import struct
import sys
import time
import zlib
from pathlib import Path

import pytest
from PIL import Image

import lince
from lince import legados

REAIS = Path(__file__).parent / "fixtures" / "reais"


def _ler(p):
    vistas = []
    r = lince.Leitor(
        ia=lince.FuncaoLeitor(lambda img, instr: vistas.append(img) or "Lido pela IA.", "m"), agrupar=False
    )
    return r.ler(p), vistas


@pytest.mark.skipif(
    sys.version_info >= (3, 15), reason="o metafile-render ainda não instala no 3.15 (o fallback tem teste)"
)
def test_imagens_do_ppt_do_xls_e_do_doc_sao_lidas_ou_registradas():
    for nome, lidas, nao_lidas in (
        ("poi_pictures.ppt", 4, {"image/pict"}),  # JPEG, PNG, EMF e WMF para a IA; o PICT do Mac fica marcado
        ("poi_SimpleWithImages.xls", 4, set()),
        ("poi_vector_image.doc", 1, set()),  # o EMF desenhado
    ):
        r, vistas = _ler(REAIS / nome)
        assert len(vistas) == lidas, nome
        assert all(Image.open(io.BytesIO(v)).size[0] > 0 for v in vistas)
        for formato in nao_lidas:  # nunca some: fica marcada e registrada
            assert f"imagem {formato} não lida]" in r.markdown, (nome, formato)
        assert len(r.metadados.get("partes_ilegiveis", [])) == len(nao_lidas), nome


def test_emf_e_wmf_sem_o_desenhista_ficam_marcados(monkeypatch):
    monkeypatch.setitem(sys.modules, "metafile_render", None)  # o pacote não está instalado (Python 3.15)
    r, vistas = _ler(REAIS / "poi_SimpleWithImages.xls")
    assert len(vistas) == 2  # só as raster
    assert "imagem WMF não lida]" in r.markdown and "imagem EMF não lida]" in r.markdown


def test_imagem_que_o_corpo_ja_entrega_nao_e_lida_de_novo():
    r, vistas = _ler(REAIS / "lince_imagem_em_linha.doc")
    assert len(vistas) == 1 and "Imagens do documento" not in r.markdown


def _blip(tipo, inst, cab, dados):
    corpo = b"\x00" * 16 + cab + dados
    return struct.pack("<HHI", inst << 4, tipo, len(corpo)) + corpo


def _png(cor, lado=60):
    b = io.BytesIO()
    Image.new("RGB", (lado, lado), cor).save(b, "PNG")
    return b.getvalue()


class _Ole:
    """OLE com o que `legados.imagens` usa: o stream Pictures e, se pedido, muitos outros (que não são lidos)."""

    def __init__(self, dados, outros=0):
        self.dados, self.outros, self.lidos = dados, outros, []

    def listdir(self):
        return [["Pictures"]] + [[f"Outro{n}"] for n in range(self.outros)]

    def exists(self, nome):
        return nome == "Pictures" or (nome.startswith("Outro") and int(nome[5:]) < self.outros)

    def get_size(self, nome):
        return len(self.dados)

    def openstream(self, nome):
        self.lidos.append(nome)
        return io.BytesIO(self.dados)


def _png_blip(cor):
    return _blip(0xF01E, 0x6E0, b"\x00", _png(cor))


def test_bomba_de_metafile_esgota_o_orcamento_e_o_que_veio_antes_fica():
    bomba = _blip(0xF01A, 0x3D4, b"\x00" * 32 + b"\x00\xfe", zlib.compress(b"\x00" * 8 * 1024 * 1024))
    stream = _png_blip("red") + bomba * 4
    achadas, corte = legados.imagens(_Ole(stream), len(stream))
    assert [m for m, _ in achadas] == ["image/png"] and "orçamento" in corte  # cada bomba paga o que descomprime


def test_registro_que_declara_mais_do_que_tem_e_ignorado():
    truncado = struct.pack("<HHI", 0x6E0 << 4, 0xF01E, 10**6) + b"\x89PNG"
    assert list(legados._blips(truncado + _png_blip("red"), legados.Orcamento(10**6)))[0][0] == "image/png"


def test_teto_de_imagens_fica_registrado_so_quando_sobra(monkeypatch):
    monkeypatch.setattr(legados, "IMAGENS_MAX", 2)
    stream = b"".join(_png_blip(c) for c in ("red", "green", "blue"))
    achadas, corte = legados.imagens(_Ole(stream), len(stream))
    assert len(achadas) == 2 and "mais de 2 imagens" in corte
    exato = b"".join(_png_blip(c) for c in ("red", "green"))
    assert legados.imagens(_Ole(exato), len(exato)) == (
        [("image/png", _png("red")), ("image/png", _png("green"))],
        None,
    )


def test_metafile_que_quebra_depois_de_muita_saida_paga_o_que_produziu():
    fluxo = bytearray(zlib.compress(b"\x00" * 8 * 1024 * 1024))
    fluxo[-1] ^= 0xFF  # adler32 errado: o erro só vem no fim, depois de 8 MB descomprimidos
    bomba = _blip(0xF01A, 0x3D4, b"\x00" * 32 + b"\x00\xfe", bytes(fluxo))
    stream = _png_blip("red") + bomba * 4
    achadas, corte = legados.imagens(_Ole(stream), len(stream))
    assert [m for m, _ in achadas] == ["image/png"] and "orçamento" in corte


def test_jpeg_cmyk_e_com_dois_uids_sao_aceitos():
    b = io.BytesIO()
    Image.new("RGB", (60, 60), "#2561C1").save(b, "JPEG")
    jpeg = b.getvalue()
    cmyk = _blip(0xF01D, 0x6E2, b"\x00", jpeg)
    dois_uids = _blip(0xF01D, 0x46B, b"\x00" * 16 + b"\x00", jpeg)  # o segundo UID vem antes do byte de marca
    for registro in (cmyk, dois_uids):
        [(mime, dados)] = list(legados._blips(registro, legados.Orcamento(10**6)))
        assert mime == "image/jpeg" and Image.open(io.BytesIO(dados)).size == (60, 60)


def test_stream_cheio_de_candidatos_nao_prende_a_leitura():
    stream = b"\x00\x00\x1e\xf0" * 2_000_000  # 8 MB de cabeçalhos falsos
    inicio = time.monotonic()
    assert list(legados._blips(stream, legados.Orcamento(len(stream)))) == []
    assert time.monotonic() - inicio < 10


def test_imagem_decorativa_do_armazem_nao_vai_para_a_ia(monkeypatch):
    monkeypatch.setattr(
        legados, "imagens", lambda o, teto: ([("image/png", _png("red", 16)), ("image/png", _png("blue"))], None)
    )
    r, vistas = _ler(REAIS / "poi_vector_image.doc")
    assert len(vistas) == 1 and Image.open(io.BytesIO(vistas[0])).convert("RGB").getpixel((5, 5)) == (0, 0, 255)


def test_extensao_antiga_que_nao_e_ole_nao_ganha_parte_ilegivel(tmp_path):
    p = tmp_path / "relatorio.doc"
    p.write_bytes(b"{\\rtf1\\ansi Relatorio em RTF.\\par}")
    r = lince.ler(p)
    assert "Relatorio em RTF." in r.markdown and not r.metadados.get("partes_ilegiveis")


def test_bitmap_do_armazem_vira_bmp_que_abre():
    b = io.BytesIO()
    Image.new("RGB", (60, 60), "#2561C1").save(b, "BMP")
    dib = b.getvalue()[14:]  # o OfficeArt guarda sem o cabeçalho de arquivo
    [(mime, dados)] = list(legados._blips(_blip(0xF01F, 0x7A8, b"\x00", dib), legados.Orcamento(10**6)))
    assert mime == "image/bmp" and Image.open(io.BytesIO(dados)).convert("RGB").getpixel((5, 5)) == (37, 97, 193)


def test_bitmap_de_32_bits_com_mascaras_abre_no_lugar_certo():
    largura = altura = 4
    mascaras = struct.pack("<III", 0x00FF0000, 0x0000FF00, 0x000000FF)
    cab = struct.pack("<IiiHHIIiiII", 40, largura, altura, 1, 32, 3, 0, 0, 0, 0, 0)  # BI_BITFIELDS
    pixels = struct.pack("<I", 0x002561C1) + struct.pack("<I", 0x0028B877) * (largura * altura - 1)
    [(_, dados)] = list(
        legados._blips(_blip(0xF01F, 0x7A8, b"\x00", cab + mascaras + pixels), legados.Orcamento(10**6))
    )
    im = Image.open(io.BytesIO(dados)).convert("RGB")
    assert im.getpixel((0, altura - 1)) == (37, 97, 193) and im.getpixel((1, altura - 1)) == (40, 184, 119)  # de baixo


def test_bitmap_com_cabecalho_hostil_e_ignorado_sem_perder_os_outros():
    hostil = struct.pack("<IiiHHIIiiII", 0xFFFFFFFF, 4, 4, 1, 8, 0, 0, 0, 0, 0xFFFFFFFF, 0)
    stream = _blip(0xF01F, 0x7A8, b"\x00", hostil) + _png_blip("red")
    achadas, corte = legados.imagens(_Ole(stream), len(stream))
    assert [m for m, _ in achadas] == ["image/png"] and corte is None


def test_metafile_com_fluxo_cortado_nao_vira_imagem():
    cab = b"\x00" * 32 + b"\x00\xfe"
    inteiro = _blip(0xF01A, 0x3D4, cab, zlib.compress(b"EMF" * 5000))
    cortado = _blip(0xF01A, 0x3D4, cab, zlib.compress(b"EMF" * 5000)[:-10])
    assert [len(d) for _, d in legados._blips(inteiro, legados.Orcamento(10**6))] == [15000]
    assert list(legados._blips(cortado, legados.Orcamento(10**6))) == []


def test_so_os_streams_do_armazem_sao_lidos():
    ole = _Ole(_png_blip("red"), outros=25_000)  # milhares de streams declarados não multiplicam o trabalho
    achadas, corte = legados.imagens(ole, 10**6)
    assert ole.lidos == ["Pictures"] and len(achadas) == 1 and corte is None


def test_stream_maior_que_o_arquivo_corta_sem_perder_o_que_foi_achado():
    class _Dois(_Ole):
        def exists(self, nome):
            return nome in ("Pictures", "Data")

        def get_size(self, nome):
            return len(self.dados) if nome == "Pictures" else 10**9  # o Data declara mais do que o arquivo

    achadas, corte = legados.imagens(_Dois(_png_blip("red")), 10**6)
    assert len(achadas) == 1 and "declara mais bytes" in corte


def test_flush_quebrado_nao_derruba_o_resto(monkeypatch):
    real = zlib.decompressobj

    class _Quebra:
        def __init__(self):
            self.d = real()

        def decompress(self, dados, n):
            return self.d.decompress(dados, n)

        @property
        def unconsumed_tail(self):
            return self.d.unconsumed_tail

        def flush(self):
            raise zlib.error("invalid distance too far back")

    monkeypatch.setattr(zlib, "decompressobj", _Quebra)
    stream = _png_blip("red") + _blip(0xF01A, 0x3D4, b"\x00" * 32 + b"\x00\xfe", zlib.compress(b"EMF" * 100))
    achadas, corte = legados.imagens(_Ole(stream), len(stream))
    assert [m for m, _ in achadas] == ["image/png"] and corte is None


def test_imagens_do_word_saem_do_data_e_do_worddocument():
    for nome in ("Data", "WordDocument", "1Table"):

        class _Word(_Ole):
            def exists(self, n, nome=nome):
                return n == nome

        ole = _Word(_png_blip("red"))
        achadas, corte = legados.imagens(ole, 10**6)
        assert ole.lidos == [nome] and len(achadas) == 1 and corte is None, nome


def test_cadeia_de_setores_cortada_corta_sem_perder_o_que_foi_achado():
    class _Cortado(_Ole):
        def exists(self, nome):
            return nome in ("Pictures", "Data")

        def openstream(self, nome):
            if nome == "Data":
                raise OSError("unexpected end of chain")
            return super().openstream(nome)

    achadas, corte = legados.imagens(_Cortado(_png_blip("red")), 10**6)
    assert len(achadas) == 1 and "OSError" in corte


def test_storage_no_lugar_do_stream_corta_sem_perder_o_que_foi_achado():
    class _Storage(_Ole):
        def exists(self, nome):
            return nome in ("Pictures", "Data")

        def get_size(self, nome):
            if nome == "Data":
                raise TypeError("object is not an OLE stream")
            return super().get_size(nome)

    achadas, corte = legados.imagens(_Storage(_png_blip("red")), 10**6)
    assert len(achadas) == 1 and "TypeError" in corte
