"""Orquestrador: arquivo → extração nativa e roteamento → leituras pela IA (com cache) → markdown + metadados."""

import contextvars
import json
import logging
import math
import os
import re
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
from .limites import LADO_MAX_PX, conferir_zip
from .metadados import metadados_arquivo
from .montagem import montar
from .office import planejar_office
from .pagina_html import planejar_html, planejar_svg
from .pdf import planejar_pdf
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
        with PDFIUM:
            return self._planejar(str(caminho), str(pasta))

    def _planejar(self, caminho, pasta):
        os.makedirs(f"{pasta}/ia", exist_ok=True)
        ext = _extensao_lida(caminho)
        if ext == ".pdf":
            plano = planejar_pdf(caminho, pasta)
        elif ext in IMAGEM:
            plano = _plano_imagem(caminho, pasta)
        elif ext in TEXTO:
            plano = {"tipo": "texto", "md": _texto(caminho), "tarefas": []}
        elif ext in HTML:
            plano = planejar_html(caminho, pasta)
        elif ext in SVG:
            plano = planejar_svg(caminho, pasta)
        else:
            if zipfile.is_zipfile(caminho):  # Office, ODF, EPUB: conferido antes de qualquer descompactação
                conferir_zip(caminho)
            plano = planejar_office(caminho, pasta, ext)
        plano["arquivo"] = os.path.basename(caminho)
        plano["meta_arquivo"] = metadados_arquivo(caminho, ext)
        if plano.get("propriedades"):  # HTML e SVG já tiram as propriedades no mesmo parse do corpo
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
        plano["iguais"] = _deduplicar(plano, pasta)  # a mesma imagem (logo em todo slide) é lida uma vez
        plano["tarefas_originais"] = list(plano.get("tarefas", []))
        if self.agrupar and plano["tipo"] in ("pdf", "office"):
            plano["tarefas"], plano["lotes"] = agrupar(plano["tarefas"], pasta)
        with open(f"{pasta}/plano.json", "w", encoding="utf-8") as f:
            json.dump(plano, f, ensure_ascii=False)
        return plano

    # ------------------------------------------------------------------ leitura
    def ler(self, caminho: str | Path, prazo: float | None = None) -> Resultado:
        """`prazo`: segundos, a partir de agora, para o documento (o chat lê com prazo; a base, sem). A extração
        nativa conta, mas não é interrompida. Nenhuma leitura pela IA começa depois do prazo, e a que está em curso
        no `OpenAICompativel` é cortada (com a folga da rede), sem novas tentativas: o que não coube vira a pendência
        "prazo esgotado" e o texto nativo sai completo. Provedor próprio lê o que sobra em `lince.prazo_restante()`."""
        if prazo is not None and not (math.isfinite(prazo) and prazo > 0):
            raise ValueError("prazo precisa ser um número de segundos maior que zero")
        token = LIMITE.set(time.monotonic() + prazo if prazo is not None else None)
        try:
            return self._ler(caminho)
        finally:
            LIMITE.reset(token)

    def _ler(self, caminho):
        with tempfile.TemporaryDirectory(prefix="lince-") as pasta:
            plano = self.planejar(caminho, pasta)
            cobertura = Cobertura(
                paginas=len(plano.get("paginas", [])) or 1,
                rotas={p["i"] + 1: p["rota"] for p in plano.get("paginas", [])},
            )
            cobertura.pendentes += plano.get("pendentes", [])
            if plano["tipo"] == "texto":
                return _com_estrutura(Resultado(plano["md"], plano.get("meta_arquivo", {}), cobertura), plano)
            tarefas = plano["tarefas"]
            if self.max_leituras is not None and len(tarefas) > self.max_leituras:
                cobertura.pendentes += [f"{t['id']}: teto de leituras" for t in tarefas[self.max_leituras :]]
                tarefas = tarefas[: self.max_leituras]
            respostas, usos = self._ler_tarefas(plano, tarefas, pasta, cobertura)
            if self.esforco == "max" and self.ia is not None and plano["tipo"] == "pdf" and not _vencido():
                respostas = distribuir_lotes(plano, respostas)
                usos += self._segunda_passada(str(caminho), plano, respostas, cobertura)
            return _resultado(pasta, plano, respostas, cobertura, usos, conferir_vazias=self.ia is not None)

    # ------------------------------------------------------------------ em duas fases (a IA é de quem chama)
    def preparar(self, caminho: str | Path) -> Plano:
        """Fase 1: extrai o texto e devolve as leituras que a IA precisa fazer, sem chamar modelo nenhum. Quem chama
        lê cada `TarefaVisual` com o seu modelo (custo, roteamento, cache e cancelamento são dele) e passa as respostas
        para `concluir`. O teto `max_leituras` vale aqui: o que passar dele fica nas pendências."""
        from PIL import Image

        from .tipos import TarefaVisual

        with tempfile.TemporaryDirectory(prefix="lince-") as pasta:
            plano = self.planejar(caminho, pasta)
            tarefas, pendentes = plano.get("tarefas", []), []
            if self.max_leituras is not None and len(tarefas) > self.max_leituras:
                pendentes = [f"{t['id']}: teto de leituras" for t in tarefas[self.max_leituras :]]
                tarefas = plano["tarefas"] = tarefas[: self.max_leituras]
            faixas = len(tarefas) if plano["tipo"] == "imagem" else None
            visuais = []
            for t in tarefas:
                img = Path(f"{pasta}/ia/{t['id']}.png").read_bytes()
                instr = instrucao(t, self.prompt, faixas)
                with Image.open(f"{pasta}/ia/{t['id']}.png") as im:
                    w, h = im.size
                pagina = t.get("pagina")
                visuais.append(
                    TarefaVisual(
                        id=t["id"],
                        tipo=t["modo"],
                        imagem=img,
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
            paginas=len(estado.get("paginas", [])) or 1,
            rotas={p["i"] + 1: p["rota"] for p in estado.get("paginas", [])},
            leituras_ia=len(respostas),
        )
        cobertura.pendentes += estado.get("pendentes", []) + list(plano.pendentes)
        cobertura.pendentes += [f"{t.id}: sem resposta" for t in plano.tarefas if t.id not in respostas]
        if estado["tipo"] == "texto":
            return _com_estrutura(Resultado(estado["md"], estado.get("meta_arquivo", {}), cobertura), estado)
        with tempfile.TemporaryDirectory(prefix="lince-") as pasta:
            with open(f"{pasta}/plano.json", "w", encoding="utf-8") as f:
                json.dump(estado, f, ensure_ascii=False)
            return _resultado(pasta, estado, respostas, cobertura, [], conferir_vazias=True)

    def _ler_tarefas(self, plano, tarefas, pasta, cobertura):
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
            if (txt := self._cache_obter(k)) is not None:
                return t["id"], txt, Uso(self.ia.modelo, cache=True, tarefa=t["id"])
            r = self.ia.ler(img, instr)
            if resposta_aproveitavel(plano, t["id"], r.texto):
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
        usos = []
        with PDFIUM:
            doc = pdfium.PdfDocument(caminho)
            preparados = []
            for t in alvos:  # renderizar em 4–8x custa: para assim que o prazo vence
                if _vencido():
                    break
                preparados.append((t, _recortes_ampliados(doc, t, io)))
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


def _recortes_ampliados(doc, t, io):
    """Recorte da tarefa em 2–3x a resolução da 1ª leitura, fatiado para caber no limite do provedor."""
    page = doc[t["pagina"]]
    W, H = page.get_size()
    l, b, r, tp = t["caixa"]
    l, b, r, tp = max(0, l), max(0, b), min(W, r), min(H, tp)
    lado = max(r - l, tp - b) or 1
    esc = min(min(8.0, max(4.0, 3000 / lado)), LADO_MAX_PX / lado)
    img = page.render(scale=esc, crop=(l, b, W - r, H - tp)).to_pil().convert("RGB")
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
    if plano["tipo"] == "imagem":
        md = "\n\n".join(respostas.get(t["id"], "") for t in plano["tarefas"])
        meta = dict(plano.get("meta_arquivo") or {})
    else:
        md = montar(pasta, respostas)
        meta = json.loads(Path(f"{pasta}/metadados.json").read_text(encoding="utf-8"))
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
    slide = None
    for k, pedaco in enumerate([plano.get("md", "")] + list(plano.get("extras", []))):
        for m in re.finditer(r"<!-- slide (\d+) -->|\{\{IA:(\w+)\}\}", pedaco):
            if m.group(1):
                slide = int(m.group(1))
            else:
                item(m.group(2), {"slide": slide} if slide is not None and k == 0 else None)
    return out


def _com_estrutura(r: Resultado, plano) -> Resultado:
    """Tira a estrutura dos metadados (onde a extração a deixa) e completa com páginas e seções do Markdown."""
    r.estrutura = dict(r.metadados.pop("estrutura", None) or {})
    if plano["tipo"] == "pdf":
        r.estrutura["paginas"] = r.cobertura.paginas
    r.estrutura.update(secoes(r.markdown, pdf=plano["tipo"] == "pdf"))
    return r


def _vencido() -> bool:
    return (restante := prazo_restante()) is not None and restante <= 0


def ler(caminho: str | Path, prazo: float | None = None, **opcoes) -> Resultado:
    """Atalho: `lince.ler("arquivo.pdf", ia=OpenAICompativel(...))`."""
    return Leitor(**opcoes).ler(caminho, prazo=prazo)


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
