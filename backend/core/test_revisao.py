"""Testes das correções da revisão de novembro/2026 (caixa, crédito, acesso, logos)."""
import shutil
import tempfile
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    Caixa, Categoria, Cliente, ContaReceber, Empresa, EventoGestao, MembroEmpresa, Produto,
    RecebimentoConta, TokenAutorizacao, Transacao, UsuarioPerfil, Venda,
)


class BaseRevisao(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user("gerente", password="Gerente#Seguro123")
        self.company = Empresa.objects.create(nome="Loja", slug="loja-revisao")
        MembroEmpresa.objects.create(empresa=self.company, usuario=self.user, papel="ADMIN")
        category = Categoria.objects.create(empresa=self.company, nome="Geral")
        self.product = Produto.objects.create(
            empresa=self.company, categoria=category, codigo="P1", nome="Produto",
            preco_venda=Decimal("10.00"), estoque_atual=Decimal("100"),
        )
        self.customer = Cliente.objects.create(empresa=self.company, nome="Cliente", limite_credito=Decimal("500"))
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.h = {"HTTP_X_EMPRESA_ID": str(self.company.pk)}

    def abrir_caixa(self, dispositivo="pdv-1", saldo="0"):
        response = self.api.post("/api/v1/caixas/abrir/", {"dispositivo_id": dispositivo, "saldo_abertura": saldo}, format="json", **self.h)
        self.assertIn(response.status_code, (200, 201), response.content)
        return response.data["id"]

    def fechar_caixa(self, caixa_id):
        response = self.api.post(f"/api/v1/caixas/{caixa_id}/fechar/", {"saldo_fechamento": "0"}, format="json", **self.h)
        self.assertEqual(response.status_code, 200, response.content)

    def vender(self, caixa_id, pagamentos, credito="0", quantidade="1"):
        response = self.api.post("/api/v1/vendas/", {
            "idempotency_key": str(uuid.uuid4()), "caixa": caixa_id, "cliente": self.customer.pk,
            "credito": credito, "itens": [{"produto": self.product.pk, "quantidade": quantidade}],
            "pagamentos": pagamentos,
        }, format="json", **self.h)
        self.assertEqual(response.status_code, 201, response.content)
        return response.data["id"]

    def token(self, venda_id, acao=TokenAutorizacao.Acao.CANCELAR_VENDA):
        codigo = TokenAutorizacao.gerar_codigo()
        TokenAutorizacao.objects.create(
            empresa=self.company, token_hash=TokenAutorizacao.hash_token(codigo), acao=acao,
            venda_id=venda_id, criado_por=self.user, expira_em=timezone.now() + timedelta(minutes=5),
        )
        return codigo

    def cancelar(self, venda_id):
        return self.api.post(f"/api/v1/vendas/{venda_id}/cancelar/", {"token": self.token(venda_id)}, format="json", **self.h)

    def resumo(self, caixa_id):
        response = self.api.get(f"/api/v1/caixas/{caixa_id}/resumo/", **self.h)
        self.assertEqual(response.status_code, 200, response.content)
        return response.data


class CancelamentoVendaPrazoTests(BaseRevisao):
    def test_cancelar_venda_a_prazo_cancela_conta_a_receber(self):
        caixa = self.abrir_caixa()
        venda = self.vender(caixa, [{"meio": "PIX", "valor": "4.00"}], credito="6.00")
        conta = ContaReceber.objects.get(venda_id=venda)
        self.assertEqual(conta.saldo, Decimal("6.00"))
        response = self.cancelar(venda)
        self.assertEqual(response.status_code, 200, response.content)
        conta.refresh_from_db()
        self.assertEqual(conta.estado, ContaReceber.Estado.CANCELADA)
        self.assertEqual(conta.saldo, Decimal("0.00"))
        # A conta cancelada não ocupa mais o limite do cliente nem aparece na cobrança.
        abertas = self.api.get("/api/v1/contas-receber/", **self.h).data
        self.assertEqual(abertas, [])

    def test_cancelar_venda_a_prazo_devolve_parcela_recebida(self):
        caixa = self.abrir_caixa(saldo="50.00")
        venda = self.vender(caixa, [], credito="10.00")
        conta = ContaReceber.objects.get(venda_id=venda)
        pago = self.api.post("/api/v1/contas-receber/", {"conta": conta.pk, "valor": "4.00", "meio": "DINHEIRO"}, format="json", **self.h)
        self.assertEqual(pago.status_code, 200, pago.content)
        self.assertEqual(Decimal(self.resumo(caixa)["esperado_dinheiro"]), Decimal("54.00"))
        self.assertEqual(self.cancelar(venda).status_code, 200)
        estorno = Transacao.objects.get(venda_id=venda, tipo="ESTORNO")
        self.assertEqual((estorno.meio, estorno.valor, estorno.caixa_id), ("DINHEIRO", Decimal("4.00"), caixa))
        self.assertEqual(Decimal(self.resumo(caixa)["esperado_dinheiro"]), Decimal("50.00"))


class EstornoNoCaixaCorretoTests(BaseRevisao):
    def test_estorno_entra_no_caixa_de_quem_cancela(self):
        ontem = self.abrir_caixa("pdv-ontem", "100.00")
        venda = self.vender(ontem, [{"meio": "DINHEIRO", "valor": "10.00"}])
        self.fechar_caixa(ontem)
        esperado_ontem = Caixa.objects.get(pk=ontem).saldo_esperado_fechamento
        hoje = self.abrir_caixa("pdv-hoje", "100.00")
        self.assertEqual(self.cancelar(venda).status_code, 200)
        self.assertEqual(Decimal(self.resumo(hoje)["esperado_dinheiro"]), Decimal("90.00"))
        # O caixa já fechado não muda depois do fechamento.
        self.assertEqual(Decimal(self.resumo(ontem)["esperado_dinheiro"]), esperado_ontem)
        self.assertEqual(Transacao.objects.get(venda_id=venda, tipo="ESTORNO").caixa_id, hoje)

    def test_estorno_em_dinheiro_exige_caixa_aberto(self):
        caixa = self.abrir_caixa()
        venda = self.vender(caixa, [{"meio": "DINHEIRO", "valor": "10.00"}])
        self.fechar_caixa(caixa)
        response = self.cancelar(venda)
        self.assertEqual(response.status_code, 400)
        self.assertIn("caixa", response.data)
        self.assertEqual(Venda.objects.get(pk=venda).estado, Venda.Estado.CONCLUIDA)

    def test_estorno_pix_sem_caixa_aberto_e_permitido(self):
        caixa = self.abrir_caixa()
        venda = self.vender(caixa, [{"meio": "PIX", "valor": "10.00"}])
        self.fechar_caixa(caixa)
        self.assertEqual(self.cancelar(venda).status_code, 200)
        self.assertIsNone(Transacao.objects.get(venda_id=venda, tipo="ESTORNO").caixa_id)

    def test_devolucao_com_estorno_em_dinheiro_usa_caixa_atual(self):
        from .management_services import devolver
        ontem = self.abrir_caixa("pdv-ontem", "0")
        venda = self.vender(ontem, [{"meio": "DINHEIRO", "valor": "20.00"}], quantidade="2")
        self.fechar_caixa(ontem)
        item = Venda.objects.get(pk=venda).itens.get()
        with self.assertRaises(Exception):
            devolver(self.company, self.user, item.pk, "1", "ESTOQUE", "ESTORNO", "Defeito", uuid.uuid4(), "DINHEIRO")
        hoje = self.abrir_caixa("pdv-hoje", "50.00")
        devolver(self.company, self.user, item.pk, "1", "ESTOQUE", "ESTORNO", "Defeito", uuid.uuid4(), "DINHEIRO")
        self.assertEqual(Decimal(self.resumo(hoje)["esperado_dinheiro"]), Decimal("40.00"))


class RecebimentoNoCaixaTests(BaseRevisao):
    def setUp(self):
        super().setUp()
        caixa = self.abrir_caixa("pdv-venda")
        self.venda = self.vender(caixa, [], credito="30.00")
        self.fechar_caixa(caixa)
        self.conta = ContaReceber.objects.get(venda_id=self.venda)

    def receber(self, meio, valor="10.00"):
        return self.api.post("/api/v1/contas-receber/", {"conta": self.conta.pk, "valor": valor, "meio": meio}, format="json", **self.h)

    def test_recebimento_em_dinheiro_soma_no_caixa(self):
        caixa = self.abrir_caixa("pdv-balcao", "20.00")
        self.assertEqual(self.receber("DINHEIRO").status_code, 200)
        resumo = self.resumo(caixa)
        self.assertEqual(Decimal(resumo["esperado_dinheiro"]), Decimal("30.00"))
        self.assertEqual(resumo["recebimentos_conta_por_meio"], {"DINHEIRO": "10.00"})
        self.assertEqual(RecebimentoConta.objects.get().caixa_id, caixa)

    def test_recebimento_em_dinheiro_sem_caixa_e_recusado(self):
        response = self.receber("DINHEIRO")
        self.assertEqual(response.status_code, 400)
        self.conta.refresh_from_db()
        self.assertEqual(self.conta.saldo, Decimal("30.00"))

    def test_recebimento_pix_sem_caixa_continua_permitido(self):
        self.assertEqual(self.receber("PIX").status_code, 200)
        self.assertIsNone(RecebimentoConta.objects.get().caixa_id)

    def test_painel_recusa_dinheiro_sem_caixa_com_mensagem(self):
        self.client.force_login(self.user)
        response = self.client.post(
            f"/empresa/{self.company.pk}/contas-receber/",
            {"conta": self.conta.pk, "valor": "5.00", "meio": "DINHEIRO"}, follow=True,
        )
        self.assertContains(response, "Abra o seu caixa")
        self.assertNotContains(response, "ErrorDetail")


class ContaDesativadaTests(BaseRevisao):
    def setUp(self):
        super().setUp()
        UsuarioPerfil.objects.create(usuario=self.user, ativo=False)

    def test_api_completa_bloqueia_conta_desativada(self):
        for url in ("/api/v1/produtos/", "/api/v1/vendas/", "/api/v1/categorias/", "/api/v1/empresa/", "/api/v1/minhas-empresas/"):
            self.assertEqual(self.api.get(url, **self.h).status_code, 403, url)
        response = self.api.post("/api/v1/vendas/", {"idempotency_key": str(uuid.uuid4())}, format="json", **self.h)
        self.assertEqual(response.status_code, 403)

    def test_painel_bloqueia_conta_desativada(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(f"/empresa/{self.company.pk}/produtos/").status_code, 403)

    def test_login_recusa_conta_desativada(self):
        response = APIClient().post("/api/v1/auth/token/", {"username": "gerente", "password": "Gerente#Seguro123"}, format="json")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("access", response.data)


class LimiteDeTentativasTests(BaseRevisao):
    def test_login_api_limita_tentativas_por_usuario(self):
        client = APIClient()
        for _ in range(10):
            self.assertEqual(client.post("/api/v1/auth/token/", {"username": "gerente", "password": "errada"}, format="json").status_code, 401)
        bloqueado = client.post("/api/v1/auth/token/", {"username": "gerente", "password": "Gerente#Seguro123"}, format="json")
        self.assertEqual(bloqueado.status_code, 429)
        outro = client.post("/api/v1/auth/token/", {"username": "outro", "password": "x"}, format="json")
        self.assertEqual(outro.status_code, 401)

    def test_login_api_continua_funcionando(self):
        response = APIClient().post("/api/v1/auth/token/", {"username": "gerente", "password": "Gerente#Seguro123"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("access", response.data)

    def test_login_web_limita_tentativas(self):
        for _ in range(10):
            self.client.post("/accounts/login/", {"username": "gerente", "password": "errada"})
        response = self.client.post("/accounts/login/", {"username": "gerente", "password": "Gerente#Seguro123"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Muitas tentativas")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_web_normal(self):
        response = self.client.post("/accounts/login/", {"username": "gerente", "password": "Gerente#Seguro123"})
        self.assertEqual(response.status_code, 302)

    def test_autorizacao_limita_senha_do_mesmo_gerente(self):
        for _ in range(10):
            self.api.post("/api/v1/descontos/autorizar/", {"usuario": "gerente", "senha": "errada", "chave": str(uuid.uuid4()), "itens": []}, format="json", **self.h)
        response = self.api.post("/api/v1/descontos/autorizar/", {"usuario": "gerente", "senha": "errada", "chave": str(uuid.uuid4()), "itens": []}, format="json", **self.h)
        self.assertEqual(response.status_code, 429)


class DescarteVendaOfflineTests(BaseRevisao):
    def payload(self, chave, senha="Gerente#Seguro123"):
        return {"chave": chave, "usuario": "gerente", "senha": senha, "justificativa": "Preço alterado, relançada",
                "erro": "itens: Preço mudou", "venda": {"itens": [{"produto": self.product.pk, "quantidade": "1"}]}}

    def test_descarte_exige_gerente_e_fica_auditado(self):
        chave = str(uuid.uuid4())
        self.assertEqual(self.api.post("/api/v1/vendas-offline/descartar/", self.payload(chave, "errada"), format="json", **self.h).status_code, 403)
        self.assertFalse(EventoGestao.objects.exists())
        response = self.api.post("/api/v1/vendas-offline/descartar/", self.payload(chave), format="json", **self.h)
        self.assertEqual(response.status_code, 201, response.content)
        evento = EventoGestao.objects.get(acao="VENDA_OFFLINE_DESCARTADA")
        self.assertEqual(evento.referencia, chave)
        self.assertEqual(evento.detalhes["aprovador"], "gerente")
        self.assertEqual(evento.detalhes["venda"]["itens"][0]["quantidade"], "1")

    def test_caixa_nao_pode_aprovar_descarte(self):
        caixa_user = get_user_model().objects.create_user("caixa1", password="Caixa#Seguro123")
        MembroEmpresa.objects.create(empresa=self.company, usuario=caixa_user, papel="CAIXA")
        data = self.payload(str(uuid.uuid4()))
        data.update(usuario="caixa1", senha="Caixa#Seguro123")
        self.assertEqual(self.api.post("/api/v1/vendas-offline/descartar/", data, format="json", **self.h).status_code, 403)

    def test_venda_ja_registrada_nao_e_descartada(self):
        caixa = self.abrir_caixa()
        chave = str(uuid.uuid4())
        self.api.post("/api/v1/vendas/", {"idempotency_key": chave, "caixa": caixa, "itens": [{"produto": self.product.pk, "quantidade": "1"}],
                                          "pagamentos": [{"meio": "PIX", "valor": "10.00"}]}, format="json", **self.h)
        response = self.api.post("/api/v1/vendas-offline/descartar/", self.payload(chave, "qualquer"), format="json", **self.h)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["sincronizada"])


class LogosTests(BaseRevisao):
    def setUp(self):
        super().setUp()
        self.media = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        (self.media / "empresas" / "logos").mkdir(parents=True)
        (self.media / "empresas" / "logos" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (self.media / "fiscal" / "xml").mkdir(parents=True)
        (self.media / "fiscal" / "xml" / "nota.xml").write_text("<nfe/>")

    def test_logo_publico_e_arquivos_fiscais_protegidos(self):
        with override_settings(MEDIA_ROOT=self.media):
            response = self.client.get("/media/empresas/logos/logo.png")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["X-Content-Type-Options"], "nosniff")
            self.assertEqual(self.client.get("/media/fiscal/xml/nota.xml").status_code, 404)
            self.assertIn(self.client.get("/media/empresas/logos/../../fiscal/xml/nota.xml").status_code, (400, 404))
