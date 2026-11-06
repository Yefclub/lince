"""Código de um DOCX: JSON canônico com ids e edição cirúrgica por operações nomeadas, com controle de alterações.

Ids:
    parágrafo  p:<w14:paraId>  (o Word grava em todo parágrafo; estável entre edições)
               p:n<k>          (arquivo sem paraId: posição no corpo, na ordem do documento; use se_texto)
    tabela     t:<k>           (posição no corpo; as células trazem os ids dos próprios parágrafos)

Toda operação de texto entra como revisão (w:ins/w:del, w:pPrChange) por padrão: quem abre no Word vê o que a IA
propôs e aceita ou rejeita. `"revisao": false` aplica direto; `"revisao": {"autor": "…"}` troca o autor.
"""

import bisect
import copy
import random
import re
from datetime import UTC, datetime

from lxml import etree

from ..limites import DocumentoRecusado
from .pacote import (
    CRESCIMENTO_MAX,
    PARAGRAFO_MAX,
    QUEBRA,
    OperacaoInvalida,
    Pacote,
    campo_texto,
    conferir,
    sha,
)
from .pacote import (
    integridade as _integridade,
)

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
}
W = f"{{{NS['w']}}}"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
PARA_ID = f"{{{NS['w14']}}}paraId"
TIPO_DOCUMENTO = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
TIPO_COMENTARIOS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
CT_COMENTARIOS = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
REVISOES = {  # alterações controladas: texto, formatação, numeração, tabela e seção
    W + n
    for n in (
        "ins",
        "del",
        "moveFrom",
        "moveTo",
        "moveFromRangeStart",
        "moveToRangeStart",
        "rPrChange",
        "pPrChange",
        "numberingChange",
        "sectPrChange",
        "tblPrChange",
        "trPrChange",
        "tcPrChange",
        "tblGridChange",
        "cellIns",
        "cellDel",
        "cellMerge",
    )
}
TROCAS_MAX = 10_000  # ocorrências trocadas por operação substituir
AUTOR_PADRAO = "Lince"
OBJETO = "￼"  # imagem, campo, nota: ocupa uma posição no texto para que nenhuma troca o atravesse
CONTEUDO_OPACO = {
    "drawing",
    "pict",
    "object",
    "fldChar",
    "instrText",
    "footnoteReference",
    "endnoteReference",
    "commentReference",
    "sym",
    "ptab",
    "AlternateContent",  # caixa de texto, forma e WordArt dentro do run
}
_ALVO_P = re.compile(r"p:(n\d{1,7}|[0-9A-Fa-f]{8})")
_ALVO_T = re.compile(r"t:(\d{1,6})")


# ------------------------------------------------------------------ leitura
def _documento(pk: Pacote) -> str:
    if "_rels/.rels" in pk.partes:
        for r in pk.arvore("_rels/.rels").findall("rel:Relationship", NS):
            alvo = (r.get("Target") or "").lstrip("/")
            if r.get("Type") == TIPO_DOCUMENTO and alvo in pk.partes:
                return alvo
    raise OperacaoInvalida("operacao-invalida", "pacote sem documento principal: não é um DOCX válido")


def _corpo(pk: Pacote, parte: str):
    corpo = pk.arvore(parte).find("w:body", NS)
    if corpo is None:
        raise OperacaoInvalida("operacao-invalida", "documento sem corpo")
    return corpo


def _paragrafos(corpo):
    """Parágrafos do corpo e das tabelas, na ordem do documento (sem caixas de texto, que ficam em desenhos)."""
    for p in corpo.iter(W + "p"):
        if not any(etree.QName(a).localname in ("txbxContent", "Fallback") for a in p.iterancestors()):
            yield p


def _ids(corpo) -> list[tuple[str, object]]:
    """(id, parágrafo) na ordem; paraId quando o arquivo traz, senão a posição."""
    out = []
    for k, p in enumerate(_paragrafos(corpo)):
        pid = p.get(PARA_ID)
        out.append((f"p:{pid}" if pid else f"p:n{k}", p))
    return out


_ENVOLTORIOS = {"hyperlink", "smartTag", "ins", "moveTo", "customXml"}
_MARCADORES = {  # filhos de parágrafo sem texto visível: ficam onde estão
    "pPr",
    "bookmarkStart",
    "bookmarkEnd",
    "commentRangeStart",
    "commentRangeEnd",
    "proofErr",
    "permStart",
    "permEnd",
    "del",
    "moveFrom",
    "moveFromRangeStart",
    "moveFromRangeEnd",
    "moveToRangeStart",
    "moveToRangeEnd",
    "customXmlInsRangeStart",
    "customXmlInsRangeEnd",
    "customXmlDelRangeStart",
    "customXmlDelRangeEnd",
    "customXmlMoveFromRangeStart",
    "customXmlMoveFromRangeEnd",
    "customXmlMoveToRangeStart",
    "customXmlMoveToRangeEnd",
}


def _runs(p, campos=False):
    """Runs visíveis do parágrafo, em ordem: os de dentro de hyperlink, smartTag e controles de conteúdo contam; os de
    revisão excluída (w:del, moveFrom) não. Todo o resto que não é run (campo simples, equação, texto bidirecional,
    o que o Lince não conhece) vem inteiro, como objeto que nenhuma troca atravessa; com `campos=True`, desce no campo
    simples e no texto bidirecional (para marcar os runs de dentro como excluídos). Não entra em caixas de texto."""
    for el in p:
        if not isinstance(el.tag, str):
            continue
        nome = etree.QName(el).localname
        if el.tag == W + "r":
            yield el
        elif el.tag[: len(W)] == W and nome in _ENVOLTORIOS:
            yield from _runs(el, campos)
        elif el.tag == W + "sdt":
            conteudo = el.find("w:sdtContent", NS)
            if conteudo is not None:
                yield from _runs(conteudo, campos)
        elif campos and el.tag in (W + "fldSimple", W + "dir", W + "bdo"):
            yield from _runs(el, campos)
        elif not (el.tag[: len(W)] == W and nome in _MARCADORES):
            yield el


def _unidade(x) -> str:
    """O texto que um filho de run ocupa (OBJETO para o que não é texto e não pode ser cortado)."""
    nome = etree.QName(x).localname
    if nome == "t":
        return x.text or ""
    if nome == "tab":
        return "\t"
    if nome == "cr" or (nome == "br" and x.get(W + "type") in (None, "textWrapping")):
        return QUEBRA
    if nome == "br":
        return OBJETO  # quebra de página e de coluna mudam o layout: trocar por \v estragaria
    if nome == "noBreakHyphen":
        return "-"
    return OBJETO if nome in CONTEUDO_OPACO else ""


def _texto_run(r) -> str:
    return "".join(_unidade(x) for x in r)


def _campos(corpo) -> dict:
    """Profundidade de campo complexo no começo de cada parágrafo: sumário e índice são um campo só que atravessa
    vários parágrafos (o dict usa o elemento como chave e o mantém vivo)."""
    out, nivel = {}, 0
    for p in _paragrafos(corpo):
        out[p] = nivel
        for r in _runs(p):
            for f in r.iter(W + "fldChar") if r.tag == W + "r" else ():
                tipo = f.get(W + "fldCharType")
                nivel = nivel + 1 if tipo == "begin" else max(0, nivel - 1) if tipo == "end" else nivel
    return out


def _conteudo(p, campo: int = 0) -> list[tuple[object, str]]:
    """(run, texto) na ordem. Campo inteiro (simples ou complexo, inclusive o resultado mostrado) vira OBJETO: o
    resultado é recalculado pelo Word, então trocar ali não teria efeito duradouro. `campo` é a profundidade de campo
    complexo em que o parágrafo começa."""
    out = []
    for r in _runs(p):
        if r.tag != W + "r":
            out.append((r, OBJETO))
            continue
        tipos = [f.get(W + "fldCharType") for f in r.findall("w:fldChar", NS)]
        if "begin" in tipos:
            campo += 1
        texto = _texto_run(r)
        out.append((r, OBJETO * len(texto) if campo else texto))
        if "end" in tipos:
            campo = max(0, campo - 1)
    return out


def _texto(p, com_objetos=False, campo: int = 0) -> str:
    t = "".join(texto for _, texto in _conteudo(p, campo))
    return t if com_objetos else t.replace(OBJETO, "")


def _revisoes(p) -> bool:
    return any(el.tag in REVISOES for el in p.iter())


def _estilos(pk: Pacote, documento: str) -> dict[str, str]:
    """styleId -> nome dos estilos de parágrafo."""
    alvo = next((a for t, a in pk.rels(documento).values() if t == "styles"), None)
    if alvo is None or alvo not in pk.partes:
        return {}
    out = {}
    for s in pk.arvore(alvo).findall("w:style", NS):
        if s.get(W + "type") == "paragraph":
            nome = s.find("w:name", NS)
            out[s.get(W + "styleId")] = nome.get(W + "val") if nome is not None else s.get(W + "styleId")
    return out


def _paragrafo(pid: str, p, campo: int = 0) -> dict:
    ppr = p.find("w:pPr", NS)
    estilo = ppr.find("w:pStyle", NS) if ppr is not None else None
    num = ppr.find("w:numPr", NS) if ppr is not None else None
    out = {"id": pid, "estilo": estilo.get(W + "val") if estilo is not None else None, "texto": _texto(p, campo=campo)}
    if num is not None:
        nivel, lista = num.find("w:ilvl", NS), num.find("w:numId", NS)
        out["lista"] = {
            "num": lista.get(W + "val") if lista is not None else None,
            "nivel": int(nivel.get(W + "val")) if nivel is not None else 0,
        }
    if OBJETO in _texto(p, com_objetos=True, campo=campo):
        out["objetos"] = True  # imagem, campo, nota, caixa de texto: `texto` recusa, `substituir` trabalha em volta
    if p.find("w:sdt", NS) is not None:
        out["controles"] = True  # controle de conteúdo no parágrafo: `texto` recusa, `substituir` troca dentro dele
    if _revisoes(p):
        out["revisoes"] = True
    return out


def extrair(dados: bytes) -> dict:
    pk = Pacote(dados)
    documento = _documento(pk)
    corpo = _corpo(pk, documento)
    ids = {p: pid for pid, p in _ids(corpo)}  # chave é o elemento: o dict mantém o proxy do lxml vivo
    campos = _campos(corpo)

    def par(p):
        return _paragrafo(ids[p], p, campos.get(p, 0))

    def proprios(raiz, tag, dono):  # descendentes `tag` cujo `dono` mais próximo é `raiz` (sem descer em aninhados)
        return [x for x in raiz.iter(W + tag) if next(x.iterancestors(W + dono), None) is raiz]

    blocos, tabelas = [], 0
    for el in corpo:
        if not isinstance(el.tag, str):
            continue
        nome = etree.QName(el).localname
        if nome == "p":
            blocos.append({"tipo": "paragrafo", **par(el)})
        elif nome == "tbl":  # linhas e células também dentro de controle de conteúdo (seção repetida)
            linhas = [
                [[par(p) for p in tc.iter(W + "p") if p in ids] for tc in proprios(tr, "tc", "tr")]
                for tr in proprios(el, "tr", "tbl")
            ]
            blocos.append({"tipo": "tabela", "id": f"t:{tabelas}", "linhas": linhas})
            tabelas += 1
        else:  # controle de conteúdo, customXml…: os parágrafos de dentro entram em ordem
            blocos += [{"tipo": "paragrafo", **par(p)} for p in el.iter(W + "p") if p in ids]
    comentarios = []
    alvo = next((a for t, a in pk.rels(documento).values() if t == "comments"), None)
    if alvo in pk.partes:
        for c in pk.arvore(alvo).findall("w:comment", NS):
            comentarios.append(
                {
                    "id": c.get(W + "id"),
                    "autor": c.get(W + "author"),
                    "texto": "\n".join(_texto(p) for p in c.iter(W + "p")),
                }
            )
    externos = []
    for n in pk.partes:
        if n.endswith(".rels"):
            dono = n.replace("_rels/", "").removesuffix(".rels")
            externos += [{"parte": "/" + dono, "url": t} for k, t in pk.rels(dono).values() if k.startswith("externo")]
    return {
        "formato": "docx",
        "esquema": 1,
        "sha256": sha(dados),
        "blocos": blocos,
        "estilos": [{"id": k, "nome": v} for k, v in _estilos(pk, documento).items()],
        "comentarios": comentarios,
        "revisoes": sum(1 for el in corpo.iter() if el.tag in REVISOES),
        "links_externos": externos,
        "macros": any(n.lower().endswith("vbaproject.bin") for n in pk.partes),
        "partes": len(pk.partes),
    }


# ------------------------------------------------------------------ edição
class _Editor:
    def __init__(self, dados: bytes):
        self.pk = Pacote(dados)
        self.documento = _documento(self.pk)
        self.corpo = _corpo(self.pk, self.documento)
        self.ids = dict(_ids(self.corpo))  # resolvidos no arquivo original: inserir não muda o alvo das próximas
        self.campos = _campos(self.corpo)
        self.para_ids = {p.get(PARA_ID) for p in self.corpo.iter(W + "p") if p.get(PARA_ID)}
        self.usa_para_id = bool(self.para_ids)
        self.tocados: set = set()  # elementos (o set mantém os proxies vivos)
        self.crescimento = 0
        self.avisos: list[str] = []
        self.agora = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self._proximo_id = 1 + max(
            (
                int(v)
                for parte in (self.documento, *self._parte_comentarios())
                for el in self.pk.arvore(parte).iter()
                if (v := el.get(W + "id")) is not None and v.lstrip("-").isdigit()
            ),
            default=0,
        )

    def _parte_comentarios(self):
        alvo = next((a for t, a in self.pk.rels(self.documento).values() if t == "comments"), None)
        return [alvo] if alvo in self.pk.partes else []

    def texto(self, p, objetos=False) -> str:
        return _texto(p, objetos, self.campos.get(p, 0))

    def novo_para_id(self) -> str:
        """paraId novo, único no documento e abaixo de 0x80000000, como o Word exige."""
        while True:
            pid = f"{random.randrange(1, 0x80000000):08X}"
            if pid not in self.para_ids:
                self.para_ids.add(pid)
                return pid

    def novo_id(self) -> str:
        self._proximo_id += 1
        return str(self._proximo_id - 1)

    def paragrafo(self, alvo):
        if not isinstance(alvo, str) or not _ALVO_P.fullmatch(alvo):
            raise OperacaoInvalida("alvo-invalido", f"alvo de parágrafo inválido: {alvo!r}", alvo=str(alvo))
        achados = [p for pid, p in _ids(self.corpo) if pid == alvo] if not alvo.startswith("p:n") else []
        if alvo.startswith("p:n"):
            p = self.ids.get(alvo)
            achados = [p] if p is not None else []
        elif len(achados) > 1:
            raise OperacaoInvalida("alvo-ambiguo", f"mais de um parágrafo com o id {alvo}", alvo=alvo)
        if not achados or achados[0].getparent() is None:
            raise OperacaoInvalida("alvo-inexistente", f"parágrafo {alvo} não existe", alvo=alvo)
        return achados[0]

    def tocar_texto(self, p, alvo):
        """Uma operação de texto por parágrafo por pedido, e nunca sobre revisões que já estavam no arquivo."""
        if p in self.tocados:
            raise OperacaoInvalida("conflito", f"{alvo} já foi alterado neste pedido: junte as mudanças", alvo=alvo)
        if _revisoes(p):
            raise OperacaoInvalida(
                "revisoes-pendentes",
                f"{alvo} tem alterações controladas pendentes: aceite ou rejeite no Word antes",
                alvo=alvo,
            )
        self.tocados.add(p)
        self.pk.tocar(self.documento)

    def crescer(self, antes: str | int, depois: str | int):
        """Confere o tamanho do parágrafo resultante e o crescimento do pedido (aceita textos ou tamanhos)."""
        antes, depois = (x if isinstance(x, int) else len(x) for x in (antes, depois))
        self.crescimento += max(0, depois - antes)
        if depois > PARAGRAFO_MAX or self.crescimento > CRESCIMENTO_MAX:
            raise OperacaoInvalida("texto-grande", "o texto resultante passa do limite de tamanho do documento")

    def revisao(self, op) -> dict | None:
        """Atributos da marca de revisão (w:id, autor, data), ou None quando a operação pede aplicação direta."""
        valor = op.get("revisao", True)
        if valor is False:
            return None
        autor = AUTOR_PADRAO
        if isinstance(valor, dict):
            autor = valor.get("autor", AUTOR_PADRAO)
            if not isinstance(autor, str) or not 0 < len(autor) <= 255:
                raise OperacaoInvalida("operacao-invalida", "revisao.autor precisa ter de 1 a 255 caracteres")
        elif valor is not True:
            raise OperacaoInvalida("operacao-invalida", 'revisao é true, false ou {"autor": "…"}')
        return {"autor": autor}

    def marca(self, nome: str, rev: dict):
        return etree.Element(W + nome, {W + "id": self.novo_id(), W + "author": rev["autor"], W + "date": self.agora})


def _fatiar(r, pontos: list[int]) -> list:
    """Corta o run em todos os `pontos` (deslocamentos em unidades de _unidade, crescentes, dentro do run) numa
    passada só, e devolve os pedaços em ordem (o primeiro é o próprio `r`). Cada pedaço leva cópia do rPr: a
    formatação não muda. Custo linear no tamanho do run, qualquer que seja o número de cortes."""
    rpr = r.find("w:rPr", NS)
    filhos = [x for x in r if x is not rpr]
    atomos, posicao = [], 0  # (começo, elemento) com os w:t já cortados nos pontos que caem dentro deles
    for x in filhos:
        tamanho = len(_unidade(x))
        dentro = [q - posicao for q in pontos if posicao < q < posicao + tamanho] if x.tag == W + "t" else []
        if not dentro:
            atomos.append((posicao, x))
        else:
            texto, anterior = x.text, 0
            for corte in [*dentro, tamanho]:
                t = etree.Element(W + "t")
                t.text = texto[anterior:corte]
                if t.text != t.text.strip():
                    t.set(XML_SPACE, "preserve")
                atomos.append((posicao + anterior, t))
                anterior = corte
        posicao += tamanho
    for x in filhos:
        r.remove(x)
    pedacos, atual, k = [r], r, 0
    for comeco, el in atomos:
        while k < len(pontos) and comeco >= pontos[k]:  # passou de um ponto de corte: run novo
            novo = etree.Element(W + "r", r.attrib)
            if rpr is not None:
                novo.append(copy.deepcopy(rpr))
            atual.addnext(novo)
            pedacos.append(novo)
            atual, k = novo, k + 1
        atual.append(el)
    return pedacos  # um pedaço por trecho: todo corte é interno, então cada trecho começa com algum filho


def _juntar_filhos(pai, tag):
    """Junta runs vizinhos de `pai` que só têm um `tag` (w:delText) e o mesmo rPr."""
    anterior = None
    for r in list(pai):
        if r.tag != W + "r" or len([x for x in r if x.tag != W + "rPr"]) != 1 or r.find(tag) is None:
            anterior = None
            continue
        if anterior is not None and _mesmo_rpr(anterior, r) and dict(anterior.attrib) == dict(r.attrib):
            ta, tb = anterior.find(tag), r.find(tag)
            ta.text = (ta.text or "") + (tb.text or "")
            if ta.text != ta.text.strip():
                ta.set(XML_SPACE, "preserve")
            pai.remove(r)
        else:
            anterior = r


def _mesmo_rpr(a, b) -> bool:
    ra, rb = a.find("w:rPr", NS), b.find("w:rPr", NS)
    return etree.tostring(ra) == etree.tostring(rb) if ra is not None and rb is not None else ra is rb


def _run(texto: str, rpr) -> list:
    """Runs para um texto (QUEBRA vira w:br, tab vira w:tab), com cópia do rPr."""
    runs = []
    for trecho in re.split(r"(\t|\v)", texto):
        if trecho == "":
            continue
        r = etree.Element(W + "r")
        if rpr is not None:
            r.append(copy.deepcopy(rpr))
        if trecho == "\t":
            etree.SubElement(r, W + "tab")
        elif trecho == QUEBRA:
            etree.SubElement(r, W + "br")
        else:
            t = etree.SubElement(r, W + "t")
            t.text = trecho
            t.set(XML_SPACE, "preserve")
        runs.append(r)
    return runs


def _rpr_limpo(r):
    rpr = r.find("w:rPr", NS) if r is not None else None
    if rpr is None:
        return None
    rpr = copy.deepcopy(rpr)
    for x in rpr.findall("w:rPrChange", NS):
        rpr.remove(x)
    return rpr


def _excluir(ed: _Editor, runs: list, rev):
    """Tira os runs: direto, ou embrulhados em w:del (w:t vira w:delText) quando é revisão."""
    marca = None
    for r in runs:
        if rev is None:
            r.getparent().remove(r)
            continue
        for t in r.findall("w:t", NS):
            t.tag = W + "delText"
        for t in r.findall("w:instrText", NS):
            t.tag = W + "delInstrText"
        if marca is None or r.getprevious() is not marca:  # runs vizinhos ficam no mesmo w:del
            marca = ed.marca("del", rev)
            r.addprevious(marca)
        marca.append(r)
        _juntar_filhos(marca, W + "delText")
    return marca


def _incluir(ed: _Editor, depois_de, texto: str, rpr, rev, pai=None):
    """Põe o texto novo logo depois de `depois_de` (ou no fim de `pai`); embrulhado em w:ins quando é revisão."""
    novos = _run(texto, rpr)
    if not novos:
        return
    if rev is not None:
        marca = ed.marca("ins", rev)
        for r in novos:
            marca.append(r)
        novos = [marca]
    for el in novos:
        if depois_de is not None:
            depois_de.addnext(el)
            depois_de = el
        else:
            pai.append(el)


def _op_texto(ed: _Editor, op, rel):
    texto = campo_texto(op)
    if "\n" in texto:
        raise OperacaoInvalida(
            "operacao-invalida", "texto é um parágrafo: use \\v para quebra de linha", alvo=op.get("alvo")
        )
    p = ed.paragrafo(op.get("alvo"))
    atual = ed.texto(p)
    conferir(op, atual, op["alvo"])
    if OBJETO in ed.texto(p, objetos=True):
        raise OperacaoInvalida(
            "operacao-invalida",
            f"{op['alvo']} tem imagem, campo, nota, equação ou caixa de texto: use substituir no trecho",
            alvo=op["alvo"],
        )
    if p.find("w:sdt", NS) is not None:
        raise OperacaoInvalida(
            "operacao-invalida", f"{op['alvo']} tem controle de conteúdo: use substituir no trecho", alvo=op["alvo"]
        )
    ed.tocar_texto(p, op["alvo"])
    ed.crescer(atual, texto)
    rev = ed.revisao(op)
    runs = list(_runs(p))
    rpr = _rpr_limpo(runs[0]) if runs else None
    if rev is None:  # links e smartTags somem junto; o texto novo fica onde estava o antigo (dentro dos bookmarks)
        removiveis = [
            e for e in p if etree.QName(e).localname in ("r", "hyperlink", "smartTag", "proofErr", "customXml")
        ]
        posicao = p.index(removiveis[0]) if removiveis else len(p)
        for el in removiveis:
            p.remove(el)
        for k, novo in enumerate(_run(texto, rpr)):
            p.insert(posicao + k, novo)
    else:
        ultima = _excluir(ed, runs, rev) if runs else None
        _incluir(ed, ultima, texto, rpr, rev, pai=p)
    rel.update(antes=atual, depois=texto, partes=["/" + ed.documento])


def _op_substituir(ed: _Editor, op, rel):
    """Busca e troca no parágrafo (ou no documento todo, sem alvo). A troca corta os runs no ponto exato: o resto do
    parágrafo mantém a formatação, e o texto novo herda a do primeiro caractere trocado."""
    busca, troca = campo_texto(op, "buscar"), campo_texto(op, "trocar")
    if not busca:
        raise OperacaoInvalida("operacao-invalida", "buscar não pode ser vazio")
    if "\n" in busca or "\n" in troca:
        raise OperacaoInvalida("operacao-invalida", "substituir trabalha dentro do parágrafo: use \\v, não \\n")
    alvo = op.get("alvo")
    paragrafos = [ed.paragrafo(alvo)] if alvo is not None else list(_paragrafos(ed.corpo))
    rev = ed.revisao(op)
    trocas, ignoradas = 0, 0
    for p in paragrafos:
        visivel = ed.texto(p)
        if busca not in visivel:
            continue
        if alvo is None and (_revisoes(p) or p in ed.tocados):
            ignoradas += visivel.count(busca)
            continue
        feitas, puladas = _trocar_no_paragrafo(ed, p, busca, troca, rev, TROCAS_MAX - trocas, alvo or "documento")
        trocas += feitas
        ignoradas += puladas
    if ignoradas:
        ed.avisos.append(
            f"{ignoradas} ocorrência(s) de '{busca[:40]}' não trocada(s): atravessa imagem, campo, link, controle de "
            "conteúdo ou revisão pendente"
        )
    if trocas == 0:
        raise OperacaoInvalida("conflito", f"{busca[:60]!r} não aparece onde dá para trocar", alvo=alvo)
    rel.update(trocas=trocas, partes=["/" + ed.documento])


def _trocar_no_paragrafo(ed: _Editor, p, busca: str, troca: str, rev, restantes: int, rotulo: str) -> tuple[int, int]:
    """Troca as ocorrências que ficam dentro de um mesmo pai (parágrafo, link, controle…) sem atravessar objeto.
    Linear: acha todas numa passada, corta cada run uma vez só em todos os pontos de troca e aplica do fim para o
    começo (as posições de trás não mudam). Só marca o parágrafo como alterado se houver troca. (trocadas, puladas)"""
    conteudo = _conteudo(p, ed.campos.get(p, 0))
    texto = "".join(t for _, t in conteudo)
    if len(texto) > PARAGRAFO_MAX:
        raise OperacaoInvalida("texto-grande", "parágrafo grande demais para substituir; use texto")
    spans, inicio = [], 0
    for r, t in conteudo:
        spans.append((inicio, inicio + len(t), r))
        inicio += len(t)
    comecos = [x[0] for x in spans]

    def indice(pos):  # o span que contém o caractere `pos` (spans vazios na mesma posição ficam antes)
        return bisect.bisect_right(comecos, pos) - 1

    validas, total, k = [], 0, texto.find(busca)
    while k >= 0:
        total += 1
        fim = k + len(busca)
        donos = [spans[x][2] for x in range(indice(k), indice(fim - 1) + 1) if spans[x][1] > spans[x][0]]
        if OBJETO not in texto[k:fim] and len({r.getparent() for r in donos}) == 1:
            validas.append(k)
        k = texto.find(busca, fim)
    if not validas:
        return 0, total
    if len(validas) > restantes:
        raise OperacaoInvalida(
            "operacao-invalida", f"mais de {TROCAS_MAX} ocorrências numa operação; seja mais específico"
        )
    ed.tocar_texto(p, rotulo)
    ed.crescer(len(texto), len(texto) + len(validas) * (len(troca) - len(busca)))
    cortes: dict[int, set] = {}  # índice do span -> pontos de corte relativos a ele
    for k in validas:
        for pos in (k, k + len(busca)):
            x = indice(pos) if pos < len(texto) else len(spans) - 1
            if spans[x][0] < pos < spans[x][1]:
                cortes.setdefault(x, set()).add(pos - spans[x][0])
    pedacos = []  # (começo, fim, run) depois dos cortes: toda ocorrência começa e termina em borda de run
    for x, (a, b, r) in enumerate(spans):
        if x in cortes and r.tag == W + "r":
            pontos = sorted(cortes[x])
            for (de, ate), pedaco in zip(
                zip([0, *pontos], [*pontos, b - a], strict=True), _fatiar(r, pontos), strict=True
            ):
                pedacos.append((a + de, a + ate, pedaco))
        else:
            pedacos.append((a, b, r))
    comecos = [x[0] for x in pedacos]
    for k in reversed(validas):
        fim = k + len(busca)
        j = bisect.bisect_left(comecos, k)
        while j < len(pedacos) and pedacos[j][1] == pedacos[j][0] and pedacos[j][0] == k:
            j += 1  # pula runs vazios que começam no mesmo ponto: ficam fora da troca
        i = bisect.bisect_left(comecos, fim) - 1
        trecho = [pedacos[x][2] for x in range(j, i + 1)]
        modelo = next((r for r in trecho if _texto_run(r)), trecho[0])
        rpr = _rpr_limpo(modelo)
        if rev is None:
            for novo in _run(troca, rpr):
                trecho[0].addprevious(novo)
            _excluir(ed, trecho, None)
        else:
            _incluir(ed, _excluir(ed, trecho, rev), troca, rpr, rev)  # logo depois do w:del
    return len(validas), total - len(validas)


def _op_estilo(ed: _Editor, op, rel):
    p = ed.paragrafo(op.get("alvo"))
    estilo = op.get("estilo")
    estilos = _estilos(ed.pk, ed.documento)
    if not isinstance(estilo, str):
        raise OperacaoInvalida("operacao-invalida", "estilo precisa ser o id ou o nome de um estilo", alvo=op["alvo"])
    if estilo not in estilos:
        por_nome = [k for k, v in estilos.items() if v.lower() == estilo.lower()]
        if not por_nome:
            raise OperacaoInvalida("alvo-inexistente", f"estilo {estilo!r} não existe no documento", alvo=op["alvo"])
        estilo = por_nome[0]
    ed.tocar_texto(p, op["alvo"])
    rev = ed.revisao(op)
    ppr = p.find("w:pPr", NS)
    if ppr is None:
        ppr = etree.Element(W + "pPr")
        p.insert(0, ppr)
    antes = ppr.find("w:pStyle", NS)
    estilo_antes = antes.get(W + "val") if antes is not None else None
    if rev is not None:
        mudanca = ed.marca("pPrChange", rev)
        anterior = etree.SubElement(mudanca, W + "pPr")
        for x in ppr:
            if etree.QName(x).localname not in ("rPr", "sectPr", "pPrChange"):
                anterior.append(copy.deepcopy(x))
        ppr.append(mudanca)  # último filho do pPr, como pede o esquema
    if antes is None:
        antes = etree.Element(W + "pStyle")
        ppr.insert(0, antes)
    antes.set(W + "val", estilo)
    rel.update(antes=estilo_antes, depois=estilo, partes=["/" + ed.documento])


def _op_inserir(ed: _Editor, op, rel):
    texto = campo_texto(op)
    if "\n" in texto:
        raise OperacaoInvalida("operacao-invalida", "um parágrafo por operação: use \\v para quebra de linha")
    p = ed.paragrafo(op.get("alvo"))
    ed.crescer("", texto)
    rev = ed.revisao(op)
    novo = etree.Element(W + "p")
    ppr_alvo = p.find("w:pPr", NS)
    ppr = copy.deepcopy(ppr_alvo) if ppr_alvo is not None else etree.Element(W + "pPr")
    for x in list(ppr):
        if etree.QName(x).localname in ("sectPr", "pPrChange", "rPr"):  # quebra de seção fica no parágrafo original
            ppr.remove(x)
    estilo = op.get("estilo")
    runs = list(_runs(p))
    rpr = _rpr_limpo(runs[0]) if runs and estilo is None else None
    if estilo is not None:
        estilos = _estilos(ed.pk, ed.documento)
        if not isinstance(estilo, str) or (
            estilo not in estilos and estilo.lower() not in {v.lower() for v in estilos.values()}
        ):
            raise OperacaoInvalida("alvo-inexistente", f"estilo {estilo!r} não existe no documento", alvo=op["alvo"])
        estilo = estilo if estilo in estilos else next(k for k, v in estilos.items() if v.lower() == estilo.lower())
        for x in ppr.findall("w:pStyle", NS) + ppr.findall("w:numPr", NS):
            ppr.remove(x)
        ps = etree.Element(W + "pStyle")
        ps.set(W + "val", estilo)
        ppr.insert(0, ps)
    if rev is not None:  # a marca de parágrafo também é inserção: rejeitar no Word tira o parágrafo inteiro
        marca_rpr = etree.SubElement(ppr, W + "rPr")
        marca_rpr.append(ed.marca("ins", rev))
    if len(ppr):
        novo.append(ppr)
    _incluir(ed, None, texto, rpr, rev, pai=novo)
    if ed.usa_para_id:
        novo.set(PARA_ID, ed.novo_para_id())
    p.addnext(novo)
    ed.tocados.add(novo)
    ed.pk.tocar(ed.documento)
    rel.update(
        depois=texto, partes=["/" + ed.documento], **({"id": f"p:{novo.get(PARA_ID)}"} if ed.usa_para_id else {})
    )


def _op_remover(ed: _Editor, op, rel):
    p = ed.paragrafo(op.get("alvo"))
    pai = p.getparent()
    blocos = [e for e in pai if etree.QName(e).localname in ("p", "tbl", "sdt", "customXml")]
    if etree.QName(pai).localname in ("body", "tc") and len(pai.findall("w:p", NS)) == 1:
        raise OperacaoInvalida("operacao-invalida", f"{op['alvo']} é o único parágrafo do bloco", alvo=op["alvo"])
    if blocos[-1] is p and len(blocos) > 1 and etree.QName(blocos[-2]).localname == "tbl":
        # célula e corpo precisam terminar em parágrafo: sem ele, o Word repara o arquivo
        raise OperacaoInvalida("operacao-invalida", f"{op['alvo']} é o parágrafo que fecha uma tabela", alvo=op["alvo"])
    if p.find("w:pPr/w:sectPr", NS) is not None:
        raise OperacaoInvalida("operacao-invalida", f"{op['alvo']} fecha uma seção do documento", alvo=op["alvo"])
    if any(el.tag in (W + "commentRangeStart", W + "commentReference") for el in p.iter()):
        raise OperacaoInvalida("operacao-invalida", f"{op['alvo']} tem comentário: resolva antes", alvo=op["alvo"])
    atual = ed.texto(p)
    conferir(op, atual, op["alvo"])
    rev = ed.revisao(op)
    if rev is None and etree.QName(pai).localname not in ("body", "tc"):
        # dentro de controle de conteúdo: tirar o parágrafo pode deixar a célula ou o controle vazio
        raise OperacaoInvalida(
            "operacao-invalida",
            f"{op['alvo']} está dentro de um controle de conteúdo: remova com revisão",
            alvo=op["alvo"],
        )
    conteudo = list(_runs(p, campos=True))  # os runs de dentro de campo simples e texto bidirecional também
    if rev is not None and any(r.tag != W + "r" for r in conteudo):
        raise OperacaoInvalida(
            "operacao-invalida",
            f"{op['alvo']} tem equação ou conteúdo que não dá para marcar como excluído",
            alvo=op["alvo"],
        )
    ed.tocar_texto(p, op["alvo"])
    if rev is None:
        pai.remove(p)
    else:
        _excluir(ed, conteudo, rev)
        ppr = p.find("w:pPr", NS)
        if ppr is None:
            ppr = etree.Element(W + "pPr")
            p.insert(0, ppr)
        marca_rpr = ppr.find("w:rPr", NS)
        if marca_rpr is None:
            marca_rpr = etree.Element(W + "rPr")
            fim = ppr.find("w:sectPr", NS)
            if fim is None:
                fim = ppr.find("w:pPrChange", NS)
            if fim is not None:
                fim.addprevious(marca_rpr)
            else:
                ppr.append(marca_rpr)
        marca_rpr.insert(0, ed.marca("del", rev))
    rel.update(antes=atual, partes=["/" + ed.documento])


def _op_comentar(ed: _Editor, op, rel):
    texto = campo_texto(op)
    p = ed.paragrafo(op.get("alvo"))
    autor = op.get("autor", AUTOR_PADRAO)
    if not isinstance(autor, str) or not 0 < len(autor) <= 255:
        raise OperacaoInvalida("operacao-invalida", "autor precisa ter de 1 a 255 caracteres", alvo=op["alvo"])
    ed.crescer("", texto)
    partes = _parte_comentarios(ed)
    comentarios = ed.pk.arvore(partes)
    cid = ed.novo_id()
    c = etree.SubElement(
        comentarios,
        W + "comment",
        {
            W + "id": cid,
            W + "author": autor,
            W + "date": ed.agora,
            W + "initials": "".join(x[0] for x in autor.split())[:9],
        },
    )
    for k, linha in enumerate(texto.split("\n")):
        cp = etree.SubElement(c, W + "p")
        if k == 0:
            etree.SubElement(etree.SubElement(cp, W + "r"), W + "annotationRef")
        for r in _run(linha, None):
            cp.append(r)
    inicio = etree.Element(W + "commentRangeStart", {W + "id": cid})
    ppr = p.find("w:pPr", NS)
    if ppr is not None:
        ppr.addnext(inicio)
    else:
        p.insert(0, inicio)
    p.append(etree.Element(W + "commentRangeEnd", {W + "id": cid}))
    ref = etree.SubElement(p, W + "r")
    etree.SubElement(ref, W + "commentReference", {W + "id": cid})
    ed.pk.tocar(ed.documento)
    ed.pk.tocar(partes)
    rel.update(comentario=cid, partes=sorted({"/" + ed.documento, "/" + partes}))


def _parte_comentarios(ed: _Editor) -> str:
    existente = ed._parte_comentarios()
    if existente:
        return existente[0]
    nome = ed.documento.rsplit("/", 1)[0] + "/comments.xml" if "/" in ed.documento else "comments.xml"
    raiz = etree.Element(W + "comments", nsmap={"w": NS["w"]})
    ed.pk.criar(nome, raiz, CT_COMENTARIOS)
    ed.pk.relacionar(ed.documento, TIPO_COMENTARIOS, nome.rsplit("/", 1)[-1])
    return nome


OPERACOES = {
    "texto": _op_texto,
    "substituir": _op_substituir,
    "estilo_paragrafo": _op_estilo,
    "inserir_paragrafo_apos": _op_inserir,
    "remover_paragrafo": _op_remover,
    "comentar": _op_comentar,
}


def aplicar(dados: bytes, operacoes: list[dict]) -> tuple[bytes, list[dict], list[str]]:
    """Tudo ou nada: aplica as operações em ordem; a primeira que falha cancela todas."""
    try:
        ed = _Editor(dados)
    except (KeyError, IndexError, ValueError, TypeError, AttributeError) as e:
        if isinstance(e, (OperacaoInvalida, DocumentoRecusado)):
            raise
        raise OperacaoInvalida("operacao-invalida", f"DOCX fora do esperado ({type(e).__name__})") from None
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
        except (KeyError, IndexError, ValueError, TypeError, AttributeError) as e:
            raise OperacaoInvalida(
                "operacao-invalida", f"não foi possível aplicar ({type(e).__name__})", indice=k, alvo=op.get("alvo")
            ) from None
        relatorio.append(rel)
    return ed.pk.salvar(), relatorio, ed.avisos


def integridade(dados: bytes) -> list[str]:
    problemas = _integridade(dados, "word/")
    pk = Pacote(dados)
    documento = _documento(pk)
    citados = {el.get(W + "id") for el in pk.arvore(documento).iter(W + "commentReference")}
    alvo = next((a for t, a in pk.rels(documento).values() if t == "comments"), None)
    existentes = {c.get(W + "id") for c in pk.arvore(alvo).findall("w:comment", NS)} if alvo in pk.partes else set()
    problemas += [f"comentário {c} citado no texto e ausente em comments.xml" for c in sorted(citados - existentes)]
    return problemas
