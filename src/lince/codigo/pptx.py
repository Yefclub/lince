"""Código de um PPTX: JSON canônico com ids estáveis, e edição cirúrgica por operações nomeadas.

Ids (vêm do próprio arquivo, sobrevivem a reordenar e editar):
    slide      sld:<p:sldId/@id>
    forma      sld:<id>/sp:<p:cNvPr/@id>
    parágrafo  sld:<id>/sp:<id>/p:<n>
    célula     sld:<id>/sp:<id>/c:<linha>,<coluna>
    série      sld:<id>/sp:<id>/ser:<c:idx>
"""

import copy
import re

from lxml import etree

from .pacote import R_EMBED, R_ID, Pacote, sha

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


class OperacaoInvalida(ValueError):
    """Operação que não pode ser aplicada; `codigo` é estável para a IA tratar."""

    def __init__(self, codigo: str, mensagem: str, indice: int | None = None, alvo: str | None = None):
        super().__init__(mensagem)
        self.codigo, self.indice, self.alvo = codigo, indice, alvo


def _texto(el) -> str:
    return "".join(t.text or "" for t in el.iter(A + "t"))


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
_ALVO = re.compile(r"(sld:\d+)(?:/sp:(\d+)(?:/(p|c|ser):([\d,]+))?)?")


class _Editor:
    def __init__(self, dados: bytes):
        self.pk = Pacote(dados)
        self.pres = self.pk.arvore("ppt/presentation.xml")
        self.prels = self.pk.rels("ppt/presentation.xml")
        self.lista = self.pres.find("p:sldIdLst", NS)

    def slides(self) -> dict[str, str]:
        return {f"sld:{s.get('id')}": self.prels[s.get(R_ID)][1] for s in self.lista}

    def forma(self, alvo: str):
        m = _ALVO.fullmatch(alvo or "")
        if not m or not m.group(2):
            raise OperacaoInvalida("alvo-invalido", f"alvo de forma inválido: {alvo!r}", alvo=alvo)
        parte = self.slides().get(m.group(1))
        if parte is None:
            raise OperacaoInvalida("alvo-inexistente", f"slide {m.group(1)} não existe", alvo=alvo)
        sl = self.pk.arvore(parte)
        nv = next((x for x in sl.iter(P + "cNvPr") if x.get("id") == m.group(2)), None)
        if nv is None:
            raise OperacaoInvalida("alvo-inexistente", f"forma {alvo} não existe", alvo=alvo)
        return parte, nv.getparent().getparent(), m.group(3), m.group(4)


def _conferir(op, atual: str, alvo: str):
    """Concorrência otimista: se a operação diz o texto que espera encontrar, ele precisa estar lá."""
    if "se_texto" in op and op["se_texto"] != atual:
        raise OperacaoInvalida("conflito", f"o texto atual de {alvo} não é o esperado: {atual[:80]!r}", alvo=alvo)


def _trocar_paragrafo(p, texto: str):
    """Troca o texto mantendo pPr e a formatação do primeiro run (e o endParaRPr)."""
    runs = p.findall("a:r", NS)
    modelo = runs[0] if runs else None
    for e in list(p):
        if etree.QName(e).localname in ("r", "br", "fld"):
            p.remove(e)
    novo = copy.deepcopy(modelo) if modelo is not None else etree.Element(A + "r")
    t = novo.find("a:t", NS)
    if t is None:
        t = etree.SubElement(novo, A + "t")
    t.text = texto
    if texto != texto.strip():
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    end = p.find("a:endParaRPr", NS)
    if end is not None:
        end.addprevious(novo)
    else:
        p.append(novo)


def _op_texto(ed: _Editor, op, rel):
    parte, sp, tipo, n = ed.forma(op.get("alvo"))
    corpo = sp.find("p:txBody", NS)
    if corpo is None:
        raise OperacaoInvalida("operacao-invalida", f"{op['alvo']} não tem texto", alvo=op["alvo"])
    ps = corpo.findall("a:p", NS)
    if tipo == "p":
        i = int(n)
        if i >= len(ps):
            raise OperacaoInvalida("alvo-inexistente", f"parágrafo {n} não existe em {op['alvo']}", alvo=op["alvo"])
        _conferir(op, _texto(ps[i]), op["alvo"])
        antes = _texto(ps[i])
        _trocar_paragrafo(ps[i], str(op["texto"]))
    else:
        antes = "\n".join(_texto(p) for p in ps)
        _conferir(op, antes, op["alvo"])
        linhas = str(op["texto"]).split("\n")
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
    rel.update(antes=antes, depois=op["texto"], partes=["/" + parte])


def _op_substituir(ed: _Editor, op, rel):
    """Busca e troca dentro de uma forma (ou do slide todo), juntando runs do mesmo parágrafo."""
    busca, troca = str(op.get("buscar", "")), str(op.get("trocar", ""))
    if not busca:
        raise OperacaoInvalida("operacao-invalida", "buscar não pode ser vazio")
    alvo = op.get("alvo", "")
    m = _ALVO.fullmatch(alvo)
    if not m:
        raise OperacaoInvalida("alvo-invalido", f"alvo inválido: {alvo!r}", alvo=alvo)
    if m.group(2):
        parte, sp, _, _ = ed.forma(alvo)
        raizes = [sp]
    else:
        parte = ed.slides().get(m.group(1))
        if parte is None:
            raise OperacaoInvalida("alvo-inexistente", f"slide {m.group(1)} não existe", alvo=alvo)
        raizes = [ed.pk.arvore(parte)]
    trocas = 0
    for raiz in raizes:
        for p in raiz.iter(A + "p"):
            atual = _texto(p)
            if busca in atual:
                trocas += atual.count(busca)
                _trocar_paragrafo(p, atual.replace(busca, troca))
    if trocas == 0:
        raise OperacaoInvalida("conflito", f"{busca[:60]!r} não aparece em {alvo}", alvo=alvo)
    ed.pk.tocar(parte)
    rel.update(trocas=trocas, partes=["/" + parte])


def _op_notas(ed: _Editor, op, rel):
    sid = op.get("slide")
    parte = ed.slides().get(sid)
    if parte is None:
        raise OperacaoInvalida("alvo-inexistente", f"slide {sid} não existe", alvo=sid)
    notas = next((t for (k, t) in ed.pk.rels(parte).values() if k == "notesSlide"), None)
    corpo = _corpo_notas(ed.pk.arvore(notas)) if notas else None
    if corpo is None:
        raise OperacaoInvalida("operacao-invalida", f"{sid} não tem página de notas para editar", alvo=sid)
    tx = corpo.find("p:txBody", NS)
    ps = tx.findall("a:p", NS)
    antes = "\n".join(_texto(p) for p in ps)
    _conferir(op, antes, sid)
    for p in ps[1:]:
        tx.remove(p)
    linhas = str(op["texto"]).split("\n")
    _trocar_paragrafo(ps[0], linhas[0])
    for linha in linhas[1:]:
        novo = copy.deepcopy(ps[0])
        tx.append(novo)
        _trocar_paragrafo(novo, linha)
    ed.pk.tocar(notas)
    rel.update(antes=antes, depois=op["texto"], partes=["/" + notas])


def _op_ordem(ed: _Editor, op, rel):
    atual = [f"sld:{s.get('id')}" for s in ed.lista]
    nova = list(op.get("slides") or [])
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
    """Tira o slide da apresentação. A parte fica no pacote sem referência (o PowerPoint a ignora e limpa ao salvar);
    apagar partes exigiria reescrever relações de notas e mídia compartilhada, com mais risco do que ganho."""
    sid = op.get("slide")
    alvo = next((s for s in ed.lista if f"sld:{s.get('id')}" == sid), None)
    if alvo is None:
        raise OperacaoInvalida("alvo-inexistente", f"slide {sid} não existe", alvo=sid)
    if len(ed.lista) == 1:
        raise OperacaoInvalida("operacao-invalida", "a apresentação ficaria sem slides", alvo=sid)
    ed.lista.remove(alvo)
    ed.pk.tocar("ppt/presentation.xml")
    rel.update(partes=["/ppt/presentation.xml"])


def _op_celula_tabela(ed: _Editor, op, rel):
    parte, sp, tipo, n = ed.forma(op.get("alvo"))
    if tipo != "c":
        raise OperacaoInvalida("alvo-invalido", "use o id da célula: sld:<id>/sp:<id>/c:<linha>,<coluna>")
    i, j = (int(x) for x in n.split(","))
    linhas = list(sp.iter(A + "tr"))
    if i >= len(linhas) or j >= len(linhas[i].findall("a:tc", NS)):
        raise OperacaoInvalida("alvo-inexistente", f"célula {n} não existe", alvo=op["alvo"])
    tc = linhas[i].findall("a:tc", NS)[j]
    ps = tc.findall("a:txBody/a:p", NS)
    antes = "\n".join(_texto(p) for p in ps)
    _conferir(op, antes, op["alvo"])
    for p in ps[1:]:
        p.getparent().remove(p)
    _trocar_paragrafo(ps[0], str(op["texto"]))
    ed.pk.tocar(parte)
    rel.update(antes=antes, depois=op["texto"], partes=["/" + parte])


def _op_valor_grafico(ed: _Editor, op, rel):
    """Muda um ponto da série no gráfico (cache que o PowerPoint mostra) e na planilha embutida (fonte ao editar)."""
    parte, sp, tipo, n = ed.forma(op.get("alvo"))
    chart = sp.find(".//c:chart", NS)
    if chart is None:
        raise OperacaoInvalida("operacao-invalida", f"{op['alvo']} não é gráfico", alvo=op["alvo"])
    parte_ch = ed.pk.rels(parte)[chart.get(R_ID)][1]
    ch = ed.pk.arvore(parte_ch)
    idx = str(n if tipo == "ser" else op.get("serie"))
    ser = next((s for s in ch.iter(C + "ser") if s.find("c:idx", NS).get("val") == idx), None)
    if ser is None:
        raise OperacaoInvalida("alvo-inexistente", f"série {idx} não existe", alvo=op["alvo"])
    ponto = int(op.get("ponto", -1))
    pt = ser.find(f"c:val//c:pt[@idx='{ponto}']/c:v", NS)
    if pt is None:
        raise OperacaoInvalida("alvo-inexistente", f"ponto {ponto} não existe na série {idx}", alvo=op["alvo"])
    try:
        valor = float(op["valor"])
    except (KeyError, TypeError, ValueError):
        raise OperacaoInvalida("operacao-invalida", "valor precisa ser número", alvo=op["alvo"]) from None
    antes = _numero(pt.text)
    pt.text = repr(valor) if valor != int(valor) else str(int(valor))
    ed.pk.tocar(parte_ch)
    partes = ["/" + parte_ch]
    planilha = next((t for (k, t) in ed.pk.rels(parte_ch).values() if k == "package"), None)
    celula = None
    ref = ser.findtext("c:val//c:f", namespaces=NS)
    if planilha and ref:
        celula = _celula_embutida(ed.pk, planilha, ref, ponto, valor)
        partes.append("/" + planilha)
    rel.update(antes=antes, depois=valor, celula=celula, partes=partes)


def _celula_embutida(pk: Pacote, planilha: str, ref: str, deslocamento: int, valor: float) -> str | None:
    """Atualiza a célula na planilha embutida no gráfico (zip dentro do zip), editando só o XML da aba."""
    aba, faixa = ref.replace("$", "").rsplit("!", 1)
    aba = aba.strip("'")
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
    parte_aba = interno.rels("xl/workbook.xml")[folha.get(R_ID)][1]
    c = interno.arvore(parte_aba).find(f".//s:c[@r='{celula}']", NS)
    if c is None or c.find("s:v", NS) is None:
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


def aplicar(dados: bytes, operacoes: list[dict]) -> tuple[bytes, list[dict]]:
    """Tudo ou nada: aplica as operações em ordem; a primeira que falha cancela todas."""
    ed = _Editor(dados)
    relatorio = []
    for k, op in enumerate(operacoes):
        if not isinstance(op, dict) or op.get("op") not in OPERACOES:
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
        relatorio.append(rel)
    return ed.pk.salvar(), relatorio


def integridade(dados: bytes) -> list[str]:
    """Problemas estruturais do pacote: relação apontando para parte que não existe, parte sem content type."""
    pk = Pacote(dados)
    problemas = []
    ct = pk.arvore("[Content_Types].xml")
    padrao = {d.get("Extension").lower() for d in ct.findall("ct:Default", NS)}
    explicito = {o.get("PartName").lstrip("/") for o in ct.findall("ct:Override", NS)}
    for nome in pk.partes:
        if nome.endswith("/") or nome == "[Content_Types].xml":
            continue
        extensao = nome.rsplit("/", 1)[-1].rsplit(".", 1)[-1].lower() if "." in nome.rsplit("/", 1)[-1] else ""
        if nome not in explicito and extensao not in padrao:
            problemas.append(f"/{nome} sem content type")
        if nome.endswith(".rels"):
            dono = nome.replace("_rels/", "").removesuffix(".rels")
            for rid, (tipo, alvo) in pk.rels(dono).items():
                if not tipo.startswith("externo") and alvo not in pk.partes:
                    problemas.append(f"/{dono} {rid} aponta para /{alvo}, que não existe")
    return problemas
