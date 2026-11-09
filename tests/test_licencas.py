"""A checagem de licença só deixa passar o que é permissivo."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from licencas import permitida  # noqa: E402


@pytest.mark.parametrize(
    ("expressao", "aceita"),
    [
        ("MIT", True),
        ("BSD-3-Clause OR Apache-2.0", True),
        ("GPL-2.0 OR MIT", True),  # escolhe-se a MIT
        ("MIT OR GPL-3.0 AND LGPL-2.1", True),  # AND tem precedência: MIT OR (GPL AND LGPL)
        ("MIT AND GPL-3.0", False),
        ("(MIT OR Apache-2.0) AND LGPL-3.0", False),
        ("ambígua: MIT; GPL-3.0", False),
        ("desconhecida", False),
    ],
)
def test_so_licenca_permissiva_passa(expressao, aceita):
    assert permitida(expressao) is aceita


def test_excecao_declarada_vale_so_para_a_versao_conferida():
    from licencas import conferir, excecoes

    declaradas = {"x": {"pacote": "x", "versao": "1.0", "licenca": "MIT"}}
    final, problemas = conferir([("x", "1.0", "desconhecida")], declaradas)
    assert final == [("x", "1.0", "MIT (exceção declarada)")] and problemas == []
    _, problemas = conferir([("x", "1.1", "desconhecida")], declaradas)  # versão nova: conferir de novo
    assert problemas and "1.0" in problemas[0]
    _, problemas = conferir([("x", "1.0", "MIT")], {"x": dict(declaradas["x"], licenca="GPL-3.0")})
    assert problemas  # a exceção não aceita licença fora da lista
    _, problemas = conferir([], declaradas)  # exceção sem pacote instalado não fica esquecida
    assert problemas == ["x: exceção declarada sem o pacote instalado"]
    for e in excecoes().values():  # toda exceção do repositório diz por quê e onde foi conferida
        assert permitida(e["licenca"]) and e["versao"] and e["motivo"] and e["evidencia"], e["pacote"]
