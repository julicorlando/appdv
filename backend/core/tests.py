from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import (
    Categoria, Cliente, CompraEntrada, ContaPagar, ContaReceber, Empresa,
    Fornecedor, ItemCompraEntrada, MembroEmpresa, Produto,
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
