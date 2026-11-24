"""Imagens que cabem no provedor chegam inteiras."""

import io

import pytest
from PIL import Image

import lince


@pytest.mark.parametrize("tamanho", [(128, 128), (120, 320), (1568, 1568)])
def test_imagem_que_cabe_chega_inteira(tmp_path, tamanho):
    arquivo = tmp_path / "foto.png"
    original = Image.new("RGB", tamanho, "navy")
    original.putpixel((0, tamanho[1] - 1), (20, 40, 180))
    original.save(arquivo)
    plano = lince.Leitor().preparar(arquivo)
    (tarefa,) = plano.tarefas
    with Image.open(io.BytesIO(tarefa.imagem)) as enviada:
        assert enviada.size == tamanho and enviada.tobytes() == original.tobytes()
    assert "imagem alta" not in tarefa.instrucao.split("Tipo do recorte:")[-1]


def test_recorte_de_imagem_alta_informa_total_mesmo_com_teto(tmp_path):
    arquivo = tmp_path / "alta.png"
    Image.new("RGB", (800, 1800)).save(arquivo)
    plano = lince.Leitor(max_leituras=1).preparar(arquivo)
    (tarefa,) = plano.tarefas
    assert "faixa 1 de 3" in tarefa.instrucao and plano.pendentes
