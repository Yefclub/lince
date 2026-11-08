---
tipo: feat
titulo: Desenhos SVG lidos pela IA, como arquivo ou dentro de uma página
---

Arquivos `.svg` (diagramas, fluxogramas, gráficos e ícones exportados de Visio, draw.io, Illustrator ou Inkscape)
agora são lidos. O desenho é renderizado e vai para a IA como qualquer outra imagem, então caixas, setas, cores e a
posição de cada rótulo entram na transcrição. O texto do desenho também fica no corpo, uma linha por rótulo, e vale
mesmo sem IA. O título e a descrição do arquivo vão para as propriedades.

#### Também dentro de páginas HTML

O desenho SVG embutido numa página HTML passa pelo mesmo caminho e é lido no lugar em que aparece, com gradientes,
setas e estilos. Ícones pequenos (abaixo de 48 px) não vão para a IA, como já acontece com as imagens, e cada página
tem um teto de 50 desenhos lidos; o que passar disso fica no texto e é registrado.

#### Seguro por padrão

Uma referência a arquivo local ou a endereço externo dentro do SVG nunca entra no desenho: só o que está no próprio
arquivo é desenhado. Texto escondido no SVG sai do corpo e gera alerta, como no HTML. Um SVG montado para travar a
leitura é cortado: a bomba de entidades na hora, e o desenho que prende o processador (filtro pesado) depois de no
máximo 30 segundos ou do prazo do documento, o que vier antes, com o texto preservado.

#### Licenças conferidas por versão

O desenho usa o resvg (MIT). Como o pacote não declara a licença no índice do Python, ele entra numa lista nova de
exceções declaradas da checagem de licenças, que vale só para a versão conferida: ao atualizar o pacote, o CI
reprova até a licença da versão nova ser conferida de novo.
