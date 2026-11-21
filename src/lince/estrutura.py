"""Estrutura do documento para quem navega ou indexa: páginas, slides, abas, nomes definidos e seções do Markdown.

Os números vêm do próprio arquivo, não do Markdown: slide sem título e aba vazia também contam. Estrutura é auxílio:
arquivo fora do padrão fica sem ela, nunca derruba a leitura. Posições (`inicio`) são em pontos de código Unicode,
como `len()` do Python; em JavaScript, conte por `Array.from(texto)` (um emoji ocupa duas unidades UTF-16)."""

import bisect
import re
import struct
import zipfile

from defusedxml.ElementTree import iterparse

from .limites import xml_ou_nada

NOMES_DEFINIDOS_MAX = 32
SECOES_MAX = 2000  # documento com mais títulos que isso é índice de livro: as primeiras bastam para navegar
# título ATX: o conteúdo perde só espaço e tab (espaço ideográfico ou NBSP no início fazem parte dele, como no
# CommonMark); linear, porque o fim é aparado fora do regex
_TITULO = re.compile(r"(#{1,6})[ \t]+([^ \t\r].*)")
_CERCA = re.compile(r" {0,3}(`{3,}|~{3,})(.*?)\r?")
_PAGINA = re.compile(r"<!-- página ([1-9]\d*) · \w+ -->\r?")  # exatamente o que a montagem do PDF escreve
_SLIDE = re.compile(r"<!-- slide ([1-9]\d*) -->\r?")  # o que a leitura da apresentação escreve no começo de cada slide
_PPTX = {".pptx", ".pptm", ".ppsx", ".ppsm"}
_XLSX = {".xlsx", ".xlsm"}
_ODF_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_ODF_STYLE = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"


def secoes(md: str, pdf: bool = False, apresentacao: bool = False) -> dict:
    """Títulos do Markdown fora de bloco de código (cercas pela regra do CommonMark), com nível e posição. No PDF,
    também onde começa cada página: o marcador é do próprio Lince e fecha qualquer cerca deixada aberta (uma resposta
    da IA cortada no teto não apaga as âncoras das páginas seguintes)."""
    titulos, paginas, slides, pos, cerca = [], [], [], 0, None  # cerca aberta: (caractere, tamanho)
    cortadas = False
    for linha in md.split("\n"):
        if pdf and (m := _PAGINA.fullmatch(linha)):
            paginas.append({"pagina": int(m.group(1)), "inicio": pos})
            cerca = None
        elif apresentacao and (m := _SLIDE.fullmatch(linha)):
            slides.append({"slide": int(m.group(1)), "inicio": pos})
            cerca = None
        elif m := _CERCA.fullmatch(linha):
            marca, resto = m.group(1), m.group(2)
            if cerca is None:
                if not (marca[0] == "`" and "`" in resto):  # ```x``` na linha é código inline, não cerca
                    cerca = (marca[0], len(marca))
            elif marca[0] == cerca[0] and len(marca) >= cerca[1] and not resto.strip():
                cerca = None  # fecha só com o mesmo caractere, pelo menos o mesmo tamanho e sem texto depois
        elif cerca is None and (m := _TITULO.fullmatch(linha)):
            if len(titulos) < SECOES_MAX:
                titulo = m.group(2).rstrip(" \t\r")
                titulos.append({"titulo": titulo, "nivel": len(m.group(1)), "inicio": pos})
            else:
                cortadas = True
        pos += len(linha) + 1
    saida = {"secoes": titulos}
    if cortadas:
        saida["secoes_cortadas"] = True
    if paginas:
        saida["inicio_das_paginas"] = paginas
    if slides:
        saida["inicio_dos_slides"] = slides
    return saida


def estrutura_arquivo(path, ext) -> dict:
    """Slides e abas dos formatos em pacote (OOXML e ODF). Os binários antigos vêm de `legados`."""
    try:
        with zipfile.ZipFile(path) as z:
            if ext in _PPTX:
                raiz = xml_ou_nada(z.read("ppt/presentation.xml"))
                if raiz is None:
                    return {}
                # só o sldIdLst filho da raiz: as seções do PowerPoint 2010+ repetem cada slide em p14:sldIdLst
                listas = [lista for lista in raiz if lista.tag.endswith("}sldIdLst")]
                return {"slides": sum(1 for s in (listas[0] if listas else []) if s.tag.endswith("}sldId"))}
            if ext in _XLSX:
                return _abas_xlsx(z.read("xl/workbook.xml"))
            if ext == ".xlsb":
                return _abas_xlsb(z.read("xl/workbook.bin"))
            if ext in (".ods", ".odp"):
                with z.open("content.xml") as f:
                    return _odf(f, ext)
    except MemoryError:
        raise
    except Exception:  # noqa: BLE001 — sem estrutura, a leitura segue igual
        return {}
    return {}


def _abas_xlsx(dados) -> dict:
    raiz = xml_ou_nada(dados)
    if raiz is None:
        return {}
    abas = [  # pelo nome local: vale também no OOXML Strict, que usa outro namespace
        {"nome": s.get("name", ""), "oculta": s.get("state") in ("hidden", "veryHidden")}
        for s in raiz.iter()
        if s.tag.endswith("}sheet")
    ]
    # intervalos com nome do usuário, por onde a IA consulta a planilha: os reservados do Excel (_xlnm.Print_Area,
    # _FilterDatabase) e os ocultos ficam de fora, e o nome com escopo de aba entra uma vez
    nomes = list(
        dict.fromkeys(
            n.get("name", "")[:120]
            for n in raiz.iter()
            if n.tag.endswith("}definedName")
            and not n.get("name", "").startswith("_xlnm.")
            and n.get("hidden") not in ("1", "true")
        )
    )
    saida = {"abas": abas} if abas else {}
    if nomes:
        saida["nomes_definidos"] = nomes[:NOMES_DEFINIDOS_MAX]
        if len(nomes) > NOMES_DEFINIDOS_MAX:
            saida["nomes_definidos_cortados"] = True
    return saida


def _abas_xlsb(b: bytes) -> dict:
    """BrtBundleSh (0x9C) do workbook.bin: hsState, iTabID, strRelID e strName (MS-XLSB 2.4.304)."""
    abas, i = [], 0
    while i < len(b):
        tipo = b[i]
        i += 1
        if tipo & 0x80:
            tipo = (tipo & 0x7F) | (b[i] << 7)
            i += 1
        tam, desloc = 0, 0
        for _ in range(4):
            c = b[i]
            i += 1
            tam |= (c & 0x7F) << desloc
            desloc += 7
            if not c & 0x80:
                break
        d = b[i : i + tam]
        i += tam
        if tipo == 0x9C and len(d) >= 16:
            estado = struct.unpack_from("<I", d, 0)[0]
            j = 8
            n_rel = struct.unpack_from("<I", d, j)[0]
            j += 4 + (0 if n_rel == 0xFFFFFFFF else 2 * n_rel)
            if j + 4 <= len(d):
                n = struct.unpack_from("<I", d, j)[0]
                nome = d[j + 4 : j + 4 + 2 * n].decode("utf-16-le", "replace")
                abas.append({"nome": nome, "oculta": bool(estado & 3)})
    return {"abas": abas} if abas else {}


def _odf(arquivo, ext) -> dict:
    """content.xml em leitura incremental (expat: linear, sem expansão de entidades), limpando cada elemento: a
    planilha pode ser enorme e só os nomes das tabelas e os estilos que as escondem interessam."""
    ocultos, abas, slides, estilo = set(), [], 0, None
    for evento, el in iterparse(arquivo, events=("start", "end")):
        if evento == "start":
            if el.tag == f"{_ODF_STYLE}style" and el.get(f"{_ODF_STYLE}family") == "table":
                estilo = el.get(f"{_ODF_STYLE}name")
            elif el.tag == f"{_ODF_STYLE}table-properties" and estilo and el.get(f"{_ODF_TABLE}display") == "false":
                ocultos.add(estilo)
            elif el.tag == f"{_ODF_TABLE}table" and ext == ".ods":
                nome = el.get(f"{_ODF_TABLE}name")
                if nome is not None:
                    abas.append({"nome": nome, "oculta": el.get(f"{_ODF_TABLE}style-name") in ocultos})
            elif el.tag == "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}page":
                slides += 1
            continue
        if el.tag == f"{_ODF_STYLE}style":
            estilo = None
        el.clear()
    if ext == ".odp":
        return {"slides": slides}
    return {"abas": abas} if abas else {}


INDICE_MAX = SECOES_MAX
# título de seção cabe numa linha: acima disto é parágrafo que o extrator promoveu a título, e nenhuma conta do índice
# (normalização, busca do número de página no fim) passa dele, para o custo ficar linear em qualquer arquivo
TITULO_INDICE_MAX = 200
_PALAVRAS_TITULO_MAX = 12  # frase em negrito promovida a título tem mais palavras que isso; título de seção, não
_NUMERO_FINAL = re.compile(r"\d{1,4}$")  # no máximo 4 caracteres por posição: linear, sem retrocesso
_SEPARADORES = " \t.·…_-"  # entre o título e o número de página no sumário impresso ("Introdução ....... 3")
_REFERENCIA = re.compile(r"\[\d{1,4}\]\s")  # "[12] Autor. Título": item da bibliografia, não seção
_ANCORA_IA = re.compile(r"<!-- lido pela IA: \S+ -->")  # o que a montagem põe antes da leitura de uma página
_NUMERACAO = re.compile(r"(?:\d{1,3}(?:\.\d{1,3}){0,5}\.?|[ivxlc]{1,6}\.)\s+")  # "1.2 ", "3. ", "iv. "


def _normal(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"</?u>|[*_`<>]", "", t)).strip().casefold()


def _titulo_normal(t: str) -> str | None:
    """O título normalizado, ou None quando ele é longo demais para ser título de seção."""
    return _normal(t) if len(t) <= TITULO_INDICE_MAX else None


def _sem_numeracao(n: str) -> str:
    return n[m.end() :] if (m := _NUMERACAO.match(n)) else n


def _entrada_de_sumario(t: str) -> str | None:
    """O título sem o número de página do fim, quando a linha é uma entrada do sumário impresso ("Conjuntura
    econômica 8" → "conjuntura econômica"); senão None. Chamar só com título curto."""
    if not (m := _NUMERO_FINAL.search(t)) or not (antes := t[: m.start()]) or antes[-1] not in _SEPARADORES:
        return None
    return _normal(antes.rstrip(_SEPARADORES)) or None


def indice(md: str, estrutura: dict, sumario: list | None = None, sumario_cortado: bool = False) -> dict:
    """O mapa do PDF para quem navega antes de ler: cada seção com a página onde começa e a última página que ela pode
    ocupar (`pagina_fim`, que inclui a página partilhada com a seção seguinte quando ela começa no meio da página).

    A fonte é o sumário do próprio PDF quando ele tem marcadores (`fonte: "sumario"`); senão, os títulos do Markdown
    (`fonte: "titulos"`), sem o que o extrator promove a título e não é seção: o cabeçalho corrido (o título que abre
    3 páginas ou mais), frase longa ou terminada em pontuação, item de bibliografia e a entrada do sumário impresso
    ("Conjuntura econômica 8") de um título que aparece no documento. O Markdown não muda: o índice é só um mapa sobre
    ele, e `inicio` é a posição, como em `secoes`, do título da página que tem o mesmo texto (com ou sem a numeração
    "1.2"); sem ele, a seção não encurta a anterior. Lista cortada (acima de SUMARIO_MAX marcadores ou de SECOES_MAX
    títulos) sai com `cortado: true`. Cada página e cada título passam uma vez: o custo é linear no Markdown."""
    paginas = estrutura.get("inicio_das_paginas") or []
    total = estrutura.get("paginas") or (paginas[-1]["pagina"] if paginas else 0)
    if not paginas or not total:
        return {}
    comecos = [p["inicio"] for p in paginas]
    numeros = [p["pagina"] for p in paginas]
    posicao_da_pagina = {n: k for k, n in enumerate(numeros)}

    def pagina_de(pos: int) -> int:
        return numeros[max(bisect.bisect_right(comecos, pos) - 1, 0)]

    def linhas_do_comeco(k: int):
        """(posição, linha) das linhas com texto da página k, da primeira em diante, sem o marcador do Lince."""
        fim_pagina = comecos[k + 1] if k + 1 < len(comecos) else len(md)
        pos = md.find("\n", comecos[k], fim_pagina)
        pos = fim_pagina if pos < 0 else pos + 1
        while pos < fim_pagina:
            fim = md.find("\n", pos, fim_pagina)
            fim = fim_pagina if fim < 0 else fim
            if md[pos:fim].strip():
                yield pos, md[pos:fim]
            pos = fim + 1

    def titulo_da_linha(linha: str) -> str | None:
        t = linha.strip()
        return _titulo_normal(t.lstrip("#").strip()) if t.startswith("#") else None

    # cabeçalho corrido promovido a título: o mesmo título como primeira linha de 3 páginas ou mais
    primeiras = {}
    for k in range(len(comecos)):
        if (primeira := next(linhas_do_comeco(k), None)) and (n := titulo_da_linha(primeira[1])):
            primeiras.setdefault(n, set()).add(k)
    corridos = {n for n, ks in primeiras.items() if len(ks) >= 3}

    def abertura(k: int) -> int:
        """Onde começa o conteúdo da página k, depois do cabeçalho corrido e da âncora da leitura pela IA."""
        for pos, linha in linhas_do_comeco(k):
            if titulo_da_linha(linha) not in corridos and not _ANCORA_IA.fullmatch(linha.strip()):
                return pos
        return comecos[k + 1] if k + 1 < len(comecos) else len(md)

    secoes_md = [(s, _titulo_normal(s["titulo"].strip())) for s in estrutura.get("secoes") or []]
    if sumario:
        na_pagina = {}  # (página, título normalizado, com e sem a numeração) → posição do primeiro título igual
        for s, n in secoes_md:
            if n:
                for forma in (n, _sem_numeracao(n)):
                    na_pagina.setdefault((pagina_de(s["inicio"]), forma), s["inicio"])
        itens = []
        for nivel, titulo, pagina in sumario[:INDICE_MAX]:
            item = {"titulo": titulo, "nivel": nivel, "pagina": pagina}
            if n := _titulo_normal(titulo):
                pos = na_pagina.get((pagina, n))
                if pos is None:  # o marcador numerado ("1. Introdução") e o título sem a numeração no texto
                    pos = na_pagina.get((pagina, _sem_numeracao(n)))
                if pos is not None:
                    item["inicio"] = pos
            itens.append(item)
        fonte, cortado = "sumario", sumario_cortado or len(sumario) > INDICE_MAX
    else:
        presentes = {n for _, n in secoes_md if n}
        itens = []
        for s, n in secoes_md:
            t = s["titulo"].strip()
            if not n or n in corridos or len(t.split()) > _PALAVRAS_TITULO_MAX or t[-1] in ".,;:":
                continue
            if _REFERENCIA.match(t) or ((base := _entrada_de_sumario(t)) and base in presentes):
                continue
            itens.append({"titulo": t, "nivel": s["nivel"], "pagina": pagina_de(s["inicio"]), "inicio": s["inicio"]})
        fonte, cortado = "titulos", bool(estrutura.get("secoes_cortadas"))
    if not itens:
        return {}
    # a seção seguinte de nível igual ou acima de cada item, da direita para a esquerda, numa passada só
    seguintes, pilha = [None] * len(itens), []
    for k in range(len(itens) - 1, -1, -1):
        while pilha and itens[pilha[-1]]["nivel"] > itens[k]["nivel"]:
            pilha.pop()
        seguintes[k] = itens[pilha[-1]] if pilha else None
        pilha.append(k)
    aberturas = {}
    for item, seguinte in zip(itens, seguintes, strict=True):
        fim = total if seguinte is None else seguinte["pagina"]
        k = posicao_da_pagina.get(fim)
        if seguinte is not None and "inicio" in seguinte and k is not None:
            if k not in aberturas:
                aberturas[k] = abertura(k)
            if seguinte["inicio"] <= aberturas[k]:  # a seção seguinte abre a página dela: esta termina na anterior
                fim -= 1
        item["pagina_fim"] = max(item["pagina"], fim)
    return {"fonte": fonte, "itens": itens} | ({"cortado": True} if cortado else {})
