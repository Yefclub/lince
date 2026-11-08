---
tipo: fix
titulo: Imagem que não abre não derruba mais o documento do Office
---

Um documento do Word, do PowerPoint ou do LibreOffice com uma imagem em formato que o Lince ainda não desenha (EMF e
WMF, comuns em diagramas colados do Visio e em gráficos copiados do Excel) ou com a imagem corrompida falhava inteiro,
sem texto nenhum. Agora o documento é lido normalmente: a imagem fica marcada no lugar em que aparecia, com
o formato (por exemplo `[imagem 1: imagem EMF não lida]`), e entra em `partes_ilegiveis`, para a plataforma saber o
que ficou de fora.

#### SVG dentro do Office

Ícones e desenhos SVG inseridos no Word e no PowerPoint (comuns no Office 365) agora são desenhados e lidos pela
IA, como as outras imagens, no lugar em que aparecem. Os desenhos de um documento são feitos juntos, com o mesmo teto
de tempo e de quantidade (50) das páginas HTML.
