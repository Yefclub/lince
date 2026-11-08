"""Processo filho que desenha SVG pelo resvg e EMF/WMF pelo metafile-render (chamado por desenho_svg.desenhar_lote,
nunca importado).

Roda à parte porque a chamada ao desenhista não pode ser interrompida: um filtro hostil prende a CPU por minutos, e
só matar o processo corta. Lê da entrada um JSON com [tipo, conteúdo, lado, destino] por desenho (o EMF/WMF vai em
base64) e escreve uma linha por desenho concluído ("indice resultado"), na hora, para quem chama saber o que terminou
antes de um corte.
"""

import base64
import contextlib
import io
import json
import os
import sys

MEMORIA_MAX = 2 * 1024**3  # 2 GiB: o desenho de 1600 px usa ~10 MB; acima disso é documento hostil
LADO_PX = 1600  # o lado maior do EMF/WMF desenhado, como o do SVG


_DIRETORIOS_DE_FONTE = ("/usr/share/fonts", "/usr/local/share/fonts", "/System/Library/Fonts", "/Library/Fonts",
                        "C:/Windows/Fonts", "~/.fonts", "~/.local/share/fonts")  # fmt: skip


def opcoes_de_fonte(diretorios=_DIRETORIOS_DE_FONTE, pasta=None) -> dict:
    """Sem fonte nenhuma no sistema (container mínimo, Alpine), o resvg não desenha texto: o rótulo do diagrama some
    do desenho. Aí entra a fonte que o Pillow traz embutida (Aileron, domínio público; só ASCII, então acento sai
    sem glifo) como padrão de todas as famílias. O texto do SVG continua no corpo de qualquer forma. O arquivo da
    fonte vai para `pasta` (a do documento, apagada no fim da leitura, mesmo que o filho seja morto pelo teto de
    tempo). Qualquer falha aqui devolve {}: o desenho sai como sairia sem a reserva, nunca pior."""

    try:
        for d in diretorios:
            for _, _, arquivos in os.walk(os.path.expanduser(d)):
                if any(a.lower().endswith((".ttf", ".otf", ".ttc")) for a in arquivos):
                    return {}
        import base64
        import inspect
        import re
        import tempfile

        from PIL import ImageFont

        embutida = re.search(r'b"""(.*?)"""', inspect.getsource(ImageFont.load_default), re.S)
        if embutida is None:
            return {}
        fd, caminho = tempfile.mkstemp(prefix="lince-fonte-", suffix=".ttf", dir=pasta)  # nome único, só nosso
        with os.fdopen(fd, "wb") as f:
            f.write(base64.b64decode(embutida.group(1)))
    except Exception:  # noqa: BLE001 — sem a reserva, o desenho sai sem texto, como antes
        return {}
    familias = (
        "font_family",
        "serif_family",
        "sans_serif_family",
        "cursive_family",
        "fantasy_family",
        "monospace_family",
    )
    return {"font_files": [caminho], **dict.fromkeys(familias, "Aileron")}


def _png(tipo, conteudo, lado, fontes=None):
    if tipo == "svg":
        import resvg_py

        return bytes(
            resvg_py.svg_to_bytes(svg_string=conteudo, background="#ffffff", dpi=96.0, **lado, **(fontes or {}))
        )
    from metafile_render import render_metafile

    return render_metafile(base64.b64decode(conteudo), output_format="png").data


def main():
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (MEMORIA_MAX, MEMORIA_MAX))
    except (ImportError, ValueError, OSError):  # sem o módulo (Windows) ou sem permissão: segue sem o teto
        pass
    from PIL import Image

    fontes = None  # decidido no primeiro SVG; o arquivo da fonte de reserva sai no fim
    try:
        for i, (tipo, conteudo, lado, destino) in enumerate(json.load(sys.stdin)):
            try:
                if tipo == "svg" and fontes is None:
                    fontes = opcoes_de_fonte(pasta=os.path.dirname(destino) or None)
                im = Image.open(io.BytesIO(_png(tipo, conteudo, lado, fontes)))
                fundo = Image.new("RGBA", im.size, "#ffffff")  # transparência vira branco, como no SVG
                im = Image.alpha_composite(fundo, im.convert("RGBA")).convert("RGB")
                im.thumbnail((LADO_PX, LADO_PX))
                if Image.eval(im, lambda v: 255 - v).getbbox() is None:  # tudo branco: nada a ler
                    resultado = "branco"
                else:
                    im.save(destino)
                    resultado = "lido"
            except Exception:  # noqa: BLE001 — desenhista com arquivo hostil levanta de tudo (memória, pelo teto)
                resultado = "ilegivel"
            print(i, resultado, flush=True)
    finally:
        for caminho in (fontes or {}).get("font_files", []):
            with contextlib.suppress(OSError):
                os.unlink(caminho)


if __name__ == "__main__":
    main()
