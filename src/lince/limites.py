"""Limites de entrada: documento hostil (zip bomb, XML com expansão de entidades) é recusado antes de ser aberto.

Office, ODF e EPUB são zips; quem lê o conteúdo (anydoc, python-pptx, openpyxl, xml.etree) descompacta tudo
em memória. Os tetos ficam muito acima do que documentos reais usam: na bateria de testes, a maior parte
descompactada tem 0,4 MB e a maior razão de compressão é 32.

O tamanho conferido é o declarado no zip. Uma parte que descompacte mais do que declara não passa: o zipfile do
Python para no tamanho declarado e acusa CRC errado, e o anydoc tem teto próprio por parte.
"""

import pyexpat
import zipfile

from PIL import Image

ITENS_MAX = 10_000  # partes dentro do zip; um PPTX grande tem algumas centenas
DESCOMPACTADO_MAX = 256 * 1024 * 1024  # 256 MiB somando todas as partes
PARTE_MAX = 128 * 1024 * 1024  # 128 MiB numa parte só (o mesmo teto do anydoc)
RAZAO_MAX = 200  # descompactado / compactado, só em parte acima de RAZAO_A_PARTIR
RAZAO_A_PARTIR = 1024 * 1024  # XML pequeno e repetitivo comprime muito e é legítimo
LADO_MAX_PX = 10_000  # renderização de página de PDF: MediaBox gigante não vira imagem de gigabytes
Image.MAX_IMAGE_PIXELS = 40_000_000  # o Pillow recusa acima do dobro (80 Mpx, ~240 MB em RGB)

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
    total = compactado = 0
    for i in itens:
        nome = i.filename.replace("\\", "/")
        if nome.startswith("/") or ".." in nome.split("/"):
            raise DocumentoRecusado(f"parte com caminho fora do pacote: {i.filename[:80]!r}")
        if i.file_size > PARTE_MAX:
            raise DocumentoRecusado(f"parte {i.filename[:80]!r} descompacta {i.file_size} bytes (máximo {PARTE_MAX})")
        if i.file_size > RAZAO_A_PARTIR and i.file_size / max(i.compress_size, 1) > RAZAO_MAX:
            raise DocumentoRecusado(f"parte {i.filename[:80]!r} comprimida demais (padrão de zip bomb)")
        total += i.file_size
        compactado += i.compress_size
    if total > DESCOMPACTADO_MAX:
        raise DocumentoRecusado(f"pacote descompacta {total} bytes (máximo {DESCOMPACTADO_MAX})")
    if total > RAZAO_A_PARTIR and total / max(compactado, 1) > RAZAO_MAX:  # muitas partes pequenas comprimidas demais
        raise DocumentoRecusado("pacote comprimido demais no conjunto (padrão de zip bomb)")


def escala(largura_pt: float, altura_pt: float, desejada: float) -> float:
    """Escala de renderização de página limitada a LADO_MAX_PX no maior lado."""
    return min(desejada, LADO_MAX_PX / max(largura_pt, altura_pt, 1))


def xml_ou_nada(dados: bytes):
    """Árvore XML da parte, ou None se ela for malformada ou hostil (o expat barra expansão de entidades).

    Parte ruim não derruba a leitura do documento: quem chama pula a parte e registra que ela ficou de fora.
    """
    from xml.etree import ElementTree as ET

    try:
        return ET.fromstring(dados)
    except ET.ParseError:
        return None
