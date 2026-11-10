# ApPdv

Sistema multi-tenant para gestão de materiais de construção: backend Django/DRF com PostgreSQL, painel web remoto e aplicativo local de frente de caixa para Windows.

## Módulos disponíveis

- **Painel Master** em `/master/`: cadastro e suspensão de empresas, primeiro administrador por empresa e criação/vinculação de usuários e perfis.
- **Painel da empresa**: catálogo, setores e endereços, estoque, vendas e movimentações, autorizações temporárias e inventário preventivo.
- **Estoque e auditoria**: endereçamento por setor/posição, entrada, perda com motivo, vendas, devoluções e ajustes de inventário; consulte histórico por produto, NF, empresa e operador. O saldo é por produto e empresa, sem saldos separados por depósito nesta versão.
- **Importação Excel**: baixe o modelo na tela de produtos. A planilha atualiza produtos pelo código e o `estoque_atual` substitui o saldo na unidade base. Configure unidade de venda, unidade base e fator (ex.: 1 caixa = 12 unidades). Cada diferença fica no histórico. Arquivos .xlsx até 5 MB e 5.000 linhas.
- **Inventário preventivo**: sorteia dias úteis e setores entre os próximos 30 dias; no dia sorteado, escolhe produtos aleatórios do setor. Durante a contagem física, as vendas dos produtos daquela amostra são pausadas. A reconciliação registra ajustes auditáveis.
- **Pedidos e entrega**: orçamento/pedido com link público único para confirmação do cliente; o operador pode informar endereço e data solicitada, e a equipe acompanha no painel.
- **Clientes e crédito**: cadastro por empresa, limite de crédito, vendas parcialmente pagas e contas a receber com baixas parciais no painel e API. Crédito exige conexão e confirmação do saldo no servidor.
- **Contas a pagar**: o recebimento de NF gera obrigação do fornecedor com vencimento; o painel e a API registram pagamentos parciais e saldo pendente.
- **Entrega**: pedidos pelo WhatsApp podem informar endereço e data solicitada; o cliente vê esses dados no link público e a equipe acompanha no painel.
- **PDV desktop**: login via API, seleção de empresa, busca por código/código de barras, abertura e fechamento de caixa, venda com estoque no servidor, cancelamento com token de gerente, cupom interno em TXT e fila local idempotente para falhas de conexão.
- **Cupom interno**: vendas são registradas no servidor; emissão fiscal NF-e/NFC-e depende da configuração de provedor e certificado, ainda não habilitada neste scaffold.

## Isolamento por empresa

O banco é compartilhado, mas cada empresa tem catálogo, estoque, caixas, vendas e autorizações próprios. O vínculo e o papel do usuário são definidos por empresa. Depois do login, o PDV lista empresas em `GET /api/v1/minhas-empresas/` e envia `X-Empresa-ID` nas chamadas de negócio; o backend verifica a associação ativa em cada requisição.

## Início local do backend

Com Docker Desktop e Python 3.10+:

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cd ..
docker compose up -d db
cd backend
python manage.py migrate
python manage.py createcachetable
python manage.py createsuperuser
python manage.py runserver
```

O `docker-compose.yml` exige `POSTGRES_DB`, `POSTGRES_USER` e `POSTGRES_PASSWORD`: copie também `.env.example` (na raiz) para `.env` e use os mesmos valores do `DATABASE_URL` de `backend/.env`. Com PostgreSQL, o cache compartilhado (limites de tentativa de login) fica na tabela criada por `createcachetable`; o entrypoint do container já executa esse comando.

Para executar também o backend em container, copie primeiro `backend/.env.example` para `backend/.env`, ajuste `SECRET_KEY` e execute `docker compose up --build`. No container, o Compose aponta o backend para o serviço PostgreSQL `db`.

Entre em `http://127.0.0.1:8000/master/` com a conta superusuária para cadastrar a primeira empresa e os usuários. O painel técnico Django fica em `/admin/`.

A entrada de compras fica no painel da empresa em **Compras e NF-e**. O recebimento só pode ser feito por gerente/administrador e exige associar todos os itens a produtos ativos da própria empresa. A confirmação registra a movimentação de entrada de cada produto.

## PDV Windows

No Windows, instale Python 3.10+ e execute `pdv/build_windows.bat`. O PyInstaller gera `pdv/dist/ApPdv.exe` com o ícone do kit. Com Inno Setup (`ISCC.exe`) instalado, o mesmo script também gera `pdv/dist/installer/ApPdv-Setup.exe`. Configure `APPDV_API_URL` ou informe o endereço do servidor na tela de login.

```bash
cd pdv
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m app.main
```

As operações protegidas exigem conexão com o servidor. Vendas que falham por conexão são mantidas localmente com chave idempotente e sincronizadas ao reconectar; rejeições de preço, caixa, estoque ou autorização são mostradas ao operador e não entram na fila.

## API principal

- `POST /api/v1/auth/token/`
- `GET /api/v1/minhas-empresas/`
- `GET /api/v1/produtos/` e `GET /api/v1/categorias/`
- `POST /api/v1/caixas/abrir/`, `POST /api/v1/caixas/{id}/fechar/`, `GET /api/v1/caixas/{id}/resumo/`, `POST /api/v1/caixas/movimentacoes/`
- `GET/POST /api/v1/clientes/`, `/api/v1/contas-receber/` e `/api/v1/contas-pagar/`
- `POST /api/v1/vendas/`
- `POST /api/v1/autorizacoes/`
- `POST /api/v1/pedidos-web/` e `GET /api/v1/pedidos-web/`
- `POST /api/v1/pedidos-web/{id}/finalizar/`
- Página pública de confirmação: `/pedido/{uuid}/`
- `POST /api/v1/vendas/{id}/cancelar/`, `/estornar/` e `/trocar/`
- `POST /api/v1/vendas-offline/descartar/` (exige usuário e senha de gerente/administrador)

Todas as chamadas de negócio exigem JWT e `X-Empresa-ID`.

## Novidades da versão 0.3 (painel e PDV instalado)

- **Produtos:** edição completa no painel (preço, inativação, foto, endereço), promoção com período (o preço promocional vale no PDV, no caixa web, em orçamentos e pedidos), dados fiscais (NCM, CEST, CFOP, CSOSN/CST, origem), etiquetas de preço com código de barras EAN-13 e geração de códigos internos, inativação dos produtos de teste.
- **NFC-e (Focus NFe):** emissão, consulta e cancelamento na Central fiscal; emissão no PDV e no caixa web logo após a venda; emissão automática opcional. Configure o token da empresa e o CST de PIS/COFINS em *Dados da empresa*; certificado A1 e CSC ficam no painel da Focus. Comece em homologação.
- **PIX (Mercado Pago):** QR Code dinâmico no PDV e no caixa web, com confirmação automática (consulta ao Mercado Pago; o webhook `/api/v1/pix/webhook/<empresa>/` só dispara a consulta). A cobrança paga é vinculada à venda e não pode ser reutilizada.
- **Caixa:** gerente fecha pelo painel o caixa que o operador deixou aberto (com contagem, motivo e auditoria).
- **Senhas:** "Minha senha" no painel e "Trocar senha" no PDV; administrador define senha provisória em *Equipe*; "Esqueci minha senha" por e-mail quando `EMAIL_HOST` estiver configurado.
- **Financeiro:** cadastro de fornecedores, contas e despesas avulsas (aluguel, energia…), extrato do cliente, relatórios gerenciais (formas de pagamento, vendas por dia, fluxo de caixa, curva ABC, estoque valorizado) com exportação para Excel.
- **Backup:** `python manage.py backup_dados` grava uma cópia compactada em `/app/backups` (volume `app_backups`) e mantém as 14 mais recentes. Agende no Coolify (*Scheduled Tasks*) e copie periodicamente para fora do servidor.
- **PDV instalado 0.3.0:** novos módulos de gestão, PIX, NFC-e, troca de senha, aviso de versão desatualizada (`PDV_VERSAO_MINIMA`). O instalador é gerado pelo GitHub Actions (*Instalador do PDV*); uma tag `pdv-v0.3.0` publica o instalador em *Releases*.

## Segurança em produção

Com `DEBUG=False` o backend não inicia sem `SECRET_KEY` própria, redireciona para HTTPS, marca os cookies como seguros e envia HSTS (30 dias; ajuste com `SECURE_HSTS_SECONDS`). Configure o endereço do servidor no PDV com `https://`. Login (API e painel) e senhas de gerente digitadas no PDV têm limite de tentativas por usuário e por IP (`THROTTLE_*`, `NUM_PROXIES`). Somente os logos das empresas (`/media/empresas/logos/`) são servidos publicamente; XML e DANFE fiscais continuam sem acesso público.

## Caixa, estornos e vendas offline

- Estornos (cancelamento, troca, devolução) entram no caixa de quem executa a operação. Estorno em dinheiro exige caixa aberto.
- Recebimentos de contas a receber em dinheiro exigem caixa aberto e entram no dinheiro esperado do fechamento.
- Cancelar uma venda a prazo cancela a conta a receber e estorna as parcelas já recebidas.
- Vendas offline recusadas pelo servidor ficam em **Sincronização** no PDV: reenvie após corrigir a causa ou descarte com usuário e senha de gerente. O descarte fica registrado na auditoria (`VENDA_OFFLINE_DESCARTADA`) com itens, pagamentos e motivo.

Nunca comite segredos, tokens ou credenciais de produção. Antes de publicar, configure `SECRET_KEY`, `DEBUG=False`, `ALLOWED_HOSTS`, PostgreSQL, HTTPS e armazenamento de backups.


## Pedidos web pelo WhatsApp
No PDV Windows, monte o carrinho e escolha **Pedido via WhatsApp**. Informe o nome, WhatsApp, endereço e data desejada para entrega (os dois últimos são opcionais); o PDV cria um pedido com link público aleatório e abre o WhatsApp com a mensagem pronta. O operador confirma o envio no próprio WhatsApp. O cliente consulta o resumo e confirma no link, sem criar conta. A confirmação aparece em **Pedidos web** no painel da empresa para a equipe separar. Ao concluir o atendimento, o operador finaliza o pedido no caixa e o sistema registra a venda e baixa o estoque.

Os links expiram após 7 dias. O pedido não reserva nem baixa estoque enquanto aguarda confirmação; disponibilidade e preços são conferidos novamente ao finalizar. O envio usa o link de compartilhamento do WhatsApp (sem API Business); envio automático requer configurar um provedor/WhatsApp Business.


## Logo no comprovante interno
A empresa pode enviar uma logo em **Empresa e cupom** no painel web. O cupom do PDV é gerado em HTML para mostrar a marca ApPdv e a logo do tenant, com opção de impressão em papel térmico. Os arquivos locais ficam em `comprovantes/cupom-<venda>.html`. O armazenamento de mídia do Docker usa o volume persistente `app_media`.


## Implantação Docker Compose no Coolify
O Compose de produção exige as variáveis `SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `POSTGRES_DB`, `POSTGRES_USER` e `POSTGRES_PASSWORD`. Configure-as no ambiente do Coolify; não versione segredos. Use uma senha de banco aleatória alfanumérica, para que a URL `DATABASE_URL` continue válida. O serviço web expõe internamente a porta 8000 para o proxy Coolify; o PostgreSQL não publica porta no host. Os volumes `postgres_data` e `app_media` preservam banco e imagens.


### PDV Windows — clientes, orçamentos e pagamentos

- Finalizar venda solicita CPF válido, busca o cadastro da empresa e permite cadastro rápido de nome/telefone sem conceder limite de crédito. Cancelar a identificação interrompe a finalização.
- Fazer/imprimir orçamento salva número, cliente, itens, preços, descontos e validade no servidor. Buscar orçamento permite pesquisar por número ou CPF, reimprimir ou carregar para venda. Não reserva estoque; preços e disponibilidade são conferidos novamente na conversão. Um orçamento não pode gerar duas vendas.
- A tela de pagamento distribui valores entre dinheiro, Pix, cartão de débito/crédito, outra forma e conta do cliente. Exibe saldo e troco; somente dinheiro gera troco. Conta do cliente continua sujeita ao limite do servidor. Cartão/Pix são registros manuais, sem captura bancária/TEF automática.
- Desconto sobre o total em reais ou percentual exige gerente/administrador e é rateado nos itens, com conferência dos centavos. Alterar itens/condições de orçamento carregado exige salvar um novo orçamento antes de convertê-lo.
- Identificação e orçamento exigem conexão. Tentativas de venda ficam salvas localmente por empresa/operador; após perda da resposta, usar Finalizar novamente consulta/conclui a mesma operação sem gerar uma nova chave.
- Aplique as migrations do backend antes de usar o executável atualizado. No Windows, atualize a main e execute `pdv/build_windows.bat` para gerar o novo executável. O redeploy Web não atualiza executáveis já instalados.
- Validação desktop: `PYTHONPATH=pdv QT_QPA_PLATFORM=offscreen python -m unittest discover -s pdv/tests`. Validação backend: `python backend/manage.py test core`.


### Desconto autorizado e operação durante queda de internet

O caixa pode aplicar desconto de até 5% sobre o subtotal. Acima disso, o PDV solicita usuário e senha de gerente/administrador da mesma empresa. A senha é validada online e não é persistida no PC. A autorização vale apenas para os itens e a chave daquela operação, por até 24 horas; na venda, é consumida e fica vinculada ao aprovador. Alterações dos itens exigem nova autorização. Não é possível obter nova autorização privilegiada sem conexão.

Com login e caixa previamente abertos, o PC continua vendendo pelo catálogo local durante uma queda. Clientes conhecidos são consultados no cache e novos CPFs podem ser registrados provisoriamente. O comprovante informa que a venda está pendente. A fila é persistida em SQLite e reenviada em segundo plano a cada 15 segundos com a mesma chave, caixa e operador, sem duplicação. Produtos/preços/estoque/permissões são revalidados no servidor; diferenças ficam pendentes, sem serem descartadas. O botão **Sincronização** mostra os motivos. O caixa não pode ser fechado com pendências do operador. Vendas a prazo exigem conexão para validar crédito. O início de uma sessão e abertura de caixa exigem conexão.

A logo exibida na consulta do PDV vem do mesmo cadastro da empresa utilizado nos cupons e fica em cache para quedas de conexão; se não houver logo cadastrada, a marca ApPdv é utilizada.


### Gestão Web integrada

O menu da empresa contém Central de orçamentos, Separação/entregas, Depósitos/transferências, Devoluções, Conferência de caixas, Sincronização, Margens, Compras/reposição, Políticas de desconto e Central fiscal. Em Clientes, **Editar cadastro** permite atualizar identificação, contatos, endereço, limite e atividade sem excluir o histórico. A edição exige gerente/administrador; a política de descontos exige administrador.

- Orçamento pode ser duplicado/revisado (novo número, original preservado), impresso com logos e convertido pelo caixa aberto do usuário. A conversão confere preços atuais, pagamentos, autorização e impede uma segunda venda do mesmo orçamento.
- Reservas são criadas pelo orçamento e liberadas ao cancelar/expirar ou converter em venda. O pedido controla separação, responsável, frete padrão da empresa e retiradas parciais com registro do recebedor. Entregar não baixa novamente o estoque já consumido pela venda.
- O depósito Principal representa o saldo global menos as quantidades distribuídas aos demais depósitos. Entradas/devoluções chegam ao Principal; transferências preservam o total; saídas respeitam reservas e saldos livres. Saldos são comerciais, com os itens já vendidos e ainda não entregues acompanhados nos pedidos de entrega.
- Devolução por item limita quantidade e valor ao restante da venda original. Pode retornar ao estoque, registrar perda, gerar crédito em loja, registrar estorno manual ou abater conta a receber. Estorno externo é feito no meio de pagamento original; o sistema registra seu valor. Crédito em loja é utilizável na conversão Web de orçamento para cliente identificado. Troca é devolução seguida de nova venda.
- Reposição considera estoque mínimo, reservas e pedidos abertos. Pedidos de compra podem ser revisados/cancelados e recebidos parcialmente contra notas importadas. Quantidades/produtos são conferidos; diferenças de custo exigem confirmação explícita. Contas a pagar e custo médio continuam sendo criados/atualizados pelo recebimento existente.
- Relatórios usam custo congelado nas novas vendas e descontam devoluções. Vendas históricas sem custo congelado são marcadas, sem inventar uma margem. Há filtros por data, vendedor e categoria.
- Políticas manual/automática/mista aplicam desconto percentual por subtotal mínimo e forma de pagamento, sem acumular descontos automaticamente: prevalece o maior desconto aplicável. Caixa Web e cliente PC atualizado calculam a promoção; o servidor valida os limites e autorizações.
- Terminais atualizados enviam último contato, quantidade pendente e motivos ao servidor. O painel não consegue ver dados de um PC totalmente desconectado até que ele volte a transmitir.
- A Central fiscal organiza solicitações. **A integração de emissão real ainda não existe**: é necessário escolher/homologar provedor, configurar certificado e tributação dos produtos. Nenhuma nota autorizada, XML ou DANFE é fabricado pelo sistema.

Aplicar migrations antes de iniciar o backend atualizado. O cliente Windows precisa ser recompilado para enviar o estado de sincronização e utilizar as novas políticas de desconto. Validação Web: `python backend/manage.py test core`.
