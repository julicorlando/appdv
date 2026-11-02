# Armazém PDV

Sistema multi-tenant para gestão de materiais de construção: backend Django/DRF com PostgreSQL, painel web administrativo e aplicativo local de frente de caixa.

## Isolamento entre empresas
Cada empresa tem seus próprios produtos, categorias, caixas, vendas e autorizações. O vínculo do usuário à empresa e seu papel são controlados individualmente por tenant. Depois do login, o cliente lista as empresas disponíveis em `GET /api/v1/minhas-empresas/` e envia `X-Empresa-ID` nas chamadas de negócio. O servidor valida o vínculo em toda requisição; trocar o ID no cliente não concede acesso.

## Componentes
- `backend/`: API REST, Django Admin e modelos de catálogo, usuários/empresas, estoque, caixa, venda e autorização.
- `pdv/`: cliente desktop Python com cliente HTTP e seleção de tenant.
- `docker-compose.yml`: PostgreSQL para desenvolvimento.
- `docs/architecture.md`: regras de isolamento, migrações e sincronização offline.

## Iniciar o backend
```bash
docker compose up -d db
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py makemigrations core
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Cadastre empresas e vínculos de usuários pelo Django Admin antes de liberar o acesso à API.

## Iniciar o PDV
```bash
cd pdv
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.main
```

O PDV exige conexão para operações restritas. O mecanismo de fila offline e a sincronização de vendas ainda precisam ser concluídos antes de operação em loja. Emissão fiscal permanece atrás de adaptador; comprovante interno não substitui documento fiscal.

Nunca comite segredos, tokens ou credenciais de produção.
