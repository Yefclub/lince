"""Imagens dos binários do Office 97-2003 (.doc, .xls, .ppt) saem do armazém OfficeArt, sem repetir e com teto."""

import io
import struct
import zlib
from pathlib import Path

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


def test_imagens_do_ppt_do_xls_e_do_doc_sao_lidas_ou_registradas():
    for nome, raster, nao_lidas in (
        ("poi_pictures.ppt", 2, {"WMF", "image/pict", "EMF"}),  # JPEG e PNG para a IA
        ("poi_SimpleWithImages.xls", 2, {"WMF", "EMF"}),
        ("poi_vector_image.doc", 0, {"EMF"}),
    ):
        r, vistas = _ler(REAIS / nome)
        assert len(vistas) == raster, nome
        assert all(Image.open(io.BytesIO(v)).size[0] > 0 for v in vistas)
        for formato in nao_lidas:  # nunca some: fica marcada e registrada
            assert f"imagem {formato} não lida]" in r.markdown, (nome, formato)
        assert len(r.metadados["partes_ilegiveis"]) == len(nao_lidas), nome


def test_imagem_que_o_corpo_ja_entrega_nao_e_lida_de_novo():
    r, vistas = _ler(REAIS / "lince_imagem_em_linha.doc")
    assert len(vistas) == 1 and "Imagens do documento" not in r.markdown


def _blip(tipo, inst, cab, dados):
    corpo = b"\x00" * 16 + cab + dados
    return struct.pack("<HHI", inst << 4, tipo, len(corpo)) + corpo


def test_armazem_hostil_nao_gasta_sem_limite():
    bomba = zlib.compress(b"\x00" * (legados._METAFILE_MAX + 1))  # metafile que descomprime além do teto
    cab_emf = b"\x00" * 32 + b"\x00\xfe"  # compressão 0 (DEFLATE), filtro 0xFE
    truncado = struct.pack("<HHI", 0x6E0 << 4, 0xF01E, 10**6) + b"\x89PNG"  # declara mais bytes do que tem
    achados = list(legados._blips(_blip(0xF01A, 0x3D4, cab_emf, bomba) + truncado, legados.Orcamento(10**9)))
    assert achados == []


def test_bitmap_do_armazem_vira_bmp_que_abre():
    b = io.BytesIO()
    Image.new("RGB", (60, 60), "#2561C1").save(b, "BMP")
    dib = b.getvalue()[14:]  # o OfficeArt guarda sem o cabeçalho de arquivo
    [(mime, dados)] = list(legados._blips(_blip(0xF01F, 0x7A8, b"\x00", dib), legados.Orcamento(10**6)))
    assert mime == "image/bmp" and Image.open(io.BytesIO(dados)).convert("RGB").getpixel((5, 5)) == (37, 97, 193)
