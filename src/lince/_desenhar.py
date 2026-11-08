"""Processo filho que desenha SVGs pelo resvg (chamado por desenho_svg.desenhar_lote, nunca importado).

Roda à parte porque a chamada ao resvg não pode ser interrompida: um filtro hostil prende a CPU por minutos, e só
matar o processo corta. Lê da entrada um JSON com [svg, lado, destino] por desenho e escreve uma linha por desenho
concluído ("indice resultado"), na hora, para quem chama saber o que terminou antes de um corte.
"""

import io
import json
import sys

MEMORIA_MAX = 2 * 1024**3  # 2 GiB: o desenho de 1600 px usa ~10 MB; acima disso é documento hostil


def main():
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (MEMORIA_MAX, MEMORIA_MAX))
    except (ImportError, ValueError, OSError):  # sem o módulo (Windows) ou sem permissão: segue sem o teto
        pass
    import resvg_py
    from PIL import Image

    for i, (svg, lado, destino) in enumerate(json.load(sys.stdin)):
        try:
            png = resvg_py.svg_to_bytes(svg_string=svg, background="#ffffff", dpi=96.0, **lado)
            im = Image.open(io.BytesIO(bytes(png))).convert("RGB")
            if Image.eval(im, lambda v: 255 - v).getbbox() is None:  # tudo branco: nada a ler
                resultado = "branco"
            else:
                im.save(destino)
                resultado = "lido"
        except (ValueError, OSError, MemoryError):
            resultado = "ilegivel"
        print(i, resultado, flush=True)


if __name__ == "__main__":
    main()
