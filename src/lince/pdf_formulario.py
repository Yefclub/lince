"""Valores dos widgets AcroForm visíveis. Todas as chamadas à PDFium ocorrem na vez do documento."""

import ctypes
import html

import pypdfium2 as pdfium
import pypdfium2.raw as c

# Um formulário cabe em centenas de controles. Limitam também listas de opções e strings UTF-16
# antes da alocação, para um formulário não ocupar sozinho a vez da PDFium ou a memória do leitor.
ANOTACOES_MAX = 512
OPCOES_MAX = 512
BYTES_CAMPOS_MAX = 32_768
# Um botão ou assinatura visual pode exigir um PNG. Limita o número de recortes por página.
VISUAIS_MAX = 32


def abrir_pdf(caminho):
    doc = pdfium.PdfDocument(caminho)
    tipo = doc.get_formtype()
    if tipo != c.FORMTYPE_NONE:
        # Só o AcroForm estático, inclusive em híbridos com XFA (W-9). Sem XFA nem callbacks de JavaScript.
        config = c.FPDF_FORMFILLINFO(version=2, xfa_disabled=True)
        raw = c.FPDFDOC_InitFormFillEnvironment(doc, config)
        if tipo == c.FORMTYPE_XFA_FULL:
            doc.erro_formulario = "camada XFA dinâmica sem leitura"
        if raw:
            doc.formenv = pdfium.PdfFormEnv(raw, config)
        else:
            doc.erro_formulario = "formulário indisponível"
    return doc


class _Limite(Exception):
    pass


def campos_visiveis(doc, page):
    falha = getattr(doc, "erro_formulario", None)
    if not doc.formenv:
        return "", [], falha

    saldo = BYTES_CAMPOS_MAX
    opcoes = OPCOES_MAX
    env = doc.formenv

    def texto(funcao, *args):
        nonlocal saldo
        tamanho = funcao(env, *args, None, 0)
        if tamanho < 2:
            return ""
        if tamanho > saldo or tamanho % 2:
            raise _Limite
        saldo -= tamanho
        buf = (c.FPDF_WCHAR * (tamanho // 2))()
        if funcao(env, *args, buf, tamanho) != tamanho:
            raise ValueError
        valor = bytes(buf)[:-2].decode("utf-16-le")
        # Delimitadores do resultado (marcas de metadados e comentários da IA) permanecem dados.
        return html.escape("\n  ".join(valor.splitlines()), quote=False).replace("[[", "&#91;&#91;")

    linhas, visuais = [], []
    l, b, r, t = page.get_bbox()
    total = c.FPDFPage_GetAnnotCount(page)
    for k in range(min(total, ANOTACOES_MAX)):
        a = c.FPDFPage_GetAnnot(page, k)
        if not a:
            falha = "anotação ilegível"
            continue
        try:
            if c.FPDFAnnot_GetSubtype(a) != c.FPDF_ANNOT_WIDGET:
                continue
            if c.FPDFAnnot_GetFlags(a) & (
                c.FPDF_ANNOT_FLAG_HIDDEN | c.FPDF_ANNOT_FLAG_INVISIBLE | c.FPDF_ANNOT_FLAG_NOVIEW
            ):
                continue
            caixa = c.FS_RECTF()
            if not c.FPDFAnnot_GetRect(a, ctypes.byref(caixa)) or not (
                max(l, caixa.left) < min(r, caixa.right) and max(b, caixa.bottom) < min(t, caixa.top)
            ):
                continue
            tipo = c.FPDFAnnot_GetFormFieldType(env, a)
            if tipo not in (
                c.FPDF_FORMFIELD_CHECKBOX,
                c.FPDF_FORMFIELD_RADIOBUTTON,
                c.FPDF_FORMFIELD_TEXTFIELD,
                c.FPDF_FORMFIELD_COMBOBOX,
                c.FPDF_FORMFIELD_LISTBOX,
            ):
                if len(visuais) >= VISUAIS_MAX:
                    falha = "teto de campos visuais do formulário"
                else:
                    visuais.append(
                        [
                            max(l, caixa.left) - l,
                            max(b, caixa.bottom) - b,
                            min(r, caixa.right) - l,
                            min(t, caixa.top) - b,
                        ]
                    )
                continue
            flags = c.FPDFAnnot_GetFormFieldFlags(env, a)
            if flags & c.FPDF_FORMFLAG_TEXT_PASSWORD and tipo == c.FPDF_FORMFIELD_TEXTFIELD:
                continue
            nome = texto(c.FPDFAnnot_GetFormFieldAlternateName, a) or texto(c.FPDFAnnot_GetFormFieldName, a) or "Campo"
            if tipo in (c.FPDF_FORMFIELD_CHECKBOX, c.FPDF_FORMFIELD_RADIOBUTTON):
                valor = "[x]" if c.FPDFAnnot_IsChecked(env, a) else "[ ]"
                if tipo == c.FPDF_FORMFIELD_RADIOBUTTON:
                    valor += " " + texto(c.FPDFAnnot_GetFormFieldExportValue, a)
            elif tipo in (c.FPDF_FORMFIELD_COMBOBOX, c.FPDF_FORMFIELD_LISTBOX):
                n = c.FPDFAnnot_GetOptionCount(env, a)
                if n > opcoes:
                    raise _Limite
                opcoes -= max(0, n)
                selecionadas = [
                    texto(c.FPDFAnnot_GetOptionLabel, a, j) for j in range(n) if c.FPDFAnnot_IsOptionSelected(env, a, j)
                ]
                valor = ", ".join(selecionadas) or texto(c.FPDFAnnot_GetFormFieldValue, a)
            else:
                valor = texto(c.FPDFAnnot_GetFormFieldValue, a)
            linhas.append(f"- {nome}: {valor or '(em branco)'}")
        except _Limite:
            falha = "teto de leitura dos campos do formulário"
            break
        except ValueError:
            falha = "campo de formulário ilegível"
        finally:
            c.FPDFPage_CloseAnnot(a)
    if total > ANOTACOES_MAX:
        falha = "teto de anotações do formulário"
    return "\n".join(linhas), visuais, falha
