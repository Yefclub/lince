"""Gráfico do Excel 97-2003 (.xls): tipo, título e séries entram na leitura; os valores já estão nas células."""

import io
import struct
from pathlib import Path

import olefile

import lince
from lince import legados

REAIS = Path(__file__).parent / "fixtures" / "reais"


def test_tipo_e_series_de_cada_grafico():
    md = lince.ler(REAIS / "poi_WithTwoCharts.xls").markdown
    assert "**Gráfico (linhas): sem título**\nSéries: 1st Column, 2nd Column." in md
    assert "**Gráfico (área): sem título**\nSéries: Squares, Base Numbers." in md


def test_titulo_do_grafico():
    md = lince.ler(REAIS / "lince_grafico.xls").markdown
    assert "**Gráfico (barras): Vendas por região**" in md and "| Norte | 120 |" in md


def test_teto_de_graficos_fica_registrado(monkeypatch):
    monkeypatch.setattr(legados, "GRAFICOS_XLS_MAX", 1)
    caminho = REAIS / "poi_WithTwoCharts.xls"
    with olefile.OleFileIO(caminho) as o:
        graficos, corte = legados.graficos_xls(o, caminho.stat().st_size)
    assert [g["tipo"] for g in graficos] == ["linhas"] and "mais de 1 gráficos" in corte


def _reg(tipo, dados=b""):
    return struct.pack("<HH", tipo, len(dados)) + dados


def _bof(dt, versao=0x0600):
    return _reg(0x0809, struct.pack("<HH", versao, dt) + b"\0" * 12)


EOF_, BEGIN, END, TEXT, SERIES, BAR = _reg(0x000A), _reg(0x1033), _reg(0x1034), _reg(0x1025), _reg(0x1003), _reg(0x1017)


def _texto(t):
    return _reg(0x100D, b"\0\0" + bytes([len(t), 0]) + t.encode("latin-1"))


def _link(alvo):
    return _reg(0x1027, struct.pack("<HHH", alvo, 0, 0))


class _Ole:
    def __init__(self, *graficos, versao=0x0600, antes=b""):
        self.b = _bof(0x0005, versao) + antes + EOF_ + b"".join(_bof(0x0020) + g + EOF_ for g in graficos)

    def exists(self, nome):
        return nome == "Workbook"

    def get_size(self, nome):
        return len(self.b)

    def openstream(self, nome):
        return io.BytesIO(self.b)


def _ler(*graficos, **k):
    ole = _Ole(*graficos, **k)
    return legados.graficos_xls(ole, len(ole.b))


def test_titulo_de_eixo_e_rotulo_de_dado_nao_viram_titulo_nem_serie():
    eixo = TEXT + BEGIN + _texto("Mês") + _link(3) + END
    titulo_sem_texto = TEXT + BEGIN + _link(1) + END
    rotulo = SERIES + BEGIN + _texto("Norte") + TEXT + BEGIN + _texto("42") + _link(4) + END + END
    [g], corte = _ler(BAR + rotulo + eixo + titulo_sem_texto)
    assert g == {"tipo": "barras", "titulo": "", "series": ["Norte"]} and corte is None
    [g], _ = _ler(BAR + eixo + TEXT + BEGIN + _texto("Receita") + _link(1) + END)
    assert g["titulo"] == "Receita"
    sem_link = TEXT + BEGIN + _texto("Mês") + END  # texto sem OBJECTLINK não fica esperando o próximo TEXT
    [g], _ = _ler(BAR + sem_link + titulo_sem_texto)
    assert g["titulo"] == ""


def test_grafico_aninhado_nao_soma_series_ao_de_fora():
    interno = _bof(0x0020) + SERIES + BEGIN + _texto("Interna") + END + EOF_
    [g], _ = _ler(SERIES + BEGIN + _texto("Externa") + END + interno + BAR)
    assert g["series"] == ["Externa"] and g["tipo"] == "barras"


def test_blocos_desbalanceados_e_aninhados_demais():
    [g], corte = _ler(END + END + BAR + SERIES + BEGIN + _texto("S") + END)
    assert g["series"] == ["S"] and corte is None
    graficos, corte = _ler(BEGIN * 10_000)
    assert graficos == [] and "aninhados demais" in corte


def test_senha_e_biff5_nao_sao_lidos():
    assert _ler(BAR, antes=_reg(0x002F, b"\0" * 4)) == ([], None)  # FILEPASS: o resto é cifrado
    assert _ler(BAR, versao=0x0500) == ([], None)


def test_xls_que_nao_e_ole_nao_ganha_parte_ilegivel(tmp_path):
    p = tmp_path / "exportado.xls"  # planilha do LibreOffice salva com a extensão antiga: o corpo lê pelo conteúdo
    p.write_bytes((REAIS / "lince_imagem_planilha.ods").read_bytes())
    r = lince.ler(p)
    assert "Pedidos" in r.markdown and not any("gráficos" in x for x in r.metadados.get("partes_ilegiveis", []))
