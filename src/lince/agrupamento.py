"""Agrupamento set-of-marks: recortes pequenos numa folha numerada, resposta em JSON por número."""

import hashlib  # noqa: F401
import io  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import zipfile  # noqa: F401
from xml.etree import ElementTree as ET  # noqa: F401

LADO = 1568


def agrupar(tarefas, pasta, max_itens=12):
    """Recortes pequenos viram folhas numeradas: uma chamada lê vários itens e responde JSON por número.

    Recorte grande (diagrama, tabela, página) continua sozinho para não perder resolução.
    """
    from PIL import Image, ImageDraw, ImageFont

    try:
        fonte = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
    except OSError:
        fonte = ImageFont.load_default(size=22)  # sem DejaVu (Windows, macOS): a fonte embutida do Pillow
    pequenas, resto = [], []
    for t in tarefas:
        if t["modo"] in ("pagina", "tabela"):
            resto.append(t)
            continue
        w, h = Image.open(f"{pasta}/ia/{t['id']}.png").size
        (pequenas if w <= 900 and h <= 260 else resto).append(t)
    if len(pequenas) < 3:
        return tarefas, {}
    lotes, folha, itens, x, y, alt_linha = {}, [], [], 0, 0, 0

    def fecha():
        if not itens:
            return
        lid = f"lote{len(lotes) + 1:02d}"
        larg = max(px + im.width for px, py, im in folha)
        alt = max(py + 32 + im.height + 4 for px, py, im in folha)  # faixa do número + recorte
        img = Image.new("RGB", (larg, alt), "white")
        d = ImageDraw.Draw(img)
        for (px, py, im), (n, _) in zip(folha, itens, strict=True):
            d.rectangle([px, py, px + 44, py + 30], fill=(200, 0, 0))
            d.text((px + 6, py + 2), str(n), fill="white", font=fonte)
            img.paste(im, (px, py + 32))
            d.rectangle([px, py + 32, px + im.width - 1, py + 32 + im.height - 1], outline=(200, 0, 0), width=2)
        img.save(f"{pasta}/ia/{lid}.png")
        lotes[lid] = list(itens)
        folha.clear()
        itens.clear()

    for t in pequenas:
        im = Image.open(f"{pasta}/ia/{t['id']}.png").convert("RGB")
        bloco_h = im.height + 32 + 16
        if x + im.width > LADO:
            x, y, alt_linha = 0, y + alt_linha, 0
        if y + bloco_h > LADO or len(itens) >= max_itens:
            fecha()
            x, y, alt_linha = 0, 0, 0
        folha.append((x, y, im))
        itens.append((len(itens) + 1, t))
        x += im.width + 24
        alt_linha = max(alt_linha, bloco_h)
    fecha()
    novas = list(resto)
    for lid, its in lotes.items():
        novas.append(
            {
                "id": lid,
                "modo": "lote",
                "itens": [
                    {"n": n, "tarefa": t["id"], "modo": t["modo"], "ancora": t.get("ancora", "")[:600]} for n, t in its
                ],
            }
        )
    mapa = {lid: [[n, t["id"]] for n, t in its] for lid, its in lotes.items()}
    for its in lotes.values():  # a tarefa original continua conhecida (2ª passada, auditoria)
        for _, t in its:
            t["no_lote"] = True
    return novas, mapa


def _json_do_lote(bruto: str) -> dict:
    i, j = bruto.find("{"), bruto.rfind("}")  # do primeiro { ao último }: linear, como o regex guloso fazia
    try:
        dados = json.loads(bruto[i : j + 1]) if 0 <= i < j else {}
    except json.JSONDecodeError:
        return {}
    return dados if isinstance(dados, dict) else {}


def resposta_aproveitavel(plano, tid, texto: str) -> bool:
    """A resposta pode ir para o cache? Vazia, ou lote cujo JSON não deu nenhum item, não: senão a falha passageira
    do provedor ficaria valendo por dias, e cada nova tentativa receberia a mesma pendência."""
    itens = (plano.get("lotes") or {}).get(tid)
    if itens is None:
        return bool(texto.strip())
    dados = _json_do_lote(texto)
    return any(_valor_do_item(dados, n).strip() for n, _ in itens)


def _valor_do_item(dados: dict, n) -> str:
    """Texto do item n do lote, pelo mesmo critério na distribuição e no cache: null, false e 0 são vazio."""
    v = dados.get(str(n), dados.get(n, ""))
    if isinstance(v, (dict, list)):
        v = json.dumps(v, ensure_ascii=False)
    return str(v or "")


def distribuir_lotes(plano, respostas):
    """Resposta JSON do lote → resposta de cada tarefa original."""
    R = dict(respostas)
    for lid, itens in (plano.get("lotes") or {}).items():
        if lid not in R:  # já distribuído (esforço máximo) ou leitura do lote falhou
            continue
        dados = _json_do_lote(R.pop(lid))
        for n, tid in itens:
            R[tid] = _valor_do_item(dados, n)
    return R
