import uuid
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from rest_framework.test import APIClient

from .models import (
    Categoria, Cliente, ContaReceber, Empresa, Fornecedor, MembroEmpresa, MovimentacaoEstoque,
    Produto, Venda,
)


class LimparEmpresaTests(TestCase):
    def _popular(self, empresa, usuario, sufixo):
        cat = Categoria.objects.create(empresa=empresa, nome=f"Cat {sufixo}")
        produto = Produto.objects.create(
            empresa=empresa, categoria=cat, codigo=f"P-{sufixo}", nome="Item", unidade="UN",
            preco_venda=Decimal("10"), custo_medio=Decimal("5"), estoque_atual=Decimal("100"),
        )
        cliente = Cliente.objects.create(empresa=empresa, nome="Cliente", limite_credito=Decimal("100"))
        Fornecedor.objects.create(empresa=empresa, nome="Fornecedor")
        api = APIClient()
        api.force_authenticate(user=usuario)
        h = {"HTTP_X_EMPRESA_ID": str(empresa.pk)}
        caixa = api.post("/api/v1/caixas/abrir/", {"dispositivo_id": f"t-{sufixo}", "saldo_abertura": "0"},
                         format="json", **h)
        venda = api.post("/api/v1/vendas/", {
            "idempotency_key": str(uuid.uuid4()), "caixa": caixa.data["id"], "cliente": cliente.pk,
            "credito": "20.00", "itens": [{"produto": produto.pk, "quantidade": "10"}],
            "pagamentos": [{"meio": "PIX", "valor": "80.00"}],
        }, format="json", **h)
        self.assertEqual(venda.status_code, 201, venda.data)

    def setUp(self):
        U = get_user_model()
        self.dono = U.objects.create_user("dono", password="Senha#Forte123")
        self.outro = U.objects.create_user("outro", password="Senha#Forte123")
        self.g4 = Empresa.objects.create(nome="Armazém G4", slug="armazem-g4")
        self.vizinha = Empresa.objects.create(nome="Vizinha", slug="vizinha")
        MembroEmpresa.objects.create(empresa=self.g4, usuario=self.dono, papel="ADMIN")
        MembroEmpresa.objects.create(empresa=self.vizinha, usuario=self.outro, papel="ADMIN")
        self._popular(self.g4, self.dono, "g4")
        self._popular(self.vizinha, self.outro, "viz")

    def test_simulacao_nao_apaga(self):
        out = StringIO()
        call_command("limpar_empresa", "--empresa", "armazém g4", stdout=out)
        self.assertIn("Simulação", out.getvalue())
        self.assertTrue(Venda.objects.filter(empresa=self.g4).exists())

    def test_apaga_so_a_empresa_alvo_e_mantem_empresa_e_usuarios(self):
        call_command("limpar_empresa", "--empresa", str(self.g4.pk), "--confirmar", "--sim", stdout=StringIO())
        for modelo in (Produto, Categoria, Cliente, Fornecedor, Venda, ContaReceber, MovimentacaoEstoque):
            self.assertFalse(modelo.objects.filter(empresa=self.g4).exists(), modelo.__name__)
            self.assertTrue(modelo.objects.filter(empresa=self.vizinha).exists(), modelo.__name__)
        self.assertTrue(Empresa.objects.filter(pk=self.g4.pk).exists())
        self.assertTrue(MembroEmpresa.objects.filter(empresa=self.g4, usuario=self.dono).exists())
        out = StringIO()
        call_command("limpar_empresa", "--empresa", str(self.g4.pk), stdout=out)
        self.assertIn("já está limpa", out.getvalue())

    def test_empresa_inexistente(self):
        with self.assertRaises(CommandError):
            call_command("limpar_empresa", "--empresa", "Não existe", stdout=StringIO())
