"""Código de um PPTX: JSON canônico com ids estáveis, e edição cirúrgica por operações nomeadas.

Ids (vêm do próprio arquivo, sobrevivem a reordenar e editar):
    slide      sld:<p:sldId/@id>
    forma      sld:<id>/sp:<p:cNvPr/@id>
    parágrafo  sld:<id>/sp:<id>/p:<n>
    célula     sld:<id>/sp:<id>/c:<linha>,<coluna>
    série      sld:<id>/sp:<id>/ser:<c:idx>
"""

import copy
import math
import re

from lxml import etree

from .pacote import (
    CRESCIMENTO_MAX,
    PARAGRAFO_MAX,
    QUEBRA,
    R_EMBED,
    R_ID,
    OperacaoInvalida,
    Pacote,
    sha,
)
from .pacote import campo_texto as _campo_texto
from .pacote import conferir as _conferir
from .pacote import integridade as _integridade

NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
A, P, C = (f"{{{NS[k]}}}" for k in ("a", "p", "c"))
FORMAS = ("sp", "pic", "graphicFrame", "grpSp", "cxnSp")


def _texto(el) -> str:
    partes = []
    for x in el.iter(A + "t", A + "br"):
        partes.append(QUEBRA if x.tag == A + "br" else (x.text or ""))
    return "".join(partes)


# ------------------------------------------------------------------ leitura
def _formas(arvore_slide):
    """Formas de primeiro nível e de dentro de grupos, na ordem de desenho."""
    for el in arvore_slide.find("p:cSld/p:spTree", NS).iter():
        if etree.QName(el).localname in FORMAS and el.find(".//p:cNvPr", NS) is not None:
            yield el


def _grafico(pk: Pacote, parte_chart: str) -> dict:
    ch = pk.arvore(parte_chart)
    plot = ch.find(".//c:plotArea", NS)
    tipos = [etree.QName(e).localname for e in plot if etree.QName(e).localname.endswith("Chart")]
    series = []
    for ser in plot.iter(C + "ser"):
        tx = ser.find("c:tx", NS)
        series.append(
            {
                "idx": int(ser.find("c:idx", NS).get("val")),
                "nome": _nome_serie(tx),
                "categorias": [v.text for v in ser.findall("c:cat//c:pt/c:v", NS)],
                "valores": [_numero(v.text) for v in ser.findall("c:val//c:pt/c:v", NS)],
            }
        )
    planilha = next((t for (k, t) in pk.rels(parte_chart).values() if k == "package"), None)
    titulo = " ".join(t.text or "" for t in ch.findall(".//c:title//a:t", NS)).strip() or None
    return {
        "parte": "/" + parte_chart,
        "tipos": tipos,
        "titulo": titulo,
        "planilha": planilha and "/" + planilha,
        "series": series,
    }


def _nome_serie(tx):
    """Nome da série: o valor em cache (c:v) ou o texto rico; nunca a fórmula da célula."""
    if tx is None:
        return None
    v = tx.findtext(".//c:v", namespaces=NS)
    return v if v is not None else (_texto(tx) or None)


def _numero(texto):
    try:
        return float(texto)
    except (TypeError, ValueError):
        return texto


def _forma(pk, parte, arvore, sp, srels, base) -> dict:
    nv = sp.find(".//p:cNvPr", NS)
    fid = f"{base}/sp:{nv.get('id')}"
    f = {"id": fid, "nome": nv.get("name"), "tipo": etree.QName(sp).localname}
    if nv.get("descr"):
        f["descricao"] = nv.get("descr")
    ph = sp.find("./*/p:nvPr/p:ph", NS)
    if ph is not None:
        f["placeholder"] = {"tipo": ph.get("type", "body"), "idx": int(ph.get("idx", 0))}
    off, ext = sp.find("./p:spPr/a:xfrm/a:off", NS), sp.find("./p:spPr/a:xfrm/a:ext", NS)
    if off is None:
        off, ext = sp.find("./p:xfrm/a:off", NS), sp.find("./p:xfrm/a:ext", NS)
    if off is not None and ext is not None:
        f["caixa_emu"] = [int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy"))]
    if f["tipo"] == "grpSp":  # o grupo em si não tem conteúdo: as formas de dentro aparecem na lista com id próprio
        f["tipo"] = "grupo"
        f["xpath"] = arvore.getroottree().getpath(sp)
        return f
    tx = sp.find("p:txBody", NS)
    if tx is not None:
        f["tipo"] = "texto"
        f["paragrafos"] = [
            {
                "id": f"{fid}/p:{n}",
                "nivel": int(
                    (p.find("a:pPr", NS) if p.find("a:pPr", NS) is not None else etree.Element("x")).get("lvl", 0)
                ),
                "texto": _texto(p),
            }
            for n, p in enumerate(tx.findall("a:p", NS))
        ]
    gd = sp.find(".//a:graphicData", NS)
    if gd is not None and gd.find("c:chart", NS) is not None:
        rid = gd.find("c:chart", NS).get(R_ID)
        if rid in srels and not srels[rid][0].startswith("externo"):
            f["tipo"] = "grafico"
            f["grafico"] = _grafico(pk, srels[rid][1])
            for s in f["grafico"]["series"]:
                s["id"] = f"{fid}/ser:{s['idx']}"
    elif gd is not None and gd.find("a:tbl", NS) is not None:
        f["tipo"] = "tabela"
        f["celulas"] = [
            [{"id": f"{fid}/c:{i},{j}", "texto": _texto(tc)} for j, tc in enumerate(tr.findall("a:tc", NS))]
            for i, tr in enumerate(gd.iter(A + "tr"))
        ]
    elif gd is not None:
        f["tipo"] = "objeto"  # SmartArt, OLE, mídia: preservado, não editável por operação
        f["uri"] = gd.get("uri")
    blip = sp.find(".//a:blip", NS)
    if blip is not None and f["tipo"] == "pic":
        f["tipo"] = "imagem"
        rid = blip.get(R_EMBED)
        if rid in srels and srels[rid][1] in pk.partes:
            f["midia"] = {"parte": "/" + srels[rid][1], "sha256": sha(pk.partes[srels[rid][1]])}
    f["xpath"] = arvore.getroottree().getpath(sp)
    return f


def extrair(dados: bytes) -> dict:
    pk = Pacote(dados)
    pres = pk.arvore("ppt/presentation.xml")
    prels = pk.rels("ppt/presentation.xml")
    slides = []
    for ordem, sid in enumerate(pres.findall("p:sldIdLst/p:sldId", NS), 1):
        parte = prels[sid.get(R_ID)][1]
        sl = pk.arvore(parte)
        srels = pk.rels(parte)
        base = f"sld:{sid.get('id')}"
        notas = next((t for (k, t) in srels.values() if k == "notesSlide"), None)
        texto_notas = None
        if notas:
            corpo = _corpo_notas(pk.arvore(notas))
            texto_notas = "\n".join(_texto(p) for p in corpo.findall("p:txBody/a:p", NS)) if corpo is not None else None
        layout = next((t for (k, t) in srels.values() if k == "slideLayout"), None)
        slides.append(
            {
                "id": base,
                "ordem": ordem,
                "parte": "/" + parte,
                "oculto": sl.get("show") == "0",
                "layout": layout and "/" + layout,
                "layout_nome": layout and pk.arvore(layout).find("p:cSld", NS).get("name"),
                "animado": sl.find("p:timing", NS) is not None,
                "notas": texto_notas,
                "formas": [_forma(pk, parte, sl, sp, srels, base) for sp in _formas(sl)],
            }
        )
    externos = []
    for n in pk.partes:
        if n.endswith(".rels"):
            dono = n.replace("_rels/", "").removesuffix(".rels")
            externos += [{"parte": "/" + dono, "url": t} for k, t in pk.rels(dono).values() if k.startswith("externo")]
    return {
        "formato": "pptx",
        "esquema": 1,
        "sha256": sha(dados),
        "slides": slides,
        "links_externos": externos,
        "macros": any(n.lower().endswith("vbaproject.bin") for n in pk.partes),
        "partes": len(pk.partes),
    }


def _corpo_notas(arvore_notas):
    for sp in arvore_notas.iter(P + "sp"):
        ph = sp.find(".//p:ph", NS)
        if ph is not None and ph.get("type") == "body":
            return sp
    return None


# ------------------------------------------------------------------ edição
_ALVO = re.compile(r"(sld:\d{1,10})(?:/sp:(\d{1,10})(?:/(?:(p|ser):(\d{1,6})|(c):(\d{1,4},\d{1,4})))?)?")


def _casar_alvo(alvo):
    """(slide, forma, tipo, n) de um id, ou None. tipo: p | ser | c."""
    m = _ALVO.fullmatch(alvo) if isinstance(alvo, str) else None
    if not m:
        return None
    tipo = m.group(3) or m.group(5)
    return m.group(1), m.group(2), tipo, m.group(4) or m.group(6)


class _Editor:
    def __init__(self, dados: bytes):
        self.pk = Pacote(dados)
        self.pres = self.pk.arvore("ppt/presentation.xml")
        self.prels = self.pk.rels("ppt/presentation.xml")
        self.lista = self.pres.find("p:sldIdLst", NS)
        self.crescimento = 0
        self.avisos: list[str] = []

    def slides(self) -> dict[str, str]:
        return {f"sld:{s.get('id')}": self.prels[s.get(R_ID)][1] for s in self.lista}

    def forma(self, alvo):
        casado = _casar_alvo(alvo)
        if not casado or not casado[1]:
            raise OperacaoInvalida("alvo-invalido", f"alvo de forma inválido: {alvo!r}", alvo=str(alvo))
        slide, forma, tipo, n = casado
        parte = self.slides().get(slide)
        if parte is None:
            raise OperacaoInvalida("alvo-inexistente", f"slide {slide} não existe", alvo=alvo)
        achados = [x for x in self.pk.arvore(parte).iter(P + "cNvPr") if x.get("id") == forma]
        if not achados:
            raise OperacaoInvalida("alvo-inexistente", f"forma {alvo} não existe", alvo=alvo)
        if len(achados) > 1:  # arquivo com id repetido no slide: editar o primeiro seria um chute
            raise OperacaoInvalida("alvo-ambiguo", f"mais de uma forma com o id de {alvo} neste slide", alvo=alvo)
        return parte, achados[0].getparent().getparent(), tipo, n

    def crescer(self, antes: str, depois: str):
        self.crescimento += max(0, len(depois) - len(antes))
        if len(depois) > PARAGRAFO_MAX or self.crescimento > CRESCIMENTO_MAX:
            raise OperacaoInvalida("texto-grande", "o texto resultante passa do limite de tamanho do documento")


def _run_modelo(p):
    """Run que dá a formatação ao texto novo: o primeiro run, senão o rPr de um campo ou do fim do parágrafo."""
    runs = p.findall("a:r", NS)
    if runs:
        return copy.deepcopy(runs[0])
    novo = etree.Element(A + "r")
    origem = p.find("a:fld/a:rPr", NS)
    if origem is None:
        origem = p.find("a:endParaRPr", NS)
    if origem is not None:
        rpr = copy.deepcopy(origem)
        rpr.tag = A + "rPr"
        novo.append(rpr)
    return novo


def _por(p, el):
    end = p.find("a:endParaRPr", NS)
    if end is not None:
        end.addprevious(el)
    else:
        p.append(el)


def _trocar_paragrafo(p, texto: str):
    """Troca o texto mantendo pPr e a formatação do primeiro run (e o endParaRPr). QUEBRA vira a:br."""
    modelo = _run_modelo(p)
    for e in list(p):
        if etree.QName(e).localname in ("r", "br", "fld"):
            p.remove(e)
    for k, trecho in enumerate(texto.split(QUEBRA)):
        if k:
            br = etree.Element(A + "br")
            if modelo.find("a:rPr", NS) is not None:
                br.append(copy.deepcopy(modelo.find("a:rPr", NS)))
            _por(p, br)
        run = copy.deepcopy(modelo)
        t = run.find("a:t", NS)
        if t is None:
            t = etree.SubElement(run, A + "t")
        t.text = trecho
        _por(p, run)


def _op_texto(ed: _Editor, op, rel):
    texto = _campo_texto(op)
    parte, sp, tipo, n = ed.forma(op.get("alvo"))
    corpo = sp.find("p:txBody", NS)
    if corpo is None:
        raise OperacaoInvalida("operacao-invalida", f"{op['alvo']} não tem texto", alvo=op["alvo"])
    ps = corpo.findall("a:p", NS)
    if tipo not in (None, "p"):
        raise OperacaoInvalida("alvo-invalido", "texto aceita forma ou parágrafo (…/p:<n>)", alvo=op["alvo"])
    if tipo == "p":
        i = int(n)
        if i >= len(ps):
            raise OperacaoInvalida("alvo-inexistente", f"parágrafo {n} não existe em {op['alvo']}", alvo=op["alvo"])
        if "\n" in texto:
            raise OperacaoInvalida("operacao-invalida", "num parágrafo, quebra de linha é \\v (\\n separa parágrafos)")
        antes = _texto(ps[i])
        _conferir(op, antes, op["alvo"])
        ed.crescer(antes, texto)
        _trocar_paragrafo(ps[i], texto)
    else:
        antes = "\n".join(_texto(p) for p in ps)
        _conferir(op, antes, op["alvo"])
        ed.crescer(antes, texto)
        linhas = texto.split("\n")
        for p in ps[len(linhas) :]:
            corpo.remove(p)
        for k, linha in enumerate(linhas):
            if k < len(ps):
                _trocar_paragrafo(ps[k], linha)
            else:  # parágrafo novo herda o último (nível, marcador, fonte)
                novo = copy.deepcopy(ps[-1])
                corpo.append(novo)
                _trocar_paragrafo(novo, linha)
    ed.pk.tocar(parte)
    rel.update(antes=antes, depois=texto, partes=["/" + parte])


def _op_substituir(ed: _Editor, op, rel):
    """Busca e troca dentro de uma forma (ou do slide todo), juntando runs do mesmo parágrafo."""
    busca, troca = _campo_texto(op, "buscar"), _campo_texto(op, "trocar")
    if not busca:
        raise OperacaoInvalida("operacao-invalida", "buscar não pode ser vazio")
    if "\n" in busca or "\n" in troca:
        raise OperacaoInvalida("operacao-invalida", "substituir trabalha dentro do parágrafo: use \\v, não \\n")
    alvo = op.get("alvo")
    casado = _casar_alvo(alvo)
    if not casado or casado[2]:
        raise OperacaoInvalida("alvo-invalido", f"substituir aceita slide ou forma: {alvo!r}", alvo=str(alvo))
    if casado[1]:
        parte, sp, _, _ = ed.forma(alvo)
        raizes = [sp]
    else:
        parte = ed.slides().get(casado[0])
        if parte is None:
            raise OperacaoInvalida("alvo-inexistente", f"slide {casado[0]} não existe", alvo=alvo)
        raizes = [ed.pk.arvore(parte)]
    trocas = 0
    for raiz in raizes:
        for p in raiz.iter(A + "p"):
            atual = _texto(p)
            if busca not in atual:
                continue
            n = atual.count(busca)
            ed.crescer(atual, atual.replace(busca, troca))
            trocas += n
            # dentro de um run só: troca ali e preserva a formatação mista do resto do parágrafo
            runs_t = [r.find("a:t", NS) for r in p.findall("a:r", NS)]
            if sum((t.text or "").count(busca) for t in runs_t if t is not None) == n:
                for t in runs_t:
                    if t is not None and t.text and busca in t.text:
                        t.text = t.text.replace(busca, troca)
            else:  # o trecho cruza runs: o parágrafo fica com a formatação do primeiro
                _trocar_paragrafo(p, atual.replace(busca, troca))
                ed.avisos.append(f"{alvo}: '{busca[:40]}' cruzava trechos de formatação; o parágrafo ficou uniforme")
    if trocas == 0:
        raise OperacaoInvalida("conflito", f"{busca[:60]!r} não aparece em {alvo}", alvo=alvo)
    ed.pk.tocar(parte)
    rel.update(trocas=trocas, partes=["/" + parte])


def _op_notas(ed: _Editor, op, rel):
    sid = op.get("slide")
    parte = ed.slides().get(sid) if isinstance(sid, str) else None
    if parte is None:
        raise OperacaoInvalida("alvo-inexistente", f"slide {sid!r} não existe", alvo=str(sid))
    notas = next((t for (k, t) in ed.pk.rels(parte).values() if k == "notesSlide"), None)
    corpo = _corpo_notas(ed.pk.arvore(notas)) if notas else None
    if corpo is None:
        raise OperacaoInvalida("operacao-invalida", f"{sid} não tem página de notas para editar", alvo=sid)
    texto = _campo_texto(op)
    tx = corpo.find("p:txBody", NS)
    ps = tx.findall("a:p", NS) if tx is not None else []
    if not ps:
        raise OperacaoInvalida("operacao-invalida", f"as notas de {sid} não têm corpo de texto", alvo=sid)
    antes = "\n".join(_texto(p) for p in ps)
    _conferir(op, antes, sid)
    ed.crescer(antes, texto)
    for p in ps[1:]:
        tx.remove(p)
    linhas = texto.split("\n")
    _trocar_paragrafo(ps[0], linhas[0])
    for linha in linhas[1:]:
        novo = copy.deepcopy(ps[0])
        tx.append(novo)
        _trocar_paragrafo(novo, linha)
    ed.pk.tocar(notas)
    rel.update(antes=antes, depois=texto, partes=["/" + notas])


def _op_ordem(ed: _Editor, op, rel):
    atual = [f"sld:{s.get('id')}" for s in ed.lista]
    nova = op.get("slides")
    if not isinstance(nova, list) or not all(isinstance(x, str) for x in nova):
        raise OperacaoInvalida("operacao-invalida", "slides precisa ser uma lista de ids")
    if sorted(atual) != sorted(nova):
        raise OperacaoInvalida("operacao-invalida", "ordem precisa listar exatamente os slides atuais, uma vez cada")
    por_id = {f"sld:{s.get('id')}": s for s in ed.lista}
    for s in list(ed.lista):
        ed.lista.remove(s)
    for sid in nova:
        ed.lista.append(por_id[sid])
    ed.pk.tocar("ppt/presentation.xml")
    rel.update(antes=atual, depois=nova, partes=["/ppt/presentation.xml"])


def _op_remover_slide(ed: _Editor, op, rel):
    """Tira o slide e apaga do pacote o que só ele usava (slide, notas, gráficos, mídia). Conteúdo removido não pode
    continuar escondido dentro do arquivo entregue."""
    sid = op.get("slide")
    alvo = next((s for s in ed.lista if f"sld:{s.get('id')}" == sid), None) if isinstance(sid, str) else None
    if alvo is None:
        raise OperacaoInvalida("alvo-inexistente", f"slide {sid!r} não existe", alvo=str(sid))
    if len(ed.lista) == 1:
        raise OperacaoInvalida("operacao-invalida", "a apresentação ficaria sem slides", alvo=sid)
    pk, rid = ed.pk, alvo.get(R_ID)
    parte = ed.prels[rid][1]
    proprias = _alvos(pk, parte)  # as notas do slide apontam de volta para ele: isso não é link de outro slide
    outros = [
        dono
        for dono in _donos_de_rels(pk)
        if dono not in ("ppt/presentation.xml", parte) and dono not in proprias and parte in _alvos(pk, dono)
    ]
    if outros:
        raise OperacaoInvalida(
            "operacao-invalida", f"{sid} é alvo de link em /{outros[0]}: tire o link antes", alvo=sid
        )
    ed.lista.remove(alvo)
    numero = alvo.get("id")
    for el in list(ed.pres.iter(etree.Element)):  # seções e custShow que o citam
        nome = etree.QName(el).localname
        if (
            nome == "sldId"
            and el.get("id") == numero
            and el.getparent() is not None
            and etree.QName(el.getparent()).localname == "sldIdLst"
            and el.getparent() is not ed.lista
        ):
            el.getparent().remove(el)
        elif nome == "sld" and el.get(R_ID) == rid:
            lista = el.getparent()
            lista.remove(el)
            if len(lista) == 0:  # apresentação personalizada que ficou vazia sai inteira
                mostra = lista.getparent()
                mostra.getparent().remove(mostra)
    for vazia in [e for e in ed.pres.iter(P + "custShowLst") if len(e) == 0]:
        vazia.getparent().remove(vazia)
    rels_pres = pk.arvore(pk.caminho_rels("ppt/presentation.xml"))
    for r in rels_pres.findall("rel:Relationship", NS):
        if r.get("Id") == rid:
            rels_pres.remove(r)
    pk.tocar(pk.caminho_rels("ppt/presentation.xml"))
    pk.tocar("ppt/presentation.xml")
    removidas = _remover_orfas(pk, parte)
    ed.prels = pk.rels("ppt/presentation.xml")
    rel.update(partes=["/ppt/presentation.xml"], removidas=["/" + x for x in sorted(removidas)])


def _donos_de_rels(pk: Pacote):
    return [
        n.replace("_rels/", "").removesuffix(".rels")
        for n in pk.partes
        if n.endswith(".rels") and n not in pk.removidas
    ]


def _alvos(pk: Pacote, dono: str) -> set[str]:
    return {t for k, t in pk.rels(dono).values() if not k.startswith("externo")}


def _remover_orfas(pk: Pacote, inicio: str) -> set[str]:
    """Remove `inicio` e, em cascata, as partes que só ele (direta ou indiretamente) referenciava."""
    removidas, fila = set(), [inicio]
    nucleo = {"ppt/presentation.xml", "[Content_Types].xml"}
    while fila:
        parte = fila.pop()
        if parte in removidas or parte in nucleo or parte not in pk.partes:
            continue
        candidatos = _alvos(pk, parte) if pk.caminho_rels(parte) in pk.partes else set()
        removidas.add(parte)
        pk.removidas.add(parte)
        if pk.caminho_rels(parte) in pk.partes:
            pk.removidas.add(pk.caminho_rels(parte))
        ainda = set().union(*(_alvos(pk, d) for d in _donos_de_rels(pk) if d not in removidas))
        fila += [
            c
            for c in candidatos
            if c not in ainda and not c.startswith(("ppt/slideLayouts/", "ppt/slideMasters/", "ppt/theme/"))
        ]
    ct = pk.arvore("[Content_Types].xml")
    for o in ct.findall("ct:Override", NS):
        if o.get("PartName", "").lstrip("/") in removidas:
            ct.remove(o)
    pk.tocar("[Content_Types].xml")
    return removidas


def _op_celula_tabela(ed: _Editor, op, rel):
    texto = _campo_texto(op)
    parte, sp, tipo, n = ed.forma(op.get("alvo"))
    if tipo != "c":
        raise OperacaoInvalida("alvo-invalido", "use o id da célula: sld:<id>/sp:<id>/c:<linha>,<coluna>")
    i, j = (int(x) for x in n.split(","))
    linhas = list(sp.iter(A + "tr"))
    if i >= len(linhas) or j >= len(linhas[i].findall("a:tc", NS)):
        raise OperacaoInvalida("alvo-inexistente", f"célula {n} não existe", alvo=op["alvo"])
    tc = linhas[i].findall("a:tc", NS)[j]
    ps = tc.findall("a:txBody/a:p", NS)
    if not ps:
        raise OperacaoInvalida("operacao-invalida", f"a célula {n} não tem corpo de texto", alvo=op["alvo"])
    antes = "\n".join(_texto(p) for p in ps)
    _conferir(op, antes, op["alvo"])
    ed.crescer(antes, texto)
    for p in ps[1:]:
        p.getparent().remove(p)
    _trocar_paragrafo(ps[0], texto.replace("\n", QUEBRA))
    ed.pk.tocar(parte)
    rel.update(antes=antes, depois=texto, partes=["/" + parte])


def _op_valor_grafico(ed: _Editor, op, rel):
    """Muda um ponto da série no gráfico (cache que o PowerPoint mostra) e na planilha embutida (fonte ao editar)."""
    try:
        valor = float(op["valor"])
        ponto = int(op.get("ponto", -1))
    except (KeyError, TypeError, ValueError):
        raise OperacaoInvalida("operacao-invalida", "valor e ponto precisam ser números", alvo=op.get("alvo")) from None
    if not math.isfinite(valor) or isinstance(op.get("ponto"), bool):
        raise OperacaoInvalida("operacao-invalida", "valor precisa ser um número finito", alvo=op.get("alvo"))
    parte, sp, tipo, n = ed.forma(op.get("alvo"))
    if tipo not in (None, "ser"):
        raise OperacaoInvalida("alvo-invalido", "valor_grafico aceita a forma ou a série (…/ser:<n>)", alvo=op["alvo"])
    chart = sp.find(".//c:chart", NS)
    rid = chart.get(R_ID) if chart is not None else None
    rels = ed.pk.rels(parte)
    if rid not in rels or rels[rid][0].startswith("externo") or rels[rid][1] not in ed.pk.partes:
        raise OperacaoInvalida("operacao-invalida", f"{op['alvo']} não é gráfico editável", alvo=op["alvo"])
    parte_ch = rels[rid][1]
    ch = ed.pk.arvore(parte_ch)
    idx = str(n if tipo == "ser" else op.get("serie"))
    ser = next(
        (s for s in ch.iter(C + "ser") if s.find("c:idx", NS) is not None and s.find("c:idx", NS).get("val") == idx),
        None,
    )
    if ser is None:
        raise OperacaoInvalida("alvo-inexistente", f"série {idx} não existe", alvo=op["alvo"])
    pt = ser.find(f"c:val//c:pt[@idx='{ponto}']/c:v", NS)
    if pt is None:
        raise OperacaoInvalida("alvo-inexistente", f"ponto {ponto} não existe na série {idx}", alvo=op["alvo"])
    antes = _numero(pt.text)
    pt.text = repr(valor) if valor != int(valor) else str(int(valor))
    ed.pk.tocar(parte_ch)
    partes = ["/" + parte_ch]
    planilha = next((t for (k, t) in ed.pk.rels(parte_ch).values() if k == "package"), None)
    celula = None
    ref = ser.findtext("c:val//c:f", namespaces=NS)
    if planilha and ref and planilha in ed.pk.partes:
        celula = _celula_embutida(ed.pk, planilha, ref, ponto, valor, ed.avisos)
        if celula:
            partes.append("/" + planilha)
    if celula is None:
        ed.avisos.append(f"{op['alvo']}: só o gráfico mudou; a planilha embutida não foi atualizada")
    rel.update(antes=antes, depois=valor, celula=celula, partes=partes)


def _celula_embutida(pk: Pacote, planilha: str, ref: str, deslocamento: int, valor: float, avisos) -> str | None:
    """Atualiza a célula na planilha embutida no gráfico (zip dentro do zip), editando só o XML da aba."""
    aba, faixa = ref.replace("$", "").rsplit("!", 1)
    aba = aba[1:-1].replace("''", "'") if aba.startswith("'") and aba.endswith("'") else aba
    m = re.fullmatch(r"([A-Z]+)(\d+)", faixa.split(":")[0])
    if not m:
        return None
    col, lin = m.groups()
    fim = re.fullmatch(r"([A-Z]+)(\d+)", faixa.split(":")[-1])
    vertical = fim is None or fim.group(1) == col
    celula = f"{col}{int(lin) + deslocamento}" if vertical else f"{_coluna(col, deslocamento)}{lin}"
    interno = Pacote(pk.partes[planilha])
    wb = interno.arvore("xl/workbook.xml")
    folha = next((s for s in wb.findall("s:sheets/s:sheet", NS) if s.get("name") == aba), None)
    if folha is None:
        return None
    parte_aba = interno.rels("xl/workbook.xml").get(folha.get(R_ID), (None, None))[1]
    if parte_aba not in interno.partes:
        return None
    c = interno.arvore(parte_aba).find(f".//s:c[@r='{celula}']", NS)
    if c is None or c.find("s:v", NS) is None:
        return None
    if c.find("s:f", NS) is not None:  # célula calculada: trocar o valor apagaria a fórmula
        avisos.append(f"{aba}!{celula} tem fórmula; só o gráfico mudou")
        return None
    c.attrib.pop("t", None)
    c.find("s:v", NS).text = repr(valor) if valor != int(valor) else str(int(valor))
    interno.tocar(parte_aba)
    pk.partes[planilha] = interno.salvar()
    pk.tocar(planilha)
    return f"{aba}!{celula}"


def _coluna(col: str, deslocamento: int) -> str:
    n = 0
    for ch in col:
        n = n * 26 + ord(ch) - 64
    n += deslocamento
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


OPERACOES = {
    "texto": _op_texto,
    "substituir": _op_substituir,
    "notas": _op_notas,
    "ordem": _op_ordem,
    "remover_slide": _op_remover_slide,
    "celula_tabela": _op_celula_tabela,
    "valor_grafico": _op_valor_grafico,
}


def aplicar(dados: bytes, operacoes: list[dict]) -> tuple[bytes, list[dict], list[str]]:
    """Tudo ou nada: aplica as operações em ordem; a primeira que falha cancela todas.

    Devolve o arquivo novo, o que cada operação fez e os avisos (o que foi aplicado com ressalva)."""
    ed = _Editor(dados)
    relatorio = []
    for k, op in enumerate(operacoes):
        if not isinstance(op, dict) or not isinstance(op.get("op"), str) or op["op"] not in OPERACOES:
            raise OperacaoInvalida(
                "operacao-invalida",
                f"op desconhecida: {op.get('op') if isinstance(op, dict) else op!r}; use uma de {', '.join(OPERACOES)}",
                indice=k,
            )
        rel = {"op": op["op"], **({"alvo": op["alvo"]} if "alvo" in op else {})}
        try:
            OPERACOES[op["op"]](ed, op, rel)
        except OperacaoInvalida as e:
            e.indice = k
            raise
        except (KeyError, IndexError, ValueError, TypeError, AttributeError) as e:
            # arquivo fora do esperado para esta operação: erro do pedido, nunca 500
            raise OperacaoInvalida(
                "operacao-invalida", f"não foi possível aplicar ({type(e).__name__})", indice=k, alvo=op.get("alvo")
            ) from None
        relatorio.append(rel)
    return ed.pk.salvar(), relatorio, ed.avisos


def integridade(dados: bytes) -> list[str]:
    return _integridade(dados, "ppt/")
