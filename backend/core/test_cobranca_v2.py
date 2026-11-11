"""Cobrança v2: avisos por e-mail, cupons, limites do plano, troca de plano, cadastro próprio,
cartão recorrente, indicadores, auditoria, saúde e backup externo."""
import datetime
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.test import override_settings
from rest_framework.test import APIClient

from . import backup_s3, cobranca
from .models import Assinatura, ConfiguracaoMaster, Cupom, Empresa, EventoGestao, Fatura, MembroEmpresa, Plano, TerminalPDV
from .test_cobranca import Base


@override_settings(EMAIL_HOST="smtp.exemplo.com", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AvisosTests(Base):
    def test_fatura_gerada_vencida_suspensao_e_pagamento_avisam_uma_vez(self):
        self.dono.email = "dono@example.com"
        self.dono.save()
        self.vencer(10)  # fatura vencida há 10 dias → suspensa
        assuntos = [m.subject for m in mail.outbox]
        self.assertTrue(any("suspenso" in a for a in assuntos))
        cobranca.processar(self.hoje)
        enviados = len(mail.outbox)
        cobranca.processar(self.hoje)
        self.assertEqual(len(mail.outbox), enviados)  # nada repetido
        assuntos = [m.subject for m in mail.outbox]
        self.assertEqual(sum("atraso" in a for a in assuntos), 1)
        self.assertGreaterEqual(sum("disponível" in a for a in assuntos), 1)
        self.assertIn("loja@example.com", mail.outbox[0].to)
        self.assertIn("dono@example.com", mail.outbox[0].to)
        cobranca.baixa_manual(Fatura.objects.get(estado="VENCIDA"), self.master, "PIX")
        self.assertTrue(mail.outbox[-1].subject.endswith("Pagamento confirmado"))

    def test_sem_servidor_de_email_nao_envia(self):
        with override_settings(EMAIL_HOST=""):
            self.vencer(10)
        self.assertEqual(len(mail.outbox), 0)

    def test_aviso_desligado_no_master(self):
        config = ConfiguracaoMaster.atual()
        config.avisos_email = False
        config.save()
        self.vencer(10)
        self.assertEqual(len(mail.outbox), 0)


class CupomTests(Base):
    def test_cupom_desconta_pelos_meses_e_acaba(self):
        Cupom.objects.create(codigo="metade", desconto_percentual=50, meses=1)
        cobranca.aplicar_cupom(self.assinatura, "METADE")
        self.assinatura.refresh_from_db()
        self.assertEqual(cobranca.calcular(self.assinatura, self.hoje)[1], Decimal("49.95"))
        fatura = cobranca.gerar_fatura(self.assinatura, cobranca.competencia(self.hoje), self.hoje)
        self.assertEqual(fatura.valor, Decimal("49.95"))
        self.assinatura.refresh_from_db()
        self.assertEqual(self.assinatura.cupom_meses_restantes, 0)
        self.assertEqual(cobranca.calcular(self.assinatura, self.hoje)[1], Decimal("99.90"))

    def test_cupom_invalido_vencido_ou_esgotado(self):
        Cupom.objects.create(codigo="VELHO", desconto_percentual=10, valido_ate=self.hoje - datetime.timedelta(days=1))
        Cupom.objects.create(codigo="UNICO", desconto_percentual=10, usos_maximos=1, usos=1)
        for codigo in ("NAOEXISTE", "VELHO", "UNICO"):
            with self.assertRaises(Exception):
                cobranca.aplicar_cupom(self.assinatura, codigo)

    def test_empresa_aplica_cupom_e_troca_de_plano(self):
        Cupom.objects.create(codigo="BEMVINDO", desconto_percentual=20, meses=3)
        pro = Plano.objects.create(nome="Pro", valor_mensal=Decimal("199.90"))
        self.client.force_login(self.dono)
        url = f"/empresa/{self.empresa.pk}/assinatura/acao/"
        self.client.post(url, {"acao": "cupom", "codigo": "bemvindo"})
        self.client.post(url, {"acao": "plano", "plano": pro.pk})
        self.assinatura.refresh_from_db()
        self.assertEqual((self.assinatura.cupom.codigo, self.assinatura.plano, self.assinatura.cupom_meses_restantes), ("BEMVINDO", pro, 3))
        self.assertContains(self.client.get(f"/empresa/{self.empresa.pk}/assinatura/"), "Mudar para este")

    def test_caixa_nao_troca_plano(self):
        caixa = MembroEmpresa.objects.create(empresa=self.empresa, papel="CAIXA",
                                             usuario=self.dono.__class__.objects.create_user("caixa1", password="Caixa#Seguro123"))
        self.client.force_login(caixa.usuario)
        self.assertEqual(self.client.post(f"/empresa/{self.empresa.pk}/assinatura/acao/", {"acao": "plano", "plano": self.plano.pk}).status_code, 403)


class LimitesTests(Base):
    def test_limite_de_usuarios_na_equipe(self):
        self.plano.limite_usuarios = 1
        self.plano.save()
        self.client.force_login(self.dono)
        self.client.post(f"/empresa/{self.empresa.pk}/equipe/", {"username": "novo", "senha": "Novo#Seguro12345", "papel": "CAIXA"})
        self.assertFalse(MembroEmpresa.objects.filter(usuario__username="novo").exists())
        self.plano.limite_usuarios = 0
        self.plano.save()
        self.client.post(f"/empresa/{self.empresa.pk}/equipe/", {"username": "novo", "senha": "Novo#Seguro12345", "papel": "CAIXA"})
        self.assertTrue(MembroEmpresa.objects.filter(usuario__username="novo").exists())

    def test_limite_de_caixas_no_pdv(self):
        self.plano.limite_caixas = 1
        self.plano.save()
        api = APIClient()
        api.force_authenticate(self.dono)
        url = "/api/v1/terminais/heartbeat/"
        cabecalho = {"HTTP_X_EMPRESA_ID": str(self.empresa.pk)}
        self.assertEqual(api.post(url, {"identificador": "pc1", "pendentes": 0}, format="json", **cabecalho).status_code, 200)
        self.assertEqual(api.post(url, {"identificador": "pc1", "pendentes": 0}, format="json", **cabecalho).status_code, 200)
        resposta = api.post(url, {"identificador": "pc2", "pendentes": 0}, format="json", **cabecalho)
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(TerminalPDV.objects.filter(identificador="pc2").exists())

    def test_api_suspensa_responde_codigo_proprio(self):
        self.vencer(10)
        api = APIClient()
        api.force_authenticate(self.dono)
        resposta = api.get("/api/v1/produtos/", HTTP_X_EMPRESA_ID=str(self.empresa.pk))
        self.assertEqual(resposta.status_code, 403)
        self.assertEqual(resposta.json()["codigo"], "assinatura_suspensa")


class CadastroTests(Base):
    dados = {"empresa": "Mercadinho Novo", "cnpj": "12.345.678/0001-90", "usuario": "mercadinho", "email": "m@novo.com",
             "senha": "Mercado#Novo2026", "senha2": "Mercado#Novo2026", "aceite": "on"}

    def test_fechado_por_padrao(self):
        self.assertEqual(self.client.get("/cadastro/").status_code, 404)

    def test_cadastro_cria_empresa_em_teste_e_entra(self):
        config = ConfiguracaoMaster.atual()
        config.cadastro_aberto = True
        config.save()
        Cupom.objects.create(codigo="LANCAMENTO", desconto_percentual=30, meses=2)
        self.assertContains(self.client.get("/accounts/login/"), "Comece grátis")
        resposta = self.client.post("/cadastro/", {**self.dados, "cupom": "lancamento"})
        empresa = Empresa.objects.get(nome="Mercadinho Novo")
        self.assertRedirects(resposta, f"/empresa/{empresa.pk}/", fetch_redirect_response=False)
        self.assertEqual((empresa.cnpj, empresa.assinatura.estado, empresa.assinatura.cupom.codigo), ("12345678000190", "TESTE", "LANCAMENTO"))
        self.assertTrue(MembroEmpresa.objects.filter(empresa=empresa, usuario__username="mercadinho", papel="ADMIN").exists())

    def test_robo_e_senhas_diferentes(self):
        config = ConfiguracaoMaster.atual()
        config.cadastro_aberto = True
        config.save()
        self.client.post("/cadastro/", {**self.dados, "site": "http://spam"})
        self.client.post("/cadastro/", {**self.dados, "senha2": "Outra#Senha2026"})
        self.assertFalse(Empresa.objects.filter(nome="Mercadinho Novo").exists())


class RecorrenteTests(Base):
    def setUp(self):
        super().setUp()
        config = ConfiguracaoMaster.atual()
        config.mp_access_token = "APP_USR-master"
        config.save()

    def test_assinar_autorizar_e_cobranca_mensal_quita_fatura(self):
        respostas = {
            "/preapproval": (201, {"id": "pre-1", "status": "pending", "init_point": "https://www.mercadopago.com.br/subscriptions/checkout?x=1"}),
            "/preapproval/pre-1": (200, {"id": "pre-1", "status": "authorized", "external_reference": f"assinatura-{self.assinatura.pk}"}),
            "/authorized_payments/777": (200, {"id": 777, "preapproval_id": "pre-1", "transaction_amount": 99.9,
                                               "payment": {"id": 4242, "status": "approved"}}),
        }
        chamadas = []

        def enviar(metodo, url, **kwargs):
            chamadas.append((metodo, url, kwargs.get("corpo")))
            return respostas.get(url.replace(cobranca.API, ""), (200, {}))
        self.client.force_login(self.dono)
        with patch("core.integracoes_http.enviar", enviar):
            resposta = self.client.post(f"/empresa/{self.empresa.pk}/assinatura/acao/", {"acao": "cartao"})
            self.assertIn("mercadopago.com.br", resposta["Location"])
            self.assertEqual(chamadas[0][2]["auto_recurring"]["transaction_amount"], 99.9)
            self.assertEqual(chamadas[0][2]["payer_email"], "loja@example.com")
            webhook = APIClient()
            webhook.post("/api/v1/cobranca/webhook/", {"type": "subscription_preapproval", "data": {"id": "pre-1"}}, format="json")
            self.assinatura.refresh_from_db()
            self.assertTrue(self.assinatura.recorrente)
            self.vencer(2)
            webhook.post("/api/v1/cobranca/webhook/", {"type": "subscription_authorized_payment", "data": {"id": "777"}}, format="json")
            webhook.post("/api/v1/cobranca/webhook/", {"type": "subscription_authorized_payment", "data": {"id": "777"}}, format="json")
        paga = Fatura.objects.get(estado="PAGA")
        self.assertEqual((paga.meio, paga.mp_payment_id), ("CARTAO_RECORRENTE", "4242"))
        self.assertEqual(Fatura.objects.filter(estado="PAGA").count(), 1)
        self.assinatura.refresh_from_db()
        self.assertEqual(self.assinatura.estado, "ATIVA")

    def test_troca_de_plano_atualiza_valor_no_cartao(self):
        Assinatura.objects.filter(pk=self.assinatura.pk).update(mp_preapproval_id="pre-9", mp_preapproval_status="authorized",
                                                                  mp_valor_recorrente=Decimal("99.90"))
        self.assinatura.refresh_from_db()
        pro = Plano.objects.create(nome="Pro", valor_mensal=Decimal("149.90"))
        with patch("core.integracoes_http.enviar", return_value=(200, {})) as enviar:
            cobranca.trocar_plano(self.assinatura, pro)
        metodo, url = enviar.call_args.args
        self.assertEqual((metodo, url.endswith("/preapproval/pre-9")), ("PUT", True))
        self.assertEqual(enviar.call_args.kwargs["corpo"]["auto_recurring"]["transaction_amount"], 149.9)


class MasterV2Tests(Base):
    def test_indicadores_cupons_auditoria_e_saude(self):
        Fatura.objects.create(empresa=self.empresa, competencia=cobranca.competencia(self.hoje), descricao="x", valor=Decimal("99.90"),
                              vencimento=self.hoje, estado="PAGA", paga_em=self.assinatura.criado_em)
        resumo = cobranca.resumo_master(self.hoje)
        self.assertEqual(len(resumo["serie"]), 12)
        self.assertIn("mrr", resumo)
        self.client.force_login(self.master)
        self.assertContains(self.client.get("/master/cobranca/"), "Receita mensal recorrente")
        self.client.post("/master/cobranca/cupons/", {"codigo": "natal", "descricao": "", "desconto_percentual": "15", "meses": "2",
                                                      "valido_ate": "", "usos_maximos": "0", "ativo": "on"})
        self.assertTrue(Cupom.objects.filter(codigo="NATAL").exists())
        EventoGestao.objects.create(empresa=self.empresa, usuario=self.master, acao="MASTER_ENTROU", referencia="1")
        resposta = self.client.get("/master/auditoria/?acao=MASTER")
        self.assertContains(resposta, "MASTER_ENTROU")
        self.assertEqual(self.client.get("/saude/").json(), {"ok": True})

    def test_auditoria_so_master(self):
        self.client.force_login(self.dono)
        self.assertEqual(self.client.get("/master/auditoria/").status_code, 403)


@override_settings(BACKUP_S3_ENDPOINT="https://s3.us-west-004.backblazeb2.com", BACKUP_S3_BUCKET="meu-bucket",
                   BACKUP_S3_REGION="us-west-004", BACKUP_S3_ACCESS_KEY="AKIAEXEMPLO", BACKUP_S3_SECRET_KEY="segredo")
class BackupS3Tests(Base):
    def test_requisicao_assinada(self):
        agora = datetime.datetime(2026, 11, 11, 9, 0, tzinfo=datetime.timezone.utc)
        req = backup_s3.requisicao_assinada("PUT", "appdv/appdv-1.json.gz", b"dados", agora)
        self.assertEqual(req.full_url, "https://s3.us-west-004.backblazeb2.com/meu-bucket/appdv/appdv-1.json.gz")
        autorizacao = req.get_header("Authorization")
        self.assertIn("Credential=AKIAEXEMPLO/20261111/us-west-004/s3/aws4_request", autorizacao)
        self.assertIn("SignedHeaders=host;x-amz-content-sha256;x-amz-date", autorizacao)
        self.assertEqual(req.get_header("X-amz-date"), "20261111T090000Z")

    def test_backup_envia_e_registra(self):
        import tempfile
        from io import StringIO

        from django.core.management import call_command
        with tempfile.TemporaryDirectory() as pasta, override_settings(BACKUP_DIR=__import__("pathlib").Path(pasta)), \
                patch("core.backup_s3.enviar", return_value="appdv/x.json.gz") as enviar:
            call_command("backup_dados", stdout=StringIO())
            self.assertTrue(enviar.called)
            self.client.force_login(self.master)
            self.assertContains(self.client.get("/master/cobranca/"), "enviada ✓")


class PointTests(Base):
    def setUp(self):
        super().setUp()
        self.empresa.mp_access_token = "APP_USR-loja"
        self.empresa.mp_point_dispositivo = "PAX_A910__SMARTPOS1"
        self.empresa.save()
        self.api = APIClient()
        self.api.force_authenticate(self.dono)
        self.h = {"HTTP_X_EMPRESA_ID": str(self.empresa.pk)}

    def test_cobra_na_maquininha_e_confere_pagamento(self):
        chamadas = []

        def enviar(metodo, url, **kwargs):
            chamadas.append((metodo, url, kwargs.get("corpo"), kwargs.get("token_bearer")))
            if url.endswith("/payment-intents") and metodo == "POST":
                return 201, {"id": "intent-1"}
            if url.endswith("/payment-intents/intent-1"):
                return 200, {"id": "intent-1", "state": "FINISHED", "amount": 2590, "payment": {"id": 999}}
            if url.endswith("/v1/payments/999"):
                return 200, {"id": 999, "status": "approved", "transaction_amount": 25.9, "payment_method_id": "master"}
            return 200, {}
        with patch("core.integracoes_http.enviar", enviar):
            criada = self.api.post("/api/v1/point/cobrancas/", {"valor": "25.90", "tipo": "credito",
                                                                 "chave": "6f9619ff-8b86-d011-b42d-00cf4fc964ff"}, format="json", **self.h)
            self.assertEqual(criada.status_code, 201)
            self.assertEqual(chamadas[0][2]["amount"], 2590)
            self.assertEqual(chamadas[0][2]["payment"]["type"], "credit_card")
            self.assertEqual(chamadas[0][3], "APP_USR-loja")
            consulta = self.api.get("/api/v1/point/cobrancas/intent-1/", **self.h).json()
        self.assertEqual((consulta["estado"], consulta["pagamento"]), ("APROVADO", "999"))
        self.assertTrue(self.api.get("/api/v1/empresa/", **self.h).json()["point_configurado"])

    def test_valor_divergente_nao_aprova(self):
        respostas = iter([(200, {"state": "FINISHED", "amount": 2590, "payment": {"id": 5}}),
                          (200, {"status": "approved", "transaction_amount": 1.0})])
        with patch("core.integracoes_http.enviar", side_effect=lambda *a, **k: next(respostas)):
            self.assertEqual(self.api.get("/api/v1/point/cobrancas/intent-1/", **self.h).json()["estado"], "RECUSADO")

    def test_id_invalido(self):
        self.assertEqual(self.api.get("/api/v1/point/cobrancas/a.b/", **self.h).status_code, 400)
