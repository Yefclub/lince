"""PDF: texto nativo por página, regiões visuais (raster e vetor), tabela duvidosa e roteamento."""

import concurrent.futures as cf
import contextvars
import ctypes
import hashlib  # noqa: F401
import io
import itertools
import json  # noqa: F401
import logging
import math
import os  # noqa: F401
import re  # noqa: F401
import threading
import time
import zipfile  # noqa: F401
from collections import OrderedDict, defaultdict, deque
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET  # noqa: F401

import pypdfium2 as pdfium
import pypdfium2.raw as c  # noqa: F401

from . import paralelo
from ._prazo import LIMITE, prazo_restante
from ._trava import PDFIUM
from .conferencia_visual import potencias
from .limites import escala
from .pdf_colunas import confundidas as colunas_confundidas
from .pdf_colunas import fundidas as colunas_fundidas
from .pdf_colunas import refazer as refazer_colunas
from .pdf_figuras import agrupar_figuras, faixa_ilustrada, paineis_panorama, papel_retangulo
from .pdf_formulario import abrir_pdf, campos_visiveis, com_rotulos
from .pdf_formulas import localizar_formulas
from .pdf_grade import aplicar as aplicar_grades
from .pdf_recortes import interseccao, janela_retangular, renderizar_regiao, validar_regiao
from .pdf_tabelas import caixa_do_bloco_principal, conferir_tabelas
from .pdf_tabelas_layout import conferir_blocos, texto_de_rotulos
from .seguranca import NAO_CONFERIDO, texto_oculto_pdf
from .texto import densidade_matematica, desifenizar, limpar_tabelas, lixo
from .tipos import PrazoEsgotado

PROMPT_VERSAO = "leitura-ia-v1"
log = logging.getLogger("lince")
# Quem é o documento na linha de log da fase 1 (o servidor põe a sessão e o documento), nunca o nome do arquivo.
ROTULO: contextvars.ContextVar[str] = contextvars.ContextVar("lince_rotulo_pdf", default="")
ETAPAS = ("abrir", "inspecionar", "coletar", "regioes", "renderizar", "recortar")
# Teto da análise visual (desenho da página, figuras, texto oculto) por documento: medido em ~15 ms por página de
# texto, 2000 páginas cabem em ~30 s. Acima dele, e depois do prazo do documento, a página entra só com o texto.
PAGINAS_ANALISE_MAX = 2000
# Teto de páginas de texto lidas por documento. Texto corrido denso tem ~4 mil caracteres por página: 5 mil páginas
# dão ~20 M caracteres, a ordem do maior resultado que a montagem entrega, e cabem em ~10 s só com o texto (medido em
# ~2 ms por página de 50 linhas). É o dobro do maior documento real já recebido (2.500 páginas); acima dele, as
# páginas restantes não são lidas e entram nas pendências, como os outros tetos contra documento hostil de limites.py.
PAGINAS_TEXTO_MAX = 5000
# Marcadores lidos do sumário do PDF: livro técnico grande tem algumas centenas; o resto de um arquivo hostil não entra
# (e o índice sai com `cortado`). O título do marcador vai até TITULO_MAX caracteres: título de seção cabe numa linha,
# e o marcador hostil de megabytes não passa para o resultado.
SUMARIO_MAX = 2000
TITULO_MAX = 200
# Páginas como imagem por chamada (`imagens_das_paginas`): a resposta a uma pergunta usa poucas páginas. O lado vai de
# LADO_IMAGEM_MIN, abaixo do qual o texto da página deixa de ser legível, a LADO_IMAGEM_MAX, o dobro do que o
# provedor de visão aceita sem reduzir (~1568 px); a chamada inteira não passa de PIXELS_IMAGEM_MAX, 50 páginas de
# 1568 px de lado (~123 Mpx: dezenas de MB em PNG, umas centenas na renderização), para nenhum pedido segurar a
# memória do serviço.
PAGINAS_IMAGEM_MAX = 50
LADO_IMAGEM_MIN = 256
LADO_IMAGEM_MAX = 4096
PIXELS_IMAGEM_MAX = PAGINAS_IMAGEM_MAX * 1568 * 1568
# Páginas por chamada do pdf_inspector. Ele não usa a PDFium, mas segura a GIL e, chamado com o arquivo, analisa o
# documento inteiro a cada vez, mesmo pedindo uma página (medido: 4 s por chamada num PDF de 2.500 páginas). Ele só lê
# cópias de FAIXA páginas feitas pela PDFium (~0,05 s cada), nunca o arquivo, e roda fora da trava: o prazo é
# conferido entre elas, um PDF que chega no meio não espera o grande, e as páginas lidas são as que a PDFium abre, as
# mesmas que o leitor mostra.
FAIXA = 50
# Tempo máximo com a trava da PDFium antes de dar a vez a outro PDF (a trava é justa: quem espera entra antes de
# quem acabou de soltar). Com a trava só rodam chamadas à PDFium, de custo limitado por ela mesma: abrir, coletar os
# objetos, extrair o texto, copiar e renderizar (no máximo LADO_MAX_PX). Toda a análise em Python roda fora dela. Uma
# página pesada (escaneada, renderizada em 1568 px) passa dele sozinha; a de texto corrido usa ~2 ms.
VEZ_S = 0.5
# O prazo é do documento inteiro, e a montagem do texto extraído vem depois: ela custa ~1/4 do tempo da extração do
# texto corrido (medido: 0,8 s para as 650 páginas lidas em 3 s). Uma faixa nova só começa se o que resta do prazo
# cobre essa parte; a proporção vale em máquina lenta ou rápida, porque as duas etapas são CPU em Python.
RESERVA_MONTAGEM = 0.3
# Teto do trabalho para tirar o texto oculto de uma página pela posição, contado em células da grade visitadas e em
# comparações entre um item oculto e um objeto de texto. A grade compara cada item só com os objetos cujo centro cai
# na caixa dele: 10 mil itens ocultos espalhados numa página custam ~40 mil. O teto segura a página hostil, com
# milhares de itens sobrepostos no mesmo lugar: acima dele, a página não é copiada (nenhum objeto sai pela PDFium)
# e fica com o texto como está, e o oculto, com o alerta e a pendência.
OCULTOS_COMPARACOES_MAX = 100_000
# Lado da célula da grade dos objetos de texto, em pontos: menos que uma linha de texto corrido.
CELULA = 8
# Teto de objetos por página (com os de dentro das formas, contados a cada vez que a forma é desenhada): coletar cada
# um pela PDFium custa ~3 µs com a trava (medido: 200 mil desenhos em 0,5 s), e uma forma desenhada mil vezes
# multiplica o percurso. Página real tem poucos milhares; acima do teto, ela entra só com o texto, com a pendência e o
# alerta de texto oculto não conferido, e o próximo PDF não espera.
OBJETOS_MAX = 100_000
# Tempo de CPU da análise de uma página (o Python fora da trava e, nas vezes dela, a cópia sem o oculto e o
# pdf_inspector): página real leva dezenas de milissegundos; a hostil (dezenas de milhares de desenhos espaçados,
# milhares de invisíveis sobre milhares de imagens, fórmula enorme) levaria de segundos a minutos, prendendo o worker e
# dividindo a GIL com os outros documentos. Passou dele, a página entra só com o texto, com a pendência e o alerta de
# texto oculto não conferido, e a leitura segue na próxima. É tempo de CPU da própria thread (`time.thread_time`), não
# de relógio: a espera pela trava e pela GIL, que depende de quantos outros documentos estão sendo lidos, não conta, e a
# mesma página sai igual com o servidor cheio ou vazio.
ANALISE_PAGINA_S = 3.0
_ANALISE = threading.local()


class _AnaliseLonga(Exception):
    """A análise da página passou de ANALISE_PAGINA_S."""


def _tique():
    """Conferido nos laços da análise da página que crescem mais que linear no que a página tem."""
    if (fim := getattr(_ANALISE, "fim", None)) is not None and time.thread_time() > fim:
        raise _AnaliseLonga


# Teto de pixels dos fundos guardados de uma vez: a coleta das páginas de uma vez fica na memória até a análise de cada
# uma, e uma folha gigante tem um fundo de até LADO_MAX_PX × LADO_MAX_PX (300 MB em RGB). 20 milhões (~60 MB) cobrem
# as 50 páginas A4 de uma faixa (0,13 milhão cada); passando dele, a vez acaba, e a página seguinte fica para a próxima.
FUNDOS_PX_MAX = 20_000_000
PESADA = "conteúdo demais para a análise visual"


def texto_pdfium(page):
    tp = page.get_textpage()
    return tp.get_text_bounded()


def regioes_vetoriais(objs, W, H, itens=(), papeis=None, fundo=None, grades=()):
    return agrupar_figuras(objs, W, H, itens, papeis or {}, _tique, fundo, grades)


def planejar_pdf(path, pasta):
    """Lê o PDF em faixas de páginas, com o prazo conferido entre elas e os tetos valendo antes da leitura.

    Com o pool de processos ligado (`paralelo.ativar`), o pdf_inspector de cada faixa e cada página (coleta, análise,
    renderização e recortes) vão para os processos, um trabalho por página; o processo principal junta as páginas na
    ordem delas e tira as imagens repetidas entre páginas, e o plano sai igual ao da leitura em série
    (`_em_processos`). Sem o pool, a leitura é em série, neste processo:

    A PDFium só é usada com a trava, tomada por pouco tempo e solta entre as vezes (`VEZ_S`): um PDF que chega
    durante a leitura de um grande entra na próxima vez. Com a trava só rodam chamadas à PDFium; cada página passa por
    fases: a coleta (objetos, fundo renderizado, texto pela PDFium; `_coleta`, com a trava), a análise e a decisão da
    rota e das regiões (`_pagina`, sem a trava), a cópia sem o texto oculto ou a renderização na escala da leitura
    (com a trava, se preciso) e os recortes e os arquivos (sem a trava). O documento é aberto uma vez só, na vez que
    lê a contagem de páginas, as propriedades e a cópia da primeira faixa (abrir um PDF com a tabela de referências
    danificada a reconstrói, e isso não pode se repetir a cada vez), e fechado dentro da trava, mesmo com exceção: o
    finalizador dele (e o das páginas) não pode rodar fora dela, em paralelo com outro PDF. O pdf_inspector não usa a
    PDFium e roda fora da trava, sobre as cópias.

    O tempo de cada etapa (abrir, inspecionar, coletar, regiões, renderizar, recortar; nos processos, a soma deles)
    vai para o log `lince`."""
    inicio = time.monotonic()
    etapas = defaultdict(float)
    distribuidor = paralelo.pool()
    doc = None
    try:
        with PDFIUM:
            fim = time.monotonic() + VEZ_S
            doc = abrir_pdf(path)
            # O /Count declarado: a PDFium confia nele, e as páginas do arquivo que ficam fora dele não abrem (como num
            # visualizador). Elas não são lidas nem contadas: só a árvore de páginas inteira diria quantas são, e
            # percorrê-la é o que o pdf_inspector faz com o arquivo, sem teto nem prazo.
            total = len(doc)
            propriedades = _propriedades(doc)
            sumario, sumario_cortado = _sumario(doc, total)
            lidas = min(total, PAGINAS_TEXTO_MAX)
            etapas["abrir"] += time.monotonic() - inicio
            if distribuidor is not None:  # cada processo abre o dele
                doc.close()
                doc = None
            elif lidas:  # na mesma vez
                vez = _vez_da_faixa(doc, range(min(FAIXA, lidas)), fim, etapas)
        if distribuidor is not None:
            paginas, tarefas, seguranca, pendentes, so_texto, nao_lidas = _em_processos(
                distribuidor, path, pasta, total, lidas, inicio, etapas
            )
        else:
            paginas, tarefas, cache, seguranca, pendentes = [], [], {}, [], []
            so_texto, nao_lidas = [], []  # [início, fim, motivo]: páginas só com o texto e páginas não lidas
            parada = None  # a primeira página que a PDFium não abre
            for a in range(0, lidas, FAIXA):
                # a primeira faixa sempre entra: o documento nunca sai sem texto por causa do prazo
                resta = prazo_restante()
                if a and resta is not None and resta <= RESERVA_MONTAGEM * (time.monotonic() - inicio):
                    _marcar(nao_lidas, a, lidas, "prazo esgotado")
                    break
                faixa = range(a, min(a + FAIXA, lidas))
                if a:
                    with PDFIUM:
                        vez = _vez_da_faixa(doc, faixa, time.monotonic() + VEZ_S, etapas)
                (dados, abertas), coletas = vez
                t = time.monotonic()
                md_de, ocr, itens_de = _extrair(dados, a)  # o pdf_inspector, fora da trava
                etapas["inspecionar"] += time.monotonic() - t
                fila = faixa[:abertas]
                if abertas < len(faixa):
                    parada = faixa[abertas]
                k = 0
                while k < len(fila):
                    i = fila[k]
                    if motivo := _so_texto(i):  # a análise não começa depois do prazo, mesmo com a página já coletada
                        coletas.clear()  # as seguintes também entram só com o texto
                        _marcar(so_texto, i, i + 1, motivo)
                        paginas.append(_so_o_texto(i, md_de.get(i, "")))
                        k += 1
                        continue
                    if not coletas:
                        t = time.monotonic()
                        with PDFIUM:  # a vez: a coleta desta página e das seguintes, até o fim da vez
                            coletas = _coletas(doc, fila[k:], time.monotonic() + VEZ_S)
                        etapas["coletar"] += time.monotonic() - t
                    col = coletas.popleft()
                    if col is None:  # a página não abre: a leitura para nela
                        parada, fila = i, fila[:k]
                        break
                    if col == PESADA:
                        _marcar(so_texto, i, i + 1, PESADA)
                        paginas.append(_so_o_texto(i, md_de.get(i, "")))
                        recorte = None
                    else:
                        marcas = len(seguranca), len(pendentes)  # tarefas e cache só mudam em `recortar`, depois
                        t = time.monotonic()
                        info, recorte, motivo = _analisar(doc, i, col, md_de.get(i, ""), i in ocr, itens_de.get(i, []),
                                                          pasta, tarefas, cache, seguranca, pendentes)  # fmt: skip
                        etapas["regioes"] += time.monotonic() - t
                        if motivo:
                            _marcar(so_texto, i, i + 1, motivo)
                        paginas.append(info)
                    k += 1
                    ultima = k == len(fila) and (parada is not None or faixa[-1] + 1 == lidas)
                    if recorte or ultima:
                        t = time.monotonic()
                        with PDFIUM:
                            img = _renderizar(doc, i, recorte[0], recorte[2]) if recorte else None
                            if ultima:
                                doc.close()  # na mesma vez: um PDF curto pega a trava só duas vezes
                                doc = None
                        etapas["renderizar"] += time.monotonic() - t
                    if recorte:
                        t = time.monotonic()
                        # os recortes e os arquivos, fora da trava, antes da próxima página
                        if motivo := _aplicar_recorte(recorte, img, tarefas, cache, seguranca, pendentes, marcas):
                            paginas[-1] = _so_o_texto(i, md_de.get(i, ""), col["formulario"][0])
                            _marcar(so_texto, i, i + 1, motivo)
                        etapas["recortar"] += time.monotonic() - t
                if parada is not None:
                    _marcar(nao_lidas, parada, total, f"a página {parada + 1} não abre")
                    break
            if parada is None:
                _marcar(nao_lidas, lidas, total, f"teto de {PAGINAS_TEXTO_MAX} páginas de texto")
    finally:
        if doc is not None:
            with PDFIUM:
                doc.close()
    for faixa, motivo in _faixas(so_texto):
        pendentes.append(f"{faixa}: só o texto, sem figuras nem páginas escaneadas lidas ({motivo})")
        seguranca.append(  # o texto oculto dessas páginas não foi conferido: suspeita, nunca "nada oculto"
            {"tecnicas": ["não conferido"], "texto": "", "alerta": f"{NAO_CONFERIDO}: {faixa} ({motivo})"}
        )
    pendentes += [f"{faixa}: não lidas ({motivo})" for faixa, motivo in _faixas(nao_lidas)]
    plano = {"tipo": "pdf", "paginas": paginas, "paginas_total": total, "tarefas": tarefas, "texto_oculto": seguranca,
             "pendentes": pendentes}  # fmt: skip
    if propriedades:
        plano["propriedades"] = propriedades
    if sumario:
        plano["sumario"] = sumario
    if sumario_cortado:
        plano["sumario_cortado"] = True
    _registrar(etapas, total, len(tarefas), time.monotonic() - inicio, distribuidor)
    return plano


def _registrar(etapas, paginas, tarefas, duracao, distribuidor):
    """Uma linha no log `lince` com o tempo da fase 1 e de cada etapa, para quem opera ver onde vai o tempo."""
    onde = f"{distribuidor.processos} processos" if distribuidor is not None else "em série"
    partes = ", ".join(f"{nome} {etapas[nome]:.2f} s" for nome in ETAPAS if etapas.get(nome))
    log.info("fase 1 do PDF%s: %d páginas, %d tarefas em %.2f s (%s; %s)", ROTULO.get(), paginas, tarefas, duracao,
             onde, partes)  # fmt: skip


def _analisar(doc, i, col, md, eh_ocr, itens, pasta, tarefas, cache, seguranca, pendentes):
    """A análise da página coletada (`_pagina`), com o teto de tempo dela: (página, recorte, motivo). Com motivo (a
    análise passou de ANALISE_PAGINA_S ou deu erro), a página entra só com o texto e não deixa rastro."""
    marcas = len(seguranca), len(pendentes)
    _ANALISE.fim = time.thread_time() + ANALISE_PAGINA_S
    try:
        info, recorte = _pagina(doc, i, col, md, eh_ocr, itens, pasta, tarefas, cache, seguranca, pendentes)
        return info, recorte, None
    except _AnaliseLonga:
        motivo = f"análise da página passou de {ANALISE_PAGINA_S:g} s"
    except MemoryError:
        raise
    except Exception as e:  # noqa: BLE001 — uma página com defeito não derruba o documento
        motivo = _erro_da_pagina(e)
    finally:
        _ANALISE.fim = None
    del seguranca[marcas[0] :], pendentes[marcas[1] :]
    return _so_o_texto(i, md, col["formulario"][0]), None, motivo


def _aplicar_recorte(recorte, img, tarefas, cache, seguranca, pendentes, marcas):
    """Os recortes, os arquivos e as tarefas da página (`recortar` de `_pagina`), fora da trava. Com erro, a página
    não deixa rastro (`marcas`: o tamanho de `seguranca` e `pendentes` antes da análise dela) e volta o motivo."""
    feitas, vistas = len(tarefas), set(cache)
    try:
        recorte[1](img)
    except MemoryError:
        raise
    except Exception as e:  # noqa: BLE001 — a página sai só com o texto, sem rastro do recorte
        del tarefas[feitas:], seguranca[marcas[0] :], pendentes[marcas[1] :]
        for chave in set(cache) - vistas:
            del cache[chave]
        return _erro_da_pagina(e)
    return None


# ------------------------------------------------------------------ nos processos (paralelo.py)
def _em_processos(distribuidor, path, pasta, total, lidas, inicio, etapas):
    """A leitura em série de `planejar_pdf`, com as faixas e as páginas nos processos. O pdf_inspector das faixas
    seguintes roda junto com as páginas desta. O processo principal só junta: as páginas na ordem delas, e a imagem que
    já saiu numa página anterior vira repetida, como na leitura em série (cada processo só vê a página dele). Se um
    processo morre no meio (o pool quebra), o documento é lido de novo no pool renovado, uma vez."""
    for tentativa in (1, 2):
        try:
            return _distribuir(distribuidor, path, pasta, total, lidas, inicio, etapas)
        except BrokenProcessPool:
            if tentativa == 2:
                raise RuntimeError("os processos de leitura do PDF caíram duas vezes neste documento") from None
    raise AssertionError  # inalcançável


def _distribuir(distribuidor, path, pasta, total, lidas, inicio, etapas):
    # `leitura`: o documento aberto num processo serve só a esta leitura, e é fechado no próximo trabalho dele depois
    # que ela termina (`Distribuidor.encerrada`)
    dono, limite, leitura = object(), LIMITE.get(), os.urandom(8).hex()
    paginas, tarefas, cache, seguranca, pendentes, so_texto, nao_lidas = [], [], {}, [], [], [], []
    parada, faixas, seguinte, despachadas = None, deque(), 0, []

    def adiantar():
        """O pdf_inspector das próximas faixas, até uma por processo à frente: ele é a parte em série de cada faixa."""
        nonlocal seguinte
        while seguinte < lidas and len(faixas) < distribuidor.processos:
            fim = min(seguinte + FAIXA, lidas)
            pedido = (path, leitura, distribuidor.encerradas(), seguinte, fim)
            faixas.append((seguinte, distribuidor.submeter(dono, _faixa_no_processo, *pedido)))
            seguinte = fim

    try:
        adiantar()
        while faixas:
            a, inspecao = faixas.popleft()
            # a primeira faixa sempre entra; as outras, só se o que resta do prazo cobre a montagem, como em série
            resta = prazo_restante()
            if a and resta is not None and resta <= RESERVA_MONTAGEM * (time.monotonic() - inicio):
                _marcar(nao_lidas, a, lidas, "prazo esgotado")
                break
            abertas, md_de, ocr, itens_de, gastos = inspecao.result()
            _somar(etapas, gastos)
            faixa = range(a, min(a + FAIXA, lidas))
            if abertas < len(faixa):
                parada = faixa[abertas]
            fechar = distribuidor.encerradas()
            trabalhos = [
                (
                    i,
                    distribuidor.submeter(
                        dono,
                        _pagina_no_processo,
                        path,
                        leitura,
                        fechar,
                        i,
                        md_de.get(i, ""),
                        i in ocr,
                        itens_de.get(i, []),
                        pasta,
                        limite,
                    ),
                )  # fmt: skip
                for i in faixa[:abertas]
            ]
            despachadas += [f for _, f in trabalhos]
            if parada is None:
                adiantar()
            for i, trabalho in trabalhos:
                if _vencido():  # o que ainda está na fila não espera a vez só para dizer "prazo esgotado"
                    distribuidor.cancelar(dono)
                if trabalho.cancelled():  # o motivo de `_so_texto`, como na série: o teto de páginas vem antes do prazo
                    motivo = _so_texto(i) or "prazo esgotado"
                    r = {"i": i, "info": _so_o_texto(i, md_de.get(i, "")), "motivo": motivo, "etapas": {}}
                else:
                    r = trabalho.result()
                _somar(etapas, r["etapas"])
                if r.get("parada"):  # a página não abre: a leitura para nela
                    parada = r["i"]
                    break
                _juntar(r, pasta, paginas, tarefas, cache, seguranca, pendentes, so_texto)
            if parada is not None:
                _marcar(nao_lidas, parada, total, f"a página {parada + 1} não abre")
                break
        if parada is None:
            _marcar(nao_lidas, lidas, total, f"teto de {PAGINAS_TEXTO_MAX} páginas de texto")
    finally:
        distribuidor.cancelar(dono)
        # as páginas que já estão num processo gravam os recortes na pasta: a leitura só termina depois delas (a
        # pasta pode ser apagada logo em seguida, e a releitura depois de um processo que caiu grava os mesmos nomes)
        cf.wait([f for f in despachadas if not f.cancelled()])
        distribuidor.encerrada(leitura)
    return paginas, tarefas, seguranca, pendentes, so_texto, nao_lidas


def _juntar(r, pasta, paginas, tarefas, cache, seguranca, pendentes, so_texto):
    """A página `r` (de `_pagina_no_processo`) no plano. A imagem que já saiu numa página anterior (o mesmo recorte,
    pelo hash) vira repetida dela, e o arquivo dela sai: na leitura em série, ela nem seria gravada."""
    i, info = r["i"], r["info"]
    paginas.append(info)
    if motivo := r.get("motivo"):
        _marcar(so_texto, i, i + 1, motivo)
        return
    seguranca += r["seguranca"]
    pendentes += r["pendentes"]
    trocas = {}
    for t in r["tarefas"]:
        chave = r["chaves"].get(t["id"])
        if chave is not None and chave in cache:
            trocas[t["id"]] = cache[chave]
            Path(f"{pasta}/ia/{t['id']}.png").unlink(missing_ok=True)
            continue
        if chave is not None:
            cache[chave] = t["id"]
        tarefas.append(t)
    if trocas:
        if (ocr := info.get("ocr_da_imagem")) in trocas:
            info["ocr_da_imagem"] = trocas[ocr]
        info["regioes"] = [
            {"tarefa": trocas[x["tarefa"]], "depois_de": "", "texto_dentro": "", "repetida": True}
            if x["tarefa"] in trocas
            else x
            for x in info["regioes"]
        ]


def _somar(etapas, gastos):
    for nome, s in gastos.items():
        etapas[nome] += s


# Documentos abertos em cada processo do pool, os usados por último no fim: as páginas de um documento chegam ao
# mesmo processo em sequência, e abrir de novo a cada página reconstruiria a tabela de referências danificada.
_DOCS: OrderedDict = OrderedDict()
DOCS_ABERTOS = 2


def _doc_do_processo(caminho, leitura, fechar):
    """O documento da `leitura` aberto neste processo. Antes, fecha os das leituras que já terminaram (`fechar`): o
    arquivo apagado não fica ocupando o disco nem a memória, e a cópia decifrada de um protegido não fica legível."""
    for k in [k for k in _DOCS if k in fechar]:
        _DOCS.pop(k).close()
    doc = _DOCS.pop(leitura, None)
    if doc is None:
        while len(_DOCS) >= DOCS_ABERTOS:
            _DOCS.popitem(last=False)[1].close()
        doc = abrir_pdf(caminho)
    _DOCS[leitura] = doc
    return doc


def _faixa_no_processo(caminho, leitura, fechar, a, b):
    """No processo: a cópia das páginas [a, b) e o pdf_inspector sobre ela (`_vez_da_faixa` e `_extrair`). Os itens do
    pdf_inspector vão como atributos simples: o TextItem dele não passa de um processo para outro."""
    gastos = {}
    t = time.monotonic()
    doc = _doc_do_processo(caminho, leitura, fechar)
    gastos["abrir"] = time.monotonic() - t
    t = time.monotonic()
    with PDFIUM:
        dados, abertas = _copia_da_faixa(doc, range(a, b))
    md_de, ocr, itens_de = _extrair(dados, a)
    gastos["inspecionar"] = time.monotonic() - t
    simples = {i: [SimpleNamespace(**_atributos(x)) for x in v] for i, v in itens_de.items()}
    return abertas, md_de, ocr, simples, gastos


def _atributos(x):
    return {k: getattr(x, k) for k in dir(x) if not k.startswith("_") and not callable(getattr(x, k))}


def _pagina_no_processo(caminho, leitura, fechar, i, md, eh_ocr, itens, pasta, limite):
    """No processo: a página `i` inteira, como no laço da leitura em série (coleta, análise, renderização e recortes),
    com o prazo do documento (`limite`). Devolve a página, as tarefas, o texto oculto e as pendências dela, o hash de
    cada recorte (as repetidas entre páginas saem em `_juntar`) e o tempo de cada etapa."""
    gastos = defaultdict(float)
    token = LIMITE.set(limite)
    try:
        if motivo := _so_texto(i):
            return {"i": i, "info": _so_o_texto(i, md), "motivo": motivo, "etapas": gastos}
        t = time.monotonic()
        doc = _doc_do_processo(caminho, leitura, fechar)
        gastos["abrir"] += time.monotonic() - t
        t = time.monotonic()
        with PDFIUM:
            col = _coleta(doc, i)
        gastos["coletar"] += time.monotonic() - t
        if col is None:
            return {"i": i, "parada": True, "etapas": gastos}
        if col == PESADA:
            return {"i": i, "info": _so_o_texto(i, md), "motivo": PESADA, "etapas": gastos}
        tarefas, cache, seguranca, pendentes = [], {}, [], []
        t = time.monotonic()
        info, recorte, motivo = _analisar(doc, i, col, md, eh_ocr, itens, pasta, tarefas, cache, seguranca, pendentes)
        gastos["regioes"] += time.monotonic() - t
        if motivo:
            return {"i": i, "info": info, "motivo": motivo, "etapas": gastos}
        if recorte:
            t = time.monotonic()
            with PDFIUM:
                img = _renderizar(doc, i, recorte[0], recorte[2])
            gastos["renderizar"] += time.monotonic() - t
            t = time.monotonic()
            motivo = _aplicar_recorte(recorte, img, tarefas, cache, seguranca, pendentes, (0, 0))
            gastos["recortar"] += time.monotonic() - t
            if motivo:
                return {"i": i, "info": _so_o_texto(i, md, col["formulario"][0]), "motivo": motivo, "etapas": gastos}
        chaves = {tid: chave for chave, tid in cache.items()}
        return {"i": i, "info": info, "tarefas": tarefas, "seguranca": seguranca, "pendentes": pendentes,
                "chaves": chaves, "etapas": gastos}  # fmt: skip
    finally:
        LIMITE.reset(token)


def _erro_da_pagina(e):
    """O motivo da pendência da página que deu erro na análise ou nos recortes."""
    return f"erro na página: {type(e).__name__}: {str(e)[:120]}"


def _vez_da_faixa(doc, faixa, fim, etapas):
    """A cópia da faixa para o pdf_inspector (`_copia_da_faixa`) e, na mesma vez, a coleta das primeiras páginas dela
    (`_coletas`), que não depende do texto dele. Chamar com a trava."""
    t = time.monotonic()
    copia = _copia_da_faixa(doc, faixa)
    etapas["inspecionar"] += time.monotonic() - t
    abertas = copia[1]
    t = time.monotonic()
    coletas = _coletas(doc, faixa[:abertas], fim) if abertas and not _so_texto(faixa[0]) else deque()
    etapas["coletar"] += time.monotonic() - t
    return copia, coletas


def _coletas(doc, paginas, fim):
    """A coleta (`_coleta`) da primeira página e das seguintes enquanto a vez não acaba (`fim`) e os fundos cabem em
    FUNDOS_PX_MAX, até a que entra só com o texto, que fica para quem chama, e até a que não abre, que fica no fim, como
    None. Chamar com a trava."""
    feitas, pixels = deque(), 0
    for k, i in enumerate(paginas):
        if k and (time.monotonic() >= fim or pixels >= FUNDOS_PX_MAX or _so_texto(i)):
            break
        feitas.append(col := _coleta(doc, i))
        if col is None:
            break
        if col != PESADA:
            pixels += col["fundo"].width * col["fundo"].height
    return feitas


def _coleta(doc, i):
    """O que a análise da página `i` precisa da PDFium: o tamanho, a origem das posições, o tipo e a caixa de cada
    objeto, a contagem dos objetos da página, o fundo renderizado (o texto oculto) e o texto pela PDFium. Só dados do
    Python, sem nada que dependa da PDFium: a análise roda depois, fora da trava. Chamar com a trava; a página é
    fechada aqui. None se a página não abre (o /Count declarado passa das páginas do arquivo); PESADA acima de
    OBJETOS_MAX."""
    try:
        page = pagina_visivel(doc, i)
    except pdfium.PdfiumError:
        return None
    try:
        objs, (ox, oy) = [], _origem_visivel(page)
        papeis = {}
        caminhos_visiveis = {}
        W, H = page.get_size()
        texto_visivel = False
        for tipo, raw, (l, b, r, t) in objetos(page, limitar_imagens=True, caminhos_visiveis=caminhos_visiveis):
            if len(objs) == OBJETOS_MAX:
                return PESADA
            # na origem da área visível, como as posições do pdf_inspector e a página renderizada (MediaBox ou CropBox
            # com a origem fora do zero, comum em desenho exportado de CAD)
            objs.append((tipo, (l - ox, b - oy, r - ox, t - oy)))
            if tipo == c.FPDF_PAGEOBJ_PATH and (papel := papel_retangulo(raw, objs[-1][1], W, H)):
                papeis[len(objs) - 1] = papel
            if not texto_visivel and tipo == c.FPDF_PAGEOBJ_TEXT:
                texto_visivel = c.FPDFTextObj_GetTextRenderMode(raw) != 3
        return {
            "W": W,
            "H": H,
            "rotacao": page.rotacao_exibida,
            "origem": (ox, oy),
            "objs": objs,
            "papeis": papeis,
            "caminhos_visiveis": {k: (l - ox, b - oy, r - ox, t - oy) for k, (l, b, r, t) in caminhos_visiveis.items()},
            "n": c.FPDFPage_CountObjects(page.raw),
            "fundo": page.render(scale=escala(W, H, 0.5), may_draw_forms=False)
            .to_pil()
            .convert("RGB"),  # uma cópia, sem a PDFium
            "texto": texto_pdfium(page),
            "formulario": campos_visiveis(doc, page),
            "texto_visivel": texto_visivel,
        }
    finally:
        page.close()


def _renderizar(doc, i, fator, desenhar_campos):
    """A página `i` renderizada na escala `fator` e convertida pelo Pillow: uma cópia, que não depende mais da PDFium.
    Chamar com a trava."""
    page = pagina_visivel(doc, i)
    try:
        return page.render(scale=fator, may_draw_forms=desenhar_campos).to_pil().convert("RGB")
    finally:
        page.close()


def imagens_das_paginas(caminho, paginas, lado=1568, *, regiao=None) -> dict[int, bytes]:
    """As páginas pedidas (lista de números a partir de 1) em PNG, como são exibidas (de pé), com o maior lado em
    `lado` pixels: a evidência original para quem lê na hora da pergunta as páginas que o Markdown só descreve
    (`paginas_visuais`). Recusa com ValueError, antes de renderizar: pedido vazio ou acima de PAGINAS_IMAGEM_MAX
    páginas, número que não é inteiro ou fora do documento, `lado` fora de LADO_IMAGEM_MIN a LADO_IMAGEM_MAX ou acima de
    PIXELS_IMAGEM_MAX na chamada; e, na vez de cada página, a que não abre ou passa de OBJETOS_MAX objetos (a mesma
    página pesada que a leitura não analisa). O prazo do contexto (`prazo_restante`) é conferido antes de cada página:
    esgotado, levanta PrazoEsgotado. A PDFium fica com a trava só durante a renderização de cada página."""
    if regiao is not None:
        validar_regiao(regiao)
    pedidas = list(itertools.islice(paginas, PAGINAS_IMAGEM_MAX + 1))  # o iterável enorme para no teto
    if not all(isinstance(n, int) and not isinstance(n, bool) for n in pedidas):
        raise ValueError("as páginas são números inteiros a partir de 1")
    if not pedidas or len(pedidas) > PAGINAS_IMAGEM_MAX:
        raise ValueError(f"peça de 1 a {PAGINAS_IMAGEM_MAX} páginas")
    pedidas = list(dict.fromkeys(pedidas))
    if not isinstance(lado, int) or not LADO_IMAGEM_MIN <= lado <= LADO_IMAGEM_MAX:
        raise ValueError(f"o lado vai de {LADO_IMAGEM_MIN} a {LADO_IMAGEM_MAX} pixels")
    if len(pedidas) * lado * lado > PIXELS_IMAGEM_MAX:
        raise ValueError(
            f"imagens demais para uma chamada: até {PIXELS_IMAGEM_MAX // (lado * lado)} páginas com {lado} px"
        )
    imagens, doc = {}, None
    try:
        with PDFIUM:
            doc = abrir_pdf(str(caminho))
            total = len(doc)
        if fora := [n for n in pedidas if not 1 <= n <= total]:
            raise ValueError(f"o documento tem {total} páginas; fora dele: {fora}")
        for n in pedidas:
            if (resta := prazo_restante()) is not None and resta <= 0:
                raise PrazoEsgotado(f"prazo esgotado antes da página {n}")
            with PDFIUM:
                try:
                    page = pagina_visivel(doc, n - 1)
                except pdfium.PdfiumError as e:
                    raise ValueError(f"a página {n} não abre") from e
                try:
                    if any(k >= OBJETOS_MAX for k, _ in enumerate(objetos(page))):
                        raise ValueError(f"a página {n} tem conteúdo demais para virar imagem")
                    if regiao is None:
                        W, H = page.get_size()
                        img = page.render(scale=escala(W, H, lado / max(W, H, 1))).to_pil().convert("RGB")
                        rotacao = page.rotacao_exibida
                    else:
                        img = renderizar_regiao(page, lado, regiao)
                        rotacao = 0  # a janela já foi renderizada de pé
                finally:
                    page.close()
            saida = io.BytesIO()
            de_pe(img, rotacao).save(saida, "PNG")
            imagens[n] = saida.getvalue()
    finally:
        if doc is not None:
            with PDFIUM:
                doc.close()
    return imagens


def _propriedades(doc):
    """Propriedades do PDF (título, autor, produtor), sem as vazias. Chamar com a trava."""
    try:
        return {k: v for k, v in doc.get_metadata_dict().items() if v}
    except Exception:  # noqa: BLE001 — PDF sem propriedades legíveis: segue sem elas
        return {}


def _sumario(doc, total):
    """Os marcadores do PDF (o sumário que o visualizador mostra ao lado) como [nível, título, página a partir de 1],
    na ordem do arquivo, e se a lista foi cortada. Marcador sem destino nesta cópia (ação externa, página fora do
    /Count) fica de fora. Até SUMARIO_MAX itens: arquivo hostil pode ter milhões. Chamar com a trava."""
    try:
        itens, vistos = [], 0
        for b in itertools.islice(doc.get_toc(), SUMARIO_MAX + 1):
            vistos += 1
            if vistos > SUMARIO_MAX:
                return itens, True
            destino = b.get_dest()
            pagina = destino.get_index() if destino is not None else None
            titulo = re.sub(r"\s+", " ", (b.get_title() or "")[: 4 * TITULO_MAX]).strip()[:TITULO_MAX]
            if titulo and isinstance(pagina, int) and 0 <= pagina < total:
                itens.append([b.level + 1, titulo, pagina + 1])
        return itens, False
    except Exception:  # noqa: BLE001 — sumário ilegível: a estrutura sai dos títulos do texto
        return [], False


def _marcar(intervalos, a, b, motivo):
    """Junta as páginas [a, b) com o motivo aos intervalos [início, fim, motivo], emendando no último quando seguem
    dele com o mesmo motivo. Guarda intervalos, não páginas: um /Count de um milhão não vira um milhão de entradas."""
    if a >= b:
        return
    if intervalos and intervalos[-1][1] == a and intervalos[-1][2] == motivo:
        intervalos[-1][1] = b
    else:
        intervalos.append([a, b, motivo])


def _faixas(intervalos):
    """(faixa de páginas, motivo) de cada intervalo [início, fim, motivo]."""
    for a, b, motivo in intervalos:
        yield (f"página {a + 1}" if b - a == 1 else f"páginas {a + 1}–{b}"), motivo


def _vencido():
    return (resta := prazo_restante()) is not None and resta <= 0


def _so_texto(i):
    """Motivo de a página entrar só com o texto (teto da análise ou prazo), ou None."""
    if i >= PAGINAS_ANALISE_MAX:
        return f"teto de {PAGINAS_ANALISE_MAX} páginas"
    return "prazo esgotado" if _vencido() else None


def _so_o_texto(i, md, campos=""):
    """A página só com o texto e os campos já coletados, sem análise visual."""
    md, tabelas = limpar_tabelas(desifenizar(md))
    return {"i": i, "rota": "nativa", "densidade_mat": 0, "origem_texto": "pdf-inspector", "md": md,
            "tabelas": [tb["md"] for tb in tabelas], "formulas": [], "regioes": [],
            **({"campos": campos} if campos else {})}  # fmt: skip


def _extrair(dados, a):
    """Texto das páginas da cópia de uma faixa (`dados`, de `_copia_da_faixa`) pelo pdf_inspector: markdown, páginas
    para OCR e itens com posição, pelo número da página no documento (a faixa começa na página `a`). Ele lê a cópia,
    nunca o arquivo, e não usa a PDFium: roda fora da trava."""
    import pdf_inspector

    if not dados:
        return {}, set(), {}
    res = pdf_inspector.extract_pages_markdown_bytes(dados)
    posicoes = pdf_inspector.extract_text_with_positions_bytes(dados)
    itens_de = defaultdict(list)
    for t in posicoes:
        if t.text.strip():
            itens_de[a + t.page - 1].append(t)
    return {a + pg.page: pg.markdown for pg in res.pages}, {a + p - 1 for p in res.pages_needing_ocr}, itens_de


def _copia_da_faixa(doc, faixa):
    """Bytes da cópia das páginas da faixa e quantas entraram: até a anterior à primeira que não abre, quando o /Count
    declarado passa das páginas que o arquivo tem. Chamar com a trava."""
    try:
        return _copia(doc, faixa), len(faixa)
    except pdfium.PdfiumError:
        abertas = next((k for k, i in enumerate(faixa) if not _abre(doc, i)), None)
        if abertas is None:  # todas abrem: a cópia falhou por outro motivo
            raise
        return (_copia(doc, faixa[:abertas]) if abertas else b""), abertas


def _abre(doc, i):
    try:
        doc[i].close()
    except pdfium.PdfiumError:
        return False
    return True


def _copia(doc, paginas, editar=None):
    """Bytes de um PDF novo com estas páginas do documento, depois de `editar(novo)`. Chamar com a trava: o documento
    original não muda."""
    novo = pdfium.PdfDocument.new()
    try:
        novo.import_pages(doc, list(paginas))
        if editar:
            editar(novo)
        buf = io.BytesIO()
        novo.save(buf)
        return buf.getvalue()
    finally:
        novo.close()


def _alvos_ocultos(doc, i, origem, ocultos):
    """Índices, na página `i`, dos objetos de texto dos itens ocultos, e os itens que não saem pela posição: sem objeto
    de texto que coincida, ou com um dentro de uma forma (Form XObject). A PDFium não regrava o conteúdo da forma, e a
    mesma forma pode estar desenhada também num lugar visível. `origem` é a da coleta (`_origem_visivel`).

    Os objetos de texto são coletados numa vez com a trava; a comparação roda fora dela. Cada item só é comparado com
    os objetos cujo centro cai na caixa dele (quem cobre metade de um objeto cobre o centro dele), achados numa grade.
    Acima de `OCULTOS_COMPARACOES_MAX`, nenhum item sai pela posição."""
    with PDFIUM:
        textos = _objetos_de_texto(doc, i)
    l0, b0 = origem
    grade = defaultdict(list)
    for o in textos:
        l, b, r, t = o[1]
        cx, cy = (l + max(r, l + 0.01)) / 2, (b + max(t, b + 0.01)) / 2
        if math.isfinite(cx) and math.isfinite(cy):
            grade[int(cx // CELULA), int(cy // CELULA)].append(o)
    alvos, sobrou, custo = set(), [], 0
    for x in ocultos:
        l, b, r, t = _caixa_do_item(x, l0, b0)
        n = ((r - l) / CELULA + 2) * ((t - b) / CELULA + 2)  # células na caixa; inf ou nan num item hostil
        if n <= len(grade):
            celulas = [(cx, cy) for cx in range(int(l // CELULA), int(r // CELULA) + 1)
                       for cy in range(int(b // CELULA), int(t // CELULA) + 1)]  # fmt: skip
        else:  # caixa maior que a grade: percorre as células ocupadas
            celulas = [k for k in grade if k[0] * CELULA <= r and (k[0] + 1) * CELULA >= l
                       and k[1] * CELULA <= t and (k[1] + 1) * CELULA >= b]  # fmt: skip
        perto = [grade[cel] for cel in celulas if cel in grade]
        custo += (len(celulas) if n <= len(grade) else len(grade)) + sum(map(len, perto))
        if custo > OCULTOS_COMPARACOES_MAX:
            return set(), list(ocultos)
        achou = [o for objs in perto for o in objs if _coincide(o, x, l0, b0)]
        if achou and all(k is not None for k, *_ in achou):
            alvos |= {k for k, *_ in achou}
        else:
            sobrou.append(x)
    return alvos, sobrou


def _sem_ocultos(doc, i, alvos, n):
    """Markdown (pdf_inspector) e texto (PDFium) da página `i` tirados de uma cópia dela sem os objetos de texto
    `alvos` (índices na página de `n` objetos), ou None se a cópia não tem os mesmos objetos. O documento original não
    muda. O oculto sai por posição, na origem: um texto visível igual a ele fica inteiro.

    A cópia, a remoção dos objetos (no máximo os `n` da página, abaixo de OBJETOS_MAX), a geração do conteúdo, a
    gravação e o texto pela PDFium pegam a trava; o pdf_inspector lê os bytes da cópia depois, fora dela."""
    import pdf_inspector

    texto = []

    def editar(novo):
        page = novo[0]
        if c.FPDFPage_CountObjects(page.raw) != n:
            return
        for raw in [c.FPDFPage_GetObject(page.raw, k) for k in sorted(alvos)]:  # antes: remover muda os índices
            if c.FPDFPage_RemoveObject(page.raw, raw):
                c.FPDFPageObj_Destroy(raw)
        page.gen_content()
        texto.append(texto_pdfium(page))

    with PDFIUM:
        dados = _copia(doc, [i], editar)
    if not texto:
        return None
    res = pdf_inspector.extract_pages_markdown_bytes(dados)
    return (res.pages[0].markdown if res.pages else ""), texto[0]


def pagina_visivel(doc, i):
    """A página `i` no referencial em que a análise trabalha (chamar com a trava). No documento aberto, nunca no
    arquivo (e o que muda aqui vale para as próximas cargas da página enquanto ele estiver aberto):
    - a CropBox que não sobrepõe a MediaBox dá caixa vazia; aí vale a MediaBox, herdada ou não, como no pdf_inspector
      (a decisão é pela caixa da PDFium, que normaliza e herda do /Pages, e não pelas caixas gravadas na página);
    - a rotação de exibição (/Rotate) é desligada: os objetos da PDFium e as posições do pdf_inspector vêm sem ela, e
      a renderização e o tamanho passam a vir também. A rotação fica em `rotacao_exibida`, para pôr de pé o recorte
      que vai para a IA (`de_pe`)."""
    page = doc[i]
    l, b, r, t = page.get_bbox()
    if not (l < r and b < t):
        page.set_cropbox(-_SEM_LIMITE, -_SEM_LIMITE, _SEM_LIMITE, _SEM_LIMITE)  # a interseção com ela é a MediaBox
    # a rotação original, guardada no documento na primeira carga: desligada, ela não volta na carga seguinte
    originais = doc.__dict__.setdefault("lince_rotacoes", {})
    page.rotacao_exibida = originais.setdefault(i, page.get_rotation())
    if page.get_rotation():
        page.set_rotation(0)
    return page


_SEM_LIMITE = 1e7  # pontos: maior que qualquer MediaBox (o PDF limita a 14.400 por lado)


def _caixa_da_faixa(frac, W, H, rotacao):
    """A faixa horizontal (de cima para baixo, frações f0 a f1) da página como é exibida, no referencial sem rotação."""
    f0, f1 = frac
    return {
        0: [0, H * (1 - f1), W, H * (1 - f0)],
        90: [W * f0, 0, W * f1, H],
        180: [0, H * f0, W, H * f1],
        270: [W * (1 - f1), 0, W * (1 - f0), H],
    }[rotacao % 360]


def de_pe(img, rotacao):
    """O recorte (no referencial sem rotação) girado como a página é exibida: /Rotate é no sentido horário."""
    return img.rotate(-rotacao, expand=True) if rotacao else img


def _origem_visivel(page):
    """Canto inferior esquerdo da caixa visível, a origem das posições do pdf_inspector e da página renderizada. Chamar
    depois de `pagina_visivel`. Vem da PDFium (`get_bbox`), que normaliza a caixa e a herda do nó /Pages, como na
    renderização: as caixas gravadas na página, lidas cruas, não trazem a herança."""
    l, b, _r, _t = page.get_bbox()
    return l, b


def _objetos_de_texto(doc, i):
    """(índice na página ou None dentro de uma forma, caixa na página, modo de pintura, cor de preenchimento RGBA ou
    None) de cada objeto de texto da página `i`, inclusive os de dentro das formas. Chamar com a trava; a página é
    fechada aqui, e a lista não depende mais da PDFium. A coleta já conferiu o teto de objetos da página."""

    def dados(raw, k, caixa):
        rgba = [ctypes.c_uint() for _ in range(4)]
        cor = tuple(v.value for v in rgba) if c.FPDFPageObj_GetFillColor(raw, *rgba) else None
        return k, caixa, c.FPDFTextObj_GetTextRenderMode(raw), cor

    def walk(raws, m):
        for raw in raws:
            tipo = c.FPDFPageObj_GetType(raw)
            if tipo == c.FPDF_PAGEOBJ_TEXT:
                yield dados(raw, None, _transform(_bounds(raw), m))
            elif tipo == c.FPDF_PAGEOBJ_FORM:
                yield from walk(_filhos(raw), _compose(_matrix(raw), m))

    def todos(page):
        for k in range(c.FPDFPage_CountObjects(page.raw)):
            raw = c.FPDFPage_GetObject(page.raw, k)
            tipo = c.FPDFPageObj_GetType(raw)
            if tipo == c.FPDF_PAGEOBJ_TEXT:
                yield dados(raw, k, _bounds(raw))
            elif tipo == c.FPDF_PAGEOBJ_FORM:
                yield from walk(_filhos(raw), _matrix(raw))

    page = pagina_visivel(doc, i)
    try:
        return list(todos(page))
    finally:
        page.close()


def _filhos(forma):
    return [c.FPDFFormObj_GetObject(forma, k) for k in range(c.FPDFFormObj_CountObjects(forma))]


def _caixa_do_item(x, l0, b0):
    """Caixa do item do pdf_inspector na página, com folga para a diferença entre as medidas dele e as da PDFium."""
    folga = 1 + 0.25 * x.height
    return l0 + x.x - folga, b0 + x.y - folga, l0 + x.x + x.width + folga, b0 + x.y + x.height + folga


def _coincide(obj, x, l0, b0):
    """O objeto de texto (de `_objetos_de_texto`) é parte do item: cai quase todo na caixa dele e não tem outra cor nem
    outro modo de pintura (um texto visível desenhado no mesmo lugar fica)."""
    _k, (l, b, r, t), modo, cor = obj
    if _sobrepoe([l, b, max(r, l + 0.01), max(t, b + 0.01)], _caixa_do_item(x, l0, b0)) < 0.5:
        return False
    if (modo == 3) != (x.render_mode == 3):
        return False
    if x.fill_color and cor is not None:
        return sum(abs(a - v) for a, v in zip(x.fill_color, cor, strict=False)) < 150
    return True


def _duas_colunas(md, itens, largura):
    """Desfaz linhas unidas pelo extrator só quando cada lado tem título e texto independentes.
    Confere cada bloco contra os dois itens: Markdown mais rico, tags de estrutura, tabelas e
    conteúdo sem correspondência geométrica mantêm a ordem estabelecida pelo extrator."""
    if not 6 <= len(itens) <= 1000 or len(itens) % 2:
        return md
    if any(x.mcid is not None or x.rotation or x.render_mode != 0 for x in itens):
        return md
    esquerda = sorted((x for x in itens if x.x + x.width <= largura * 0.48), key=lambda x: -x.y)
    direita = sorted((x for x in itens if x.x >= largura * 0.52), key=lambda x: -x.y)
    if not (len(esquerda) == len(direita) == len(itens) // 2):
        return md
    for coluna in (esquerda, direita):
        titulo, *corpo = coluna
        if not titulo.is_bold or not all(titulo.font_size >= x.font_size * 1.15 for x in corpo):
            return md
        if any(not x.text.strip() or not any(c.isalpha() for c in x.text) for x in coluna):
            return md
    linhas = []
    for a, b in zip(esquerda, direita, strict=True):
        if abs(a.y - b.y) > min(a.height, b.height) / 4:
            return md
        linhas.append(f"{a.text.strip()} {b.text.strip()}")
    blocos = md.strip().split("\n\n")
    plano = " ".join(re.sub(r"^#{1,6} ", "", bloco) for bloco in blocos)
    negrito = plano.startswith(f"**{linhas[0]}**")
    if negrito:
        plano = plano.replace(f"**{linhas[0]}**", linhas[0], 1)
    if plano != " ".join(linhas):
        return md
    prefixo = re.match(r"^(?:#{1,6} )?", blocos[0])[0]
    saida = []
    for titulo, *corpo in (esquerda, direita):
        nome = titulo.text.strip()
        saida.append(f"**{nome}**" if negrito else prefixo + nome)
        saida.extend(x.text.strip() for x in corpo)
    return "\n\n".join(saida)


MARCA_DAGUA_CORPO_MIN = 24  # pontos: texto inclinado deste tamanho para cima atravessa a página, não é rótulo de eixo
BORDA = 0.1  # fração da altura da página, em cima e embaixo, em que ficam o cabeçalho e o rodapé corridos


def _sem_marca_dagua(md, itens):
    """Tira do texto a marca d'água (texto grande em diagonal, como "MINUTA" ou "CONFIDENCIAL") e a devolve para os
    metadados: o extrator a entrega como um título no meio do corpo. Só sai o bloco que é exatamente a marca."""
    marcas = []
    for x in itens:
        texto = x.text.strip()
        if not (x.rotation and 15 <= abs(x.rotation) % 90 <= 75 and x.font_size >= MARCA_DAGUA_CORPO_MIN):
            continue
        if not 0 < len(texto) <= 40:
            continue
        blocos = md.split("\n\n")
        iguais = [k for k, b in enumerate(blocos) if re.sub(r"^#{1,6}[ \t]+|\*\*", "", b.strip()) == texto]
        if len(iguais) == 1:
            del blocos[iguais[0]]
            md = "\n\n".join(blocos)
            marcas.append(texto)
    return md, marcas


def _bordas_soltas(md, itens, H):
    """Separa do parágrafo a linha de cabeçalho ou rodapé que o extrator colou nele ("…pro rata die. Empresa · Página
    1 de 3"): a linha mais alta e a mais baixa da página, quando estão na faixa da borda, viram blocos próprios, e a
    conferência de cabeçalho corrido entre as páginas decide depois se saem do corpo."""
    textos = [x for x in itens if x.text.strip() and not x.text.startswith("[Image:") and not x.rotation]
    if len(textos) < 4:
        return md
    for extremo, na_borda in ((min, lambda y: y < BORDA * H), (max, lambda y: y > (1 - BORDA) * H)):
        y = extremo(x.y for x in textos)
        if not na_borda(y):
            continue
        linha = sorted((x for x in textos if abs(x.y - y) <= 2), key=lambda x: x.x)
        # o cabeçalho e o rodapé ficam afastados do corpo; a última linha de um texto de espaçamento regular é corpo
        outras = [abs(x.y - y) for x in textos if abs(x.y - y) > 2]
        if not outras or min(outras) < 2.5 * max(x.height for x in linha):
            continue
        s = " ".join(" ".join(x.text.split()) for x in linha)
        if not 4 <= len(s) <= 80:
            continue
        blocos = md.split("\n\n")
        if any(re.sub(r"^#{1,6}[ \t]+|\*\*", "", b.strip()) == s for b in blocos):
            continue  # a linha da borda já é um bloco próprio (o título da página, por exemplo)
        for k, b in enumerate(blocos):
            if b.lstrip().startswith(("|", "#", "{{")):
                continue
            if b.rstrip().endswith((" " + s, "\n" + s)) and b.count(s) == 1:
                blocos[k : k + 1] = [b.rstrip()[: -len(s)].rstrip(), s]
                break
            if b.startswith((s + " ", s + "\n")) and b.count(s) == 1:
                blocos[k : k + 1] = [s, b[len(s) :].lstrip()]
                break
        md = "\n\n".join(blocos)
    return md


def _texto_das_celulas(doc, i, caixas):
    # O item do extrator às vezes abrange vários meses. Lê os caracteres entre os filetes do cabeçalho.
    with PDFIUM:
        pagina = pagina_visivel(doc, i)
        try:
            x, y, _, _ = pagina.get_bbox()
            texto = pagina.get_textpage()
            return [" ".join(texto.get_text_bounded(l + x, b + y, r + x, t + y).split()) for l, b, r, t in caixas]
        except pdfium.PdfiumError:
            return []
        finally:
            pagina.close()


def _pagina(doc, i, col, md, eh_ocr, itens_pg, pasta, tarefas, cache, seguranca, pendentes):
    """Roteamento de uma página: texto, regiões visuais, tabela duvidosa, fórmula e texto oculto, sobre a coleta dela
    (`_coleta`). Roda sem a trava: a análise em Python, por mais que custe numa página hostil, não faz outro PDF
    esperar. Só a cópia sem o texto oculto pega a trava, nas vezes dela (`_alvos_ocultos` e `_sem_ocultos`). Devolve a
    página e, se ela vai para a IA, a escala da renderização e a função que faz os recortes, os arquivos e as tarefas
    dela com a imagem renderizada: quem chama renderiza com a trava e recorta sem ela, antes da próxima página."""
    W, H, objs, rotacao = col["W"], col["H"], col["objs"], col["rotacao"]
    # Imagens vizinhas viram um grupo: texto fatiado em imagens ("Version 8 –
    # March 2025" da capa da Falcon são 6 fatias) e logotipo pequeno contam.
    imgs = [list(bx) for tipo, bx in objs if tipo == c.FPDF_PAGEOBJ_IMAGE and bx[0] < bx[2] and bx[1] < bx[3]]
    itens_txt = [x for x in itens_pg if not x.text.startswith("[Image:")]
    ocultos = texto_oculto_pdf(col["fundo"], itens_txt, imgs, W, H, tique=_tique)
    alt = col["texto"] if eh_ocr else ""
    vistos = re.sub(r"\s+", " ", " ".join(x.text for x in itens_pg)).lower()
    sobrou = []
    if ocultos:
        alvos, sobrou = _alvos_ocultos(doc, i, col["origem"], [x for _, x in ocultos])
        if alvos and (limpo := _sem_ocultos(doc, i, alvos, col["n"])):
            md, alt_limpo = limpo
            alt = alt_limpo if eh_ocr else ""
        elif alvos:  # a cópia não tem os mesmos objetos: nenhum item sai
            sobrou = [x for _, x in ocultos]
        ids = {id(x) for _, x in ocultos}
        itens_pg = [x for x in itens_pg if id(x) not in ids]
    origem_texto = "pdf-inspector"
    if eh_ocr and len(alt.strip()) > 200 and lixo(alt) < 0.02:
        md, origem_texto = alt, "pdfium"  # p.ex. a p.4 do manual da Falcon
    campos, campos_visuais, erro_campos, posicoes_campos = col["formulario"]
    if campos and not rotacao:
        campos = com_rotulos(campos, posicoes_campos, itens_pg, W)
    if erro_campos:
        pendentes.append(f"página {i + 1}: {erro_campos}")
    if not (eh_ocr or rotacao or ocultos or campos) and all(tipo == c.FPDF_PAGEOBJ_TEXT for tipo, _ in objs):
        md = _duas_colunas(md, itens_pg, W)
    md, marcas_dagua = _sem_marca_dagua(md, itens_pg)
    if not (eh_ocr or rotacao or campos) and (
        colunas_confundidas(limpar_tabelas(md)[1]) or colunas_fundidas(md, itens_pg, W)
    ):
        if refeito := refazer_colunas(md, itens_pg, W, _tique):
            md = refeito
    md = _bordas_soltas(md, itens_pg, H)
    dens = densidade_matematica(md)
    nativo = desifenizar(md)
    md, tabelas = limpar_tabelas(nativo)
    caixas_de_grade = []
    if tabelas and not (rotacao or sobrou or eh_ocr):
        tracos = [bx for tipo, bx in objs if tipo == c.FPDF_PAGEOBJ_PATH]
        filetes = [bx for bx in tracos if bx[2] - bx[0] <= 2 < bx[3] - bx[1]]
        caixas_de_grade = aplicar_grades(tabelas, tracos, itens_pg, _tique)
        if not ocultos:
            marcas = [bx for tipo, bx in objs if tipo in (c.FPDF_PAGEOBJ_PATH, c.FPDF_PAGEOBJ_IMAGE)]
            md = conferir_tabelas(md, tabelas, itens_pg, filetes, _tique, marcas)
        ler_celulas = (lambda caixas: _texto_das_celulas(doc, i, caixas)) if not ocultos else lambda caixas: []
        md = conferir_blocos(md, tabelas, itens_pg, filetes, tracos, W, H, _tique, ler_celulas)
    # só tabela nativa CONFIÁVEL anula um desenho; tabela duvidosa pode ser rótulo de gráfico
    palavras_tabela = set().union(*[tb["palavras"] for tb in tabelas if not tb["complexa"]]) if tabelas else set()
    tabela_de = {}
    # regiões: raster + vetor
    regioes = []
    grupos = []
    for bx in imgs:
        _tique()
        mudou = True
        while mudou:
            mudou = False
            for g in grupos:
                if not (bx[2] + 8 < g[0] or g[2] + 8 < bx[0] or bx[3] + 2 < g[1] or g[3] + 2 < bx[1]):
                    bx = [min(bx[0], g[0]), min(bx[1], g[1]), max(bx[2], g[2]), max(bx[3], g[3])]
                    grupos.remove(g)
                    mudou = True
                    break
        grupos.append(bx)
    for l, b, r, t in sorted(grupos, key=lambda g: -g[3]):
        linha_de_texto = (t - b) >= 8 and (r - l) / max(t - b, 1) >= 4
        if (r - l) * (t - b) / (W * H) >= 0.005 or linha_de_texto:
            # Uma ilustração de fundo quase da largura da folha pode começar no meio
            # de uma coluna digital. Inclui as linhas realmente atravessadas por sua borda.
            if W > 2 * H and r - l >= 0.8 * W and t - b >= 0.9 * H:
                cruzam = [
                    x
                    for x in itens_txt
                    if x.width > 0 and b <= x.y <= t and (x.x < l < x.x + x.width or x.x < r < x.x + x.width)
                ]
                if cruzam:
                    l = min(l, min(x.x for x in cruzam))
                    r = max(r, max(x.x + x.width for x in cruzam))
            regioes.append(("imagem", [l, b, r, t]))
    registro = [(m, x.text) for m, x in ocultos]
    # Texto que o extrator descartou (ex.: modo de renderização invisível) mas a camada de texto tem:
    # não vai para o corpo, mas é sinal de ataque quando a página não é um scan.
    if not imgs:
        linhas = col["texto"].splitlines()
        for ln in linhas:
            ln = re.sub(r"\s+", " ", ln).strip()
            if len(ln) >= 20 and ln.lower() not in vistos and ln.lower()[:20] not in vistos:
                registro.append(("não renderizado (invisível)", ln))
    if registro:
        # Item oculto que não saiu da cópia (sem objeto de texto, dentro de forma, acima do teto), ou texto não
        # renderizado que o extrator pôs no corpo: o corpo pode ter o oculto, e quem chama precisa saber.
        corpo = re.sub(r"\s+", " ", md).lower()
        ficou = sobrou or any(m == "não renderizado (invisível)" and t.lower() in corpo for m, t in registro)
        alerta = f"texto oculto que pode ter ficado no corpo: página {i + 1}" if ficou else None
        seguranca.append(
            {
                "pagina": i + 1,
                "tecnicas": sorted({m for m, _ in registro}),
                "texto": " ".join(t for _, t in registro)[:500],
            }
            | ({"alerta": alerta} if ficou else {})
        )
        if ficou:
            pendentes.append(alerta)
    # Tabela nativa duvidosa: recorte da tabela vai para a IA (com o nativo como âncora).
    itens_conferidos = set()
    for tb in tabelas:
        if cx := tb.get("caixa"):
            _tique()
            l, b, r, t = cx
            itens_conferidos.update(
                id(x) for x in itens_pg if l <= x.x + x.width / 2 <= r and b <= x.y + x.height / 2 <= t
            )
    for k, tb in enumerate(tabelas):
        _tique()
        if not tb["complexa"]:
            continue
        if caixa := tb.get("caixa"):
            tabela_de[id(caixa)] = k
            regioes.append(("tabela", caixa))
            continue
        dentro = [
            x
            for x in itens_pg
            if not x.text.startswith("[Image:")
            and id(x) not in itens_conferidos
            and set(re.findall(r"\w+", x.text.lower()))
            and set(re.findall(r"\w+", x.text.lower())) <= tb["palavras"]
        ]
        if len(dentro) >= 4 and (caixa := caixa_do_bloco_principal(dentro)):
            tabela_de[id(caixa)] = k
            regioes.append(("tabela", caixa))
    papeis = col.get("papeis", {})
    imagens = [g for tp, g in regioes if tp == "imagem"]
    recortes_exatos = set()
    panorama = paineis_panorama(objs, W, H, itens_pg, papeis, imagens, _tique)
    if not panorama:
        caminhos_visiveis = col.get("caminhos_visiveis")
        objetos_figuras = (
            [(tp, caminhos_visiveis.get(k, g)) for k, (tp, g) in enumerate(objs)] if caminhos_visiveis else objs
        )
        figuras = regioes_vetoriais(objetos_figuras, W, H, itens_pg, papeis, col["fundo"], caixas_de_grade)
        if faixa := faixa_ilustrada(objs, W, H, itens_pg, papeis, imagens):
            base, corpo = faixa
            if len(figuras) == 1 and _sobrepoe(corpo, figuras[0][0]) >= 0.9:
                panorama = base, [corpo]
    if panorama:
        imagem, paineis = panorama
        recortes_exatos = {id(imagem), *(id(g) for g in paineis)}
        regioes = [(tp, imagem if tp == "imagem" else g) for tp, g in regioes]
        figuras = [(g, g, True) for g in paineis]
    for g, desenho, figura_identificada in figuras:
        _tique()
        if any(_sobrepoe(g, rr) > 0.5 for tp, rr in regioes if tp == "imagem"):
            continue
        # Desenho sobre uma tabela duvidosa: conserva os rótulos como texto e deixa o desenho para a IA.
        for tp, rr in list(regioes):
            if tp == "tabela" and not tabelas[tabela_de[id(rr)]].get("confirmada") and _sobrepoe(rr, g) > 0.5:
                regioes.remove((tp, rr))
                tabela = tabelas[tabela_de[id(rr)]]
                tabela["md"] = texto_de_rotulos(tabela)
                tabela["complexa"] = False
                tabela["rotulos"] = True
        # Tabela desenhada já sai como tabela no nativo: não é figura.
        dentro = re.findall(
            r"\w+", " ".join(x.text for x in itens_pg if g[0] <= x.x <= g[2] and g[1] <= x.y <= g[3]).lower()
        )
        if not figura_identificada and dentro and sum(w in palavras_tabela for w in dentro) / len(dentro) > 0.6:
            continue
        regioes.append(("desenho", desenho))
    # Quando a tabela nativa contém rótulos do gráfico, só a estrutura tabular sai do texto.
    for k, tb in enumerate(tabelas):
        _tique()
        if tb.get("confirmada") or tb.get("rotulos"):
            continue
        caixas = [g for tp, g in regioes if tp in ("desenho", "imagem")]
        itens_tb = [
            x
            for x in itens_pg
            if set(re.findall(r"\w+", x.text.lower())) and set(re.findall(r"\w+", x.text.lower())) <= tb["palavras"]
        ]
        if (
            caixas
            and itens_tb
            and sum(any(g[0] - 4 <= x.x <= g[2] + 4 and g[1] - 4 <= x.y <= g[3] + 4 for g in caixas) for x in itens_tb)
            / len(itens_tb)
            > 0.7
        ):
            tb["md"] = texto_de_rotulos(tb)
            tb["complexa"] = False
            regioes[:] = [(tp, g) for tp, g in regioes if not (tp == "tabela" and tabela_de.get(id(g)) == k)]
    lixo_nativo = lixo(md) if md.strip() else 0.0
    # fórmulas escritas como texto (sub/sobrescrito, Σ, ×, ÷): o nativo embaralha a notação
    blocos_md = md.split("\n\n")
    formulas = [
        (j, b)
        for j, b in enumerate(blocos_md)
        if len(b) >= 15
        and "|" not in b
        and "=" in b
        and (sum(b.count(ch) for ch in "=×÷Σ∑·±√∫≤≥≈∞^") + 3 * len(re.findall(r"<su[bp]>", b))) / len(b) >= 0.02
    ]
    precisa_pagina = (eh_ocr and origem_texto != "pdfium") or dens >= 4.0 or lixo_nativo > 0.02
    # Fórmula escrita como texto: só o RECORTE do bloco vai para a IA (a página de texto não é retranscrita).
    formula_de, originais_formula = {}, []
    if formulas and not precisa_pagina:
        itens_txt = [x for x in itens_pg if not x.text.startswith("[Image:")]
        localizadas = localizar_formulas((bloco for _, bloco in formulas), itens_txt, W, _tique)
        for j, bloco in formulas:
            _tique()
            if limites := localizadas.get(bloco):
                l, b, r, t = limites
            else:
                # A ordem dos glifos nem sempre corresponde à do extrator (p.ex. em frações).
                # A imagem aproximada continua disponível, com a incerteza registrada.
                limpo = re.sub(r"</?su[bp]>|[*_]", "", bloco)
                dentro = [x for x in itens_txt if x.text.strip() and x.text.strip() in limpo]
                pendentes.append(f"página {i + 1}: localização da fórmula não confirmada")
                if not dentro:
                    continue
                l, b = min(x.x for x in dentro), min(x.y for x in dentro)
                r, t = max(x.x + x.width for x in dentro), max(x.y + x.height for x in dentro)
                # Os pedaços achados podem estar espalhados (um título de outra coluna entrou no bloco): a caixa
                # que os une leva o texto vizinho, cortado. Sem uma caixa só da fórmula, o texto nativo fica.
                na_caixa = [x for x in itens_txt if l <= x.x + x.width / 2 <= r and b <= x.y + x.height / 2 <= t]
                if len(dentro) < 0.7 * len(na_caixa):
                    continue
            margem_formula = 2 if limites else 8
            caixa = [
                max(0, l - margem_formula),
                max(0, b - 8),  # a tipografia pode estender a base de frações e colchetes abaixo dos glifos
                min(W, r + margem_formula),
                min(H, t + margem_formula),
            ]
            if limites:
                recortes_exatos.add(id(caixa))
            indice = None
            if limites:
                indice = len(originais_formula)
                originais_formula.append(bloco)
                blocos_md[j] = f"{{{{FORMULA:{indice}}}}}"
            formula_de[id(caixa)] = (bloco, indice)
            regioes.append(("formula", caixa))
        md = "\n\n".join(blocos_md)
    # Na escala normal, o desenho já contém os mesmos pixels das fotos internas (p.ex. espectrogramas).
    # Em um mapa grande, preserve o detalhe raster separado, pois o provedor pode reduzir o quadro inteiro.
    if not precisa_pagina and not eh_ocr and imgs:
        fator = escala(W, H, 2.0)
        desenhos_legiveis = [
            g for tipo, g in regioes if tipo == "desenho" and (max(g[2] - g[0], g[3] - g[1]) + 12) * fator <= 1568
        ]
        # a imagem quase toda dentro de um desenho é lida com ele: o desenho cresce até contê-la inteira
        maiores = {}
        for tipo, g in regioes:
            if tipo == "imagem" and id(g) not in recortes_exatos:
                for d in desenhos_legiveis:
                    if 0.9 <= _sobrepoe(g, d) < 1 and id(d) not in recortes_exatos:
                        d0 = maiores.get(id(d), d)
                        maior = [min(d0[0], g[0]), min(d0[1], g[1]), max(d0[2], g[2]), max(d0[3], g[3])]
                        # só cresce se continuar legível na escala do recorte; senão a imagem segue com a tarefa dela
                        if (max(maior[2] - maior[0], maior[3] - maior[1]) + 12) * fator <= 1568:
                            maiores[id(d)] = maior
        regioes = [(tipo, maiores.get(id(g), g) if tipo == "desenho" else g) for tipo, g in regioes]
        desenhos_legiveis = [maiores.get(id(d), d) for d in desenhos_legiveis]
        regioes = [
            (tipo, g)
            for tipo, g in regioes
            if tipo != "imagem" or not any(_sobrepoe(g, d) >= 1 for d in desenhos_legiveis)
        ]
    # região toda fora da área visível (objeto fora da folha, comum em desenho de CAD) não tem o que recortar
    regioes = [(tp, g) for tp, g in regioes if g[0] < W and g[2] > 0 and g[1] < H and g[3] > 0]
    regioes.extend(("imagem", caixa) for caixa in campos_visuais)
    # Formulário com widgets apenas: os campos fornecem o texto, e o que só tem aparência vai como recorte.
    if (campos or campos_visuais) and not objs:
        precisa_pagina = False
    rota = "pagina" if precisa_pagina else ("regioes" if regioes else "nativa")
    info = {
        "i": i,
        "rota": rota,
        "densidade_mat": round(dens, 2),
        "origem_texto": origem_texto,
        "md": md,
        "tabelas": [tb["md"] for tb in tabelas],
        "formulas": originais_formula,
        "regioes": [],
    }
    if rota == "pagina" and not registro and col.get("texto_visivel"):
        if referencias := potencias(col["texto"], camada_pdf=True):
            info["potencias_visiveis"] = referencias
    if campos:
        info["campos"] = campos
    if marcas_dagua:
        info["marca_dagua"] = marcas_dagua
    if rota == "nativa":
        return info, None
    retrato = (H >= W) if rotacao in (0, 180) else (W >= H)  # como é exibida: a de pé ganha o dobro e é dividida
    fator = (2 * 1568 / max(W, H) if retrato else 1568 / max(W, H)) if rota == "pagina" else escala(W, H, 2.0)
    texto_fora_do_scan = []
    if (
        rota == "pagina"
        and eh_ocr
        and origem_texto != "pdfium"
        and not (registro and ficou)
        and col.get("texto_visivel")
        and 1 <= sum(tipo == c.FPDF_PAGEOBJ_TEXT for tipo, _ in objs) <= 128
    ):
        # A geometria e a opacidade vêm dos objetos pintados, não da camada
        # extraída. O PDF é fechado antes dos recortes na última página.
        with PDFIUM:
            textos = _objetos_de_texto(doc, i)
        ox, oy = col["origem"]
        texto_fora_do_scan = [
            ((l - ox, b - oy, r - ox, t - oy), rgba[:3])
            for _, (l, b, r, t), modo, rgba in textos
            if modo == 0 and rgba is not None and rgba[3] >= 128
        ]

    def recortar(img):
        """Recortes, arquivos e tarefas da página, com a imagem renderizada na escala `fator`: quem chama roda fora da
        trava, na ordem das páginas (as tarefas e o cache de recortes repetidos saem na mesma ordem de antes)."""
        if rota == "pagina":
            from .cobertura_scan import limiar_de_texto, texto_digital_fora_do_raster

            # a página inteira vai para a IA como é exibida
            digital = texto_digital_fora_do_raster(img, itens_pg, texto_fora_do_scan, imgs, W, H, rotacao, _tique)
            img = de_pe(img, rotacao)
            # Duas metades com sobreposição: o dobro de resolução por leitura (o provedor reduz acima de ~1568 px).
            # Texto que já passou pela conferência de ocultos. A camada de OCR rejeitada
            # e o texto cuja limpeza ficou incerta não servem de reserva.
            pode_usar_nativo = (not eh_ocr or origem_texto == "pdfium") and not (registro and ficou)
            metades = [(0, img.height)]
            if img.height >= img.width:
                # Linhas de texto pela projeção horizontal; só divide página densa, numa faixa sem tinta.
                cinza = img.convert("L").resize((img.width // 4, img.height // 4))
                larg, alt = cinza.size
                px = cinza.load()
                tinta = [sum(1 for x in range(0, larg, 2) if px[x, y] < 160) for y in range(alt)]
                linhas_txt = sum(1 for y in range(1, alt) if tinta[y] > 2 and tinta[y - 1] <= 2)
                if linhas_txt > 55:
                    meio = alt // 2
                    vazias = [y for y in range(int(alt * 0.35), int(alt * 0.65)) if tinta[y] <= 1]
                    if pode_usar_nativo and nativo.strip():
                        # A reserva por faixa precisa de uma fronteira sem texto compartilhado.
                        # Confirmar no bitmap original: a projeção reduzida só indica candidatos.
                        for linha in sorted(vazias, key=lambda y: abs(y - meio)):
                            _tique()
                            corte = 4 * linha
                            minimo, _ = img.crop((0, corte - 1, img.width, corte + 1)).convert("L").getextrema()
                            if minimo >= 245:
                                metades = [(0, corte + 1), (corte - 1, img.height)]
                                break
                    else:
                        corte = 4 * (min(vazias, key=lambda y: abs(y - meio)) if vazias else meio)
                        metades = [(0, min(img.height, corte + 24)), (max(0, corte - 24), img.height)]
            sufixos = ("a", "b") if len(metades) > 1 else ("",)
            ids = [f"p{i + 1:03d}{s}" for s in sufixos]
            for inicio, fim, texto in digital:
                # Um texto entre os centros das faixas fica entre as leituras.
                centro = (inicio + fim) / 2
                pos = sum((y0 + y1) / 2 < centro * img.height for y0, y1 in metades)
                # Uma ocorrência visual só substitui a digital se o PNG dela
                # contém a caixa inteira. Na sobreposição, prioriza a faixa mais próxima.
                candidatas = [
                    (abs((y0 + y1) / 2 - centro * img.height), tid)
                    for tid, (y0, y1) in zip(ids, metades, strict=True)
                    if y0 <= inicio * img.height and fim * img.height <= y1
                ]
                info.setdefault("digital_scan", {}).setdefault(str(pos), []).append(
                    [texto, [tid for _, tid in sorted(candidatas)]]
                )
            motivo = (
                "escaneada"
                if eh_ocr
                else ("texto nativo corrompido" if lixo_nativo > 0.02 else f"matemática densa ({dens:.1f})")
            )
            info["tarefas"] = []
            for num, (tid, (y0, y1)) in enumerate(zip(ids, metades, strict=True), 1):
                parte = f"{num}/{len(metades)}"
                faixa = img.crop((0, y0, img.width, y1))
                if eh_ocr and not pode_usar_nativo and (limiar := limiar_de_texto(faixa)):
                    info.setdefault("sinais_scan", {})[tid] = [parte, limiar]
                faixa.save(f"{pasta}/ia/{tid}.png")
                frac = (y0 / img.height, y1 / img.height)
                tarefas.append(
                    {
                        "id": tid,
                        "modo": "pagina",
                        "parte": parte,
                        "motivo": motivo,
                        "ancora": md[:6000],
                        "pagina": i,
                        "caixa": _caixa_da_faixa(frac, W, H, rotacao),
                    }
                )
                if pode_usar_nativo:
                    if len(metades) == 1:
                        trecho = "\n\n".join(b for b in nativo.split("\n\n") if lixo(b) <= 0.02)
                    else:
                        # O centro do item determina uma só metade, sem as margens sobrepostas do PNG.
                        divisao = corte / img.height
                        partes = _caixa_da_faixa((0, divisao), W, H, rotacao)
                        l, b, r, t = partes
                        trecho = " ".join(
                            item.text
                            for item in itens_pg
                            if not item.text.startswith("[Image:")
                            and lixo(item.text) <= 0.02
                            and (
                                (l <= item.x + item.width / 2 <= r and b <= item.y + item.height / 2 <= t) == (num == 1)
                            )
                        )
                    if trecho.strip():
                        info.setdefault("nativo_pendente", {})[tid] = trecho
                info["tarefas"].append(tid)
        else:
            full = img
            itens = [t for t in itens_pg if not t.text.startswith("[Image:")]
            # A camada invisível de um scan representa a mesma folha que o recorte. Uma foto
            # parcial, uma imagem composta de fatias ou um texto digital sobreposto não bastam.
            so_ocr = (
                not registro
                and origem_texto == "pdfium"
                and len(regioes) == 1
                and not col["texto_visivel"]
                and any(tipo == c.FPDF_PAGEOBJ_TEXT for tipo, _ in objs)
                and any(_sobrepoe((0, 0, W, H), bx) >= 0.99999 for bx in imgs)
            )
            for j, (tipo, cx) in enumerate(regioes, 1):
                l, b, r, t = cx
                m = 0 if id(cx) in recortes_exatos else 6
                visivel = [max(0, l - m), max(0, b - m), min(W, r + m), min(H, t + m)]
                if tipo == "desenho" and m:
                    # A margem do recorte não deve cortar a última linha de uma seção vizinha.
                    for x in itens:
                        if x.x < r and x.x + x.width > l:
                            if x.y >= t:
                                # O inspetor fornece a base tipográfica: os descendentes podem cair abaixo dela.
                                visivel[3] = max(t, min(visivel[3], x.y - 0.3 * x.height - 1))
                            if visivel[1] < x.y + x.height <= b:
                                visivel[1] = min(b, x.y + x.height + 1)
                box = (
                    max(0, int(visivel[0] * fator)),
                    max(0, int((H - visivel[3]) * fator)),
                    min(full.width, int(visivel[2] * fator)),
                    min(full.height, int((H - visivel[1]) * fator)),
                )
                if box[2] <= box[0] or box[3] <= box[1]:  # nada visível desta região na imagem renderizada
                    continue
                tid = f"p{i + 1:03d}-r{j}"
                recorte = de_pe(full.crop(box), rotacao)  # de pé, como a página é exibida
                chave = hashlib.sha256(recorte.tobytes()).hexdigest()
                if so_ocr and tipo == "imagem":
                    info["ocr_da_imagem"] = cache.get(chave, tid)
                if chave in cache:  # mesma imagem já lida (logo do cabeçalho etc.)
                    info["regioes"].append(
                        {"tarefa": cache[chave], "depois_de": "", "texto_dentro": "", "repetida": True}
                    )
                    continue
                cache[chave] = tid
                recorte.save(f"{pasta}/ia/{tid}.png")
                dentro = [x for x in itens if l - 2 <= x.x <= r + 2 and b - 2 <= x.y <= t + 2]
                ancora = " ".join(x.text for x in sorted(dentro, key=lambda x: (-x.y, x.x)))
                if tipo == "tabela":
                    ancora = tabelas[tabela_de[id(cx)]]["md"]
                if tipo == "formula":
                    ancora, indice_formula = formula_de[id(cx)]
                acima = [x for x in itens if x.y >= t and not (l <= x.x <= r and b <= x.y <= t)]
                linha_acima = min(acima, key=lambda x: x.y).text.strip()[:25] if acima else ""
                tarefas.append(
                    {
                        "id": tid,
                        "modo": tipo,
                        "ancora": ancora[:3000],
                        "pagina": i,
                        "caixa": visivel,
                    }
                )
                info["regioes"].append(
                    {
                        "tarefa": tid,
                        "depois_de": linha_acima,
                        "texto_dentro": ancora,
                        **({"desenho": True} if tipo == "desenho" else {}),
                        **({"texto_fora": fora} if tipo == "desenho" and (fora := _texto_fora(itens, dentro)) else {}),
                        # A legenda digital sobre uma foto de fundo é texto da página.
                        **(
                            {"preserva_texto": True}
                            if tipo == "imagem"
                            and col["texto_visivel"]
                            and (id(cx) in recortes_exatos or _sobrepoe((0, 0, W, H), cx) >= 0.99999)
                            else {}
                        ),
                        **({"substitui_tabela": tabela_de[id(cx)]} if tipo == "tabela" else {}),
                        **(
                            {
                                "tabela_confirmada": True,
                                "estrutura_tabela_confirmada": tabelas[tabela_de[id(cx)]]["estrutura_confirmada"],
                            }
                            if tipo == "tabela" and tabelas[tabela_de[id(cx)]].get("confirmada")
                            else {}
                        ),
                        **(
                            {"substitui_formula": indice_formula}
                            if tipo == "formula" and indice_formula is not None
                            else {"preserva_texto": True}
                            if tipo == "formula"
                            else {}
                        ),
                    }
                )

    return info, (fator, recortar, bool(campos_visuais))


TEXTO_FORA_MAX = 20_000  # caracteres do texto fora de um desenho guardados para a conferência dos rótulos


def _texto_fora(itens, dentro):
    """O texto da página que está fora do recorte de um desenho, para a montagem não tirar do corpo um bloco que pode
    ser de fora dele. Vazio quando não cabe no teto: sem a lista inteira, a montagem não tira nada."""
    ids = {id(x) for x in dentro}
    fora = " ".join(x.text for x in itens if id(x) not in ids)
    return fora if len(fora) <= TEXTO_FORA_MAX else ""


def _sobrepoe(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = (a[2] - a[0]) * (a[3] - a[1]) or 1
    return ix * iy / area


def _bounds(raw):
    l, b, r, t = (ctypes.c_float() for _ in range(4))
    c.FPDFPageObj_GetBounds(raw, l, b, r, t)
    return l.value, b.value, r.value, t.value


def _matrix(raw):
    m = c.FS_MATRIX()
    c.FPDFPageObj_GetMatrix(raw, m)
    return (m.a, m.b, m.c, m.d, m.e, m.f)


def _compose(m1, m2):  # aplica m1 e depois m2
    a, b, cc, d, e, f = m1
    A, B, C, D, E, F = m2
    return (a * A + b * C, a * B + b * D, cc * A + d * C, cc * B + d * D, e * A + f * C + E, e * B + f * D + F)


def _transform(box, m):
    l, b, r, t = box
    pts = [(l, b), (l, t), (r, b), (r, t)]
    xs = [m[0] * x + m[2] * y + m[4] for x, y in pts]
    ys = [m[1] * x + m[3] * y + m[5] for x, y in pts]
    return min(xs), min(ys), max(xs), max(ys)


def objetos(page, *, limitar_imagens=False, caminhos_visiveis=None):
    """Gera (tipo, raw, caixa_na_pagina)."""
    janela_pagina = page.get_bbox() if limitar_imagens or caminhos_visiveis is not None else None
    area_minima = 0
    if caminhos_visiveis is not None:
        l, b, r, t = janela_pagina
        area_minima = 0.012 * (r - l) * (t - b)
    indice = -1

    def walk(raws, m, janela):
        nonlocal indice
        for raw in raws:
            indice += 1
            tipo = c.FPDFPageObj_GetType(raw)
            box = _bounds(raw)
            box = _transform(box, m) if m else box
            limite = janela
            if limitar_imagens and tipo in (c.FPDF_PAGEOBJ_FORM, c.FPDF_PAGEOBJ_IMAGE):
                recorte = janela_retangular(raw)
                if recorte is not None:
                    recorte = _transform(recorte, m) if m else recorte
                    limite = interseccao(limite, recorte)
                if tipo == c.FPDF_PAGEOBJ_IMAGE:
                    box = interseccao(box, limite)
            if caminhos_visiveis is not None and tipo == c.FPDF_PAGEOBJ_PATH:
                l, b, r, t = box
                if (r - l) * (t - b) >= area_minima and (recorte := janela_retangular(raw)) is not None:
                    recorte = _transform(recorte, m) if m else recorte
                    visivel = interseccao(box, interseccao(janela, recorte))
                    if visivel != box:
                        caminhos_visiveis[indice] = visivel
            yield tipo, raw, box
            if tipo == c.FPDF_PAGEOBJ_FORM:
                mm = _compose(_matrix(raw), m) if m else _matrix(raw)
                n = c.FPDFFormObj_CountObjects(raw)
                yield from walk((c.FPDFFormObj_GetObject(raw, k) for k in range(n)), mm, limite)

    n = c.FPDFPage_CountObjects(page.raw)
    yield from walk((c.FPDFPage_GetObject(page.raw, k) for k in range(n)), None, janela_pagina)
