---
tipo: fix
titulo: PDF com imagem fora da folha e planilha exportada com "\" nos nomes voltam a ser lidos
---

**PDF de desenho técnico.** Um PDF com uma imagem fora da área visível da página (comum em diagramas elétricos e
desenhos exportados de CAD, que têm objetos fora da folha ou a origem da página deslocada) falhava inteiro na versão
0.4.0, sem texto nenhum. Agora a posição de cada imagem e desenho é medida a partir da área visível da página, a
região que cai toda fora dela é ignorada e a que passa da borda é recortada só na parte que aparece. Se ainda assim
uma página der erro, ela entra só com o texto, com o motivo em `cobertura.pendentes`, e as outras páginas seguem
normalmente.

**Planilha, documento ou apresentação com "\" nos nomes internos.** Alguns sistemas (cadastros exportados de ERP)
gravam o pacote do Office com os nomes internos escritos como `xl\workbook.xml`. O Excel e o LibreOffice abrem esses
arquivos, mas o Lince os recusava como formato não reconhecido. Agora os nomes são normalizados numa cópia temporária
antes da leitura, para `.xlsx`, `.xlsm`, `.docx`, `.pptx` e os outros pacotes, e o resultado é igual ao do arquivo
gravado do jeito comum. A conferência de segurança continua: caminho fora do pacote e nome que fica repetido depois
da troca são recusados.

Os documentos que já eram lidos saem iguais.
