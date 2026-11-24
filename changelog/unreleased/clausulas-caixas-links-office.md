---
tipo: fix
titulo: Word conserva a numeração das cláusulas e as caixas de formulário, e PowerPoint preserva os links
---

As listas do DOCX mantêm os identificadores multinível, alfabéticos e romanos calculados a partir dos estilos e da
numeração do arquivo. Uma caixa de formulário legada aparece junto da opção como `[x]` ou `[ ]`, com prioridade
para o valor atual e, na ausência dele, para o padrão salvo.

No PowerPoint, o Markdown preserva os rótulos e os destinos dos links nos slides e nas notas do apresentador.
Os links internos apontam para âncoras na ordem da apresentação. Um vínculo sem slide de destino conserva o rótulo.
