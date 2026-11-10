"""Orquestrador: arquivo → extração nativa e roteamento → leituras pela IA (com cache) → markdown + metadados."""

import contextvars
import json
import logging
import math
import os
import re
import shutil
import tempfile
import time
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import anydoc

from ._prazo import LIMITE, prazo_restante
from ._trava import PDFIUM
from .agrupamento import agrupar, distribuir_lotes, resposta_aproveitavel
from .cache import Cache, chave
from .estrutura import secoes
from .instrucoes import PROMPT_PADRAO, VERSAO_PROMPT, instrucao
from .limites import LADO_MAX_PX, com_barras_normais, conferir_zip
from .metadados import metadados_arquivo
from .montagem import SAIDA_MAX, montar, tamanho_da_saida
from .office import planejar_office
from .pagina_html import planejar_html, planejar_svg
from .pdf import de_pe, pagina_visivel, planejar_pdf
from .protecao import DocumentoProtegido, aberto, formato_protegido
from .provedores import LeitorVisual
from .tipos import Cobertura, Plano, PrazoEsgotado, Resultado, Uso

TEXTO = {".txt", ".md", ".json", ".yaml", ".yml", ".tex", ".xml"}
IMAGEM = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
HTML = {".html", ".htm"}
SVG = {".svg"}
OFFICE = {  # lidos pelo anydoc (com gráficos, fluxogramas e imagens à parte)
    ".docx",
    ".docm",
    ".doc",
    ".pptx",
    ".pptm",
    ".ppsx",
    ".ppsm",
    ".ppt",
    ".pps",
    ".pot",
    ".xlsx",
    ".xlsm",
    ".xls",
    ".xlsb",
    ".odt",
    ".ods",
    ".odp",
    ".rtf",
    ".epub",
    ".csv",
    ".tsv",
}
FORMATOS = frozenset({".pdf"} | IMAGEM | TEXTO | HTML | SVG | OFFICE)  # o que o Lince lê; a API recusa o resto com 415


class FormatoNaoSuportado(anydoc.UnsupportedError):
    """Formato que o Lince não lê. É um `anydoc.ConvertError`, como as demais falhas de conversão."""


try:  # a versão vem do pacote instalado (pyproject), carimbada pelos workflows de versão
    VERSAO = version("lince")
except PackageNotFoundError:  # rodando direto do código-fonte, sem instalar
    VERSAO = "0+fonte"


class Leitor:
    """Leitor único de documentos.

    ia:          modelo de visão da plataforma (opcional: sem IA, sai o nativo com as pendências marcadas).
    cache:       cache de leituras (opcional).
    ao_usar:     callback de custo, chamado a cada leitura (para o ledger da plataforma).
    agrupar:     junta recortes pequenos numa folha numerada (menos chamadas).
    max_leituras: teto de leituras pela IA por documento (o resto fica pendente, registrado).
    """

    def __init__(
        self,
        ia: LeitorVisual | None = None,
        cache: Cache | None = None,
        ao_usar: Callable[[Uso], None] | None = None,
        prompt: str = PROMPT_PADRAO,
        concorrencia: int = 8,
        agrupar: bool = True,
        max_leituras: int | None = None,
        esforco: str = "padrao",
    ):
        self.ia, self.cache, self.ao_usar, self.prompt = ia, cache, ao_usar, prompt
        self.concorrencia, self.agrupar, self.max_leituras = concorrencia, agrupar, max_leituras
        self.esforco = esforco  # "padrao" | "max": 2ª passada ampliada no que saiu ilegível/estimado

    # ------------------------------------------------------------------ planejamento
    def planejar(self, caminho: str | Path, pasta: str | Path) -> dict:
        return self._planejar(str(caminho), str(pasta))

    def _planejar(self, caminho, pasta):
        if formato := formato_protegido(caminho):  # nada é lido nem vai para a IA: quem chama pede a senha
            raise DocumentoProtegido(formato)
        os.makedirs(f"{pasta}/ia", exist_ok=True)
        nome_diz = os.path.splitext(caminho)[1].lower()
        if nome_diz not in (".pdf", *IMAGEM, *TEXTO, *HTML, *SVG) and zipfile.is_zipfile(caminho):
            conferir_zip(caminho)  # o pacote, antes de descompactar; `\\` nos nomes internos vira `/` numa cópia
            caminho = com_barras_normais(caminho, pasta)
        ext = _extensao_lida(caminho)
        if ext == ".pdf":  # nos processos de paralelo.py, ou em série com a trava da PDFium em vezes curtas
            plano = planejar_pdf(caminho, pasta)
        elif ext in IMAGEM:
            plano = _plano_imagem(caminho, pasta)
        elif ext in TEXTO:
            plano = {"tipo": "texto", "md": _texto(caminho), "tarefas": []}
        elif ext in HTML:
            plano = planejar_html(caminho, pasta)
        elif ext in SVG:
            plano = planejar_svg(caminho, pasta)
        else:  # Office, ODF, EPUB: o pacote já foi conferido no começo
            plano = planejar_office(caminho, pasta, ext)
        plano["arquivo"] = os.path.basename(caminho)
        plano["meta_arquivo"] = metadados_arquivo(caminho, ext)
        if plano.get("propriedades"):  # HTML, SVG e PDF já tiram as propriedades na leitura do corpo
            plano["meta_arquivo"]["propriedades"] = plano.pop("propriedades")
        if plano.get("ilegiveis"):
            plano["meta_arquivo"].setdefault("partes_ilegiveis", []).extend(plano["ilegiveis"])
        if perfis := plano.pop("perfil_tabular", None):
            estrutura = plano["meta_arquivo"].setdefault("estrutura", {})
            visiveis = [a["nome"] for a in estrutura.get("abas", []) if not a["oculta"]]
            for p in perfis:  # aba única sai sem título no texto: o nome vem da estrutura do arquivo
                if p["aba"] is None and len(visiveis) == 1:
                    p["aba"] = visiveis[0]
            estrutura["perfil_tabular"] = perfis
        # a mesma imagem (logo em todo slide) é lida uma vez; faixas de uma imagem avulsa são partes dela, não cópias
        plano["iguais"] = _deduplicar(plano, pasta) if plano["tipo"] != "imagem" else {}
        plano["tarefas_originais"] = list(plano.get("tarefas", []))
        if self.agrupar and plano["tipo"] in ("pdf", "office"):
            plano["tarefas"], plano["lotes"] = agrupar(plano["tarefas"], pasta)
        with open(f"{pasta}/plano.json", "w", encoding="utf-8") as f:
            json.dump(plano, f, ensure_ascii=False)
        return plano

    # ------------------------------------------------------------------ leitura
    def ler(self, caminho: str | Path, prazo: float | None = None, senha: str | None = None) -> Resultado:
        """`prazo`: segundos, a partir de agora, para o documento (o chat lê com prazo; a base, sem). A extração
        nativa conta: no PDF, a página que começa depois do prazo entra só com o texto, e a faixa de páginas que não
        cabe nele não é lida (pendências registradas; a primeira faixa sempre entra).
        Nenhuma leitura pela IA começa depois do prazo, e a que está em curso no `OpenAICompativel` é cortada (com a
        folga da rede), sem novas tentativas: o que não coube vira a pendência "prazo esgotado" e o texto nativo sai
        completo. Provedor próprio lê o que sobra em `lince.prazo_restante()`.

        `senha`: abre o arquivo protegido por senha (PDF, Office, ODF), decifrado numa cópia temporária só para esta
        leitura; sem ela, o arquivo protegido levanta `DocumentoProtegido`, e com a senha errada, `SenhaIncorreta`. A
        senha não é guardada, e as leituras do documento protegido não entram no cache. No arquivo sem senha, é
        ignorada."""
        if prazo is not None and not (math.isfinite(prazo) and prazo > 0):
            raise ValueError("prazo precisa ser um número de segundos maior que zero")
        token = LIMITE.set(time.monotonic() + prazo if prazo is not None else None)
        try:
            return self._ler(caminho, senha)
        finally:
            LIMITE.reset(token)

    def _ler(self, caminho, senha):
        with tempfile.TemporaryDirectory(prefix="lince-") as pasta:
            caminho, protegido = _com_senha(caminho, senha, pasta)
            plano = self.planejar(caminho, pasta)
            cobertura = Cobertura(
                paginas=_paginas(plano),
                rotas={p["i"] + 1: p["rota"] for p in plano.get("paginas", [])},
            )
            cobertura.pendentes += plano.get("pendentes", [])
            if plano["tipo"] == "texto":
                return _com_estrutura(Resultado(plano["md"], plano.get("meta_arquivo", {}), cobertura), plano)
            tarefas = plano["tarefas"]
            if self.max_leituras is not None and len(tarefas) > self.max_leituras:
                cobertura.pendentes += [f"{t['id']}: teto de leituras" for t in tarefas[self.max_leituras :]]
                tarefas = tarefas[: self.max_leituras]
            respostas, usos = self._ler_tarefas(plano, tarefas, pasta, cobertura, com_cache=not protegido)
            if self.esforco == "max" and self.ia is not None and plano["tipo"] == "pdf" and not _vencido():
                respostas = distribuir_lotes(plano, respostas)
                usos += self._segunda_passada(str(caminho), plano, respostas, cobertura)
            return _resultado(pasta, plano, respostas, cobertura, usos, conferir_vazias=self.ia is not None)

    # ------------------------------------------------------------------ em duas fases (a IA é de quem chama)
    def preparar(
        self,
        caminho: str | Path,
        prazo: float | None = None,
        senha: str | None = None,
        imagens_em: str | Path | None = None,
    ) -> Plano:
        """Fase 1: extrai o texto e devolve as leituras que a IA precisa fazer, sem chamar modelo nenhum. Quem chama
        lê cada `TarefaVisual` com o seu modelo (custo, roteamento, cache e cancelamento são dele) e passa as respostas
        para `concluir`. O teto `max_leituras` vale aqui: o que passar dele fica nas pendências, pelas páginas
        (`páginas 188–207: teto de 200 tarefas por documento`). `prazo` vale para a extração, como em `ler`: no PDF, a
        página que começa depois dele entra só com o texto, e a faixa que não cabe nele não é lida. `senha` abre o
        arquivo protegido, como em `ler`; o plano não a guarda, e `concluir` não a pede.

        `imagens_em`: uma pasta que já existe. A imagem de cada tarefa vai para `{id}.png` nela, em vez de ficar na
        memória (`TarefaVisual.imagem` sai vazia; a `chave` e os `tokens_estimados` são os mesmos): um documento com
        centenas de páginas escaneadas não segura todas as imagens de uma vez."""
        if prazo is not None and not (math.isfinite(prazo) and prazo > 0):
            raise ValueError("prazo precisa ser um número de segundos maior que zero")
        token = LIMITE.set(time.monotonic() + prazo if prazo is not None else None)
        try:
            return self._preparar(caminho, senha, imagens_em)
        finally:
            LIMITE.reset(token)

    def _preparar(self, caminho, senha, imagens_em=None):
        from PIL import Image

        from .tipos import TarefaVisual

        with tempfile.TemporaryDirectory(prefix="lince-") as pasta:
            caminho, _ = _com_senha(caminho, senha, pasta)
            plano = self.planejar(caminho, pasta)
            tarefas, pendentes = plano.get("tarefas", []), []
            if self.max_leituras is not None and len(tarefas) > self.max_leituras:
                motivo = f"teto de {_tarefas(self.max_leituras)} por documento"
                pendentes = pendencias_do_teto(tarefas[self.max_leituras :], plano, motivo)
                tarefas = plano["tarefas"] = tarefas[: self.max_leituras]
            faixas = len(tarefas) if plano["tipo"] == "imagem" else None
            visuais = []
            for t in tarefas:
                arquivo = f"{pasta}/ia/{t['id']}.png"
                img = Path(arquivo).read_bytes()
                instr = instrucao(t, self.prompt, faixas)
                with Image.open(arquivo) as im:
                    w, h = im.size
                if imagens_em is not None:  # uma imagem por vez na memória
                    shutil.move(arquivo, os.path.join(imagens_em, f"{t['id']}.png"))
                pagina = t.get("pagina")
                visuais.append(
                    TarefaVisual(
                        id=t["id"],
                        tipo=t["modo"],
                        imagem=img if imagens_em is None else b"",
                        instrucao=instr,
                        chave=chave(img, instr, "", VERSAO_PROMPT),
                        tokens_estimados=round(w * h / 750 + len(instr) / 4),
                        pagina=pagina + 1 if isinstance(pagina, int) else None,
                        caixa=t.get("caixa"),
                        onde="" if t["modo"] in ("lote", "pagina") or pagina is not None else t.get("ancora", ""),
                        itens=[
                            {"n": it["n"], "tarefa": it["tarefa"], "tipo": it["modo"], "onde": it.get("ancora", "")}
                            for it in t.get("itens", [])
                        ],
                    )
                )
        return Plano(visuais, plano, pendentes)

    def concluir(self, plano: Plano, respostas: dict[str, str]) -> Resultado:
        """Fase 2: monta o resultado com as respostas de quem chamou ({id da tarefa: texto}). Tarefa sem resposta
        fica pendente ("sem resposta"), com o nativo no lugar; resposta a id que não está no plano é ignorada."""
        estado = json.loads(json.dumps(plano.estado))  # cópia: o mesmo plano pode ser concluído de novo
        ids = {t.id for t in plano.tarefas}
        respostas = {k: v for k, v in (respostas or {}).items() if k in ids and isinstance(v, str)}
        cobertura = Cobertura(
            paginas=_paginas(estado),
            rotas={p["i"] + 1: p["rota"] for p in estado.get("paginas", [])},
        )
        cobertura.pendentes += estado.get("pendentes", []) + list(plano.pendentes)
        cobertura.pendentes += [f"{t.id}: sem resposta" for t in plano.tarefas if t.id not in respostas]
        if estado["tipo"] == "texto":
            return _com_estrutura(Resultado(estado["md"], estado.get("meta_arquivo", {}), cobertura), estado)
        with tempfile.TemporaryDirectory(prefix="lince-") as pasta:
            with open(f"{pasta}/plano.json", "w", encoding="utf-8") as f:
                json.dump(estado, f, ensure_ascii=False)
            r = _resultado(pasta, estado, respostas, cobertura, [], conferir_vazias=True)
        # as leituras que entraram no texto: o teto de saída tira as outras, e a resposta vazia não entra
        fora = (": resultado grande demais", ": resposta vazia da IA")
        cortadas = {p.split(":", 1)[0] for p in cobertura.pendentes if p.endswith(fora)}
        cobertura.leituras_ia = len(respostas.keys() - cortadas)
        return r

    def _ler_tarefas(self, plano, tarefas, pasta, cobertura, com_cache=True):
        respostas, usos = {}, []
        if not tarefas:
            return respostas, usos
        if self.ia is None:
            cobertura.pendentes += [f"{t['id']}: sem modelo de visão configurado" for t in tarefas]
            return respostas, usos
        faixas = len(plano["tarefas"]) if plano["tipo"] == "imagem" else None

        def um(t):
            if _vencido():
                raise PrazoEsgotado
            img = Path(f"{pasta}/ia/{t['id']}.png").read_bytes()
            instr = instrucao(t, self.prompt, faixas)
            # a chave é o que muda a resposta: imagem, instrução, modelo e versão do prompt (não a versão do pacote,
            # senão cada release leria e pagaria tudo de novo)
            k = chave(img, instr, getattr(self.ia, "identidade", self.ia.modelo), VERSAO_PROMPT)
            if com_cache and (txt := self._cache_obter(k)) is not None:
                return t["id"], txt, Uso(self.ia.modelo, cache=True, tarefa=t["id"])
            r = self.ia.ler(img, instr)
            if com_cache and resposta_aproveitavel(plano, t["id"], r.texto):
                self._cache_guardar(k, r.texto)
            r.uso.tarefa = t["id"]
            return t["id"], r.texto, r.uso

        with ThreadPoolExecutor(self.concorrencia) as ex:
            futuros = {ex.submit(contextvars.copy_context().run, um, t): t for t in tarefas}  # o prazo vai junto
            for f, t in futuros.items():
                try:
                    tid, txt, uso = f.result()
                except PrazoEsgotado:
                    cobertura.pendentes.append(f"{t['id']}: prazo esgotado")
                    continue
                except Exception as e:  # noqa: BLE001 — leitura falhou: o nativo segue, a pendência fica registrada
                    cobertura.pendentes.append(f"{t['id']}: {type(e).__name__}: {str(e)[:120]}")
                    continue
                respostas[tid] = txt
                usos.append(uso)
                if uso.cache:
                    cobertura.leituras_cache += 1
                else:
                    cobertura.leituras_ia += 1
                if self.ao_usar:
                    self.ao_usar(uso)
        return respostas, usos

    def _cache_obter(self, k):
        """Cache fora do ar (Redis caído, rede) não derruba a leitura: segue lendo pela IA, como sem cache."""
        if not self.cache:
            return None
        try:
            return self.cache.obter(k)
        except Exception as e:  # noqa: BLE001
            _avisar_cache(e)
            return None

    def _cache_guardar(self, k, texto):
        if not self.cache:
            return
        try:
            self.cache.guardar(k, texto)
        except Exception as e:  # noqa: BLE001
            _avisar_cache(e)

    # ------------------------------------------------------------------ esforço máximo
    def _segunda_passada(self, caminho, plano, R, cobertura):
        """Releitura ampliada (2–3x) do que saiu ilegível, estimado ou vazio, com a 1ª leitura como contexto."""
        import io

        import pypdfium2 as pdfium

        alvos = [t for t in plano.get("tarefas_originais", []) if "caixa" in t and _precisa_rever(R.get(t["id"], ""))]
        if not alvos:
            return []
        usos, preparados = [], []
        with PDFIUM:  # aberto uma vez só: um PDF danificado não reconstrói a tabela de referências a cada recorte
            doc = pdfium.PdfDocument(caminho)
        try:
            for t in alvos:  # renderizar em 4–8x custa: para assim que o prazo vence
                if _vencido():
                    break
                with PDFIUM:  # uma renderização por vez: outro PDF entra entre elas (a trava é justa)
                    img = _ampliada(doc, t)
                preparados.append((t, _recortes_ampliados(img, io)))  # o recorte e o PNG, fora da trava
        finally:
            with PDFIUM:  # fechado dentro da trava, mesmo com exceção: o finalizador da PDFium não roda fora dela
                doc.close()

        def um(item):
            if _vencido():
                raise PrazoEsgotado
            t, partes = item
            instr = instrucao(t, self.prompt) + (
                f"\n\nSEGUNDA LEITURA, com resolução maior, em {len(partes)} parte(s) na ordem de leitura."
                " Primeira leitura do mesmo trecho:\n<<<\n" + R.get(t["id"], "")[:6000] + "\n>>>\n"
                "Devolva a leitura COMPLETA e corrigida: resolva os trechos [ilegível] e os valores ≈ que agora"
                " estiverem legíveis; o que continuar ilegível fica [ilegível]. Não invente."
            )
            resp = self.ia.ler(partes[0], instr, extras=partes[1:])
            resp.uso.tarefa = t["id"] + ":zoom"
            return t["id"], resp

        relidos = 0
        with ThreadPoolExecutor(self.concorrencia) as ex:
            for f in [ex.submit(contextvars.copy_context().run, um, item) for item in preparados]:
                try:
                    tid, resp = f.result()
                except PrazoEsgotado:
                    continue  # a 1ª leitura fica valendo
                except Exception as e:  # noqa: BLE001 — a 1ª leitura fica valendo
                    cobertura.pendentes.append(f"zoom: {type(e).__name__}: {str(e)[:100]}")
                    continue
                if resp.texto.strip():
                    R[tid] = resp.texto
                usos.append(resp.uso)
                cobertura.leituras_ia += 1
                relidos += 1
                if self.ao_usar:
                    self.ao_usar(resp.uso)
        cobertura.rotas["segunda_passada"] = relidos
        return usos


def _ampliada(doc, t):
    """A caixa da tarefa renderizada em 2–3x a resolução da 1ª leitura (no máximo LADO_MAX_PX) e convertida pelo
    Pillow: uma cópia, que não depende mais da PDFium. Chamar com a trava; a página vale pela mesma caixa da 1ª
    leitura e é fechada aqui."""
    page = pagina_visivel(doc, t["pagina"])
    try:
        W, H = page.get_size()
        l, b, r, tp = t["caixa"]
        l, b, r, tp = max(0, l), max(0, b), min(W, r), min(H, tp)
        lado = max(r - l, tp - b) or 1
        esc = min(min(8.0, max(4.0, 3000 / lado)), LADO_MAX_PX / lado)
        img = page.render(scale=esc, crop=(l, b, W - r, H - tp)).to_pil().convert("RGB")
        return de_pe(img, page.rotacao_exibida)  # de pé, como a página é exibida
    finally:
        page.close()


def _recortes_ampliados(img, io):
    """O recorte ampliado (`_ampliada`) fatiado para caber no limite do provedor, em PNG. Sem a PDFium: roda fora da
    trava."""
    partes, n = [], max(1, -(-max(img.width, img.height) // 1500))
    for k in range(n):
        if img.height >= img.width:
            pedaco = img.crop(
                (0, max(0, k * img.height // n - 40), img.width, min(img.height, (k + 1) * img.height // n + 40))
            )
        else:
            pedaco = img.crop(
                (max(0, k * img.width // n - 40), 0, min(img.width, (k + 1) * img.width // n + 40), img.height)
            )
        buf = io.BytesIO()
        pedaco.save(buf, "PNG")
        partes.append(buf.getvalue())
    return partes


def _precisa_rever(txt):
    return (not txt.strip()) or "[ilegível]" in txt or "[illegible]" in txt or txt.count("≈") >= 2


def _extensao_lida(caminho: str) -> str:
    """A extensão que decide a leitura. Fora da lista, o conteúdo decide (marcador do formato: PDF, RTF, OLE, pacote
    Office ou ODF); sem marcador conhecido, o formato não é suportado."""
    ext = os.path.splitext(caminho)[1].lower()
    if ext in FORMATOS:
        return ext
    if zipfile.is_zipfile(caminho):  # a detecção abre o pacote: conferido antes de qualquer descompactação
        conferir_zip(caminho)
    with open(caminho, "rb") as f:
        detectado = anydoc.format_from_bytes(f.read())
    if detectado is None:
        raise FormatoNaoSuportado(f"formato {ext or '(sem extensão)'} não suportado")
    return f".{detectado}"


def _avisar_cache(e):
    logging.getLogger("lince").warning("cache indisponível, lendo sem ele: %s: %s", type(e).__name__, str(e)[:200])


def _resultado(pasta, plano, respostas, cobertura, usos, conferir_vazias):
    """O fim da leitura, igual com a IA do Lince ou com a de quem chama: pendência de resposta vazia, montagem do
    texto e dos metadados, estrutura."""

    def montado(respostas):  # o texto e os metadados com estas respostas
        if plano["tipo"] == "imagem":
            md = "\n\n".join(respostas.get(t["id"], "") for t in plano["tarefas"])
            return md, dict(plano.get("meta_arquivo") or {})
        md = montar(pasta, respostas)
        return md, json.loads(Path(f"{pasta}/metadados.json").read_text(encoding="utf-8"))

    posicao = {t["id"]: k for k, t in enumerate(plano.get("tarefas", []))}
    ordem = sorted(respostas, key=lambda tid: posicao.get(tid, len(posicao)))  # a ordem do plano
    medidas = {}  # o texto de cada resposta é medido uma vez na busca do que cabe
    if respostas and tamanho_da_saida(plano, respostas, medidas) > SAIDA_MAX:
        # acima do teto de saída, entram as leituras que cabem, na ordem do plano; as outras ficam pendentes, com o
        # motivo, e o texto nativo sai inteiro no lugar delas. A busca é binária: o tamanho só cresce com cada leitura
        cabem, sobra = 0, len(ordem)
        while cabem + 1 < sobra:
            meio = (cabem + sobra) // 2
            if tamanho_da_saida(plano, {tid: respostas[tid] for tid in ordem[:meio]}, medidas) <= SAIDA_MAX:
                cabem = meio
            else:
                sobra = meio
        cobertura.pendentes += [f"{tid}: resultado grande demais" for tid in ordem[cabem:]]
        ordem = ordem[:cabem]
        respostas = {tid: respostas[tid] for tid in ordem}
    md, meta = montado(respostas)
    if respostas and len(md) > SAIDA_MAX:
        # a conta errou e as leituras passaram do teto assim mesmo: o teto vale, e sai só o texto nativo
        cobertura.pendentes += [f"{tid}: resultado grande demais" for tid in ordem]
        respostas = {}
        md, meta = montado(respostas)
    if conferir_vazias:  # resposta vazia não pode virar lacuna silenciosa no documento
        lidos = distribuir_lotes(plano, respostas)
        ja = {p.split(":", 1)[0] for p in cobertura.pendentes}
        # item de um lote que já tem pendência (erro, prazo, sem resposta) não é "resposta vazia"
        ja |= {tid for lid, itens in (plano.get("lotes") or {}).items() if lid in ja for _, tid in itens}
        cobertura.pendentes += [
            f"{t['id']}: resposta vazia da IA"
            for t in plano.get("tarefas_originais", [])
            if t["id"] not in ja and not str(lidos.get(t["id"], "")).strip()
        ]
    r = _com_estrutura(Resultado(md, meta, cobertura, usos), plano)
    if imagens := _imagens(plano, distribuir_lotes(plano, respostas)):
        r.estrutura["imagens"] = imagens
    return r


def _deduplicar(plano, pasta) -> dict[str, str]:
    """Tira das tarefas a repetição exata (mesma imagem, mesmo tipo de leitura) e devolve {repetida: a que lê}; a
    montagem põe a mesma leitura em cada lugar. Guarda em `posicoes` onde cada uma estava, para a estrutura."""
    import hashlib

    vistas, iguais, unicas = {}, {}, []
    plano["posicoes"] = [
        {k: t[k] for k in ("id", "modo", "pagina", "caixa") if t.get(k) is not None}
        | ({"onde": t.get("ancora", "")} if plano["tipo"] != "pdf" else {})
        for t in plano.get("tarefas", [])
    ]
    for t in plano.get("tarefas", []):
        h = hashlib.sha256(t["modo"].encode() + Path(f"{pasta}/ia/{t['id']}.png").read_bytes()).digest()
        if h in vistas:
            iguais[t["id"]] = vistas[h]
        else:
            vistas[h] = t["id"]
            unicas.append(t)
    plano["tarefas"] = unicas
    return iguais


def _imagens(plano, lidos) -> list[dict]:
    """Onde está cada leitura visual (página e caixa no PDF; slide e lugar no Office e no HTML), qual tarefa a leu e
    se ela foi lida. Página escaneada inteira fica de fora: ela já está em `paginas` e nas rotas da cobertura."""
    iguais = plano.get("iguais", {})
    posicoes = {p["id"]: p for p in plano.get("posicoes", [])}
    out = []

    def item(tid, extra=None):
        p = posicoes.get(tid, {"id": tid})
        if p.get("modo") == "pagina":
            return
        leitor = iguais.get(tid, tid)
        out.append(
            {"tarefa": tid, "tipo": p.get("modo", "imagem"), "lida_por": leitor,
             "lida": bool(str(lidos.get(leitor, "")).strip())}
            | ({"pagina": p["pagina"] + 1} if isinstance(p.get("pagina"), int) else {})
            | ({"caixa": p["caixa"]} if p.get("caixa") else {})
            | ({"onde": p["onde"]} if p.get("onde") else {})
            | (extra or {})
        )  # fmt: skip

    if plano["tipo"] == "pdf":
        for p in plano.get("posicoes", []):
            item(p["id"])
        return out
    ids = {t["id"] for t in plano.get("tarefas_originais", [])} | set(iguais)  # a marca que o texto imita não conta
    slide = None
    for k, pedaco in enumerate([plano.get("md", "")] + list(plano.get("extras", []))):
        for m in re.finditer(r"(?m)^<!-- slide (\d+) -->$|\{\{IA:(\w+)\}\}", pedaco):
            if m.group(1):
                slide = int(m.group(1))
            elif m.group(2) in ids:
                item(m.group(2), {"slide": slide} if slide is not None and k == 0 else None)
    return out


def _com_estrutura(r: Resultado, plano) -> Resultado:
    """Tira a estrutura dos metadados (onde a extração a deixa) e completa com páginas e seções do Markdown."""
    r.estrutura = dict(r.metadados.pop("estrutura", None) or {})
    if plano["tipo"] == "pdf":
        r.estrutura["paginas"] = r.cobertura.paginas
    r.estrutura.update(secoes(r.markdown, pdf=plano["tipo"] == "pdf", apresentacao=bool(plano.get("slides"))))
    return r


def _paginas(plano) -> int:
    """Páginas do documento, inclusive as que o prazo ou o teto deixaram sem ler (estão nas pendências)."""
    total = plano.get("paginas_total")
    return total if isinstance(total, int) and total > 0 else len(plano.get("paginas", [])) or 1


def _vencido() -> bool:
    return (restante := prazo_restante()) is not None and restante <= 0


def ler(caminho: str | Path, prazo: float | None = None, senha: str | None = None, **opcoes) -> Resultado:
    """Atalho: `lince.ler("arquivo.pdf", ia=OpenAICompativel(...))`."""
    return Leitor(**opcoes).ler(caminho, prazo=prazo, senha=senha)


def _tarefas(n) -> str:
    return f"{n} tarefa" if n == 1 else f"{n} tarefas"


def pendencias_do_teto(cortadas, plano, motivo) -> list[str]:
    """As pendências das tarefas que passaram de um teto, pelas páginas que elas leriam: `páginas 188–207: {motivo}`
    (faixas emendadas; o lote, pelas páginas dos recortes dele). A tarefa sem página (Office, HTML, imagem avulsa)
    fica pelo id. `cortadas`: as tarefas do plano (`id`, `modo`, `pagina` a partir de 0, `itens` com `tarefa`)."""
    originais = {t["id"]: t for t in plano.get("tarefas_originais", [])}
    paginas, sem_pagina = set(), []
    for t in cortadas:
        if t.get("modo") == "lote":
            delas = [originais.get(it["tarefa"], {}).get("pagina") for it in t.get("itens", [])]
        else:
            delas = [t.get("pagina")]
        delas = [p for p in delas if isinstance(p, int)]
        if delas:
            paginas.update(delas)
        else:
            sem_pagina.append(t["id"])
    faixas, ordem = [], sorted(paginas)
    for p in ordem:
        if faixas and faixas[-1][1] == p - 1:
            faixas[-1][1] = p
        else:
            faixas.append([p, p])
    texto = [f"página {a + 1}" if a == b else f"páginas {a + 1}–{b + 1}" for a, b in faixas]
    return [f"{f}: {motivo}" for f in texto] + [f"{i}: {motivo}" for i in sem_pagina]


def _com_senha(caminho, senha, pasta) -> tuple[str, bool]:
    """O arquivo a ler e se ele estava protegido. Com senha, o protegido é decifrado numa subpasta da leitura (apagada
    com ela, com o mesmo nome do arquivo); sem senha, a detecção do `planejar` é que recusa."""
    if senha is None:
        return str(caminho), False
    lido = aberto(caminho, senha, os.path.join(pasta, "aberto"))
    return lido, lido != str(caminho)


def _texto(caminho):
    raw = Path(caminho).read_bytes()
    for cod in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(cod)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _plano_imagem(caminho, pasta):
    """Imagem avulsa: faixas que cabem no limite do provedor (imagem alta não é reduzida até ficar ilegível)."""
    from PIL import Image

    im = Image.open(caminho).convert("RGB")
    alt = min(im.height, int(im.width * 0.75)) or im.height
    tarefas, y, k = [], 0, 1
    while y < im.height:
        tid = f"faixa{k}"
        im.crop((0, max(0, y - 40), im.width, min(im.height, y + alt))).save(f"{pasta}/ia/{tid}.png")
        tarefas.append({"id": tid, "modo": "pagina", "ancora": ""})
        y, k = y + alt, k + 1
    return {"tipo": "imagem", "tarefas": tarefas}
