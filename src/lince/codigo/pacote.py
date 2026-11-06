"""Pacote OOXML em memória: leitura segura, partes como bytes, escrita cirúrgica.

O arquivo original é a fonte; quem edita só reserializa as partes que tocou. Todas as outras entradas do zip saem
idênticas em bytes, na mesma ordem e com o mesmo ZipInfo, inclusive as que o Lince não entende.
"""

import hashlib
import io
import posixpath
import zipfile

from lxml import etree

from ..limites import DocumentoRecusado, conferir_zip

PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, remove_blank_text=False)
R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
R_EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_REL = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
NS_CT = {"ct": "http://schemas.openxmlformats.org/package/2006/content-types"}
QUEBRA = "\v"  # quebra de linha dentro do parágrafo (a:br, w:br), como no python-pptx; "\n" separa parágrafos
TEXTO_MAX = 100_000  # caracteres num campo de texto de uma operação
PARAGRAFO_MAX = 200_000  # caracteres num parágrafo depois de uma troca (substituir não amplifica sem limite)
CRESCIMENTO_MAX = 5_000_000  # caracteres acrescentados somando todas as operações de um pedido


class OperacaoInvalida(ValueError):
    """Operação que não pode ser aplicada; `codigo` é estável para a IA tratar."""

    def __init__(self, codigo: str, mensagem: str, indice: int | None = None, alvo: str | None = None):
        super().__init__(mensagem)
        self.codigo, self.indice, self.alvo = codigo, indice, alvo


def campo_texto(op, nome="texto") -> str:
    valor = op.get(nome)
    if not isinstance(valor, str):
        raise OperacaoInvalida("operacao-invalida", f"{nome} precisa ser texto", alvo=op.get("alvo"))
    if len(valor) > TEXTO_MAX:
        raise OperacaoInvalida("texto-grande", f"{nome} passa de {TEXTO_MAX} caracteres", alvo=op.get("alvo"))
    return valor


def conferir(op, atual: str, alvo: str):
    """Concorrência otimista: se a operação diz o texto que espera encontrar, ele precisa estar lá."""
    if "se_texto" in op and op["se_texto"] != atual:
        raise OperacaoInvalida("conflito", f"o texto atual de {alvo} não é o esperado: {atual[:80]!r}", alvo=alvo)


def sha(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


def xml(dados: bytes):
    """XML de uma parte OOXML. O padrão não usa DTD: qualquer DOCTYPE é recusado (fecha XXE e expansão)."""
    if any(
        m in dados
        for m in (b"<!DOCTYPE", b"<!ENTITY", "<!DOCTYPE".encode("utf-16-le"), "<!DOCTYPE".encode("utf-16-be"))
    ):
        raise DocumentoRecusado("parte XML com DOCTYPE ou ENTITY não é aceita")
    try:
        return etree.fromstring(dados, PARSER)
    except etree.XMLSyntaxError as e:
        raise DocumentoRecusado(f"parte XML malformada: {e}") from None


class Pacote:
    def __init__(self, dados: bytes):
        conferir_zip(io.BytesIO(dados))
        self.infos: list[zipfile.ZipInfo] = []
        self.partes: dict[str, bytes] = {}
        nomes_vistos = set()
        try:
            self._ler(dados, nomes_vistos)
        except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError) as e:  # CRC errado, entrada truncada
            raise DocumentoRecusado(f"pacote corrompido: {type(e).__name__}") from None
        self.arvores: dict = {}
        self.tocadas: set[str] = set()
        self.removidas: set[str] = set()

    def _ler(self, dados, nomes_vistos):
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            for i in z.infolist():
                chave = i.filename.lower()
                if chave in nomes_vistos:  # nomes repetidos (ou que só diferem em maiúsculas) confundem leitores
                    raise DocumentoRecusado(f"parte repetida no pacote: {i.filename[:80]}")
                nomes_vistos.add(chave)
                self.infos.append(i)
                self.partes[i.filename] = z.read(i)

    def arvore(self, nome: str):
        if nome not in self.arvores:
            self.arvores[nome] = xml(self.partes[nome])
        return self.arvores[nome]

    def caminho_rels(self, nome: str) -> str:
        d, b = posixpath.split(nome)
        return posixpath.join(d, "_rels", b + ".rels")

    def rels(self, nome: str) -> dict[str, tuple[str, str]]:
        """rId -> (tipo curto, parte alvo normalizada | URL externa)."""
        rn = self.caminho_rels(nome)
        if rn not in self.partes:
            return {}
        d = posixpath.dirname(nome)
        out = {}
        for r in self.arvore(rn).findall("rel:Relationship", NS_REL):
            tipo = r.get("Type", "").rsplit("/", 1)[-1]
            if r.get("TargetMode") == "External":
                out[r.get("Id")] = ("externo:" + tipo, r.get("Target"))
            else:
                alvo = r.get("Target", "")
                alvo = alvo.lstrip("/") if alvo.startswith("/") else posixpath.normpath(posixpath.join(d, alvo))
                out[r.get("Id")] = (tipo, alvo)
        return out

    def tocar(self, nome: str):
        self.tocadas.add(nome)

    def criar(self, nome: str, arvore, content_type: str | None = None):
        """Parte nova no fim do pacote (com Override no [Content_Types].xml quando o tipo vem explícito)."""
        if nome in self.partes:
            raise ValueError(f"/{nome} já existe")
        info = zipfile.ZipInfo(nome, date_time=self.infos[0].date_time)
        info.compress_type = zipfile.ZIP_DEFLATED
        self.infos.append(info)
        self.partes[nome] = b""
        self.arvores[nome] = arvore
        self.tocar(nome)
        if content_type:
            ct = self.arvore("[Content_Types].xml")
            etree.SubElement(ct, f"{{{NS_CT['ct']}}}Override", PartName="/" + nome, ContentType=content_type)
            self.tocar("[Content_Types].xml")

    def relacionar(self, dono: str, tipo_url: str, alvo_relativo: str) -> str:
        """Acrescenta uma relação interna em `dono` e devolve o rId novo (o arquivo .rels já precisa existir)."""
        rels = self.arvore(self.caminho_rels(dono))
        usados = {r.get("Id") for r in rels}
        n = len(usados) + 1
        while f"rId{n}" in usados:
            n += 1
        etree.SubElement(rels, f"{{{NS_REL['rel']}}}Relationship", Id=f"rId{n}", Type=tipo_url, Target=alvo_relativo)
        self.tocar(self.caminho_rels(dono))
        return f"rId{n}"

    def salvar(self) -> bytes:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            for i in self.infos:
                if i.filename in self.removidas:
                    continue
                dados = self.partes[i.filename]
                if i.filename in self.tocadas and i.filename in self.arvores:
                    dados = etree.tostring(
                        self.arvores[i.filename], xml_declaration=True, encoding="UTF-8", standalone=True
                    )
                z.writestr(i, dados)  # mesmo ZipInfo: nome, ordem, data e compressão
        return out.getvalue()


def integridade(dados: bytes, prefixo: str) -> list[str]:
    """Problemas estruturais do pacote: relação apontando para parte que não existe, parte sem content type, atributo
    r:* (id, embed, link…) em parte XML de `prefixo` sem a relação correspondente (o Office "repara" o arquivo)."""
    pk = Pacote(dados)
    problemas = []
    ct = pk.arvore("[Content_Types].xml")
    padrao = {d.get("Extension").lower() for d in ct.findall("ct:Default", NS_CT)}
    explicito = {o.get("PartName").lstrip("/") for o in ct.findall("ct:Override", NS_CT)}
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
        elif nome.endswith(".xml") and nome.startswith(prefixo):
            ids = set(pk.rels(nome))
            for el in pk.arvore(nome).iter():
                for chave, valor in el.attrib.items():
                    if chave.startswith(R_NS) and valor and valor not in ids:
                        problemas.append(f"/{nome} cita {valor}, que não está nas relações")
                        break
    return problemas


def comparar(antes: bytes, depois: bytes) -> dict:
    """Diff por parte entre dois pacotes: o que ficou idêntico em bytes, o que mudou, entrou ou saiu."""

    def mapa(dados):
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            return {i.filename: sha(z.read(i)) for i in z.infolist()}

    a, b = mapa(antes), mapa(depois)
    return {
        "partes_identicas": sum(1 for n in a if n in b and a[n] == b[n]),
        "partes_alteradas": sorted("/" + n for n in a if n in b and a[n] != b[n]),
        "partes_novas": sorted("/" + n for n in b if n not in a),
        "partes_removidas": sorted("/" + n for n in a if n not in b),
    }
