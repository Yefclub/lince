"""Lote incompleto no provedor ou no cache pode ser recuperado."""

import io
import json

import pytest
from docx import Document
from PIL import Image

import lince


@pytest.mark.parametrize("legado", [False, True])
def test_lote_parcial_volta_ao_provedor(tmp_path, legado):
    class Cache:
        valor = '{"1": "um"}' if legado else None

        def obter(self, chave):
            return self.valor

        def guardar(self, chave, texto):
            self.valor = texto

    cache = Cache()
    doc = Document()
    for cor in ("navy", "orange", "green"):
        buf = io.BytesIO()
        Image.new("RGB", (350, 140), cor).save(buf, "PNG")
        doc.add_picture(buf)
    arq = tmp_path / "lote.docx"
    doc.save(arq)
    completa = json.dumps({"1": "um", "2": "dois", "3": "três"})
    tentativas = [completa] if legado else ['{"1": "um"}', completa]
    leitor = lince.Leitor(ia=lince.FuncaoLeitor(lambda i, s: tentativas.pop(0)), cache=cache)
    if not legado:
        primeira = leitor.ler(arq)
        assert len(primeira.cobertura.pendentes) == 2 and "um" in primeira.markdown
    segunda = leitor.ler(arq)
    assert not segunda.cobertura.pendentes and "três" in segunda.markdown
    assert segunda.cobertura.leituras_cache == 0
    assert leitor.ler(arq).cobertura.leituras_cache == 1
    assert not tentativas
