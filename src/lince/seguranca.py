"""Segurança: texto oculto (branco, minúsculo, invisível, vanish) e padrões de prompt injection."""

import hashlib  # noqa: F401
import io  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import zipfile  # noqa: F401
from xml.etree import ElementTree as ET  # noqa: F401

PADROES_INJECAO = [
    r"ignor\w*\s+(all|todas?|as|the)?\s*(previous|prior|anteriores|acima)?\s*(instru\w+|regras|rules)",
    r"(system|sistema)\s*prompt|prompt\s+do\s+sistema",
    r"(you are now|voc[eê] (agora )?[eé] (um|uma|o|a))\b",
    r"(revele|reveal|exfiltr\w+|envie para|send to)\b.{0,40}(prompt|senha|password|dados|token|credencia)",
    r"(d[eê]|give|atribua)\s+(a )?nota\s+(10|dez|m[aá]xima)",
    r"recomend\w+\s+(a\s+)?contrata\w+",
    r"desconsider\w+\s+(a|as|o|os|todas?|todos?)?\s*\w+",
    r"(envie|encaminhe|mande|forward|send)\b.{0,60}(e-?mail|@|para fora|externo)",
    r"(nota|instru[cç][aã]o|aviso|mensagem)\s+(para|ao|à|a)\s+(a\s+)?(ia|assistente|sistemas? de ia|modelo|llm)",
    r"system\s+override|modo\s+desenvolvedor|developer\s+mode|jailbreak",
    r"(classifique|aprove|marque)\s+(este|esta|o|a)\s+(candidat\w+|documento|pedido)\s+como",
    r"(pontua[cç][aã]o|score|nota)\s+m[aá]xima",
    r"(assistente|assistant|ia|ai|llm|chatgpt|claude)\s*[:,]\s*(ignore|desconsidere|revele|responda)",
]


def alertas_injecao(txt):
    out = []
    for pad in PADROES_INJECAO:
        for m in re.finditer(pad, txt, flags=re.I):
            ini = max(0, m.start() - 60)
            out.append(txt[ini : m.end() + 60].replace("\n", " ").strip())
    return sorted(set(out))[:20]


def texto_oculto_pdf(page, itens, imgs, W, H):
    """Texto que a IA leria e o humano não vê: invisível fora de imagem (OCR legítimo fica),
    fonte < 2,5 pt, fora da página, ou cor de texto ≈ cor do fundo."""
    from PIL import Image  # noqa: F401

    esc = 0.5
    fundo = page.render(scale=esc).to_pil().convert("RGB")
    ocultos = []
    for x in itens:
        motivo = None
        sobre_imagem = any(b[0] - 2 <= x.x <= b[2] + 2 and b[1] - 2 <= x.y <= b[3] + 2 for b in imgs)
        if x.render_mode == 3 and not sobre_imagem:
            motivo = "invisível"
        elif 0 < x.font_size < 2.5:
            motivo = "fonte minúscula"
        elif x.x > W or x.x + x.width < 0 or x.y < 0 or x.y > H:
            motivo = "fora da página"
        else:
            cor = x.fill_color or (0, 0, 0)
            if min(cor) >= 225:  # texto claro: confere o fundo logo acima da linha
                px = (
                    min(fundo.width - 1, max(0, int((x.x + 1) * esc))),
                    min(fundo.height - 1, max(0, int((H - x.y - x.height - 2) * esc))),
                )
                bg = fundo.getpixel(px)
                if sum(abs(a - b) for a, b in zip(cor, bg, strict=False)) < 60:
                    motivo = "cor igual ao fundo"
        if motivo:
            ocultos.append((motivo, x.text))
    return ocultos


def texto_oculto_docx(path):
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    out = []
    with zipfile.ZipFile(path) as z:
        if "word/document.xml" not in z.namelist():
            return out
        for r in ET.fromstring(z.read("word/document.xml")).iter(f"{W}r"):
            rpr = r.find(f"{W}rPr")
            if rpr is None:
                continue
            txt = "".join(t.text or "" for t in r.iter(f"{W}t"))
            if not txt.strip():
                continue
            cor = rpr.find(f"{W}color")
            sz = rpr.find(f"{W}sz")
            if rpr.find(f"{W}vanish") is not None:
                out.append(("oculto (vanish)", txt))
            elif cor is not None and (cor.get(f"{W}val") or "").upper() in ("FFFFFF", "FEFEFE"):
                out.append(("cor branca", txt))
            elif sz is not None and (sz.get(f"{W}val") or "99").isdigit() and int(sz.get(f"{W}val")) <= 4:
                out.append(("fonte minúscula", txt))
    return out


def remover_ocultos(md, ocultos):
    """Tira do corpo o texto oculto (juntando fragmentos seguidos) e devolve o que saiu."""
    removidos = []
    corrida = " ".join(t for _, t in ocultos).strip()
    for alvo in [corrida] + [t.strip() for _, t in ocultos]:
        if len(alvo) >= 6 and alvo in md:
            md = md.replace(alvo, "")
            removidos.append(alvo)
    return md, removidos
