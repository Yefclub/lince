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

    Decide pelo vocabulário do próprio documento: se a 2ª metade existe sozinha no texto, ou se o par com hífen
    aparece em outro lugar como duas partes vizinhas de uma palavra hifenizada ('propellant-based', 'x-co-op'), é
    palavra composta e o hífen fica. O par vale inteiro: 'co- op' junta em 'coop' mesmo com 'co-operation' no texto
    (procurar 'co-op' como trecho de qualquer palavra custava o texto inteiro a cada quebra).

    Linear no tamanho do texto: as expressões começam no início de uma palavra (`\b`). Sem a âncora, o `re` tentava
    de novo em cada letra de uma palavra sem hífen e recuava até o começo dela a cada tentativa: uma palavra de 20 mil
    letras levava ~7 s.
    """
    padrao = r"\b(\w+)-(?:[ \t]+|[ \t]*\n[ \t]*)([a-zà-ÿ]\w*)"
    if vocab is None:  # vocabulário sem os próprios fragmentos hifenizados
        vocab = set(re.findall(r"\w+", re.sub(padrao, " ", txt).lower()))

    # os pares com hífen que o texto já tem, num conjunto: procurar cada um no texto inteiro deixava o texto com
    # milhares de quebras quadrático (20 mil levavam 4 s segurando a GIL)
    compostas = {par for p in re.findall(r"\b\w+(?:-\w+)+", txt.lower()) for par in _pares(p.split("-"))}

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


_TITULO_DO_BLOCO = re.compile(r"(#{1,6})[ \t]+(\S.*)")
# "i. ", "2) ", "a. ": item de lista novo, não continuação da frase
_ITEM_DE_LISTA = re.compile(r"(?:[ivxlc]{1,6}|\d{1,3}|[a-z])[.)][ \t]")
_CERCA = re.compile(r" {0,3}(?:`{3,}|~{3,})")  # linha que abre ou fecha bloco de código
_FIM_DE_FRASE = re.compile(r"[.!?…][\d⁰¹²³⁴⁵⁶⁷⁸⁹)\]”\"']*")  # "aquecido.", "crescendo.⁵", "(nota 3)."
PALAVRAS_FRASE_MIN = 6  # título curto ("Atividade econômica") nunca é tratado como frase partida


def _continua(t: str) -> bool:
    """O texto continua uma frase: começa em letra minúscula e não é um item de lista novo."""
    t = t.lstrip()
    return bool(t) and t[0].isalpha() and t[0].islower() and not _ITEM_DE_LISTA.match(t)


def _fecha_a_frase(negrito: str) -> bool:
    """O negrito do começo do bloco continua a frase e a termina: com a pontuação final dentro ("**seguiu aquecido.**
    O PIB…") ou logo depois dele ("**… de 2026**. Logo…"), ou ocupando o bloco inteiro. Trechos em negrito colados
    ("**… e** **perspectivas da economia.**") contam como um só."""
    texto = negrito.replace("** **", " ")
    fim = texto.find("**", 2)
    if fim < 0 or not _continua(texto[2:fim]):
        return False
    trecho, depois = texto[2:fim].rstrip(), texto[fim + 2 :]
    pontuado = any(_FIM_DE_FRASE.fullmatch(trecho[k:]) for k in range(max(0, len(trecho) - 8), len(trecho)))
    return pontuado or depois[:1] in (".", "!", "?", "…") or not depois.strip()


def grupos_de_frases(blocos: list[str]) -> list[tuple[int, int, str]]:
    """Os blocos da página em grupos (início, fim, texto): a frase de abertura em negrito que o extrator do PDF parte em
    título e continuação ("## A atividade econômica expandiu-se em ritmo mais moderado, mas o mercado de trabalho" +
    "**seguiu aquecido.** O PIB…") volta a ser um parágrafo só, com a frase em negrito ("**A atividade… trabalho
    seguiu aquecido.** O PIB…"), e o título partido em vários títulos seguidos do mesmo nível, cada um continuando o
    anterior em minúscula, vira uma frase em negrito. Os outros blocos são grupos de um bloco, com o texto como está.

    Só junta com a prova da quebra: título de PALAVRAS_FRASE_MIN palavras ou mais, que não termina em pontuação,
    seguido de título do mesmo nível que continua a frase ou de negrito que continua e termina a frase. Fica como está:
    título seguido de frase nova, de subtítulo (nível mais fundo), de item de lista, de nota ("**nota:** …") ou de
    rótulo ("Riscos para a inflação:"), e tudo dentro de bloco de código. Nenhuma palavra sai do texto, e cada bloco é
    visto no máximo duas vezes."""
    grupos, k, codigo = [], 0, False
    while k < len(blocos):
        bloco = blocos[k].strip()
        cercas = sum(1 for ln in bloco.split("\n") if _CERCA.match(ln))
        m = _TITULO_DO_BLOCO.fullmatch(bloco) if not codigo and not cercas and "\n" not in bloco else None
        codigo ^= cercas % 2 == 1
        if not m or len(m.group(2).split()) < PALAVRAS_FRASE_MIN or m.group(2).rstrip()[-1] in ".:;!?":
            grupos.append((k, k + 1, blocos[k]))
            k += 1
            continue
        nivel, partes, j = m.group(1), [m.group(2).strip()], k + 1
        while (
            j < len(blocos)
            and (n := _TITULO_DO_BLOCO.fullmatch(blocos[j].strip()))
            and n.group(1) == nivel
            and _continua(n.group(2))
        ):
            partes.append(n.group(2).strip())
            j += 1
        seguinte = blocos[j].lstrip() if j < len(blocos) else ""
        if seguinte.startswith("**") and _fecha_a_frase(seguinte):
            grupos.append((k, j + 1, "**" + " ".join(partes) + " " + seguinte[2:]))
            k = j + 1
        elif len(partes) > 1:
            grupos.append((k, j, "**" + " ".join(partes) + "**"))
            k = j
        else:
            grupos.append((k, k + 1, blocos[k]))
            k += 1
    return grupos


def juntar_frases_partidas(blocos: list[str]) -> list[str]:
    """Os blocos com as frases partidas juntadas (`grupos_de_frases`)."""
    return [texto for _, _, texto in grupos_de_frases(blocos)]
