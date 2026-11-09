---
tipo: feat
titulo: Arquivos protegidos por senha são reconhecidos e lidos com a senha, sem que ela fique guardada
---

**Erro próprio para arquivo com senha.** Antes, um PDF com senha de abertura falhava como "arquivo não pôde ser
aberto", igual a um arquivo corrompido, e quem enviava não tinha como saber que bastava a senha. Agora PDF com senha de
abertura, Word, Excel e PowerPoint protegidos (`.docx`, `.xlsx`, `.pptx`, `.doc`, `.xls`, `.ppt`) e documentos do
LibreOffice com senha (`.odt`, `.ods`, `.odp`) são reconhecidos antes de qualquer leitura: a API recusa o envio na hora
com `422 arquivo-protegido`, dizendo o formato e o nome do arquivo, sem ler nada nem chamar a IA. Na biblioteca, a
leitura levanta `lince.DocumentoProtegido`. O PDF que só tem senha de permissões (abre sem senha, mas não deixa
imprimir ou editar) continua sendo lido como sempre.

**Senha no envio.** O `POST /v1/sessoes` aceita o campo opcional `senha`, nos modos `leitura` e `tarefas`. Com a senha
certa, o arquivo é decifrado numa cópia temporária, só para a leitura, e apagado junto com a sessão; a montagem do
modo tarefas, a releitura ampliada e o código editável (`codigo=sim`) usam essa cópia, sem pedir a senha de novo. A
senha errada responde `422 senha-incorreta`, para o sistema pedir de novo à pessoa. Na biblioteca, é o parâmetro
`senha` de `Leitor.ler` e `Leitor.preparar`, e a senha errada levanta `lince.SenhaIncorreta`.

**A senha não fica em lugar nenhum.** Ela só abre o arquivo e é descartada: não vai para a sessão, o plano assinado,
os logs, os metadados, a resposta nem as mensagens de erro. As leituras do documento protegido também não entram no
cache de transcrições, para ninguém receber o conteúdo sem ter a senha. A cópia decifrada passa pelo antivírus, como o
arquivo enviado.

**Contra tentativa e erro.** Depois de 5 senhas erradas para o mesmo arquivo, ou 50 no mesmo token, em 15 minutos, as
tentativas seguintes são recusadas com `429 tentativas-de-senha` e `Retry-After`, até a senha errada mais antiga sair
da janela.

**O que fica só reconhecido.** Documentos do OpenOffice antigo (cifra Blowfish), do formato ODF 1.4 (AES-GCM) e Word
com ofuscação XOR são reconhecidos como protegidos, mas não decifrados; voltam como `arquivo-protegido`, com o motivo. O
arquivo que a senha certa abre, mas que está corrompido, também volta como `arquivo-protegido` ("arquivo corrompido"),
sem contar como senha errada.

Contrato 1.13.0: o campo `senha` e os códigos `arquivo-protegido`, `senha-incorreta`, `tentativas-de-senha` e
`senha-invalida` (senha acima de 1024 caracteres) são novos e opcionais; nada muda para quem já usa a `/v1`.
