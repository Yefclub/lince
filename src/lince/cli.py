"""Linha de comando: `lince arquivo.pdf [--url URL --modelo M] [--saida pasta]`.

A chave da API vem do argumento --chave ou é lida de um arquivo (--chave-arquivo); nada é lido do ambiente.
"""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from . import CacheDisco, Leitor, OpenAICompativel


def main():
    ap = argparse.ArgumentParser(prog="lince", description="Leitor único de documentos")
    ap.add_argument("arquivos", nargs="+")
    ap.add_argument("--url", help="endpoint compatível com OpenAI (/v1/chat/completions)")
    ap.add_argument("--modelo")
    ap.add_argument("--chave")
    ap.add_argument("--chave-arquivo")
    ap.add_argument("--cache", help="pasta do cache de leituras")
    ap.add_argument("--saida", default=".")
    ap.add_argument("--sem-agrupar", action="store_true")
    a = ap.parse_args()
    chave = a.chave or (Path(a.chave_arquivo).read_text().strip() if a.chave_arquivo else None)
    ia = OpenAICompativel(a.url, a.modelo, chave) if a.url and a.modelo else None
    leitor = Leitor(ia=ia, cache=CacheDisco(a.cache) if a.cache else None, agrupar=not a.sem_agrupar)
    out = Path(a.saida)
    out.mkdir(parents=True, exist_ok=True)
    for arq in a.arquivos:
        r = leitor.ler(arq)
        nome = Path(arq).name
        (out / f"{nome}.md").write_text(r.markdown, encoding="utf-8")
        (out / f"{nome}.json").write_text(
            json.dumps(
                {"metadados": r.metadados, "cobertura": asdict(r.cobertura), "uso": [asdict(u) for u in r.uso]},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        print(
            f"{nome}: {len(r.markdown)} caracteres, {r.cobertura.leituras_ia} leituras pela IA, "
            f"{len(r.cobertura.pendentes)} pendências, {len(r.alertas_injecao)} alertas"
        )


if __name__ == "__main__":
    main()
