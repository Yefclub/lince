"""Limites de entrada: documento hostil (zip bomb, XML com expansão de entidades) é recusado antes de ser aberto.

Office, ODF e EPUB são zips; quem lê o conteúdo (anydoc, python-pptx, openpyxl, xml.etree) descompacta tudo
em memória. Os tetos ficam muito acima do que documentos reais usam: na bateria de testes, a maior parte
descompactada tem 0,4 MB e a maior razão de compressão é 32.
"""

import pyexpat
import zipfile

ITENS_MAX = 10_000  # partes dentro do zip; um PPTX grande tem algumas centenas
DESCOMPACTADO_MAX = 1024 * 1024 * 1024  # 1 GiB somando todas as partes
PARTE_MAX = 512 * 1024 * 1024  # 512 MiB numa parte só
RAZAO_MAX = 200  # descompactado / compactado, só em parte acima de RAZAO_A_PARTIR
RAZAO_A_PARTIR = 1024 * 1024  # XML pequeno e repetitivo comprime muito e é legítimo

# expat < 2.4.1 é vulnerável a expansão exponencial de entidades ("billion laughs"); xml.etree usa o expat do sistema
if pyexpat.version_info < (2, 4, 1):
    raise ImportError(f"expat {pyexpat.EXPAT_VERSION} é antigo demais: atualize para 2.4.1 ou mais novo")


class DocumentoRecusado(ValueError):
    """O arquivo passa dos limites de segurança e não é lido."""


def conferir_zip(caminho) -> None:
    """Recusa zip que descompacta demais, tem partes demais ou caminho fora do pacote. Não extrai nada."""
    try:
        with zipfile.ZipFile(caminho) as z:
            itens = z.infolist()
    except zipfile.BadZipFile as e:
        raise DocumentoRecusado(f"pacote zip corrompido: {e}") from None
    if len(itens) > ITENS_MAX:
        raise DocumentoRecusado(f"{len(itens)} partes no pacote (máximo {ITENS_MAX})")
    total = 0
    for i in itens:
        nome = i.filename.replace("\\", "/")
        if nome.startswith("/") or ".." in nome.split("/"):
            raise DocumentoRecusado(f"parte com caminho fora do pacote: {i.filename[:80]}")
        if i.file_size > PARTE_MAX:
            raise DocumentoRecusado(f"parte {i.filename[:80]} descompacta {i.file_size} bytes (máximo {PARTE_MAX})")
        if i.file_size > RAZAO_A_PARTIR and i.file_size / max(i.compress_size, 1) > RAZAO_MAX:
            raise DocumentoRecusado(f"parte {i.filename[:80]} comprimida demais (padrão de zip bomb)")
        total += i.file_size
    if total > DESCOMPACTADO_MAX:
        raise DocumentoRecusado(f"pacote descompacta {total} bytes (máximo {DESCOMPACTADO_MAX})")


def xml_ou_nada(dados: bytes):
    """Árvore XML da parte, ou None se ela for malformada ou hostil (o expat barra expansão de entidades).

    Parte ruim não derruba a leitura do documento: quem chama pula a parte e registra que ela ficou de fora.
    """
    from xml.etree import ElementTree as ET

    try:
        return ET.fromstring(dados)
    except ET.ParseError:
        return None
