"""A bancada do README roda no CI (versão curta): toda conferência dela tem de passar (issue #33, item 10)."""

import importlib.util
from pathlib import Path


def test_bancada_rapida_passa_em_tudo():
    caminho = Path(__file__).parent.parent / "bench" / "medir.py"
    spec = importlib.util.spec_from_file_location("bench_medir", caminho)
    medir = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(medir)
    linhas = medir.medir(rapido=True)
    assert [nome for nome, _, ok in linhas if not ok] == []
