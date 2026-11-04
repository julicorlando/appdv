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
