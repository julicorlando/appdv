"""Cobrança da plataforma: teste grátis, faturas, carência, suspensão automática, pagamento e Master."""
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from . import cobranca
from .models import Assinatura, ConfiguracaoMaster, Empresa, Fatura, MembroEmpresa, Plano, TerminalPDV


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.hoje = timezone.localdate()
        self.plano = Plano.objects.create(nome="Básico", valor_mensal=Decimal("99.90"), caixas_incluidos=1,
                                          valor_caixa_adicional=Decimal("30.00"))
        config = ConfiguracaoMaster.atual()
        config.plano_padrao = self.plano
        config.dias_teste = 15
        config.carencia_dias = 5
        config.save()
        self.dono = get_user_model().objects.create_user("dono", password="Dono#Seguro123")
        self.empresa = Empresa.objects.create(nome="Loja Cobrada", slug="loja-cobrada", email="loja@example.com")
        MembroEmpresa.objects.create(empresa=self.empresa, usuario=self.dono, papel="ADMIN")
        self.assinatura = cobranca.garantir_assinatura(self.empresa, self.hoje)
        self.master = get_user_model().objects.create_superuser("master", password="Master#Seguro123", email="m@example.com")

    def vencer(self, dias_atraso):
        """Fim do teste e uma fatura vencida há `dias_atraso` dias."""
        Assinatura.objects.filter(pk=self.assinatura.pk).update(teste_ate=self.hoje - timedelta(days=40))
        Fatura.objects.create(empresa=self.empresa, competencia=cobranca.competencia(self.hoje) - timedelta(days=1),
                              descricao="Mensalidade", valor=Decimal("99.90"),
                              vencimento=self.hoje - timedelta(days=dias_atraso))
        self.assinatura.refresh_from_db()
        return cobranca.processar_assinatura(self.assinatura, self.hoje)


class CicloTests(Base):
    def test_empresa_nova_comeca_em_teste(self):
        self.assertEqual(self.assinatura.estado, "TESTE")
        self.assertEqual(self.assinatura.teste_ate, self.hoje + timedelta(days=15))
        self.assertEqual(self.assinatura.plano, self.plano)

    def test_fim_do_teste_gera_fatura_com_vencimento_futuro(self):
        Assinatura.objects.filter(pk=self.assinatura.pk).update(teste_ate=self.hoje - timedelta(days=1), dia_vencimento=self.hoje.day if self.hoje.day <= 28 else 28)
        self.assinatura.refresh_from_db()
        cobranca.processar_assinatura(self.assinatura, self.hoje)
        fatura = Fatura.objects.get(empresa=self.empresa, competencia=cobranca.competencia(self.hoje))
        self.assertEqual(fatura.valor, Decimal("99.90"))
        if self.hoje.day <= 28:
            self.assertGreaterEqual(fatura.vencimento, self.hoje)

    def test_calculo_com_caixas_extras_desconto_e_valor_especial(self):
        for nome in ("pc1", "pc2", "pc3"):
            TerminalPDV.objects.create(empresa=self.empresa, operador=self.dono, identificador=nome)
        itens, total = cobranca.calcular(self.assinatura, self.hoje)
        self.assertEqual(total, Decimal("159.90"))
        self.assinatura.valor_especial = Decimal("50")
        self.assinatura.desconto_percentual = Decimal("10")
        itens, total = cobranca.calcular(self.assinatura, self.hoje)
        self.assertEqual(total, Decimal("99.00"))

    def test_carencia_atrasada_e_depois_suspensa(self):
        self.assertEqual(self.vencer(3).estado, "ATRASADA")
        Fatura.objects.all().delete()
        self.assertEqual(self.vencer(6).estado, "SUSPENSA")

    def test_isenta_nao_gera_fatura(self):
        Assinatura.objects.filter(pk=self.assinatura.pk).update(teste_ate=self.hoje - timedelta(days=40), isenta=True)
        self.assinatura.refresh_from_db()
        self.assertEqual(cobranca.processar_assinatura(self.assinatura, self.hoje).estado, "ISENTA")
        self.assertFalse(Fatura.objects.exists())

    def test_comando_diario(self):
        saida = StringIO()
        call_command("processar_cobranca", stdout=saida)
        self.assertIn("Cobrança processada", saida.getvalue())


class BloqueioTests(Base):
    def test_suspensa_bloqueia_painel_e_pdv_e_baixa_libera(self):
        self.vencer(10)
        self.client.force_login(self.dono)
        resposta = self.client.get(f"/empresa/{self.empresa.pk}/produtos/")
        self.assertRedirects(resposta, f"/empresa/{self.empresa.pk}/assinatura/", fetch_redirect_response=False)
        self.assertContains(self.client.get(f"/empresa/{self.empresa.pk}/assinatura/"), "suspenso")
        api = APIClient()
        api.force_authenticate(self.dono)
        self.assertEqual(api.get("/api/v1/produtos/", HTTP_X_EMPRESA_ID=str(self.empresa.pk)).status_code, 403)
        cobranca.baixa_manual(Fatura.objects.get(estado="VENCIDA"), self.master, "PIX")
        self.assinatura.refresh_from_db()
        self.assertEqual(self.assinatura.estado, "ATIVA")
        self.assertEqual(self.client.get(f"/empresa/{self.empresa.pk}/produtos/").status_code, 200)
        self.assertEqual(api.get("/api/v1/produtos/", HTTP_X_EMPRESA_ID=str(self.empresa.pk)).status_code, 200)

    def test_aviso_de_atraso_aparece_no_painel(self):
        self.vencer(2)
        self.client.force_login(self.dono)
        self.assertContains(self.client.get(f"/empresa/{self.empresa.pk}/produtos/"), "vencida")


class PagamentoTests(Base):
    def setUp(self):
        super().setUp()
        config = ConfiguracaoMaster.atual()
        config.mp_access_token = "APP_USR-master"
        config.save()
        self.vencer(10)
        self.fatura = Fatura.objects.get(estado="VENCIDA")

    def test_link_e_webhook_liberam_a_empresa(self):
        chamadas = []

        def enviar(metodo, url, **kwargs):
            chamadas.append((metodo, url, kwargs))
            if url.endswith("/checkout/preferences"):
                return 201, {"id": "pref-1", "init_point": "https://www.mercadopago.com.br/checkout/v1/redirect?pref_id=pref-1"}
            return 200, {"id": 555, "status": "approved", "external_reference": f"fatura-{self.fatura.pk}",
                         "transaction_amount": 99.9, "payment_method_id": "pix"}
        self.client.force_login(self.dono)
        with patch("core.integracoes_http.enviar", enviar):
            resposta = self.client.post(f"/empresa/{self.empresa.pk}/assinatura/faturas/{self.fatura.pk}/pagar/")
            self.assertEqual(resposta.status_code, 302)
            self.assertIn("mercadopago.com.br", resposta["Location"])
            corpo = chamadas[0][2]["corpo"]
            self.assertEqual(corpo["external_reference"], f"fatura-{self.fatura.pk}")
            self.assertEqual(chamadas[0][2]["token_bearer"], "APP_USR-master")
            self.assertEqual(APIClient().post("/api/v1/cobranca/webhook/", {"type": "payment", "data": {"id": "555"}}, format="json").status_code, 200)
        self.fatura.refresh_from_db()
        self.assinatura.refresh_from_db()
        self.assertEqual((self.fatura.estado, self.fatura.meio, self.assinatura.estado), ("PAGA", "PIX", "ATIVA"))

    def test_pagamento_menor_nao_baixa(self):
        with patch("core.integracoes_http.enviar", return_value=(200, {"id": 9, "status": "approved", "external_reference": f"fatura-{self.fatura.pk}", "transaction_amount": 10})):
            cobranca.consultar_pagamento("9")
        self.fatura.refresh_from_db()
        self.assertNotEqual(self.fatura.estado, "PAGA")


class MasterTests(Base):
    def test_master_entra_em_qualquer_empresa_mesmo_suspensa(self):
        self.vencer(10)
        self.client.force_login(self.master)
        self.assertEqual(self.client.get(f"/empresa/{self.empresa.pk}/produtos/").status_code, 403)
        self.client.post(f"/master/empresas/{self.empresa.pk}/entrar/")
        resposta = self.client.get(f"/empresa/{self.empresa.pk}/produtos/")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "como Master")
        self.client.post("/master/sair-da-empresa/")
        self.assertEqual(self.client.get(f"/empresa/{self.empresa.pk}/produtos/").status_code, 403)

    def test_telas_do_master(self):
        self.client.force_login(self.master)
        self.assertEqual(self.client.get("/master/cobranca/").status_code, 200)
        self.assertEqual(self.client.get(f"/master/empresas/{self.empresa.pk}/assinatura/").status_code, 200)
        self.client.post("/master/cobranca/planos/", {"nome": "Pro", "descricao": "", "valor_mensal": "199.90", "caixas_incluidos": "3",
                                                     "valor_caixa_adicional": "25", "limite_usuarios": "0", "limite_caixas": "0", "ordem": "1", "ativo": "on"})
        self.assertTrue(Plano.objects.filter(nome="Pro").exists())
        self.client.post(f"/master/empresas/{self.empresa.pk}/assinatura/", {
            "acao": "salvar", "plano": self.plano.pk, "teste_ate": "", "isenta": "on", "isencao_ate": "", "motivo_isencao": "Parceiro",
            "dia_vencimento": "10", "carencia_dias": "", "valor_especial": "", "desconto_percentual": "0", "observacao": ""})
        self.assinatura.refresh_from_db()
        self.assertEqual((self.assinatura.estado, self.assinatura.motivo_isencao), ("ISENTA", "Parceiro"))
        self.client.post("/master/cobranca/configuracao/", {"dias_teste": "30", "carencia_dias": "7", "dias_antecedencia": "10", "aviso_dias_antes": "3",
                                                            "plano_padrao": self.plano.pk, "mp_access_token": "APP_USR-x"})
        config = ConfiguracaoMaster.atual()
        self.assertEqual((config.dias_teste, config.mp_access_token), (30, "APP_USR-x"))
        self.assertNotContains(self.client.get("/master/cobranca/"), "APP_USR-x")

    def test_empresa_nao_acessa_master(self):
        self.client.force_login(self.dono)
        self.assertEqual(self.client.get("/master/cobranca/").status_code, 403)
