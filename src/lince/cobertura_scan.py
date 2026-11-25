"""Sinal de volume de texto para scans sem camada textual confiável."""

import re
from itertools import groupby
from statistics import median

from PIL import Image, ImageChops, ImageFilter

from .texto import lixo

_LADO_MAX = 960  # a projeção não precisa dos pixels da imagem enviada à visão
_TRACOS = re.compile(rb"\xff+")
_MARCAS = re.compile(r"<!--[^<>]*-->|<[^<>]{1,256}>")


def limiar_de_texto(imagem):
    """Estima um limiar conservador de caracteres para indício de transcrição parcial.

    O contraste local elimina sombras do papel. Filetes longos e linhas soltas não
    contam. O sinal exige várias linhas de altura parecida, separadas por vales de
    tinta. Serve para achar déficits grandes, sem reconhecer palavras ou atestar
    a cobertura de um trecho específico dentro da imagem.
    """
    fator = min(1, _LADO_MAX / max(imagem.size))
    w, h = (max(1, round(n * fator)) for n in imagem.size)
    if min(w, h) < 80:
        return 0
    cinza = imagem.resize((w, h), Image.Resampling.BILINEAR, reducing_gap=2).convert("L")
    contraste = ImageChops.subtract(cinza.filter(ImageFilter.BoxBlur(7)), cinza)
    tinta = contraste.point([0] * 27 + [255] * (256 - 27)).tobytes()
    margem = w // 50
    max_traco = max(4, w // 50)
    contagem = [
        sum(len(m.group()) <= max_traco for m in _TRACOS.finditer(tinta[y * w + margem : (y + 1) * w - margem]))
        for y in range(h)
    ]
    # Soma sobre três fileiras para não tomar a abertura de um glifo por uma
    # separação entre linhas. Múltiplos vales acomodam papel pautado e manchado.
    projecao = [a + b + c for a, b, c in zip(contagem, contagem[1:], contagem[2:], strict=False)]
    picos = max(_picos_de_linhas(projecao, piso) for piso in (15, 27, 45))
    # Um caractere pode cruzar a mesma fileira mais de uma vez. Exigir apenas
    # metade dos picos dá margem para pontuação, diagramação e ruído do scan.
    return picos // 6 if picos >= 720 else 0


def _picos_de_linhas(projecao, piso):
    linhas = []
    for acima, parte in groupby(projecao, key=lambda n: n >= piso):
        if not acima:
            continue
        faixa = list(parte)
        pico = max(faixa)
        if 3 <= len(faixa) <= len(projecao) // 12 and pico >= max(36, 2 * piso):
            linhas.append((len(faixa), pico))
    if len(linhas) < 8:
        return 0
    altura = median(h for h, _ in linhas)
    # Faixas muito altas podem ser ornamentos, marcas ou grupos de linhas
    # colados pela projeção. Contá-las superestima o volume de texto.
    regulares = [pico for h, pico in linhas if altura / 2 <= h <= 1.5 * altura]
    return sum(regulares) if len(regulares) >= 8 else 0


def parece_parcial(resposta, limiar):
    """Markdown e tags do formato de saída não aumentam a contagem do texto."""
    if not limiar or not resposta.strip():
        return False
    texto = _MARCAS.sub(" ", resposta)
    return sum(letra.isalnum() for letra in texto) < limiar


def texto_digital_fora_do_raster(imagem, itens, objetos_texto, rasters, w, h, rotacao, tique):
    """Devolve o texto conferido fora da união dos rasters, em ordem de exibição."""
    # A busca de objetos pelas posições é para rótulos esparsos de página mista.
    if not rasters or not 1 <= len(objetos_texto) <= 128:
        return []

    orientacao = rotacao % 360 // 90
    altura = w if orientacao % 2 else h
    digital = []

    def y_exibido(x, y):
        return (h - y, x, y, w - x)[orientacao] / altura

    for item in itens:
        tique()
        if item.render_mode != 0 or item.text.startswith("[Image:") or lixo(item.text) > 0.02:
            continue
        # O inspetor dá a altura da fonte, a PDFium dá a caixa efetiva. Um rodapé
        # junto do raster pode estar inteiramente fora dele mesmo assim.
        folga = max(1, 0.2 * item.height)
        objetos = [
            ((l, b, r, t), cor)
            for (l, b, r, t), cor in objetos_texto
            if abs(l - item.x) <= folga
            and abs(r - item.x - item.width) <= folga
            and b <= item.y + folga
            and t >= item.y
            and (item.fill_color is None or cor == item.fill_color)
        ]
        if len(objetos) != 1:
            continue
        (l, b, r, t), (vermelho, verde, azul) = objetos[0]
        if not (0 <= l < r <= w and 0 <= b < t <= h):
            continue
        # A caixa conjunta dos rasters também cobre vãos e margens em que o
        # PDF pode ter texto independente, como a assinatura entre dois scans.
        if any(max(l, il) < min(r, ir) and max(b, ib) < min(t, it) for il, ib, ir, it in rasters):
            continue
        # Confirma diferença de tinta na caixa. Um objeto declarado como pintado
        # também pode estar transparente, sob outro objeto ou sem contraste.
        box = (
            int(l * imagem.width / w),
            int((h - t) * imagem.height / h),
            round(r * imagem.width / w),
            round((h - b) * imagem.height / h),
        )
        if box[0] >= box[2] or box[1] >= box[3]:
            continue
        hist = imagem.crop(box).convert("L").histogram()
        fundo_local = max(range(256), key=hist.__getitem__)
        cor = (299 * vermelho + 587 * verde + 114 * azul) // 1000
        if abs(fundo_local - cor) >= 30 and sum(hist[max(0, cor - 20) : cor + 21]) >= 3:
            x, y = (l + r) / 2, (b + t) / 2
            horizontal = (x, y, w - x, h - y)[orientacao]
            inicio, fim = sorted((y_exibido(l, b), y_exibido(r, t)))
            digital.append((inicio, horizontal, fim, item.text.strip()))
    return [(inicio, fim, texto) for inicio, _, fim, texto in sorted(digital)]
