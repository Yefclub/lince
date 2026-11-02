"""Instrução de cada leitura: prompt versionado + tipo do recorte + texto nativo como âncora."""

from importlib.resources import files

PROMPT_PADRAO = files("lince").joinpath("prompts/leitura.md").read_text(encoding="utf-8")
VERSAO_PROMPT = "leitura-v5"

MODOS = {
    "pagina": "Página inteira de documento: transcreva tudo, na ordem de leitura.",
    "imagem": "Recorte de imagem dentro de um documento.",
    "desenho": (
        "Recorte de desenho vetorial: pode ser GRÁFICO, DIAGRAMA ou TABELA desenhada — identifique e siga"
        " a regra do tipo. Os números do texto nativo abaixo são exatos: use-os nos rótulos e células."
    ),
    "tabela": (
        "Recorte de uma TABELA: reconstrua a estrutura exata (cabeçalhos, células mescladas, cada valor"
        " na sua célula). O texto nativo abaixo tem os valores certos, mas a estrutura pode estar errada."
    ),
    "formula": (
        "Recorte de FÓRMULA(S) escritas no documento: transcreva cada fórmula em LaTeX ($$ … $$), com a"
        " numeração e o texto que estiver junto. O texto nativo abaixo traz os símbolos, mas embaralhados."
    ),
}


def instrucao(t: dict, prompt: str = PROMPT_PADRAO, total_faixas: int | None = None) -> str:
    if t["modo"] == "lote":
        out = prompt + (
            f"\n\nEsta imagem reúne {len(t['itens'])} recortes do MESMO documento, cada um com um número"
            " em vermelho no canto. Leia cada recorte separadamente, com as regras acima, e responda"
            ' SOMENTE um JSON: {"1": "markdown do recorte 1", "2": "..."}. Recorte sem conteúdo'
            " vira string vazia. Não misture o conteúdo de um recorte com o de outro."
        )
        for it in t["itens"]:
            if it.get("ancora"):
                out += f"\n\nTEXTO NATIVO do recorte {it['n']}:\n<<<\n{it['ancora']}\n>>>"
        return out
    out = prompt + f"\n\nTipo do recorte: {MODOS.get(t['modo'], 'Imagem dentro de um documento.')}"
    if t.get("ancora"):
        out += "\n\nTEXTO NATIVO do mesmo trecho (use para grafia e números):\n<<<\n" + t["ancora"] + "\n>>>"
    if t.get("parte") and t["parte"] != "1/1":
        n, tot = t["parte"].split("/")
        out += (
            f"\n\nEsta imagem é a parte {n} de {tot} da página (de cima para baixo, com uma faixa de"
            " sobreposição). Transcreva só as linhas inteiras visíveis; linha cortada na borda de baixo"
            " fica para a próxima parte, linha cortada na borda de cima já saiu na parte anterior."
        )
    if total_faixas:
        out += f"\n\nEsta é a faixa {t['id'][5:]} de {total_faixas} de uma imagem alta; transcreva só o que está nela."
    return out
