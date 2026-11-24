# O rastro de um número

O filme acompanha a projeção de crescimento do PIB de 2024 publicada pelo Banco Central em dezembro daquele ano. A linha da tabela contém o resultado de 2023 (3,2%), a projeção anterior de 2024 (3,2%) e a projeção atual naquela edição (3,5%). [PDF original do Banco Central](https://www.bcb.gov.br/content/ri/relatorioinflacao/202412/ri202412b1p.pdf)

Na execução demonstrada, `Leitor.preparar` gerou três tarefas para as três páginas. O recorte de 1.033 × 1.459 pixels da tarefa `p002-r1` foi comparado pixel a pixel com a entrada de uma leitura do Luna já gravada. Ao receber essa resposta, `Leitor.concluir` montou a tabela no Markdown e registrou as duas regiões que continuavam sem resposta.

A edição usa nove cenas de OpenMotion. As animações dos conectores, das colunas e da marca adaptam os blocos Decision Tree, Data Chart e Logo Outro. [OpenMotion na revisão usada](https://github.com/nexu-io/openmotion/tree/53f1822344a4d1f6310b64b2e3e99d8cbf6f3f74)

A trilha instrumental foi gerada localmente com ACE-Step 1.5 Turbo em uma RTX 4070 Laptop, com semente `710397` e andamento de 96 BPM. A narração em português brasileiro foi sintetizada localmente com Kokoro-82M, voz `pf_dora`. As legendas em português acompanham o MP4 como faixa selecionável. [ACE-Step](https://huggingface.co/ACE-Step/Ace-Step1.5) · [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M)
