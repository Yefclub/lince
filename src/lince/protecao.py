"""Arquivo protegido por senha: reconhecido antes de qualquer leitura e, com a senha, decifrado numa cópia só para ela.

Formatos: PDF com senha de abertura (o que só tem senha de permissões abre sem ela e é lido como sempre), Office OOXML
cifrado (um contêiner OLE com `EncryptionInfo` e `EncryptedPackage`), Office legado (`.doc`, `.xls`, `.ppt`) e ODF
(`manifest:encryption-data` no manifesto). O PDF é decifrado pela PDFium; o Office, pelo msoffcrypto-tool; o ODF,
aqui, pelo padrão ODF 1.2 (AES-256-CBC, chave por PBKDF2 sobre o SHA-256 da senha, conferida pelo checksum de cada
parte). Cifra que não tem como ser decifrada com segurança fica protegida, com o motivo.

A senha só passa pelos argumentos: não vai para exceção, log, plano nem cache, e quem chama a descarta depois de abrir.
"""

import base64
import hashlib
import logging
import mmap
import os
import re
import zipfile
import zlib

import anydoc
import olefile

from ._trava import PDFIUM
from .limites import DESCOMPACTADO_MAX, PARTE_MAX, DocumentoRecusado, conferir_zip, xml_ou_nada

# Voltas de hash para derivar a chave do Office (spinCount) e de cada parte do ODF (PBKDF2). O Office e o LibreOffice
# gravam 100 mil (medido: ~0,05 s no msoffcrypto, em Python, e ~0,03 s no PBKDF2); o OOXML aceita até 10 milhões, e um
# arquivo hostil prenderia o envio por segundos a cada tentativa. Acima de 10x o que os programas gravam, o arquivo
# fica protegido, com o motivo.
VOLTAS_MAX = 1_000_000
# Voltas somadas das partes de um ODF: cada parte tem o próprio sal, e o LibreOffice cifra também cada imagem. 20
# milhões são ~200 partes com 100 mil voltas (~6 s, só com a senha certa: a errada para na primeira parte).
VOLTAS_ODF_MAX = 20_000_000

# O msoffcrypto registra em DEBUG a chave derivada da senha (cifra Standard do OOXML) e o verificador dela (Word com
# XOR): com o DEBUG ligado na aplicação, ela iria para o log. Abaixo de INFO, nada dele sai.
logging.getLogger("msoffcrypto").setLevel(logging.INFO)

_PDF_MARCA = b"%PDF-"
_OOXML = {".docx", ".docm", ".xlsx", ".xlsm", ".xlsb", ".pptx", ".pptm", ".ppsx", ".ppsm"}
_LEGADOS = {"doc": {".doc"}, "xls": {".xls"}, "ppt": {".ppt", ".pps", ".pot"}}
_ODF = {
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.presentation": "odp",
}
_M = "{urn:oasis:names:tc:opendocument:xmlns:manifest:1.0}"
_AES256_CBC = "http://www.w3.org/2001/04/xmlenc#aes256-cbc"
_INICIO = {  # start-key-generation: o hash da senha que entra no PBKDF2 (sem o elemento, SHA-1, do ODF 1.0)
    "http://www.w3.org/2000/09/xmldsig#sha256": "sha256",
    "http://www.w3.org/2001/04/xmlenc#sha256": "sha256",
    "http://www.w3.org/2000/09/xmldsig#sha1": "sha1",
    "SHA1": "sha1",
}
_CHECKSUM = {  # o hash do primeiro 1 KB da parte decifrada, ainda compactada: é ele que diz se a senha está certa
    "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0#sha256-1k": "sha256",
    "SHA256/1K": "sha256",
    "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0#sha1-1k": "sha1",
    "SHA1/1K": "sha1",
}


class DocumentoProtegido(anydoc.ConvertError):
    """O arquivo só abre com senha, e nada dele foi lido. `formato` diz qual (pdf, docx, xls, odt...); `motivo`, quando
    há, diz por que nem a senha basta aqui (cifra não suportada). Nunca traz a senha."""

    def __init__(self, formato: str, motivo: str = ""):
        self.formato, self.motivo = formato, motivo
        super().__init__(f"arquivo {formato} protegido por senha" + (f": {motivo}" if motivo else ""))


class SenhaIncorreta(DocumentoProtegido):
    """A senha dada não abre o arquivo."""

    def __init__(self, formato: str):
        super().__init__(formato, "senha incorreta")


def formato_protegido(caminho) -> str | None:
    """O formato do arquivo se ele só abre com senha; senão None. Não decifra nada e não lê o conteúdo: só o que diz
    se há cifra. Arquivo malformado ou hostil também dá None: a leitura recusa com o motivo de sempre."""
    achado = _protecao(str(caminho))
    return achado[1] if achado else None


def aberto(caminho, senha: str | None, pasta) -> str:
    """O caminho para ler: o próprio arquivo, se ele não pede senha, ou uma cópia decifrada com `senha` em `pasta`, com
    o mesmo nome. Sem senha, `DocumentoProtegido`; senha que não abre, `SenhaIncorreta`; cifra que não dá para decifrar
    aqui, `DocumentoProtegido` com o motivo. A senha não fica guardada em lugar nenhum."""
    caminho = str(caminho)
    if (achado := _protecao(caminho)) is None:
        return caminho
    decifrar, formato = achado
    if not senha:
        raise DocumentoProtegido(formato)
    os.makedirs(pasta, exist_ok=True)
    destino = os.path.join(str(pasta), os.path.basename(caminho))
    if os.path.abspath(destino) == os.path.abspath(caminho):
        raise ValueError("a cópia decifrada precisa de outra pasta, não a do arquivo")
    try:
        decifrar(caminho, senha, destino, formato)
    except BaseException:
        if os.path.exists(destino):
            os.unlink(destino)  # cópia pela metade não fica para trás
        raise
    return destino


def _protecao(caminho):
    """(como decifrar, formato) do arquivo que só abre com senha, ou None."""
    ext = os.path.splitext(caminho)[1].lower()
    try:
        with open(caminho, "rb") as f:
            cabeca = f.read(1024)
        if cabeca.startswith(olefile.MAGIC):
            formato = _ole_protegido(caminho, ext)
            return (_decifrar_office, formato) if formato else None
        if _PDF_MARCA in cabeca:  # a PDFium aceita o cabeçalho em qualquer lugar do primeiro 1 KB
            return (_decifrar_pdf, "pdf") if _pdf_pede_senha(caminho) else None
        if zipfile.is_zipfile(caminho):
            formato = _odf_protegido(caminho)
            return (_decifrar_odf, formato) if formato else None
    except MemoryError:
        raise  # falta de memória não é arquivo ruim
    except Exception:  # noqa: BLE001 — malformado ou hostil: não é a detecção que recusa, é a leitura, com o motivo dela
        return None
    return None


# -------------------------------------------------------------------- PDF
def _pdf_pede_senha(caminho) -> bool:
    """Só o PDF com `/Encrypt` pode ter senha; ele abre pela PDFium sem senha e, se ela recusar por senha, é a de
    abertura (o que só tem senha de permissões abre)."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as c

    if not os.path.getsize(caminho):
        return False
    with open(caminho, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
        if m.find(b"/Encrypt") < 0:
            return False
    with PDFIUM:  # aberta e fechada dentro da trava, como em todo o resto
        try:
            pdfium.PdfDocument(caminho).close()
        except pdfium.PdfiumError as e:
            return e.err_code == c.FPDF_ERR_PASSWORD
    return False


def _decifrar_pdf(caminho, senha, destino, formato):
    """Uma cópia sem a criptografia, gravada pela PDFium com a senha certa."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as c

    with PDFIUM:
        try:
            doc = pdfium.PdfDocument(caminho, password=senha)
        except pdfium.PdfiumError as e:
            if e.err_code == c.FPDF_ERR_PASSWORD:
                raise SenhaIncorreta(formato) from None
            raise DocumentoProtegido(formato, "a PDFium não abriu o arquivo com a senha") from None
        try:
            doc.save(destino, flags=c.FPDF_REMOVE_SECURITY)
        finally:
            doc.close()


# -------------------------------------------------------------------- Office (OOXML cifrado e legado)
def _ole_protegido(caminho, ext) -> str | None:
    with olefile.OleFileIO(caminho) as ole:
        if ole.exists("EncryptionInfo") and ole.exists("EncryptedPackage"):
            return ext[1:] if ext in _OOXML else "ooxml"
    familia = _familia_legada(caminho)
    if familia is None:
        return None
    return ext[1:] if ext in _LEGADOS[familia] else familia


def _familia_legada(caminho) -> str | None:
    """doc, xls ou ppt, se o binário do Office está cifrado (o msoffcrypto lê só o cabeçalho que diz isso); None se
    não está ou se o arquivo não é um deles."""
    import msoffcrypto
    from msoffcrypto.format.doc97 import Doc97File
    from msoffcrypto.format.ppt97 import Ppt97File
    from msoffcrypto.format.xls97 import Xls97File

    familias = {Doc97File: "doc", Xls97File: "xls", Ppt97File: "ppt"}
    with open(caminho, "rb") as f:
        try:
            arquivo = msoffcrypto.OfficeFile(f)
            cifrado = arquivo.is_encrypted()
        except Exception:  # noqa: BLE001 — binário que o msoffcrypto não entende: a leitura decide o que fazer com ele
            return None
    return familias.get(type(arquivo)) if cifrado else None


def _decifrar_office(caminho, senha, destino, formato):
    import io

    import msoffcrypto
    from msoffcrypto import exceptions

    with open(caminho, "rb") as f:
        try:
            arquivo = msoffcrypto.OfficeFile(f)
        except Exception:  # noqa: BLE001 — a mensagem do msoffcrypto não volta: só o motivo, sem nada do arquivo
            raise DocumentoProtegido(formato, "criptografia que o Lince não decifra") from None
        if arquivo.format == "ooxml" and getattr(arquivo, "info", {}).get("spinValue", 0) > VOLTAS_MAX:
            raise DocumentoProtegido(formato, f"derivação da chave acima de {VOLTAS_MAX} voltas")
        saida = io.BytesIO()
        try:
            if arquivo.format == "ooxml":  # a senha conferida antes, e o pacote decifrado conferido pelo HMAC
                arquivo.load_key(password=senha, verify_password=True)
                arquivo.decrypt(saida, verify_integrity=True)
            else:  # os binários sempre conferem a senha ao carregar a chave
                arquivo.load_key(password=senha)
                arquivo.decrypt(saida)
        except exceptions.InvalidKeyError:
            raise SenhaIncorreta(formato) from None
        except Exception:  # noqa: BLE001 — cifra ou variante fora do que o msoffcrypto decifra (XOR do Word, por exemplo)
            raise DocumentoProtegido(formato, "criptografia que o Lince não decifra") from None
    with open(destino, "wb") as f:
        f.write(saida.getbuffer())


# -------------------------------------------------------------------- ODF
def _odf_protegido(caminho) -> str | None:
    conferir_zip(caminho)  # antes de abrir qualquer parte
    with zipfile.ZipFile(caminho) as z:
        nomes = set(z.namelist())
        if "META-INF/manifest.xml" not in nomes:
            return None
        manifesto = xml_ou_nada(z.read("META-INF/manifest.xml"))
        if manifesto is None or manifesto.find(f".//{_M}encryption-data") is None:
            return None
        tipo = z.read("mimetype").decode("ascii", "replace").strip() if "mimetype" in nomes else ""
    return _ODF.get(tipo, "odf")


def _decifrar_odf(caminho, senha, destino, formato):
    """Cada parte cifrada, decifrada (AES-256-CBC) e descompactada, num pacote novo com o manifesto sem a cifra. A senha
    é conferida pelo checksum da primeira parte, antes de decifrar o resto."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    with zipfile.ZipFile(caminho) as z:
        manifesto_xml = z.read("META-INF/manifest.xml")
        cifradas = _partes_cifradas(xml_ou_nada(manifesto_xml), formato)
        if sum(p["voltas"] for p in cifradas.values()) > VOLTAS_ODF_MAX:
            raise DocumentoProtegido(formato, f"derivação das chaves acima de {VOLTAS_ODF_MAX} voltas")
        conferida, total = False, 0
        with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as saida:
            for item in z.infolist():
                dados = z.read(item)
                if item.filename == "META-INF/manifest.xml":
                    dados = _sem_cifra(dados)
                elif (p := cifradas.get(item.filename)) is not None:
                    inicio = hashlib.new(p["inicio"], senha.encode("utf-8")).digest()
                    chave = hashlib.pbkdf2_hmac("sha1", inicio, p["sal"], p["voltas"], 32)
                    if len(dados) % 16:
                        raise DocumentoProtegido(formato, "parte cifrada corrompida")
                    decifrador = Cipher(algorithms.AES(chave), modes.CBC(p["iv"])).decryptor()
                    claro = decifrador.update(dados) + decifrador.finalize()
                    sobra = claro[-1] if claro else 0
                    if not conferida and not _checksum_confere(claro, sobra, p):
                        raise SenhaIncorreta(formato)
                    conferida = True
                    if not 1 <= sobra <= 16:  # a senha já conferiu: o enchimento errado é parte corrompida
                        raise DocumentoProtegido(formato, "parte cifrada corrompida")
                    dados = _descompactar(claro[:-sobra], formato, PARTE_MAX)
                    total += len(dados)
                    if total > DESCOMPACTADO_MAX:
                        raise DocumentoRecusado(f"pacote decifrado passa de {DESCOMPACTADO_MAX} bytes")
                tipo = zipfile.ZIP_STORED if item.filename == "mimetype" else zipfile.ZIP_DEFLATED
                saida.writestr(zipfile.ZipInfo(item.filename, date_time=item.date_time), dados, compress_type=tipo)


def _partes_cifradas(manifesto, formato) -> dict[str, dict]:
    """{parte: parâmetros da cifra} do manifesto. Cifra fora do ODF 1.2 (Blowfish do OpenOffice antigo, AES-GCM com
    Argon2 do ODF 1.4) fica protegida, com o motivo."""
    if manifesto is None:
        raise DocumentoProtegido(formato, "manifesto ilegível")
    partes = {}
    for entrada in manifesto.iter(f"{_M}file-entry"):
        cifra = entrada.find(f"{_M}encryption-data")
        if cifra is None:
            continue
        algoritmo, derivacao = cifra.find(f"{_M}algorithm"), cifra.find(f"{_M}key-derivation")
        inicio = cifra.find(f"{_M}start-key-generation")
        nome_inicio = inicio.get(f"{_M}start-key-generation-name") if inicio is not None else "SHA1"
        if (
            algoritmo is None
            or algoritmo.get(f"{_M}algorithm-name") != _AES256_CBC
            or derivacao is None
            or derivacao.get(f"{_M}key-derivation-name") != "PBKDF2"
            or derivacao.get(f"{_M}key-size", "32") != "32"
            or nome_inicio not in _INICIO
            or cifra.get(f"{_M}checksum-type") not in _CHECKSUM
        ):
            raise DocumentoProtegido(formato, "cifra do ODF fora do padrão ODF 1.2 (AES-256-CBC com PBKDF2)")
        try:
            voltas = int(derivacao.get(f"{_M}iteration-count", ""))
            parametros = {
                "iv": base64.b64decode(algoritmo.get(f"{_M}initialisation-vector", ""), validate=True),
                "sal": base64.b64decode(derivacao.get(f"{_M}salt", ""), validate=True),
                "checksum": base64.b64decode(cifra.get(f"{_M}checksum", ""), validate=True),
            }
        except ValueError:
            raise DocumentoProtegido(formato, "parâmetros da cifra ilegíveis no manifesto") from None
        if not 1 <= voltas <= VOLTAS_MAX:
            raise DocumentoProtegido(formato, f"derivação da chave fora de 1 a {VOLTAS_MAX} voltas")
        if len(parametros["iv"]) != 16:
            raise DocumentoProtegido(formato, "parâmetros da cifra ilegíveis no manifesto")
        parametros |= {
            "voltas": voltas,
            "inicio": _INICIO[nome_inicio],
            "hash_checksum": _CHECKSUM[cifra.get(f"{_M}checksum-type")],
        }
        partes[entrada.get(f"{_M}full-path", "")] = parametros
    if not partes:
        raise DocumentoProtegido(formato, "manifesto sem partes cifradas legíveis")
    return partes


def _checksum_confere(claro, sobra, p) -> bool:
    """O ODF 1.2 diz que o checksum é do primeiro 1 KB decifrado e ainda compactado; numa parte menor que isso, o
    enchimento pode entrar ou não conforme o programa que gravou: as duas formas valem (a senha errada não acerta
    nenhuma)."""
    candidatos = [claro[:1024]]
    if 1 <= sobra <= 16:
        candidatos.append(claro[:-sobra][:1024])
    return any(hashlib.new(p["hash_checksum"], c).digest() == p["checksum"] for c in candidatos)


def _descompactar(dados, formato, teto) -> bytes:
    """Deflate cru, com o teto de uma parte (limites.PARTE_MAX): a parte cifrada não passou pelo `conferir_zip`."""
    d = zlib.decompressobj(-15)
    try:
        saida = d.decompress(dados, teto)
    except zlib.error:
        raise DocumentoProtegido(formato, "parte cifrada corrompida") from None
    if d.unconsumed_tail:
        raise DocumentoRecusado(f"parte decifrada passa de {teto} bytes")
    return saida


def _sem_cifra(manifesto: bytes) -> bytes:
    """O manifesto sem os blocos `encryption-data`: o pacote decifrado não é mais reconhecido como protegido."""
    return re.sub(rb"<(\w+:|)encryption-data\b.*?</\1encryption-data>\s*", b"", manifesto, flags=re.S)
