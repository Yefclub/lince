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
