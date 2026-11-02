# Inventário, Excel e endereçamento

## Cadastro e importação

Na tela Produtos e estoque, o gerente/admin pode baixar `modelo_importacao_armazem.xlsx`. A aba Produtos tem cabeçalhos fixos; a aba Instruções descreve os campos. Não renomeie cabeçalhos.

A importação valida o arquivo inteiro antes de gravar, impede códigos repetidos na mesma planilha, cria categorias ausentes e cria/atualiza produtos pela combinação empresa + código. O campo `estoque_atual` é o saldo absoluto desejado. Diferenças são gravadas como movimentação AJUSTE. Setor e endereço são obrigatórios na importação; use, por exemplo, setor `Depósito A` e endereço `A-03-02`.

## Sorteio preventivo

O gerente/admin informa quantas sessões e quantos itens por sessão deseja. O sistema sorteia combinações de setor/data ao longo dos próximos 30 dias e não agenda domingos nem repete o mesmo setor no mesmo dia. É necessário ter produtos ativos com setor cadastrado.

Na data sorteada, o gerente inicia a sessão. O sistema seleciona aleatoriamente até a quantidade indicada entre os produtos ativos do setor, sem revelar o saldo esperado ao contador. Para proteger a contagem, o servidor rejeita vendas e entradas/importações para os itens incluídos enquanto a sessão estiver aberta.

O gerente registra as quantidades físicas e conclui. O sistema compara com o saldo vigente, atualiza os produtos e cria movimentações AJUSTE com referência à contagem. A tela mostra o histórico e as variações.

As sessões programadas que não foram iniciadas na data podem ser iniciadas em data posterior, mas não antes do dia sorteado. Para uma contagem mais fiel, conclua a amostra no momento da verificação; o PDV mantém bloqueados os produtos da sessão até a conclusão.
