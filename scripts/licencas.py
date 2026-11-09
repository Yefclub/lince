"""Confere a licença de cada dependência de produção instalada e gera THIRD_PARTY_NOTICES.md.

Uso (no ambiente com só as dependências de produção):
    python scripts/licencas.py --checar           # falha se alguma licença estiver fora da lista
    python scripts/licencas.py --gerar ARQUIVO     # escreve a tabela de créditos

Só entram licenças permissivas. Copyleft (GPL, LGPL, AGPL, SSPL, MPL, EUPL...) reprova. Pacote permissivo cujo
metadado não diz isso entra pelas exceções declaradas (`excecoes-de-licenca.toml`), presas à versão conferida.
"""

import argparse
import importlib.metadata as md
import re
import sys
import tomllib
from pathlib import Path

PERMITIDAS = {
    "MIT",
    "MIT-CMU",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "PSF-2.0",
    "0BSD",
    "MIT-0",  # a MIT sem a exigência de manter o aviso: mais permissiva que ela (o cffi, desde a 2.1)
    "Zlib",
    "Unlicense",
    "BSL-1.0",  # Boost: permissiva, sem copyleft (a biblioteca Clipper, dentro do pyclipper)
}
CLASSIFICADORES = {
    "MIT License": "MIT",
    "Apache Software License": "Apache-2.0",
    "BSD License": "BSD-3-Clause",
    "ISC License (ISCL)": "ISC",
    "Python Software Foundation License": "PSF-2.0",
}
FORA = {"lince", "pip", "setuptools", "wheel"}
EXCECOES = Path(__file__).with_name("excecoes-de-licenca.toml")


def excecoes(caminho=EXCECOES) -> dict:
    """Exceções declaradas, por nome do pacote em minúsculas: {nome: entrada}."""
    with open(caminho, "rb") as f:
        return {e["pacote"].lower(): e for e in tomllib.load(f).get("excecao", [])}


def licenca(dist) -> str:
    if expr := dist.metadata.get("License-Expression"):
        return expr.strip()
    classes = [c.split("::")[-1].strip() for c in dist.metadata.get_all("Classifier") or [] if c.startswith("License")]
    spdx = list(dict.fromkeys(CLASSIFICADORES.get(c, c) for c in classes))
    if len(spdx) == 1:
        return spdx[0]
    if spdx:  # vários classificadores nem sempre são licença dupla: exige exceção declarada
        return "ambígua: " + "; ".join(spdx)
    return (dist.metadata.get("License") or "desconhecida").splitlines()[0].strip()


def permitida(expr: str) -> bool:
    """SPDX sem parênteses (AND tem precedência): `A OR B` basta uma; `A AND B` exige todas.

    Expressão com parênteses não é interpretada aqui: reprova e vai para as exceções declaradas depois de conferida.
    """
    if "(" in expr or ")" in expr:
        return False
    for alternativa in re.split(r"\s+OR\s+", expr.strip()):
        if all(p.strip() in PERMITIDAS for p in re.split(r"\s+AND\s+", alternativa)):
            return True
    return False


def pacotes():
    vistos = {}
    for d in md.distributions():
        nome = d.metadata["Name"]
        if nome and nome.lower() not in FORA:
            vistos[nome.lower()] = (nome, d.version, licenca(d))
    return [vistos[k] for k in sorted(vistos)]


def conferir(lista, declaradas) -> tuple[list, list]:
    """Aplica as exceções declaradas. Devolve (pacotes com a licença final, problemas). A exceção só vale para a
    versão declarada; exceção sem pacote instalado e licença declarada fora da lista também são problema."""
    final, problemas = [], []
    for n, v, lic in lista:
        if (e := declaradas.get(n.lower())) is not None:
            if e["versao"] != v:
                problemas.append(f"{n} {v}: exceção declarada para a versão {e['versao']}; confira a licença desta")
            elif not permitida(e["licenca"]):
                problemas.append(f"{n} {v}: exceção declara '{e['licenca']}', fora da lista permitida")
            else:
                lic = f"{e['licenca']} (exceção declarada)"
        elif not permitida(lic):
            problemas.append(f"{n} {v}: licença '{lic}' fora da lista permitida")
        final.append((n, v, lic))
    instalados = {n.lower() for n, _, _ in lista}
    problemas += [f"{e['pacote']}: exceção declarada sem o pacote instalado" for k, e in declaradas.items()
                  if k not in instalados]  # fmt: skip
    return final, problemas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checar", action="store_true")
    ap.add_argument("--gerar")
    a = ap.parse_args()
    lista, problemas = conferir(pacotes(), excecoes())
    if a.checar:
        for p in problemas:
            print(f"::error::{p}")
        print(f"{len(lista)} pacotes conferidos, {len(problemas)} problema(s)")
        if problemas:
            sys.exit(1)
    if a.gerar:
        linhas = [
            "# Créditos de terceiros",
            "",
            "O Lince distribui (na imagem Docker) os pacotes abaixo, todos com licença permissiva.",
            "Os textos completos acompanham cada pacote instalado (`*.dist-info/licenses`).",
            "A imagem também inclui a fonte DejaVu (licença Bitstream Vera / domínio público).",
            "",
            "| Pacote | Versão | Licença |",
            "| --- | --- | --- |",
        ]
        linhas += [f"| {n} | {v} | {lic} |" for n, v, lic in lista]
        with open(a.gerar, "w", encoding="utf-8") as f:
            f.write("\n".join(linhas) + "\n")


if __name__ == "__main__":
    main()
