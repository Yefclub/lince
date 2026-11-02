"""Changelog por PR: cada mudança traz changelog/unreleased/<slug>.md; a release junta tudo no CHANGELOG.md.

Formato do arquivo:

    ---
    tipo: feat        # feat | fix | perf | security | infra | docs | chore
    titulo: Frase curta, para quem usa o Lince
    ---

    O que mudou, onde aparece e por quê.

Uso:
    python scripts/changelog.py --conferir                       # valida os arquivos pendentes (CI de PR)
    python scripts/changelog.py --consumir 1.2.0 notas.md        # release: CHANGELOG.md + notas; apaga os pendentes
"""

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

PENDENTES = Path("changelog/unreleased")
TIPOS = {
    "feat": "Novidades",
    "fix": "Correções",
    "perf": "Desempenho",
    "security": "Segurança",
    "infra": "Infraestrutura",
    "docs": "Documentação",
    "chore": "Manutenção",
}


def ler(caminho: Path) -> dict:
    texto = caminho.read_text(encoding="utf-8")
    m = re.fullmatch(r"---\n(.*?)\n---\n(.*)", texto, flags=re.S)
    if not m:
        raise ValueError("falta o cabeçalho entre linhas '---'")
    campos = dict(re.findall(r"^(\w+):\s*(.+?)\s*(?:#.*)?$", m.group(1), flags=re.M))
    if campos.get("tipo") not in TIPOS:
        raise ValueError(f"tipo deve ser um de: {', '.join(TIPOS)}")
    if not campos.get("titulo"):
        raise ValueError("falta o titulo")
    corpo = m.group(2).strip()
    if not corpo:
        raise ValueError("o corpo não pode ficar vazio: diga o que mudou e por quê")
    if re.search(r"^#{1,3} ", corpo, flags=re.M):
        raise ValueError("use #### ou **negrito** no corpo; #, ## e ### são da página do changelog")
    return {"tipo": campos["tipo"], "titulo": campos["titulo"], "corpo": corpo}


def pendentes() -> list[Path]:
    return sorted(p for p in PENDENTES.glob("*.md"))


def conferir() -> int:
    erros = 0
    for p in pendentes():
        try:
            ler(p)
        except ValueError as e:
            print(f"::error file={p}::{e}")
            erros += 1
    return erros


def consumir(versao: str, notas: Path) -> None:
    itens = [(p, ler(p)) for p in pendentes()]
    blocos = []
    for tipo, nome in TIPOS.items():
        do_tipo = [i for _, i in itens if i["tipo"] == tipo]
        if do_tipo:
            blocos.append(f"### {nome}\n\n" + "\n\n".join(f"**{i['titulo']}**\n\n{i['corpo']}" for i in do_tipo))
    corpo = "\n\n".join(blocos) or "Atualizações de manutenção e dependências."
    notas.write_text(corpo + "\n", encoding="utf-8")
    secao = f"## {versao} — {dt.date.today().isoformat()}\n\n{corpo}\n"
    changelog = Path("CHANGELOG.md")
    atual = changelog.read_text(encoding="utf-8") if changelog.exists() else "# Changelog\n"
    cabeca, _, resto = atual.partition("\n")
    changelog.write_text(f"{cabeca}\n\n{secao}\n{resto.lstrip()}".rstrip() + "\n", encoding="utf-8")
    for p, _ in itens:
        p.unlink()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conferir", action="store_true")
    ap.add_argument("--consumir", nargs=2, metavar=("VERSAO", "NOTAS"))
    a = ap.parse_args()
    if a.conferir:
        sys.exit(1 if conferir() else 0)
    if a.consumir:
        consumir(a.consumir[0], Path(a.consumir[1]))


if __name__ == "__main__":
    main()
