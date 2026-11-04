from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import (
    Categoria, Cliente, CompraEntrada, ContaPagar, ContaReceber, Empresa,
    Fornecedor, ItemCompraEntrada, MembroEmpresa, Produto, PedidoWeb, MovimentacaoEstoque,
)
from .purchases import confirmar_recebimento


class SalesAndFinanceFlowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user("operator", password="QaPassword!123")
        self.company = Empresa.objects.create(nome="Armazem QA", slug="armazem-qa")
        MembroEmpresa.objects.create(
            empresa=self.company, usuario=self.user, papel=MembroEmpresa.Papel.ADMIN
        )
        category = Categoria.objects.create(empresa=self.company, nome="Materiais")
        self.product = Produto.objects.create(
            empresa=self.company, categoria=category, codigo="CIM-50",
            nome="Cimento 50kg", unidade="SC", preco_venda=Decimal("10.00"),
            custo_medio=Decimal("5.00"), estoque_atual=Decimal("100.000"),
            estoque_minimo=Decimal("5.000"),
        )
        self.customer = Cliente.objects.create(
            empresa=self.company, nome="Cliente de teste",
            limite_credito=Decimal("100.00"),
        )
        self.api = APIClient()
        self.api.force_authenticate(user=self.user)
        self.tenant_headers = {"HTTP_X_EMPRESA_ID": str(self.company.pk)}

    def test_split_sale_with_credit_creates_receivable_and_decrements_stock(self):
        opened = self.api.post(
            "/api/v1/caixas/abrir/",
            {"dispositivo_id": "qa-terminal", "saldo_abertura": "50.00"},
            format="json", **self.tenant_headers,
        )
        self.assertIn(opened.status_code, (200, 201), opened.content)
        sale = self.api.post(
            "/api/v1/vendas/",
            {
                "idempotency_key": str(uuid.uuid4()),
                "caixa": opened.data["id"],
                "cliente": self.customer.pk,
                "credito": "20.00",
                "itens": [{"produto": self.product.pk, "quantidade": "10", "desconto": "0"}],
                "pagamentos": [{"meio": "PIX", "valor": "80.00"}],
            },
            format="json", **self.tenant_headers,
        )
        self.assertEqual(sale.status_code, 201, sale.data)
        self.product.refresh_from_db()
        self.assertEqual(self.product.estoque_atual, Decimal("90.000"))
        account = ContaReceber.objects.get(venda_id=sale.data["id"])
        self.assertEqual(account.saldo, Decimal("20.00"))

    def test_sale_and_receipt_convert_pack_to_stock_units(self):
        self.product.unidade = "CX"
        self.product.unidade_estoque = "UN"
        self.product.fator_conversao = Decimal("12")
        self.product.preco_venda = Decimal("120.00")
        self.product.estoque_atual = Decimal("36.000")
        self.product.save()
        opened = self.api.post(
            "/api/v1/caixas/abrir/",
            {"dispositivo_id": "qa-conversion", "saldo_abertura": "0"},
            format="json", **self.tenant_headers,
        )
        sale = self.api.post(
            "/api/v1/vendas/",
            {
                "idempotency_key": str(uuid.uuid4()),
                "caixa": opened.data["id"],
                "itens": [{"produto": self.product.pk, "quantidade": "2"}],
                "pagamentos": [{"meio": "PIX", "valor": "240.00"}],
            },
            format="json", **self.tenant_headers,
        )
        self.assertEqual(sale.status_code, 201, sale.data)
        self.product.refresh_from_db()
        self.assertEqual(self.product.estoque_atual, Decimal("12.000"))
        self.assertEqual(sale.data["itens"][0]["fator_conversao"], "12.000000")

        supplier = Fornecedor.objects.create(empresa=self.company, nome="Fornecedor de caixas")
        purchase = CompraEntrada.objects.create(
            empresa=self.company, fornecedor=supplier, numero_nf="NF-CX-1",
            valor_total=Decimal("120.00"), criado_por=self.user,
        )
        ItemCompraEntrada.objects.create(
            compra=purchase, produto=self.product, codigo_xml="CX",
            descricao_xml=self.product.nome, unidade_xml="CX",
            quantidade=Decimal("1"), custo_unitario=Decimal("120"), total=Decimal("120"),
        )
        confirmar_recebimento(compra_id=purchase.pk, empresa=self.company, usuario=self.user)
        self.product.refresh_from_db()
        self.assertEqual(self.product.estoque_atual, Decimal("24.000"))

    def test_receiving_purchase_creates_payable_and_partial_payment(self):
        supplier = Fornecedor.objects.create(empresa=self.company, nome="Fornecedor QA")
        purchase = CompraEntrada.objects.create(
            empresa=self.company, fornecedor=supplier, numero_nf="NF-QA-1",
            valor_total=Decimal("30.00"), criado_por=self.user,
        )
        ItemCompraEntrada.objects.create(
            compra=purchase, produto=self.product, codigo_xml=self.product.codigo,
            descricao_xml=self.product.nome, unidade_xml="SC",
            quantidade=Decimal("2.000"), custo_unitario=Decimal("15.0000"),
            total=Decimal("30.00"),
        )
        confirmar_recebimento(compra_id=purchase.pk, empresa=self.company, usuario=self.user)
        payable = ContaPagar.objects.get(compra=purchase)
        self.assertEqual(payable.saldo, Decimal("30.00"))
        response = self.api.post(
            "/api/v1/contas-pagar/",
            {"conta": payable.pk, "valor": "10.00", "meio": "PIX"},
            format="json", **self.tenant_headers,
        )
        self.assertEqual(response.status_code, 200, response.data)
        payable.refresh_from_db()
        self.assertEqual(payable.saldo, Decimal("20.00"))
        self.assertEqual(payable.estado, ContaPagar.Estado.PARCIAL)

    def test_web_order_keeps_delivery_request(self):
        from django.utils import timezone
        response = self.api.post(
            "/api/v1/pedidos-web/",
            {
                "cliente_nome": "Cliente Entrega",
                "cliente_telefone": "5581999990000",
                "endereco_entrega": "Rua de Teste, 100",
                "entrega_solicitada_em": timezone.localdate().isoformat(),
                "itens": [{"produto": self.product.pk, "quantidade": "2"}],
            },
            format="json", **self.tenant_headers,
        )
        self.assertEqual(response.status_code, 201, response.data)
        order = PedidoWeb.objects.get(pk=response.data["id"])
        self.assertEqual(order.endereco_entrega, "Rua de Teste, 100")
        self.assertEqual(order.entrega_solicitada_em, timezone.localdate())

    def test_stock_loss_reduces_balance_and_is_audited(self):
        from django.test import Client
        browser = Client()
        browser.force_login(self.user)
        response = browser.post(
            f"/empresa/{self.company.pk}/produtos/{self.product.pk}/perda/",
            {"quantidade": "3", "motivo": "Material danificado"},
        )
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.estoque_atual, Decimal("97.000"))
        movement = MovimentacaoEstoque.objects.get(produto=self.product)
        self.assertEqual(movement.tipo, MovimentacaoEstoque.Tipo.PERDA)
        self.assertEqual(movement.quantidade, Decimal("-3"))

    def test_company_finance_screens_render_for_member(self):
        from django.test import Client
        browser = Client()
        browser.force_login(self.user)
        for path in (
            f"/empresa/{self.company.pk}/",
            f"/empresa/{self.company.pk}/clientes/",
            f"/empresa/{self.company.pk}/contas-receber/",
            f"/empresa/{self.company.pk}/contas-pagar/",
            f"/empresa/{self.company.pk}/compras/",
            f"/empresa/{self.company.pk}/produtos/",
        ):
            with self.subTest(path=path):
                self.assertEqual(browser.get(path).status_code, 200)


class CompanyDocumentSettingsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('fiscal-admin', password='test-only')
        self.company = Empresa.objects.create(nome='Loja', slug='fiscal-loja')
        self.membership = MembroEmpresa.objects.create(empresa=self.company, usuario=self.user, papel='ADMIN')
        self.client.force_login(self.user)

    def test_admin_saves_company_data_and_checkout_exposes_document_identity(self):
        response = self.client.post(f'/empresa/{self.company.pk}/configuracao/', {
            'nome': 'Loja', 'razao_social': 'Empresa & Filhos', 'cnpj': '12345678000199',
            'uf': 'PE', 'cep': '55810000', 'codigo_municipio': '2604007',
            'ambiente_fiscal': 'HOMOLOGACAO', 'frete_padrao': '0',
        })
        self.assertEqual(response.status_code, 302)
        self.company.refresh_from_db()
        self.assertEqual(self.company.razao_social, 'Empresa & Filhos')
        response = self.client.get(f'/empresa/{self.company.pk}/caixa/')
        self.assertContains(response, 'pos-company-details')
        self.assertContains(response, '12345678000199')

    def test_manager_cannot_modify_fiscal_identity(self):
        self.membership.papel = 'GERENTE'
        self.membership.save()
        self.client.post(f'/empresa/{self.company.pk}/configuracao/', {'cnpj': '12345678000199', 'nome': 'Changed'})
        self.company.refresh_from_db()
        self.assertEqual(self.company.cnpj, '')
        self.assertEqual(self.company.nome, 'Loja')

    def test_other_tenant_cannot_edit_company(self):
        other = Empresa.objects.create(nome='Outra', slug='outra-fiscal')
        self.assertEqual(self.client.get(f'/empresa/{other.pk}/configuracao/').status_code, 403)


class DesktopCustomerQuoteFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('desktop-cashier', password='test-only')
        self.company = Empresa.objects.create(nome='PDV QA', slug='pdv-qa')
        self.member = MembroEmpresa.objects.create(empresa=self.company, usuario=self.user, papel='CAIXA')
        category = Categoria.objects.create(empresa=self.company, nome='QA')
        self.product = Produto.objects.create(empresa=self.company, categoria=category, codigo='QA', nome='Item', unidade='UN', preco_venda='10', custo_medio='5', estoque_atual='100')
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.headers = {'HTTP_X_EMPRESA_ID': str(self.company.pk)}
        self.customer = self.api.post('/api/v1/clientes/cpf/', {'cpf': '529.982.247-25', 'nome': 'Cliente QA', 'limite_credito': '9000'}, format='json', **self.headers)
        self.assertEqual(self.customer.status_code, 201, self.customer.data)
        self.customer = self.customer.data

    def create_quote(self):
        from django.utils import timezone
        payload = {'chave': str(uuid.uuid4()), 'cliente': self.customer['id'], 'validade': timezone.localdate().isoformat(), 'itens': [{'produto': self.product.pk, 'quantidade': '2', 'desconto': '0'}]}
        response = self.api.post('/api/v1/orcamentos/', payload, format='json', **self.headers)
        self.assertEqual(response.status_code, 201, response.data)
        return response.data, payload

    def test_cashier_lookup_duplicate_cpf_and_no_credit_escalation(self):
        response = self.api.get('/api/v1/clientes/cpf/', {'cpf': '52998224725'}, **self.headers)
        self.assertEqual(response.data['id'], self.customer['id'])
        self.assertEqual(Decimal(response.data['limite_credito']), 0)
        repeated = self.api.post('/api/v1/clientes/cpf/', {'cpf': '52998224725', 'nome': 'Outro'}, format='json', **self.headers)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(Cliente.objects.filter(empresa=self.company).count(), 1)
        invalid = self.api.get('/api/v1/clientes/cpf/', {'cpf': '11111111111'}, **self.headers)
        self.assertEqual(invalid.status_code, 400)

    def test_quote_search_retry_and_stock_not_reserved(self):
        quote, payload = self.create_quote()
        repeated = self.api.post('/api/v1/orcamentos/', payload, format='json', **self.headers)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.data['id'], quote['id'])
        for params in ({'busca': str(quote['id'])}, {'busca': '529.982.247-25', 'tipo': 'cpf'}):
            result = self.api.get('/api/v1/orcamentos/', params, **self.headers)
            self.assertEqual(result.data[0]['id'], quote['id'])
        self.product.refresh_from_db()
        self.assertEqual(self.product.estoque_atual, Decimal('100'))

    def test_cross_company_and_cashier_discount_rejected(self):
        quote, payload = self.create_quote()
        other = Empresa.objects.create(nome='Outra', slug='other-desktop')
        MembroEmpresa.objects.create(empresa=other, usuario=self.user, papel='ADMIN')
        response = self.api.get(f"/api/v1/orcamentos/{quote['id']}/", HTTP_X_EMPRESA_ID=str(other.pk))
        self.assertEqual(response.status_code, 404)
        payload['chave'] = str(uuid.uuid4())
        payload['itens'][0]['desconto'] = '1'
        response = self.api.post('/api/v1/orcamentos/', payload, format='json', **self.headers)
        self.assertEqual(response.status_code, 400)

    def test_quote_conversion_is_idempotent_and_cannot_be_sold_twice(self):
        quote, _ = self.create_quote()
        cash = self.api.post('/api/v1/caixas/abrir/', {'dispositivo_id': 'desktop'}, format='json', **self.headers).data
        payload = {'idempotency_key': str(uuid.uuid4()), 'caixa': cash['id'], 'cliente': self.customer['id'], 'orcamento': quote['id'], 'total_confirmado': '20.00', 'itens': [{'produto': self.product.pk, 'quantidade': '2', 'desconto': '0'}], 'pagamentos': [{'meio': 'PIX', 'valor': '10'}, {'meio': 'DINHEIRO', 'valor': '10', 'valor_recebido': '15', 'troco': '5'}]}
        sale = self.api.post('/api/v1/vendas/', payload, format='json', **self.headers)
        self.assertEqual(sale.status_code, 201, sale.data)
        retry = self.api.post('/api/v1/vendas/', payload, format='json', **self.headers)
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(sale.data['id'], retry.data['id'])
        payload['idempotency_key'] = str(uuid.uuid4())
        self.assertEqual(self.api.post('/api/v1/vendas/', payload, format='json', **self.headers).status_code, 400)
        self.product.refresh_from_db()
        self.assertEqual(self.product.estoque_atual, Decimal('98'))

    def test_expired_quote_and_changed_prices_block_conversion(self):
        from .models import Orcamento
        from django.utils import timezone
        from datetime import timedelta
        quote, _ = self.create_quote()
        cash = self.api.post('/api/v1/caixas/abrir/', {'dispositivo_id': 'desktop'}, format='json', **self.headers).data
        payload = {'idempotency_key': str(uuid.uuid4()), 'caixa': cash['id'], 'cliente': self.customer['id'], 'orcamento': quote['id'], 'total_confirmado': '20.00', 'itens': [{'produto': self.product.pk, 'quantidade': '2'}], 'pagamentos': [{'meio': 'PIX', 'valor': '20'}]}
        self.product.preco_venda = Decimal('11')
        self.product.save()
        self.assertEqual(self.api.post('/api/v1/vendas/', payload, format='json', **self.headers).status_code, 400)
        Orcamento.objects.filter(pk=quote['id']).update(validade=timezone.localdate() - timedelta(days=1))
        self.assertEqual(self.api.post('/api/v1/vendas/', payload, format='json', **self.headers).status_code, 400)

    def test_total_discount_and_zero_total_require_authorized_role(self):
        self.member.papel = 'ADMIN'
        self.member.save()
        cash = self.api.post('/api/v1/caixas/abrir/', {'dispositivo_id': 'desktop'}, format='json', **self.headers).data
        payload = {'idempotency_key': str(uuid.uuid4()), 'caixa': cash['id'], 'cliente': self.customer['id'], 'itens': [{'produto': self.product.pk, 'quantidade': '2', 'desconto': '2'}], 'pagamentos': [{'meio': 'PIX', 'valor': '18'}]}
        sale = self.api.post('/api/v1/vendas/', payload, format='json', **self.headers)
        self.assertEqual(sale.status_code, 201, sale.data)
        self.assertEqual(Decimal(sale.data['total']), Decimal('18'))
        payload['idempotency_key'] = str(uuid.uuid4())
        payload['itens'][0]['desconto'] = '20'
        payload['pagamentos'] = []
        sale = self.api.post('/api/v1/vendas/', payload, format='json', **self.headers)
        self.assertEqual(sale.status_code, 201, sale.data)
        self.assertEqual(Decimal(sale.data['total']), 0)
        self.member.papel = 'CAIXA'
        self.member.save()
        payload['idempotency_key'] = str(uuid.uuid4())
        self.assertEqual(self.api.post('/api/v1/vendas/', payload, format='json', **self.headers).status_code, 400)


class UnknownCPFResponseTests(TestCase):
    def test_unknown_cpf_returns_explicit_json_instead_of_empty_body(self):
        user = get_user_model().objects.create_user('cpf-lookup', password='test-only')
        company = Empresa.objects.create(nome='CPF QA', slug='cpf-qa')
        MembroEmpresa.objects.create(empresa=company, usuario=user, papel='CAIXA')
        api = APIClient()
        api.force_authenticate(user)
        response = api.get('/api/v1/clientes/cpf/', {'cpf': '52998224725'}, HTTP_X_EMPRESA_ID=str(company.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'null')
        self.assertIsNone(response.json())
