# Armazém PDV

Sistema híbrido para gestão de material de construção: backend Django/DRF com PostgreSQL, painel web e aplicativo local de frente de caixa.

## Componentes
- `backend/`: API REST, painel administrativo e domínio de estoque/vendas/autorização.
- `pdv/`: cliente desktop local com cache SQLite e fila de sincronização.
- `docker-compose.yml`: ambiente local de desenvolvimento com PostgreSQL.

## Iniciar o backend
```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

## Iniciar o PDV
```bash
cd pdv
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.main
```

O PDV exige conexão com o servidor para cancelamentos, estornos e trocas. Vendas podem ser colocadas na fila local quando offline e sincronizadas com chave idempotente. Emissão fiscal permanece atrás de adaptador; o comprovante interno não substitui documento fiscal.

## Variáveis
Consulte `backend/.env.example`. Nunca comite segredos, tokens ou credenciais de produção.
