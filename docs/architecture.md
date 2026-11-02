# Arquitetura e segurança

O PostgreSQL no backend é a fonte oficial de preços, estoque, vendas e autorizações. O PDV pode manter catálogo em cache SQLite e formar uma fila local de vendas com UUID idempotente quando a conexão cair. Na sincronização, o backend recalcula valores e controla concorrência de estoque; conflitos vão para revisão em vez de sobrescrever dados.

Cancelamentos, estornos e trocas exigem conexão: autorização temporária é gerada por gerente/admin, guardada como hash e consumida em transação atômica, vinculada à ação e, quando possível, à venda. O operador nunca valida o código localmente. Tokens não devem aparecer em logs.

O endpoint de venda e os endpoints de autorização precisam ser implementados como operações de serviço transacionais antes de uso em produção. Cupom não fiscal deve ser claramente identificado. A integração fiscal será plugável via adaptador e depende do provedor/UF e certificado configurados.
