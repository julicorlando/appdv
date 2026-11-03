# ApPdv

Sistema multi-tenant para gestão de materiais de construção: backend Django/DRF com PostgreSQL, painel web remoto e aplicativo local de frente de caixa para Windows.

## Módulos disponíveis

- **Painel Master** em `/master/`: cadastro e suspensão de empresas, primeiro administrador por empresa e criação/vinculação de usuários e perfis.
- **Painel da empresa**: catálogo, setores e endereços, estoque, vendas e movimentações, autorizações temporárias e inventário preventivo.
- **Auditoria de estoque**: consulte entradas, vendas, devoluções e ajustes por produto, referência, empresa e operador.
- **Importação Excel**: baixe o modelo na tela de produtos. A planilha atualiza produtos pelo código e o `estoque_atual` substitui o saldo do cadastro. Cada diferença fica no histórico. Arquivos .xlsx até 5 MB e 5.000 linhas.
- **Inventário preventivo**: sorteia dias úteis e setores entre os próximos 30 dias; no dia sorteado, escolhe produtos aleatórios do setor. Durante a contagem física, as vendas dos produtos daquela amostra são pausadas. A reconciliação registra ajustes auditáveis.
- **Clientes e crédito**: cadastro por empresa, limite de crédito, vendas parcialmente pagas e contas a receber com baixas parciais no painel e API. Crédito exige conexão e confirmação do saldo no servidor.
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
python manage.py createsuperuser
python manage.py runserver
```

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
- `GET/POST /api/v1/clientes/` e `GET/POST /api/v1/contas-receber/`
- `POST /api/v1/vendas/`
- `POST /api/v1/autorizacoes/`
- `POST /api/v1/pedidos-web/` e `GET /api/v1/pedidos-web/`
- `POST /api/v1/pedidos-web/{id}/finalizar/`
- Página pública de confirmação: `/pedido/{uuid}/`
- `POST /api/v1/vendas/{id}/cancelar/`, `/estornar/` e `/trocar/`

Todas as chamadas de negócio exigem JWT e `X-Empresa-ID`.

Nunca comite segredos, tokens ou credenciais de produção. Antes de publicar, configure `SECRET_KEY`, `DEBUG=False`, `ALLOWED_HOSTS`, PostgreSQL, HTTPS e armazenamento de backups.


## Pedidos web pelo WhatsApp
No PDV Windows, monte o carrinho e escolha **Pedido via WhatsApp**. Informe o nome e o WhatsApp do cliente; o PDV cria um pedido com link público aleatório e abre o WhatsApp com a mensagem pronta. O operador confirma o envio no próprio WhatsApp. O cliente consulta o resumo e confirma no link, sem criar conta. A confirmação aparece em **Pedidos web** no painel da empresa para a equipe separar. Ao concluir o atendimento, o operador finaliza o pedido no caixa e o sistema registra a venda e baixa o estoque.

Os links expiram após 7 dias. O pedido não reserva nem baixa estoque enquanto aguarda confirmação; disponibilidade e preços são conferidos novamente ao finalizar. O envio usa o link de compartilhamento do WhatsApp (sem API Business); envio automático requer configurar um provedor/WhatsApp Business.


## Logo no comprovante interno
A empresa pode enviar uma logo em **Empresa e cupom** no painel web. O cupom do PDV é gerado em HTML para mostrar a marca ApPdv e a logo do tenant, com opção de impressão em papel térmico. Os arquivos locais ficam em `comprovantes/cupom-<venda>.html`. O armazenamento de mídia do Docker usa o volume persistente `app_media`.


## Implantação Docker Compose no Coolify
O Compose de produção exige as variáveis `SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `POSTGRES_DB`, `POSTGRES_USER` e `POSTGRES_PASSWORD`. Configure-as no ambiente do Coolify; não versione segredos. Use uma senha de banco aleatória alfanumérica, para que a URL `DATABASE_URL` continue válida. O serviço web expõe internamente a porta 8000 para o proxy Coolify; o PostgreSQL não publica porta no host. Os volumes `postgres_data` e `app_media` preservam banco e imagens.
