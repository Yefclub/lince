"""Confere a licença de cada dependência de produção instalada e gera THIRD_PARTY_NOTICES.md.

Uso (no ambiente com só as dependências de produção):
    python scripts/licencas.py --checar           # falha se alguma licença estiver fora da lista
    python scripts/licencas.py --gerar ARQUIVO     # escreve a tabela de créditos

Só entram licenças permissivas. Copyleft (GPL, LGPL, AGPL, SSPL, MPL, EUPL...) reprova.
"""

import argparse
import importlib.metadata as md
import re
import sys

PERMITIDAS = {
    "MIT",
    "MIT-CMU",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "PSF-2.0",
    "0BSD",
    "Zlib",
    "Unlicense",
}
CLASSIFICADORES = {
    "MIT License": "MIT",
    "Apache Software License": "Apache-2.0",
    "BSD License": "BSD-3-Clause",
    "ISC License (ISCL)": "ISC",
    "Python Software Foundation License": "PSF-2.0",
}
# Conferidas à mão: o metadado não traz expressão SPDX limpa.
REVISADAS = {
    # BSD-3-Clause OR Apache-2.0 para o pypdfium2; o binário do PDFium traz os textos de terceiros
    # (FreeType FTL, libjpeg-turbo, OpenJPEG, libpng, zlib, lcms, ICU, abseil...), todos permissivos.
    "pypdfium2": "BSD-3-Clause OR Apache-2.0",
}
FORA = {"lince", "pip", "setuptools", "wheel"}


def licenca(dist) -> str:
    nome = dist.metadata["Name"].lower()
    if nome in REVISADAS:
        return REVISADAS[nome]
    if expr := dist.metadata.get("License-Expression"):
        return expr.strip()
    classes = [c.split("::")[-1].strip() for c in dist.metadata.get_all("Classifier") or [] if c.startswith("License")]
    spdx = list(dict.fromkeys(CLASSIFICADORES.get(c, c) for c in classes))
    if len(spdx) == 1:
        return spdx[0]
    if spdx:  # vários classificadores nem sempre são licença dupla: exige revisão em REVISADAS
        return "ambígua: " + "; ".join(spdx)
    return (dist.metadata.get("License") or "desconhecida").splitlines()[0].strip()


def permitida(expr: str) -> bool:
    """SPDX sem parênteses (AND tem precedência): `A OR B` basta uma; `A AND B` exige todas.

    Expressão com parênteses não é interpretada aqui: reprova e vai para REVISADAS depois de conferida.
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
            vistos[nome.lower()] = (nome, d.version, licenca(d), d.metadata.get("Home-page") or "")
    return [vistos[k] for k in sorted(vistos)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checar", action="store_true")
    ap.add_argument("--gerar")
    a = ap.parse_args()
    lista = pacotes()
    ruins = [(n, v, lic) for n, v, lic, _ in lista if not permitida(lic)]
    if a.checar:
        for n, v, lic in ruins:
            print(f"::error::{n} {v}: licença '{lic}' fora da lista permitida")
        print(f"{len(lista)} pacotes conferidos, {len(ruins)} fora da lista")
        if ruins:
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
        linhas += [f"| {n} | {v} | {lic} |" for n, v, lic, _ in lista]
        with open(a.gerar, "w", encoding="utf-8") as f:
            f.write("\n".join(linhas) + "\n")


if __name__ == "__main__":
    main()
