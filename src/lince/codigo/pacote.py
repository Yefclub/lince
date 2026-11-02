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
NS_REL = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}


def sha(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


def xml(dados: bytes):
    """XML de uma parte OOXML. O padrão não usa DTD: qualquer DOCTYPE é recusado (fecha XXE e expansão)."""
    if b"<!DOCTYPE" in dados[:4096] or b"<!ENTITY" in dados[:4096]:
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
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            for i in z.infolist():
                chave = i.filename.lower()
                if chave in nomes_vistos:  # nomes repetidos (ou que só diferem em maiúsculas) confundem leitores
                    raise DocumentoRecusado(f"parte repetida no pacote: {i.filename[:80]}")
                nomes_vistos.add(chave)
                self.infos.append(i)
                self.partes[i.filename] = z.read(i)
        self.arvores: dict = {}
        self.tocadas: set[str] = set()
        self.removidas: set[str] = set()

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
