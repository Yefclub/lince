---
tipo: fix
titulo: PDF grande não segura os outros, respeita o prazo desde o início e o texto oculto não apaga o visível
---

**Um PDF grande não faz os outros esperarem.** O PDF agora é lido em faixas de páginas, e a trava da PDFium é
tomada por pouco tempo e passada, por ordem de chegada, a quem está esperando. Um PDF de uma página que chega
durante a leitura de um de 2.500 sai em menos de meio segundo; antes, esperava dezenas de segundos. O pdf_inspector,
que não usa a PDFium, roda fora da trava.

**O prazo vale desde a extração.** Com `prazo`, a leitura para entre uma faixa e outra quando o tempo que resta não
cobre a montagem do que já foi lido: o documento sai com o texto extraído, e as páginas restantes aparecem em
`cobertura.pendentes` como "páginas N–M: não lidas (prazo esgotado)". Num PDF de 2.500 páginas, `prazo=3` termina em
cerca de 3 s; antes, levava mais de 10 s. A primeira faixa sempre é lida, para o documento nunca sair sem texto, e
`cobertura.paginas` continua contando o documento inteiro.

**Teto de páginas de texto.** Além do teto de 2.000 páginas da análise visual, um PDF tem no máximo 5.000 páginas de
texto lidas. As seguintes não são lidas e entram nas pendências ("não lidas (teto de 5000 páginas de texto)").

**Texto oculto sai pela posição.** O texto branco, minúsculo, invisível ou fora da página é tirado de uma cópia da
página, objeto por objeto, antes de o texto dela ser gerado; o documento original não muda. Antes, o trecho oculto
era procurado no texto pronto e apagava também as cópias visíveis iguais a ele: um "não se responsabiliza" branco
cortava a frase visível "O fornecedor não se responsabiliza por atrasos na entrega.". Agora a frase visível fica
inteira e o oculto continua registrado em `texto_oculto_removido`. Se um trecho oculto não puder ser tirado da
cópia, sai o alerta "texto oculto que pode ter ficado no corpo" em `alertas_injecao`.
