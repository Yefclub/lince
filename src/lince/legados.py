"""Metadados dos formatos binários e do RTF, que o leitor do corpo não entrega: cabeçalho e rodapé (DOC, XLS, PPT,
RTF), abas ocultas (XLS) e macros (VBA no PPT, folha de macro do Excel 4.0 no XLS).

Leitura defensiva: arquivo hostil ou fora do padrão nunca derruba a leitura do documento; quem chama trata a exceção
e segue sem esses metadados. Os laços andam sobre os bytes, sem recursão, e nunca passam do fim do stream; todo o
trabalho (bytes percorridos, copiados e decodificados, textos guardados) sai de um `Orcamento` proporcional ao
stream, então nenhuma estrutura sobreposta ou repetida multiplica o custo."""

import codecs
import hashlib
import itertools
import re
import struct

_BOF, _EOF, _FILEPASS, _BOUNDSHEET, _DIMENSIONS = 0x0809, 0x000A, 0x002F, 0x0085, 0x0200
_HEADER, _FOOTER, _CODEPAGE, _HEADER_FOOTER = 0x0014, 0x0015, 0x0042, 0x089C
FOLHA_DE_MACRO, MODULO_VBA = 1, 6  # BOUNDSHEET.dt: folha de macro do Excel 4.0 e módulo do Excel 5
_REGISTROS_POR_ABA = 4096  # o cabeçalho fica no bloco de configuração da página, antes das células
_CODEPAGES = {367: "ascii", 1200: "utf-16-le", 10000: "mac_roman", 32768: "mac_roman", 32769: "cp1252"}
_FATOR = 2  # arquivo legítimo percorre cada região uma vez; o dobro sobra para diretórios e cabeçalhos de registro
_FOLGA = 64 * 1024
_TEXTO_MAX = 1024  # caracteres por cabeçalho ou rodapé (o Excel limita a 255; o PowerPoint, na prática, a poucos)
_TEXTOS_MAX = 1024  # cabeçalhos e rodapés distintos por arquivo


class Orcamento:
    """Teto de trabalho de um arquivo. Estourar é estrutura hostil (regiões sobrepostas, cadeias repetidas): levanta
    ValueError, e quem chama registra os metadados como ilegíveis em vez de gastar memória e CPU sem limite."""

    def __init__(self, tamanho):
        self.bytes, self.textos = _FATOR * tamanho + _FOLGA, _TEXTOS_MAX

    def gastar(self, n):
        self.bytes -= n
        if self.bytes < 0:
            raise ValueError("estrutura sobreposta ou repetida: orçamento de leitura esgotado")

    def guardar(self, bordas: dict, texto: str):
        if texto not in bordas:
            self.textos -= 1
            if self.textos < 0:
                raise ValueError("cabeçalhos e rodapés distintos demais")
            bordas[texto] = None


def ler_stream(o, nome, teto):
    """Bytes do stream OLE. Tamanho declarado acima do próprio arquivo é estrutura hostil (o olefile só registra
    o defeito e seguiria a cadeia de setores): recusa antes de ler."""
    if o.get_size(nome) > teto:
        raise ValueError(f"stream {nome} declara mais bytes que o arquivo")
    return o.openstream(nome).read()


# ---------- Excel 97-2003 (BIFF8) e Excel 5/95 (BIFF5) ----------


def _registros(b, i, orcamento):
    """(tipo, dados) de cada registro a partir de `i`; cada registro (cabeçalho e cópia dos dados) sai do orçamento."""
    while i + 4 <= len(b):
        tipo, tam = struct.unpack_from("<HH", b, i)
        orcamento.gastar(4 + tam)
        yield tipo, b[i + 4 : i + 4 + tam]
        i += 4 + tam


def _texto_xls(d, i, codificacao, cch_bytes):
    """(string, posição seguinte) do BIFF. No BIFF8 (`codificacao` None) vem a flag: 16 bits, ou só o byte baixo
    (latin-1); no BIFF5, os bytes na página de código do registro CODEPAGE."""
    if len(d) < i + cch_bytes:
        return "", len(d)
    cch = d[i] if cch_bytes == 1 else struct.unpack_from("<H", d, i)[0]
    i += cch_bytes
    n = min(cch, _TEXTO_MAX)
    if codificacao is None:
        alto = i < len(d) and d[i] & 1
        i += 1
        if alto:
            return d[i : i + 2 * n].decode("utf-16-le", "replace"), i + 2 * cch
        return d[i : i + n].decode("latin-1"), i + cch
    return d[i : i + n].decode(codificacao, "replace"), i + cch


def _codificacao_biff5(cp):
    nome = _CODEPAGES.get(cp, f"cp{cp}")
    try:
        return codecs.lookup(nome).name
    except LookupError:
        return "cp1252"


def _header_footer_2007(d):
    """Registro HeaderFooter (MS-XLS 2.4.137), que o Excel 2007+ grava: cabeçalho e rodapé das páginas pares e da
    primeira página, depois de 38 bytes fixos (frtHeader, guidSView, flags e os quatro cch)."""
    if len(d) < 38:
        return []
    textos, i = [], 38
    for cch in struct.unpack_from("<4H", d, 30):
        if cch:
            texto, i = _texto_xls(d, i, None, 2)
            textos.append(texto)
    return textos


def xls(o, teto) -> dict:
    """Cabeçalho e rodapé de cada aba, abas ocultas e se há folha de macro (Excel 4.0) ou módulo VBA do Excel 5."""
    b = ler_stream(o, "Workbook" if o.exists("Workbook") else "Book", teto)
    if len(b) < 8 or struct.unpack_from("<H", b, 0)[0] != _BOF:
        return {}
    codificacao = None if struct.unpack_from("<H", b, 4)[0] == 0x0600 else "cp1252"  # BOF.vers: BIFF8 ou não
    orcamento = Orcamento(len(b))
    abas, bordas, saida = set(), {}, {"ocultas": [], "macro": False}
    for tipo, d in _registros(b, 0, orcamento):
        if tipo == _FILEPASS:
            return {}  # protegida por senha: o resto do stream é cifrado
        if tipo == _EOF:
            break
        if tipo == _CODEPAGE and len(d) >= 2 and codificacao:
            codificacao = _codificacao_biff5(struct.unpack_from("<H", d, 0)[0])
        if tipo == _BOUNDSHEET and len(d) >= 7:
            abas.add(struct.unpack_from("<I", d, 0)[0])
            nome_aba = _texto_xls(d, 6, codificacao, 1)[0]
            saida.setdefault("abas", []).append({"nome": nome_aba, "oculta": bool(d[4] & 3)})
            if d[4] & 3:  # hsState: 1 oculta, 2 muito oculta (só macro reexibe)
                saida["ocultas"].append(nome_aba)
            if d[5] in (FOLHA_DE_MACRO, MODULO_VBA):
                saida["macro"] = True
    for pos in sorted(abas):
        lidos = 0
        for tipo, d in _registros(b, pos, orcamento):
            lidos += 1
            if tipo in (_DIMENSIONS, _EOF) or lidos > _REGISTROS_POR_ABA:
                break
            if tipo in (_HEADER, _FOOTER) and d:
                orcamento.guardar(bordas, _texto_xls(d, 0, codificacao, 1 if codificacao else 2)[0])
            elif tipo == _HEADER_FOOTER:
                for texto in _header_footer_2007(d):
                    orcamento.guardar(bordas, texto)
    saida["bordas"] = list(bordas)
    return saida


# ---------- Imagens dos binários (OfficeArt) ----------

# BLIP do OfficeArt (o armazém de imagens do Word, do Excel e do PowerPoint 97-2003): tipo do registro -> (MIME,
# instância com um UID, bytes de cabeçalho depois do UID). A instância seguinte indica um segundo UID.
_BLIPS = {0xF01A: ("image/x-emf", (0x3D4,), 34), 0xF01B: ("image/x-wmf", (0x216,), 34),
          0xF01C: ("image/pict", (0x542,), 34), 0xF01D: ("image/jpeg", (0x46A, 0x6E2), 1),
          0xF02A: ("image/jpeg", (0x46A, 0x6E2), 1), 0xF01E: ("image/png", (0x6E0,), 1),
          0xF01F: ("image/bmp", (0x7A8,), 1), 0xF029: ("image/tiff", (0x6E4,), 1)}  # fmt: skip
# o tipo do registro (byte baixo, depois 0xF0) de cada BLIP: a busca anda em C, não byte a byte
_CANDIDATO = re.compile(rb"[\x1a-\x1f\x29\x2a]\xf0")
_MSODRAWINGGROUP, _CONTINUE = 0x00EB, 0x003C
IMAGENS_MAX = 512  # imagens distintas por arquivo; acima disso é arquivo hostil ou repetição sem fim
_METAFILE_MAX = 64 * 1024 * 1024  # EMF/WMF guardado comprimido: teto do descomprimido
_PEDACO = 1024 * 1024  # a descompressão anda neste passo, cobrando o orçamento a cada pedaço
# onde o OfficeArt guarda as imagens: Pictures no PowerPoint, o Workbook (ou Book, no Excel 5) e, no Word, o Data
# (imagens em linha), o WordDocument (o armazém das flutuantes) e a tabela, onde há gerador que grava o bloco
_STREAMS_DE_IMAGEM = ("Pictures", "Workbook", "Book", "Data", "WordDocument", "0Table", "1Table")
_CABECALHOS_DIB = {12, 40, 52, 56, 64, 108, 124}  # BITMAPCOREHEADER, INFO, V2, V3, OS/2 2, V4 e V5


def _blips(b, orcamento):
    """(MIME, bytes) de cada BLIP do OfficeArt achado em `b`. O cabeçalho do registro é conferido inteiro (versão 0,
    instância do tipo, tamanho dentro do stream) antes de aceitar; metafile comprimido é descomprimido dentro do
    orçamento, e o que ele produz é cobrado mesmo quando a imagem é recusada."""
    pos = 0
    while m := _CANDIDATO.search(b, pos):
        k, pos = m.start() - 2, m.start() + 1
        if k < 0 or k + 8 > len(b):
            continue
        vi, tipo, tam = struct.unpack_from("<HHI", b, k)
        mime, instancias, extra = _BLIPS[tipo]
        if vi & 0xF or k + 8 + tam > len(b):
            continue
        dois_uids = (vi >> 4) - 1 in instancias
        if vi >> 4 not in instancias and not dois_uids:
            continue
        cab = 16 * (2 if dois_uids else 1) + extra
        if cab > tam:
            continue
        orcamento.gastar(tam)
        dados = b[k + 8 + cab : k + 8 + tam]
        pos = k + 8 + tam
        if extra == 34 and b[k + 8 + cab - 2] == 0 and (dados := _inflar(dados, orcamento)) is None:
            continue  # metafile com DEFLATE quebrado ou além do teto
        if mime == "image/bmp" and (dados := _bmp_do_dib(dados)) is None:
            continue
        yield mime, dados


def _inflar(dados, orcamento) -> bytes | None:
    """Metafile com DEFLATE, descomprimido em pedaços, cada um cobrado do orçamento antes do seguinte: um fluxo que
    quebra depois de muita saída já pagou o que produziu. None se quebrado ou além de _METAFILE_MAX."""
    import zlib

    d, partes, total = zlib.decompressobj(), [], 0
    while True:
        try:
            pedaco = d.decompress(dados, _PEDACO)
        except zlib.error:
            return None
        orcamento.gastar(len(pedaco))
        total += len(pedaco)
        if total > _METAFILE_MAX:
            return None  # bomba
        partes.append(pedaco)
        if not (dados := d.unconsumed_tail):
            break
    try:
        partes.append(d.flush())  # o resto que o zlib guardou: no máximo uma cópia pendente
    except zlib.error:
        return None
    return b"".join(partes) if d.eof else None  # fluxo cortado antes do fim não é metafile válido


def _bmp_do_dib(dib: bytes) -> bytes | None:
    """O OfficeArt guarda o bitmap sem o cabeçalho de arquivo BMP: o Pillow só abre com ele na frente. Cabeçalho
    fora do padrão devolve None. O início dos pixels conta a paleta e, no cabeçalho de 40 bytes com BI_BITFIELDS,
    as máscaras de cor que vêm depois dele."""
    if len(dib) < 16 or (tam_cab := struct.unpack_from("<I", dib, 0)[0]) not in _CABECALHOS_DIB or tam_cab > len(dib):
        return None
    if tam_cab == 12:  # BITMAPCOREHEADER: paleta de 3 bytes por cor
        bits = struct.unpack_from("<H", dib, 10)[0]
        paleta = 3 * (1 << bits) if bits <= 8 else 0
    else:
        bits = struct.unpack_from("<H", dib, 14)[0]
        compressao = struct.unpack_from("<I", dib, 16)[0] if tam_cab >= 20 else 0
        cores = struct.unpack_from("<I", dib, 32)[0] if tam_cab >= 36 else 0
        if bits <= 8:
            cores = cores if 0 < cores <= 1 << bits else 1 << bits
        elif cores > 1 << 16:
            return None
        mascaras = {3: 12, 6: 16}.get(compressao, 0) if tam_cab == 40 else 0
        paleta = 4 * cores + mascaras
    if 14 + tam_cab + paleta > 14 + len(dib):
        return None
    return b"BM" + struct.pack("<IHHI", 14 + len(dib), 0, 0, 14 + tam_cab + paleta) + dib


def _desenhos_xls(b, orcamento) -> bytes:
    """O armazém de imagens do Excel vem em MSODRAWINGGROUP, partido em CONTINUE a cada 8 KB: remonta os pedaços."""
    partes, juntando = [], False
    for tipo, d in _registros(b, 0, orcamento):
        if tipo == _MSODRAWINGGROUP:
            partes.append(d)
            juntando = True
        elif tipo == _CONTINUE and juntando:
            partes.append(d)
        else:
            juntando = False
    return b"".join(partes)


def imagens(o, teto) -> tuple[list[tuple[str, bytes]], str | None]:
    """Imagens distintas do .doc, .xls ou .ppt (armazém OfficeArt), na ordem em que estão gravadas, e o motivo do
    corte quando a busca parou antes do fim (teto de imagens ou orçamento esgotado); o que já foi achado fica. A
    posição no documento não é recuperada: quem chama as põe juntas depois do texto."""
    vistas, saida = set(), []
    for nome in _STREAMS_DE_IMAGEM:  # nomes fixos: um arquivo com milhares de streams não multiplica o trabalho
        if not o.exists(nome):
            continue
        try:
            b = ler_stream(o, nome, teto)
            orcamento = Orcamento(len(b))
            if nome in ("Workbook", "Book") and len(b) >= 8 and struct.unpack_from("<H", b, 0)[0] == _BOF:
                b = _desenhos_xls(b, orcamento)
            for mime, dados in _blips(b, orcamento):
                if (marca := hashlib.sha256(dados).digest()) not in vistas and dados:
                    if len(saida) >= IMAGENS_MAX:  # há mais uma: só agora é corte
                        return saida, f"mais de {IMAGENS_MAX} imagens: o resto ficou de fora"
                    vistas.add(marca)
                    saida.append((mime, dados))
        except (ValueError, OSError, TypeError) as e:  # stream hostil, cadeia de setores cortada, storage no lugar
            return saida, f"{type(e).__name__}: {e}"[:200]  # do stream: o que já foi achado fica
    return saida, None


# ---------- Gráficos do Excel 97-2003 ----------

_CHART_BOF, _TEXT, _SERIESTEXT, _OBJECTLINK, _SERIES, _BEGIN, _END = (
    0x0020,
    0x1025,
    0x100D,
    0x1027,
    0x1003,
    0x1033,
    0x1034,
)
_TIPOS_XLS = {0x1017: "barras", 0x1018: "linhas", 0x1019: "pizza", 0x101A: "área", 0x101B: "dispersão",
              0x103E: "radar", 0x103F: "superfície", 0x1040: "radar"}  # fmt: skip
GRAFICOS_XLS_MAX = 256  # gráficos por arquivo; um painel real tem dezenas
_SERIES_XLS_MAX = 64


def graficos_xls(o, teto) -> list[dict]:
    """Tipo, título e nomes das séries de cada gráfico do .xls (subfluxo BIFF de gráfico, embutido na aba ou em
    folha própria). Os valores ficam nas células da planilha, que o corpo já lê. Andando registro a registro com o
    orçamento do stream; cada SERIESTEXT é classificado pelo bloco em que está (BEGIN/END depois de SERIES ou TEXT), e
    o do TEXT ligado ao gráfico (OBJECTLINK com wLinkObj = 1) é o título."""
    b = ler_stream(o, "Workbook" if o.exists("Workbook") else "Book", teto)
    if len(b) < 8 or struct.unpack_from("<H", b, 0)[0] != _BOF or struct.unpack_from("<H", b, 4)[0] != 0x0600:
        return []  # só BIFF8: o Excel 5/95 grava gráfico de outro jeito
    saida, atual, profundidade, blocos, anterior, pendente = [], None, 0, [], None, None
    for tipo, d in _registros(b, 0, Orcamento(len(b))):
        if tipo == _FILEPASS:
            return saida  # protegida por senha: o resto é cifrado
        if tipo == _BOF:
            if atual is not None:
                profundidade += 1
            elif len(d) >= 4 and struct.unpack_from("<H", d, 2)[0] == _CHART_BOF:
                atual, profundidade, blocos, pendente = {"tipo": "", "titulo": "", "series": []}, 0, [], None
        elif atual is None:
            pass
        elif tipo == _EOF:
            if profundidade:
                profundidade -= 1
                continue
            saida.append(atual)
            atual = None
            if len(saida) >= GRAFICOS_XLS_MAX:
                return saida
        elif tipo == _BEGIN:
            blocos.append(anterior)
        elif tipo == _END:
            if blocos:
                blocos.pop()
        elif tipo in _TIPOS_XLS and not atual["tipo"]:
            atual["tipo"] = _TIPOS_XLS[tipo]
        elif tipo == _SERIESTEXT and len(d) >= 4:
            texto = _texto_xls(d, 2, None, 1)[0].strip()
            if blocos and blocos[-1] == _SERIES and len(atual["series"]) < _SERIES_XLS_MAX:
                atual["series"].append(texto)
            elif blocos and blocos[-1] == _TEXT:
                pendente = texto
        elif tipo == _OBJECTLINK and len(d) >= 2 and struct.unpack_from("<H", d, 0)[0] == 1 and pendente:
            atual["titulo"] = pendente
        anterior = tipo
    return saida


# ---------- PowerPoint 97-2003 ----------

_CONTEINER, _VBA_INFO, _VBA_INFO_ATOM, _CABECALHOS_RODAPES, _CSTRING = 0xF, 0x03FF, 0x0400, 0x0FD9, 0x0FBA
_USER_EDIT, _PERSIST_DIRECTORY, _EDICOES_MAX = 0x0FF5, 0x1772, 4096
_DOCUMENTO, _LISTA_SLIDES, _SLIDE_PERSIST = 0x03E8, 0x0FF0, 0x03F3


def _objetos_vivos(b, usuario, orcamento):
    """Posições dos objetos da versão atual: Current User → último UserEditAtom → diretórios de persistência, do mais
    novo ao mais antigo (MS-PPT 2.3). Assim o salvamento rápido, que deixa versões antigas no stream, não traz macro
    ou rodapé que já foram apagados. Devolve (posições, id persistente do documento), ou None se a cadeia não fecha:
    quem chama varre o stream inteiro."""
    if len(usuario) < 20:
        return None
    edicao, vistos, posicoes, documento = struct.unpack_from("<I", usuario, 16)[0], set(), {}, None
    while edicao and edicao not in vistos and len(vistos) < _EDICOES_MAX:
        vistos.add(edicao)
        orcamento.gastar(36)
        if edicao + 36 > len(b) or struct.unpack_from("<H", b, edicao + 2)[0] != _USER_EDIT:
            return None
        anterior, diretorio, ref_doc = struct.unpack_from("<III", b, edicao + 16)
        if documento is None:
            documento = ref_doc  # docPersistIdRef do UserEditAtom mais novo
        if diretorio + 8 > len(b) or struct.unpack_from("<H", b, diretorio + 2)[0] != _PERSIST_DIRECTORY:
            return None
        fim = min(diretorio + 8 + struct.unpack_from("<I", b, diretorio + 4)[0], len(b))
        orcamento.gastar(fim - diretorio)  # edições diferentes apontando o mesmo diretório pagam cada leitura
        i = diretorio + 8
        while i + 4 <= fim:
            v = struct.unpack_from("<I", b, i)[0]
            ident, quantos = v & 0xFFFFF, v >> 20
            for k in range(min(quantos, (fim - i - 4) // 4)):
                posicoes.setdefault(ident + k, struct.unpack_from("<I", b, i + 4 + 4 * k)[0])
            i += 4 + 4 * quantos
        edicao = anterior
    validas = sorted({p for p in posicoes.values() if p + 8 <= len(b)})
    return (validas, posicoes.get(documento)) if validas else None


def _slides_ppt(b, doc, orcamento):
    """Slides de verdade: SlidePersistAtom (0x03F3) da SlideListWithText de instância 0 do DocumentContainer
    (MS-PPT 2.4.14.3). O tipo SlideContainer sozinho contaria também o mestre de título e slides apagados que um
    salvamento rápido deixou mapeados."""
    if doc is None or doc + 8 > len(b) or struct.unpack_from("<H", b, doc + 2)[0] != _DOCUMENTO:
        return None
    i, fim = doc + 8, min(doc + 8 + struct.unpack_from("<I", b, doc + 4)[0], len(b))
    while i + 8 <= fim:
        orcamento.gastar(8)
        ver, tipo, tam = struct.unpack_from("<HHI", b, i)
        if tipo == _LISTA_SLIDES and ver >> 4 == 0:
            j, fim_lista, n = i + 8, min(i + 8 + tam, fim), 0
            while j + 8 <= fim_lista:
                orcamento.gastar(8)
                _, t, tt = struct.unpack_from("<HHI", b, j)
                n += t == _SLIDE_PERSIST
                j += 8 + tt
            return n
        i += 8 + tam
    return 0  # documento sem lista de slides: apresentação vazia


def ppt(o, teto) -> dict:
    """Macro (VBAInfoAtom com fHasMacros) e textos de cabeçalho, rodapé e data dos slides e das anotações."""
    b = ler_stream(o, "PowerPoint Document", teto)
    orcamento = Orcamento(len(b))
    cadeia = _objetos_vivos(b, ler_stream(o, "Current User", teto), orcamento) if o.exists("Current User") else None
    vivos, doc = cadeia if cadeia else (None, None)
    if vivos is None:
        pilha = [(0, len(b), None)]
    else:  # cada objeto vivo é um registro: o tamanho dele delimita a varredura
        pilha = [(p, min(p + 8 + struct.unpack_from("<I", b, p + 4)[0], len(b)), None) for p in vivos]
    saida, bordas = {"macro": False}, {}
    if (slides := _slides_ppt(b, doc, orcamento)) is not None:  # sem a cadeia de edições, não há o que contar
        saida["slides"] = slides
    while pilha:
        i, fim, pai = pilha.pop()
        while i + 8 <= fim:
            orcamento.gastar(8)  # objetos vivos sobrepostos pagam de novo cada registro que repetem
            ver, tipo, tam = struct.unpack_from("<HHI", b, i)
            corpo = i + 8
            if ver & 0xF == _CONTEINER:
                pilha.append((corpo, min(corpo + tam, fim), tipo))
            elif pai == _VBA_INFO and tipo == _VBA_INFO_ATOM and tam >= 8 and corpo + 8 <= len(b):
                saida["macro"] |= struct.unpack_from("<I", b, corpo + 4)[0] == 1
            elif pai == _CABECALHOS_RODAPES and tipo == _CSTRING:
                texto = b[corpo : min(corpo + tam, fim, corpo + 2 * _TEXTO_MAX)]
                orcamento.gastar(len(texto))
                orcamento.guardar(bordas, texto.decode("utf-16-le", "replace"))
            i = corpo + tam
    saida["bordas"] = list(bordas)
    return saida


# ---------- Word 97-2003 ----------

_ANINHAMENTO_MAX = 64  # o Word aninha poucos níveis; acima disso é arquivo hostil
_CAMPOS = {"PAGE": "[página]", "NUMPAGES": "[páginas]", "SECTIONPAGES": "[páginas]", "DATE": "[data]",
           "TIME": "[hora]", "FILENAME": "[arquivo]"}  # fmt: skip


def _sem_campos(texto: str) -> str:
    """Campo do Word (0x13 código 0x14 resultado 0x15) vira o resultado; número de página e data viram nome, como no
    cabeçalho do Excel. Campos aninhados são tratados com pilha e um contador dos que escondem o texto (código ou
    resultado pulado), para custo linear em qualquer entrada."""
    saida, pilha, escondem = [], [], 0  # pilha: [código, fase]; fase "codigo", "resultado" ou "pular"
    for c in texto:
        if c == "\x13":
            if len(pilha) >= _ANINHAMENTO_MAX:
                raise ValueError("campos aninhados demais")
            pilha.append([[], "codigo"])
            escondem += 1
        elif c == "\x14" and pilha:
            codigo, fase = pilha[-1]
            if fase != "codigo":
                continue
            nome = "".join(codigo).split()[:1]
            if nome and nome[0].upper() in _CAMPOS:
                if escondem == 1:  # só este campo esconde: o nome aparece
                    saida.append(_CAMPOS[nome[0].upper()])
                pilha[-1][1] = "pular"
            else:
                pilha[-1][1] = "resultado"
                escondem -= 1
        elif c == "\x15" and pilha:
            if pilha.pop()[1] != "resultado":
                escondem -= 1
        elif not escondem:
            saida.append(c)
        elif pilha[-1][1] == "codigo" and len(pilha[-1][0]) < 32:  # só a primeira palavra do código importa
            pilha[-1][0].append(c)
    return "".join(saida)


def _texto_por_cp(wd, tabela, fib, ini, fim) -> str:
    """Texto entre as posições de caractere [ini, fim) pela piece table (CLX) do Word 97."""
    fcclx, lcbclx = struct.unpack_from("<II", wd, fib + 33 * 8)
    clx = tabela[fcclx : fcclx + lcbclx]
    i = 0
    while i + 3 <= len(clx) and clx[i] == 0x01:  # Prc: formatação, pulada
        i += 3 + struct.unpack_from("<H", clx, i + 1)[0]
    if i + 5 > len(clx) or clx[i] != 0x02:
        return ""
    lcb = struct.unpack_from("<I", clx, i + 1)[0]
    plc = clx[i + 5 : i + 5 + lcb]
    n = (len(plc) - 4) // 12
    if n <= 0:
        return ""
    cps = struct.unpack_from(f"<{n + 1}I", plc, 0)
    if any(x >= y for x, y in itertools.pairwise(cps)):
        return ""  # a especificação exige posições crescentes: fora disso, peças repetidas multiplicariam o texto
    partes, restante = [], min(fim - ini, len(wd))  # o cabeçalho nunca é maior que o próprio stream
    for k in range(n):
        a, z = max(cps[k], ini), min(cps[k + 1], fim, max(cps[k], ini) + restante)
        if a >= z:
            continue
        restante -= z - a
        fc = struct.unpack_from("<I", plc, 4 * (n + 1) + 8 * k + 2)[0]
        if fc & 0x40000000:  # comprimido: um byte por caractere, cp1252
            o = (fc & 0x3FFFFFFF) // 2 + (a - cps[k])
            partes.append(wd[o : o + (z - a)].decode("cp1252", "replace"))
        else:
            o = fc + 2 * (a - cps[k])
            partes.append(wd[o : o + 2 * (z - a)].decode("utf-16-le", "replace"))
        if restante <= 0:
            break
    return "".join(partes)


def doc(o, teto) -> list[str]:
    """Parágrafos da história de cabeçalho e rodapé do Word 97-2003 (vem depois do corpo e das notas de rodapé)."""
    wd = ler_stream(o, "WordDocument", teto)
    if len(wd) < 64 or struct.unpack_from("<H", wd, 0)[0] != 0xA5EC:
        return []
    flags = struct.unpack_from("<H", wd, 0x0A)[0]
    if flags & 0x0100:  # cifrado
        return []
    nome_tabela = "1Table" if flags & 0x0200 else "0Table"
    if not o.exists(nome_tabela):
        return []
    csw = struct.unpack_from("<H", wd, 32)[0]
    lw = 34 + 2 * csw
    cslw = struct.unpack_from("<H", wd, lw)[0]
    fib = lw + 2 + 4 * cslw + 2  # início do fibRgFcLcb
    if cslw < 6 or fib + 34 * 8 > len(wd):
        return []
    ccp_texto, ccp_notas, ccp_cabecalho = struct.unpack_from("<3i", wd, lw + 2 + 12)
    if ccp_cabecalho <= 0 or min(ccp_texto, ccp_notas) < 0:
        return []
    ini = ccp_texto + ccp_notas
    texto = _sem_campos(_texto_por_cp(wd, ler_stream(o, nome_tabela, teto), fib, ini, ini + ccp_cabecalho))
    texto = texto.replace("\x07", " ").replace("\x0b", " ").replace("\x1e", "-")  # 0x1E: hífen inseparável
    texto = re.sub(r"[\x00-\x08\x0e-\x1f]", "", texto)
    return [p.replace("\t", " ") for p in texto.split("\r")]


# ---------- RTF ----------

_GRUPO_BORDA = re.compile(rb"\{\\(?:header|footer)[lrf]?(?![a-z])")
_RELEITURA_MAX = 4  # bytes relidos por byte do arquivo; arquivo real relê bem menos (cabeçalhos curtos e repetidos)
_GRUPOS_MAX = 64  # distintos; o Word grava até seis por seção, quase sempre repetidos de uma seção para outra


def _fim_do_grupo(s: bytes, i: int) -> int:
    """Posição logo depois do `}` que fecha o grupo aberto antes de `i` (\\{ e \\} escapados não contam), ou -1 se
    ele nunca fecha."""
    prof = 1
    while i < len(s) and prof:
        c = s[i]
        if c == 0x5C:  # barra invertida: o caractere seguinte é escapado ou começa palavra de controle
            i += 2
            continue
        prof += (c == 0x7B) - (c == 0x7D)
        i += 1
    return -1 if prof else i


# \\pict a ler, e os grupos que trazem uma cópia de reserva da mesma imagem (o WMF do \\nonshppict) e são pulados
_PICT = re.compile(rb"\{\\(?:(pict)|nonshppict|(?:\*\\)?shprslt)(?![a-z])")
_TIPO_PICT = re.compile(rb"\\(pngblip|jpegblip|emfblip|wmetafile|dibitmap)(?![a-z])")
_MIME_PICT = {b"pngblip": "image/png", b"jpegblip": "image/jpeg", b"emfblip": "image/x-emf",
              b"wmetafile": "image/x-wmf", b"dibitmap": "image/bmp"}  # fmt: skip
_PALAVRA = re.compile(rb"\\[a-z]+-?\d* ?")


def _nivel_de_cima(seg: bytes) -> bytes | None:
    """O conteúdo do grupo sem os subgrupos ({\\*\\picprop{\\sp...}}, {\\*\\blipuid ...}), em qualquer profundidade."""
    partes, pos = [], 0
    while (k := seg.find(b"{", pos)) >= 0:
        partes.append(seg[pos:k])
        if (pos := _fim_do_grupo(seg, k + 1)) < 0:
            return None
    partes.append(seg[pos:])
    return b"".join(partes)


def _pict(seg: bytes) -> tuple[str, bytes] | None:
    """(MIME, bytes) de um \\pict em hexadecimal, ou None se o tipo não é conhecido ou o conteúdo não é hexadecimal
    (\\bin, quebrado). O bitmap cru (\\dibitmap) ganha o cabeçalho de arquivo BMP."""
    if (topo := _nivel_de_cima(seg)) is None or not (tipo := _TIPO_PICT.search(topo)):
        return None
    try:
        imagem = bytes.fromhex(re.sub(rb"\s", b"", _PALAVRA.sub(b"", topo)).decode("ascii"))
    except ValueError:
        return None
    if tipo.group(1) == b"dibitmap":
        imagem = _bmp_do_dib(imagem)
    return (_MIME_PICT[tipo.group(1)], imagem) if imagem else None


def imagens_rtf(dados: bytes) -> tuple[list[tuple[str, bytes]], bool]:
    """Imagens (\\pict em hexadecimal) dos grupos \\header e \\footer, com os mesmos tetos de rtf(), distintas e até
    IMAGENS_MAX. Devolve também se a leitura foi completa. A busca anda para a frente: cada \\pict (ou cópia de
    reserva) é percorrido uma vez, mesmo aninhado."""
    vistos, saida, marcas, percorridos = set(), [], set(), 0
    for m in _GRUPO_BORDA.finditer(dados):
        fim = _fim_do_grupo(dados, m.end())
        if fim < 0:
            return saida, False
        percorridos += fim - m.end()
        if percorridos > _RELEITURA_MAX * len(dados):
            return saida, False
        corpo = dados[m.end() : fim - 1]
        if corpo in vistos:
            continue
        vistos.add(corpo)
        if len(vistos) > _GRUPOS_MAX:
            return saida, False
        pos = 0
        while p := _PICT.search(corpo, pos):
            if (pos := _fim_do_grupo(corpo, p.end())) < 0:
                break
            if p.group(1) and (achada := _pict(corpo[p.end() : pos - 1])):
                if (marca := hashlib.sha256(achada[1]).digest()) not in marcas:
                    if len(saida) >= IMAGENS_MAX:
                        return saida, False
                    marcas.add(marca)
                    saida.append(achada)
    return saida, True


def rtf(dados: bytes, ler_rtf) -> tuple[list[str], bool]:
    """Texto dos grupos \\header e \\footer (e variantes l, r, f). Cada grupo vira um RTF mínimo com o prólogo e a
    tabela de fontes do original e é lido pelo mesmo leitor do corpo (`ler_rtf`), que já resolve página de código
    por fonte, \\u e campos. Devolve também se todos os grupos foram lidos (False: algum teto cortou a leitura)."""
    cab = re.match(rb"\{\\rtf1(?:\\[a-z]+-?\d* ?)*", dados)
    prologo = cab.group(0) if cab else b"{\\rtf1"
    if (m := re.search(rb"\{\\fonttbl", dados)) and (fim := _fim_do_grupo(dados, m.end())) > 0:
        prologo += dados[m.start() : fim]
    vistos, saida, lidos, percorridos = set(), [], 0, 0
    for m in _GRUPO_BORDA.finditer(dados):
        fim = _fim_do_grupo(dados, m.end())
        if fim < 0:
            return saida, False  # grupo sem fim: o resto do arquivo não é cabeçalho
        percorridos += fim - m.end()  # antes do teste de repetição: grupo repetido também custa a busca do fim
        if percorridos > _RELEITURA_MAX * len(dados):
            return saida, False
        corpo = dados[m.end() : fim - 1]
        if corpo in vistos:
            continue  # mala direta repete o mesmo cabeçalho em cada seção: não conta para os tetos
        vistos.add(corpo)
        lidos += len(prologo) + len(corpo)  # o prólogo (tabela de fontes) é relido a cada grupo distinto
        if len(vistos) > _GRUPOS_MAX or lidos > _RELEITURA_MAX * len(dados):
            return saida, False  # grupos aninhados ou prólogo enorme de propósito: custo proporcional ao arquivo
        saida += ler_rtf(prologo + corpo + b"}")
    return saida, True
