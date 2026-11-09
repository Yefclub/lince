"""Limites de entrada: documento hostil (zip bomb, XML com expansão de entidades) é recusado antes de ser aberto.

Office, ODF e EPUB são zips; quem lê o conteúdo (anydoc, python-pptx, openpyxl, xml.etree) descompacta tudo
em memória. Os tetos ficam muito acima do que documentos reais usam: na bateria de testes, a maior parte
descompactada tem 0,4 MB e a maior razão de compressão é 32.

O tamanho conferido é o declarado no zip. Uma parte que descompacte mais do que declara não passa: o zipfile do
Python para no tamanho declarado e acusa CRC errado, e o anydoc tem teto próprio por parte.
"""

import os
import pyexpat
import shutil
import zipfile

from PIL import Image

ITENS_MAX = 10_000  # partes dentro do zip; um PPTX grande tem algumas centenas
DESCOMPACTADO_MAX = 256 * 1024 * 1024  # 256 MiB somando todas as partes
PARTE_MAX = 128 * 1024 * 1024  # 128 MiB numa parte só (o mesmo teto do anydoc)
RAZAO_MAX = 200  # descompactado / compactado, só em parte acima de RAZAO_A_PARTIR
RAZAO_A_PARTIR = 1024 * 1024  # XML pequeno e repetitivo comprime muito e é legítimo
# Resultado esperando entrega por token, padrão. Um documento pesado retém de ~70 a ~170 MB (o arquivo enviado, até
# 100 MB, e depois as imagens do modo tarefas, que o cliente pede até 64 MB): 1 GiB cabe ~6 desses ao mesmo tempo no
# pior caso, 4x o teto anterior (256 MB), batido com 2. Fica em disco (servidor.py), não na memória do processo; cada
# token pode ter o seu na LINCE_TOKENS (`nome:token:teto`).
RETIDOS_POR_TOKEN = 1024 * 1024 * 1024
LADO_MAX_PX = 10_000  # renderização de página de PDF: MediaBox gigante não vira imagem de gigabytes
# Processos da fase 1 do PDF (paralelo.py), no máximo: um por CPU disponível até este teto. Cada processo tem o
# documento aberto e uma página renderizada por vez (~100 a ~300 MB numa página pesada): 8 cabem em ~2,5 GB, e acima
# disso a fase 1 de um documento passa a esperar mais o pdf_inspector de cada faixa (em série) do que as páginas.
PROCESSOS_PDF_MAX = 8
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


def com_barras_normais(caminho, pasta) -> str:
    """O pacote com `\\` nos nomes internos (`xl\\workbook.xml`, gravado por alguns exportadores de ERP; o Excel e o
    LibreOffice abrem) regravado com `/` numa cópia em `pasta`, com o mesmo nome do arquivo; sem `\\`, o próprio
    caminho. Chamar depois de `conferir_zip`, que já recusa caminho absoluto, `..` e os tetos com os nomes trocados;
    aqui ainda é recusado o nome que fica repetido depois da troca."""
    with zipfile.ZipFile(caminho) as z:
        itens = z.infolist()
        # o nome gravado: no Windows, o zipfile já troca `\\` por `/` em `filename`, e o anydoc lê os bytes crus
        if not any("\\" in i.orig_filename for i in itens):
            return caminho
        nomes = [i.orig_filename.replace("\\", "/") for i in itens]
        if len(set(nomes)) != len(nomes):
            raise DocumentoRecusado("parte repetida no pacote depois de trocar \\ por /")
        os.makedirs(destino := os.path.join(pasta, "pacote"), exist_ok=True)
        novo = os.path.join(destino, os.path.basename(caminho))
        with zipfile.ZipFile(novo, "w") as saida:
            for item, nome in zip(itens, nomes, strict=True):
                copia = zipfile.ZipInfo(nome, date_time=item.date_time)
                copia.compress_type, copia.external_attr = item.compress_type, item.external_attr
                with z.open(item) as origem, saida.open(copia, "w") as alvo:
                    shutil.copyfileobj(origem, alvo)
    return novo


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
