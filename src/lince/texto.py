"""Texto: densidade matemática, tabelas nativas, hifenização, número de página e trava de repetição."""

import hashlib  # noqa: F401
import io  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import zipfile  # noqa: F401
from xml.etree import ElementTree as ET  # noqa: F401

MATH_SIMBOLOS = set("∑∫∏√∇∂±≤≥≠≈∞∈∉⊂⊆∀∃→←⇒⇔·×÷∝αβγδεζηθικλμνξπρστυφχψωΓΔΘΛΞΠΣΦΨΩ")


def densidade_matematica(md):
    if not md:
        return 0.0
    marcas = len(re.findall(r"<su[bp]>", md))
    simbolos = sum(1 for ch in md if ch in MATH_SIMBOLOS)
    return (marcas * 3 + simbolos) / max(len(md), 1) * 100


def limpar_tabelas(md):
    """Tabelas nativas: tira coluna 100% vazia (coluna falsa do extrator) e mede a complexidade.

    Devolve (md, tabelas) onde cada tabela tem o texto limpo e `complexa` = estrutura
    duvidosa (linhas com nº de colunas diferente, muita célula vazia, célula com dois valores).
    """
    linhas, out, tabelas, bloco = md.splitlines(), [], [], []

    def fecha():
        if not bloco:
            return
        rows = [[c.strip() for c in r.strip().strip("|").split("|")] for r in bloco]
        sep = [k for k, r in enumerate(rows) if all(re.fullmatch(r":?-{2,}:?|", c) for c in r)]
        dados = [r for k, r in enumerate(rows) if k not in sep]
        n = max(len(r) for r in dados) if dados else 0
        dados = [r + [""] * (n - len(r)) for r in dados]
        vivas = [j for j in range(n) if any(r[j] for r in dados)]
        dados = [[r[j] for j in vivas] for r in dados]
        celulas = [c for r in dados for c in r]
        vazias = sum(1 for c in celulas if not c) / max(len(celulas), 1)
        duplas = sum(1 for c in celulas if re.fullmatch(r"\S+ \S+", c) and len(set(c.split())) == 1)
        # duas linhas fundidas numa célula ("341,7 2.884,0") ou texto corrido virando tabela
        fundidas = sum(
            1 for c in celulas if len(re.findall(r"[-+]?\d[\d.,]*%?", c)) >= 2 and not re.search(r"[a-zA-Z]{4}", c)
        )
        corridas = sum(1 for c in celulas if len(c) > 120)
        complexa = len(dados) >= 2 and (
            vazias > 0.25 or duplas > 0 or fundidas > 0 or corridas > 0 or len(vivas) < n - 1
        )
        if dados:
            txt = ["| " + " | ".join(dados[0]) + " |", "|" + " --- |" * len(vivas)]
            txt += ["| " + " | ".join(r) + " |" for r in dados[1:]]
            tabelas.append(
                {
                    "md": "\n".join(txt),
                    "complexa": complexa,
                    "palavras": set(re.findall(r"\w+", " ".join(celulas).lower())),
                }
            )
            out.append(f"{{{{TABELA:{len(tabelas) - 1}}}}}")
        bloco.clear()

    for ln in linhas:
        if ln.strip().startswith("|"):
            bloco.append(ln)
        else:
            fecha()
            out.append(ln)
    fecha()
    return "\n".join(out), tabelas


def desifenizar(txt, vocab=None):
    """Hífen de fim de linha: 'nega- tive' → 'negative', mas 'propellant- based' → 'propellant-based'.

    Decide pelo vocabulário do próprio documento: se a 2ª metade existe sozinha no texto
    (ou a forma com hífen aparece em outro lugar), é palavra composta e o hífen fica.
    """
    padrao = r"(\w+)-(?:[ \t]+|[ \t]*\n[ \t]*)([a-zà-ÿ]\w*)"
    if vocab is None:  # vocabulário sem os próprios fragmentos hifenizados
        vocab = set(re.findall(r"\w+", re.sub(padrao, " ", txt).lower()))

    # as formas com hífen que o texto já tem, num conjunto: procurar cada uma no texto inteiro deixava o texto com
    # milhares de quebras quadrático (20 mil levavam 4 s segurando a GIL)
    compostas = {par for p in re.findall(r"\w+(?:-\w+)+", txt.lower()) for par in _pares(p.split("-"))}

    def troca(m):
        a, b = m.group(1), m.group(2)
        if b.lower() in vocab or f"{a}-{b}".lower() in compostas:
            return f"{a}-{b}"
        return a + b

    return re.sub(padrao, troca, txt)


def _pares(partes):
    return (f"{x}-{y}" for x, y in zip(partes, partes[1:], strict=False))


def lixo(txt):
    if not txt.strip():
        return 1.0
    ruins = sum(1 for ch in txt if ch == "�" or 0xE000 <= ord(ch) <= 0xF8FF)
    return ruins / len(txt)


# a linha que é só o número da página: "3", "- 3 -", "— 3 —", "(3)", "Página 3 de 12", "pág. 3/12", "iv"; o número
# fica no grupo `n` (algarismos) ou `r` (romano, só a palavra inteira, nunca o "i" de "Página")
NUM_PAGINA = (
    r"\s*(?:[-–—]\s*|\(\s*)?"
    r"(?:(?:p[aá]g(?:ina)?\.?\s*)?(?P<n>\d{1,4})(?:\s*(?:de|/|of)\s*\d{1,4})?|(?P<r>[ivxlc]{1,6}))"
    r"(?:\s*[-–—]|\s*\))?\s*"
)


def sem_repeticao(txt, limite=10):
    """Trava de loop do modelo: só age em sequência longa (>= limite) de linhas idênticas.

    Mantém a primeira ocorrência e deixa no texto quantas linhas foram removidas.
    """
    linhas, out, k = txt.splitlines(), [], 0
    while k < len(linhas):
        j = k
        while j + 1 < len(linhas) and linhas[j + 1].strip() and linhas[j + 1].strip() == linhas[k].strip():
            j += 1
        run = j - k + 1
        if run >= limite:
            out += [linhas[k], f"[repetição removida: {run - 1} linhas iguais]"]
        else:
            out += linhas[k : j + 1]
        k = j + 1
    return "\n".join(out)
