# Arquitetura multi-tenant e segurança

## Isolamento por empresa

O banco é compartilhado, mas cada organização é um tenant representado por `Empresa`. Usuários entram em uma ou mais empresas por `MembroEmpresa`, que guarda seu papel e estado de acesso dentro daquele tenant. Os papéis (caixa, gerente, administrador) são por empresa: a mesma pessoa pode ser gerente de uma loja e caixa em outra.

Após autenticar, o cliente consulta `GET /api/v1/minhas-empresas/` e apresenta a empresa selecionada em todas as chamadas de negócio:

```http
X-Empresa-ID: 12
Authorization: Bearer <access-token>
```

O backend valida a associação ativa do usuário à empresa e filtra produtos, categorias e vendas por esse tenant. O cliente desktop não escolhe um tenant arbitrário: IDs sem associação válida recebem resposta de acesso negado. Membros inativos e empresas suspensas também perdem acesso.

Toda nova entidade de negócio deve receber uma FK obrigatória para `Empresa`; consultas, gravações, exportações, relatórios e tarefas em background precisam aplicar o mesmo escopo. Chaves de produto e idempotência de venda são únicas dentro da empresa, não globalmente. Cadastros de usuários e empresas ficam sob administração da plataforma.

## Dados existentes e migrações

O projeto ainda está no scaffold inicial e não possui dados de clientes para migrar. Antes do primeiro ambiente compartilhado, gere e aplique as migrações:

```bash
cd backend
python manage.py makemigrations core
python manage.py migrate
```

Crie a empresa e seus membros pelo Django Admin, depois autentique os usuários normalmente. Não cadastre dados de produção antes de aplicar as migrações.

## Offline e operações protegidas

O PDV pode manter catálogo em cache SQLite e formar uma fila local de vendas com UUID idempotente quando a conexão cair. Na sincronização, o backend precisa recalcular valores e controlar concorrência do estoque; conflitos vão para revisão em vez de sobrescrever dados.

Cancelamentos, estornos e trocas exigem conexão: autorização temporária é gerada por gerente/admin da mesma empresa, guardada como hash e consumida em transação atômica, vinculada à ação, tenant e venda. O operador nunca valida o código localmente. Tokens não devem aparecer em logs.

A API de vendas e os endpoints de autorização ainda precisam da implementação transacional antes do uso operacional. O cupom interno deve ser identificado como não fiscal. A integração fiscal será plugável via adaptador e depende do provedor, UF e certificado configurados.
