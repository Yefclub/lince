"""Localização do texto matemático no fluxo da página ou de uma coluna."""

import re
import unicodedata


def _compacto(texto):
    return re.sub(r"</?(?:su[bp]|u)>|[\s*]", "", unicodedata.normalize("NFKC", texto))


def localizar_formulas(blocos, itens, largura, tique):
    """Exige que o bloco inteiro corresponda a itens inteiros em uma só posição.
    Um caractere de outra coluna que também apareça na fórmula não amplia a caixa."""
    alvos = {b: _compacto(b) for b in blocos}
    achados = {b: set() for b in alvos}
    if sum(len(x.text) for x in itens) > 128_000:
        return {}
    esquerda, direita = [], []
    for x in itens:
        if x.x + x.width <= largura / 2:
            esquerda.append(x)
        elif x.x >= largura / 2:
            direita.append(x)
    fluxos = [itens]
    if esquerda and direita:
        fluxos.extend((esquerda, direita))
    for fluxo in fluxos:
        partes, limites = [], {0: 0}
        offset = 0
        for x in fluxo:
            trecho = _compacto(x.text)
            if not trecho:
                continue
            partes.append((x, trecho))
            offset += len(trecho)
            limites[offset] = len(partes)
        texto = "".join(trecho for _, trecho in partes)
        for bloco, alvo in alvos.items():
            tique()
            if not alvo or len(achados[bloco]) > 1:
                continue
            for i, ini in limites.items():
                fim = limites.get(i + len(alvo))
                if fim is None or not texto.startswith(alvo, i):
                    continue
                usados = [x for x, _ in partes[ini:fim]]
                achados[bloco].add(
                    (
                        min(x.x for x in usados),
                        min(x.y for x in usados),
                        max(x.x + x.width for x in usados),
                        max(x.y + x.height for x in usados),
                    )
                )
                if len(achados[bloco]) > 1:
                    break
    return {b: next(iter(caixas)) for b, caixas in achados.items() if len(caixas) == 1}
