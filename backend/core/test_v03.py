"""Testes da versão 0.3: produtos, promoções, etiquetas, fornecedores, extrato, relatórios,
fechamento de caixa pela gerência, senhas, NFC-e (Focus NFe) e PIX (Mercado Pago)."""
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .codigo_barras import ean13_valido
from .models import (
    Caixa, Categoria, Cliente, CobrancaPix, ContaPagar, DocumentoFiscal, Empresa, EventoGestao,
    Fornecedor, MembroEmpresa, Produto, TerminalPDV, Venda,
)


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user("admin03", password="Admin#Seguro123", email="a@example.com")
        self.company = Empresa.objects.create(nome="Loja 03", slug="loja-03", cnpj="12345678000199", email="loja@example.com")
        self.member = MembroEmpresa.objects.create(empresa=self.company, usuario=self.user, papel="ADMIN")
        self.category = Categoria.objects.create(empresa=self.company, nome="Geral")
        self.product = Produto.objects.create(
            empresa=self.company, categoria=self.category, codigo="P1", nome="Produto", setor="A", endereco="A-1",
            preco_venda=Decimal("10.00"), custo_medio=Decimal("6.00"), estoque_atual=Decimal("100"), ncm="12345678",
        )
        self.customer = Cliente.objects.create(empresa=self.company, nome="Cliente", documento="52998224725", limite_credito=Decimal("500"))
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.h = {"HTTP_X_EMPRESA_ID": str(self.company.pk)}
        self.client.force_login(self.user)
        self.url = f"/empresa/{self.company.pk}"

    def caixa(self, dispositivo="pdv"):
        return self.api.post("/api/v1/caixas/abrir/", {"dispositivo_id": dispositivo}, format="json", **self.h).data["id"]

    def vender(self, caixa, pagamentos, preco=None):
        item = {"produto": self.product.pk, "quantidade": "1"}
        if preco is not None:
            item["preco_esperado"] = preco
        return self.api.post("/api/v1/vendas/", {
            "idempotency_key": str(uuid.uuid4()), "caixa": caixa, "cliente": self.customer.pk,
            "itens": [item], "pagamentos": pagamentos,
        }, format="json", **self.h)


class TelasTests(Base):
    def test_novas_telas_abrem(self):
        for caminho in ("fornecedores/", "etiquetas/", f"clientes/{self.customer.pk}/extrato/", "relatorios/gerenciais/",
                        f"produtos/{self.product.pk}/editar/", "fiscal/", "contas-pagar/", "produtos/", "sincronizacao/"):
            with self.subTest(caminho=caminho):
                self.assertEqual(self.client.get(f"{self.url}/{caminho}").status_code, 200)
        self.assertEqual(self.client.get("/conta/senha/").status_code, 200)
        self.assertContains(self.client.get("/accounts/senha/esqueci/"), "administrador")

    def test_caixa_nao_acessa_telas_de_gestao(self):
        operador = get_user_model().objects.create_user("op03", password="Operador#123x")
        MembroEmpresa.objects.create(empresa=self.company, usuario=operador, papel="CAIXA")
        self.client.force_login(operador)
        for caminho in ("fornecedores/", "relatorios/gerenciais/", f"produtos/{self.product.pk}/editar/"):
            self.assertEqual(self.client.get(f"{self.url}/{caminho}").status_code, 403)


class ProdutosTests(Base):
    def dados(self, **extra):
        base = {"categoria": self.category.pk, "codigo": "P1", "codigo_barras": "", "nome": "Produto editado",
                "unidade": "UN", "unidade_estoque": "", "fator_conversao": "1", "setor": "A", "endereco": "A-1",
                "preco_venda": "12.00", "custo_medio": "6.00", "estoque_minimo": "0", "ativo": "on",
                "ncm": "1234.56.78", "cest": "", "cfop": "5102", "csosn": "102", "origem": "0"}
        base.update(extra)
        return base

    def test_editar_produto_preco_e_auditoria(self):
        resposta = self.client.post(f"{self.url}/produtos/{self.product.pk}/editar/", self.dados())
        self.assertEqual(resposta.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual((self.product.nome, self.product.preco_venda, self.product.ncm), ("Produto editado", Decimal("12.00"), "12345678"))
        self.assertTrue(EventoGestao.objects.filter(acao="PRODUTO_EDITADO").exists())

    def test_inativar_tira_do_pdv(self):
        self.client.post(f"{self.url}/produtos/{self.product.pk}/editar/", self.dados(ativo=""))
        self.assertEqual(self.api.get("/api/v1/produtos/", **self.h).data, [])

    def test_promocao_invalida_recusada(self):
        resposta = self.client.post(f"{self.url}/produtos/{self.product.pk}/editar/", self.dados(preco_promocional="15"))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "menor que o preço de venda")

    def test_promocao_vigente_vale_no_catalogo_e_na_venda(self):
        hoje = timezone.localdate()
        Produto.objects.filter(pk=self.product.pk).update(preco_promocional=Decimal("8.00"), promocao_inicio=hoje, promocao_fim=hoje)
        catalogo = self.api.get("/api/v1/produtos/", **self.h).data[0]
        self.assertEqual((catalogo["preco_venda"], catalogo["preco_base"], catalogo["em_promocao"]), ("8.00", "10.00", True))
        caixa = self.caixa()
        resposta = self.vender(caixa, [{"meio": "PIX", "valor": "8.00"}], preco="8.00")
        self.assertEqual(resposta.status_code, 201, resposta.content)
        self.assertEqual(Venda.objects.get(pk=resposta.data["id"]).total, Decimal("8.00"))

    def test_promocao_vencida_nao_vale(self):
        ontem = timezone.localdate() - timedelta(days=1)
        Produto.objects.filter(pk=self.product.pk).update(preco_promocional=Decimal("8.00"), promocao_fim=ontem)
        self.assertEqual(self.api.get("/api/v1/produtos/", **self.h).data[0]["preco_venda"], "10.00")

    def test_inativar_produtos_de_teste(self):
        demo = Produto.objects.create(empresa=self.company, categoria=self.category, codigo="DEMO-0001", nome="[TESTE] X", preco_venda=1)
        self.client.post(f"{self.url}/produtos/teste/inativar/")
        demo.refresh_from_db()
        self.product.refresh_from_db()
        self.assertFalse(demo.ativo)
        self.assertTrue(self.product.ativo)

    def test_etiquetas_e_codigos_internos(self):
        self.client.post(f"{self.url}/etiquetas/", {"acao": "gerar_codigos"})
        self.product.refresh_from_db()
        self.assertTrue(ean13_valido(self.product.codigo_barras))
        resposta = self.client.get(f"{self.url}/etiquetas/", {"p": self.product.pk, "copias": "2"})
        self.assertContains(resposta, "<svg", count=2)


class FinanceiroTests(Base):
    def test_fornecedor_e_despesa_avulsa(self):
        self.client.post(f"{self.url}/fornecedores/", {"nome": "Energia SA", "cnpj": "11.222.333/0001-81", "ativo": "on"})
        fornecedor = Fornecedor.objects.get(nome="Energia SA")
        self.assertEqual(fornecedor.cnpj, "11222333000181")
        resposta = self.client.post(f"{self.url}/contas-pagar/nova/", {
            "fornecedor": fornecedor.pk, "categoria": "ENERGIA", "descricao": "Conta de luz",
            "valor": "250.40", "vencimento": str(timezone.localdate()),
        })
        self.assertEqual(resposta.status_code, 302)
        conta = ContaPagar.objects.get()
        self.assertEqual((conta.saldo, conta.categoria, conta.compra), (Decimal("250.40"), "ENERGIA", None))
        self.assertContains(self.client.get(f"{self.url}/contas-pagar/"), "Conta de luz")

    def test_relatorios_e_exportacao(self):
        caixa = self.caixa()
        self.assertEqual(self.vender(caixa, [{"meio": "DINHEIRO", "valor": "10.00", "valor_recebido": "20.00", "troco": "10.00"}]).status_code, 201)
        resposta = self.client.get(f"{self.url}/relatorios/gerenciais/")
        self.assertContains(resposta, "Curva ABC")
        self.assertEqual(resposta.context["totais"]["faturamento"], Decimal("10.00"))
        self.assertEqual(resposta.context["abc"][0]["classe"], "A")
        exportado = self.client.get(f"{self.url}/relatorios/gerenciais/", {"exportar": "xlsx"})
        self.assertEqual(exportado["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertTrue(exportado.content.startswith(b"PK"))

    def test_extrato_mostra_compras(self):
        caixa = self.caixa()
        self.vender(caixa, [{"meio": "PIX", "valor": "10.00"}])
        resposta = self.client.get(f"{self.url}/clientes/{self.customer.pk}/extrato/")
        self.assertEqual(resposta.context["resumo"]["compras"], 1)

    def test_gerente_fecha_caixa_de_outro_operador(self):
        operador = get_user_model().objects.create_user("op-esquecido", password="Operador#123x")
        MembroEmpresa.objects.create(empresa=self.company, usuario=operador, papel="CAIXA")
        caixa = Caixa.objects.create(empresa=self.company, operador=operador, dispositivo_id="pdv-x")
        resposta = self.client.post(f"{self.url}/caixas/{caixa.pk}/fechar/", {"saldo_fechamento": "0", "motivo": "Operador saiu sem fechar"})
        self.assertEqual(resposta.status_code, 302)
        caixa.refresh_from_db()
        self.assertEqual(caixa.estado, Caixa.Estado.FECHADO)
        self.assertTrue(EventoGestao.objects.filter(acao="CAIXA_FECHADO_PELA_GERENCIA").exists())


class SenhaTests(Base):
    def test_trocar_pelo_painel(self):
        resposta = self.client.post("/conta/senha/", {"old_password": "Admin#Seguro123", "new_password1": "Nova#Senha456x", "new_password2": "Nova#Senha456x"})
        self.assertEqual(resposta.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Nova#Senha456x"))

    def test_trocar_pela_api_do_pdv(self):
        self.assertEqual(self.api.post("/api/v1/conta/senha/", {"senha_atual": "errada", "nova_senha": "Nova#Senha456x"}, format="json").status_code, 400)
        self.assertEqual(self.api.post("/api/v1/conta/senha/", {"senha_atual": "Admin#Seguro123", "nova_senha": "123"}, format="json").status_code, 400)
        resposta = self.api.post("/api/v1/conta/senha/", {"senha_atual": "Admin#Seguro123", "nova_senha": "Nova#Senha456x"}, format="json")
        self.assertEqual(resposta.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Nova#Senha456x"))

    def test_admin_redefine_senha_de_membro(self):
        operador = get_user_model().objects.create_user("op-senha", password="Operador#123x")
        alvo = MembroEmpresa.objects.create(empresa=self.company, usuario=operador, papel="CAIXA")
        self.client.post(f"{self.url}/equipe/senha/", {"membro": alvo.pk, "nova_senha": "Provisoria#2026"})
        operador.refresh_from_db()
        self.assertTrue(operador.check_password("Provisoria#2026"))


class VersaoTests(Base):
    def test_versao_e_heartbeat(self):
        self.assertIn("versao_minima", APIClient().get("/api/v1/pdv/versao/").data)
        self.api.post("/api/v1/terminais/heartbeat/", {"identificador": "pc:admin03", "pendentes": 0, "versao": "0.3.0"}, format="json", **self.h)
        self.assertEqual(TerminalPDV.objects.get().versao, "0.3.0")
        info = self.api.get("/api/v1/empresa/", **self.h).data
        self.assertEqual((info["pix_configurado"], info["nfce_configurada"]), (False, False))


def focus_falso(respostas):
    """Dublê de integracoes_http.enviar: devolve respostas em sequência e guarda as chamadas."""
    chamadas = []

    def enviar(metodo, url, **kwargs):
        chamadas.append((metodo, url, kwargs))
        return respostas.pop(0)
    return enviar, chamadas


class NfceTests(Base):
    def setUp(self):
        super().setUp()
        Empresa.objects.filter(pk=self.company.pk).update(focus_token="tok-focus", pis_cofins_cst="49")
        self.company.refresh_from_db()
        caixa = self.caixa()
        self.venda_id = self.vender(caixa, [{"meio": "DINHEIRO", "valor": "10.00", "valor_recebido": "20.00", "troco": "10.00"}]).data["id"]

    def test_emissao_autorizada(self):
        enviar, chamadas = focus_falso([(201, {"status": "autorizado", "chave_nfe": "NFe" + "1" * 44, "numero": "15",
                                               "caminho_danfe": "/arquivos/danfe.html", "mensagem_sefaz": "Autorizado o uso"})])
        with patch("core.integracoes_http.enviar", enviar):
            resposta = self.api.post(f"/api/v1/vendas/{self.venda_id}/nfce/", {}, format="json", **self.h)
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(resposta.data["nfce"]["estado"], "AUTORIZADO")
        self.assertEqual(resposta.data["nfce"]["danfe_url"], "https://homologacao.focusnfe.com.br/arquivos/danfe.html")
        metodo, url, kwargs = chamadas[0]
        self.assertEqual(metodo, "POST")
        self.assertIn(f"ref=appdv-{self.company.pk}-venda-{self.venda_id}-1", url)
        nota = kwargs["corpo"]
        self.assertEqual(nota["items"][0]["codigo_ncm"], "12345678")
        self.assertEqual(nota["formas_pagamento"], [{"forma_pagamento": "01", "valor_pagamento": "20.00"}])
        self.assertEqual(nota["valor_troco"], "10.00")
        self.assertEqual(nota["cpf_destinatario"], "52998224725")
        self.assertEqual(kwargs["token_basico"], "tok-focus")
        # Nota autorizada não é reenviada.
        with patch("core.integracoes_http.enviar", side_effect=AssertionError("não deveria chamar")):
            self.assertEqual(self.api.post(f"/api/v1/vendas/{self.venda_id}/nfce/", {}, format="json", **self.h).data["nfce"]["estado"], "AUTORIZADO")

    def test_pendencias_fiscais_bloqueiam(self):
        Produto.objects.filter(pk=self.product.pk).update(ncm="")
        with patch("core.integracoes_http.enviar", side_effect=AssertionError("não deveria chamar")):
            resposta = self.api.post(f"/api/v1/vendas/{self.venda_id}/nfce/", {}, format="json", **self.h)
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("NCM", str(resposta.data))

    def test_rejeitada_reenvia_com_nova_referencia(self):
        enviar, chamadas = focus_falso([
            (422, {"status": "erro_autorizacao", "mensagem_sefaz": "Rejeição: CSC inválido"}),
            (200, {"status": "erro_autorizacao", "mensagem_sefaz": "Rejeição: CSC inválido"}),
            (201, {"status": "autorizado", "chave_nfe": "2" * 44}),
        ])
        with patch("core.integracoes_http.enviar", enviar):
            primeira = self.api.post(f"/api/v1/vendas/{self.venda_id}/nfce/", {}, format="json", **self.h).data["nfce"]
            segunda = self.api.post(f"/api/v1/vendas/{self.venda_id}/nfce/", {}, format="json", **self.h).data["nfce"]
        self.assertEqual(primeira["estado"], "REJEITADO")
        self.assertEqual(segunda["estado"], "AUTORIZADO")
        self.assertEqual([c[0] for c in chamadas], ["POST", "GET", "POST"])
        self.assertTrue(chamadas[2][1].endswith(f"venda-{self.venda_id}-2"))

    def test_falha_de_rede_reutiliza_referencia(self):
        from .integracoes_http import ErroIntegracao

        def caiu(*args, **kwargs):
            raise ErroIntegracao("sem rede")
        with patch("core.integracoes_http.enviar", caiu):
            self.assertEqual(self.api.post(f"/api/v1/vendas/{self.venda_id}/nfce/", {}, format="json", **self.h).status_code, 400)
        self.assertEqual(DocumentoFiscal.objects.get().estado, "PENDENTE")
        enviar, chamadas = focus_falso([(404, {"codigo": "nao_encontrado"}), (201, {"status": "autorizado", "chave_nfe": "3" * 44})])
        with patch("core.integracoes_http.enviar", enviar):
            self.api.post(f"/api/v1/vendas/{self.venda_id}/nfce/", {}, format="json", **self.h)
        self.assertTrue(chamadas[1][1].endswith(f"venda-{self.venda_id}-1"))

    def test_emissao_automatica_nao_desfaz_venda(self):
        Empresa.objects.filter(pk=self.company.pk).update(nfce_automatica=True)
        Produto.objects.filter(pk=self.product.pk).update(ncm="")
        caixa = Caixa.objects.get(empresa=self.company, estado="ABERTO").pk
        resposta = self.vender(caixa, [{"meio": "PIX", "valor": "10.00"}])
        self.assertEqual(resposta.status_code, 201)
        self.assertEqual(resposta.data["nfce"]["estado"], "CONFIGURAR")

    def test_cancelamento_pelo_painel(self):
        enviar, _ = focus_falso([(201, {"status": "autorizado", "chave_nfe": "4" * 44}), (200, {"status": "cancelado", "mensagem_sefaz": "Evento registrado"})])
        with patch("core.integracoes_http.enviar", enviar):
            self.client.post(f"{self.url}/fiscal/", {"venda": self.venda_id, "acao": "emitir"})
            self.client.post(f"{self.url}/fiscal/", {"venda": self.venda_id, "acao": "cancelar", "justificativa": "Venda registrada em duplicidade"})
        self.assertEqual(DocumentoFiscal.objects.get().estado, "CANCELADO")


class PixTests(Base):
    def setUp(self):
        super().setUp()
        Empresa.objects.filter(pk=self.company.pk).update(mp_access_token="APP_USR-teste")
        self.company.refresh_from_db()

    def cobrar(self, valor="10.00"):
        enviar, chamadas = focus_falso([(201, {"id": 987654, "status": "pending", "point_of_interaction": {"transaction_data": {"qr_code": "000201...", "qr_code_base64": "iVBOR"}}})])
        with patch("core.integracoes_http.enviar", enviar):
            resposta = self.api.post("/api/v1/pix/cobrancas/", {"valor": valor, "chave": str(uuid.uuid4())}, format="json", **self.h)
        self.assertEqual(resposta.status_code, 201, resposta.content)
        self.assertEqual(chamadas[0][2]["token_bearer"], "APP_USR-teste")
        return resposta.data

    def aprovar(self, cobranca, valor=10.0):
        enviar, _ = focus_falso([(200, {"id": 987654, "status": "approved", "transaction_amount": valor})])
        with patch("core.integracoes_http.enviar", enviar):
            return self.api.get(f"/api/v1/pix/cobrancas/{cobranca['id']}/", **self.h).data

    def test_fluxo_completo_vincula_venda(self):
        cobranca = self.cobrar()
        self.assertEqual((cobranca["estado"], cobranca["qr_code"]), ("PENDENTE", "000201..."))
        self.assertEqual(self.aprovar(cobranca)["estado"], "APROVADO")
        caixa = self.caixa()
        resposta = self.vender(caixa, [{"meio": "PIX", "valor": "10.00", "cobranca_pix": cobranca["id"]}])
        self.assertEqual(resposta.status_code, 201, resposta.content)
        self.assertEqual(CobrancaPix.objects.get().venda_id, resposta.data["id"])
        # A mesma cobrança não paga outra venda.
        self.assertEqual(self.vender(caixa, [{"meio": "PIX", "valor": "10.00", "cobranca_pix": cobranca["id"]}]).status_code, 400)

    def test_cobranca_pendente_ou_de_outro_valor_recusada(self):
        cobranca = self.cobrar()
        caixa = self.caixa()
        self.assertEqual(self.vender(caixa, [{"meio": "PIX", "valor": "10.00", "cobranca_pix": cobranca["id"]}]).status_code, 400)
        self.assertEqual(self.aprovar(cobranca, valor=9.0)["estado"], "PENDENTE")

    def test_webhook_so_consulta_o_provedor(self):
        cobranca = self.cobrar()
        enviar, chamadas = focus_falso([(200, {"id": 987654, "status": "approved", "transaction_amount": 10.0})])
        with patch("core.integracoes_http.enviar", enviar):
            resposta = APIClient().post(f"/api/v1/pix/webhook/{self.company.pk}/", {"type": "payment", "data": {"id": "987654"}}, format="json")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(chamadas[0][0], "GET")
        self.assertEqual(CobrancaPix.objects.get(pk=cobranca["id"]).estado, "APROVADO")

    def test_sem_token_informa_configuracao(self):
        Empresa.objects.filter(pk=self.company.pk).update(mp_access_token="")
        resposta = self.api.post("/api/v1/pix/cobrancas/", {"valor": "10.00", "chave": str(uuid.uuid4())}, format="json", **self.h)
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("Mercado Pago", str(resposta.data))


class BackupTests(Base):
    def test_backup_gera_arquivo_e_rotaciona(self):
        import gzip
        import json
        import tempfile
        from pathlib import Path
        from django.core.management import call_command
        with tempfile.TemporaryDirectory() as pasta:
            for _ in range(3):
                call_command("backup_dados", destino=pasta, manter=2, stdout=open("/dev/null", "w"))
            arquivos = sorted(Path(pasta).glob("appdv-*.json.gz"))
            self.assertLessEqual(len(arquivos), 2)
            dados = json.loads(gzip.open(arquivos[-1], "rt", encoding="utf-8").read())
            self.assertTrue(any(obj["model"] == "core.produto" for obj in dados))
