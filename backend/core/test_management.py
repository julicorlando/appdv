from decimal import Decimal
import uuid
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from .models import *
from .management_services import *
from .orcamentos import salvar_orcamento
from .services import registrar_venda


class WebManagementTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('manager-web',password='test-only')
        self.company=Empresa.objects.create(nome='Loja QA',slug='web-qa')
        self.member=MembroEmpresa.objects.create(empresa=self.company,usuario=self.user,papel='ADMIN')
        self.category=Categoria.objects.create(empresa=self.company,nome='Categoria')
        self.product=Produto.objects.create(empresa=self.company,categoria=self.category,codigo='Q',nome='Produto',preco_venda=10,custo_medio=4,estoque_atual=100,estoque_minimo=20)
        self.customer=Cliente.objects.create(empresa=self.company,nome='Cliente',documento='52998224725')
        self.cash=Caixa.objects.create(empresa=self.company,operador=self.user,dispositivo_id='web')
        self.client.force_login(self.user)
        self.api=APIClient();self.api.force_authenticate(self.user)
        self.headers={'HTTP_X_EMPRESA_ID':str(self.company.pk)}
        self.depot=principal(self.company)

    def quote(self,q='10'):
        return salvar_orcamento(self.company,self.member,{'cliente':self.customer.pk,'chave':str(uuid.uuid4()),'validade':str(timezone.localdate()),'itens':[{'produto':self.product.pk,'quantidade':q} ]})[0]

    def test_all_management_screens_render(self):
        for path in ['orcamentos','orcamentos/novo','entregas','depositos','devolucoes','caixas','sincronizacao','descontos','relatorios','reposicao','fiscal',f'clientes/{self.customer.pk}/editar']:
            with self.subTest(path=path):self.assertEqual(self.client.get(f'/empresa/{self.company.pk}/{path}/').status_code,200)
        quote=self.quote()
        for suffix in ['', 'revisar/']:
            self.assertEqual(self.client.get(f'/empresa/{self.company.pk}/orcamentos/{quote.pk}/{suffix}').status_code,200)

    def test_customer_edit_preserves_sales_and_rejects_other_company(self):
        quote=self.quote()
        response=self.client.post(f'/empresa/{self.company.pk}/clientes/{self.customer.pk}/editar/',{'nome':'Nome alterado','documento':'52998224725','telefone':'8199','email':'cliente@example.com','endereco':'Rua A','limite_credito':'50','ativo':'on'})
        self.assertEqual(response.status_code,302)
        self.customer.refresh_from_db();self.assertEqual(self.customer.nome,'Nome alterado')
        quote.refresh_from_db();self.assertEqual(quote.cliente_id,self.customer.pk)
        other=Empresa.objects.create(nome='Outra',slug='outra-gestao')
        outsider=Cliente.objects.create(empresa=other,nome='Outra pessoa')
        self.assertEqual(self.client.get(f'/empresa/{self.company.pk}/clientes/{outsider.pk}/editar/').status_code,404)

    def test_depot_transfer_reserve_sale_and_partial_delivery(self):
        other=Deposito.objects.create(empresa=self.company,nome='Depósito B')
        transferir(self.company,self.user,self.product.pk,self.depot.pk,other.pk,'40')
        self.product.refresh_from_db()
        self.assertEqual(self.product.estoque_atual,100)
        self.assertEqual(saldo_local(self.product,self.depot),60)
        self.assertEqual(saldo_local(self.product,other),40)
        quote=self.quote('30')
        order=reservar_orcamento(self.company,self.user,quote.pk,other.pk)
        self.assertEqual(reservado(self.product),30)
        with self.assertRaises(Exception):transferir(self.company,self.user,self.product.pk,other.pk,self.depot.pk,'11')
        payload={'idempotency_key':str(uuid.uuid4()),'caixa':self.cash.pk,'cliente':self.customer.pk,'orcamento':quote.pk,'total_confirmado':'300.00','itens':[{'produto':self.product.pk,'quantidade':'30','desconto':'0'}],'pagamentos':[{'meio':'PIX','valor':'300'}]}
        sale,_=vender_orcamento(self.company,self.user,payload)
        self.product.refresh_from_db();self.assertEqual(self.product.estoque_atual,70)
        self.assertEqual(reservado(self.product),0)
        self.assertEqual(saldo_local(self.product,other),10)
        line=order.itens.get()
        registrar_entrega(self.company,self.user,order.pk,{str(line.pk):'10'},'Cliente')
        order.refresh_from_db();self.assertEqual(order.estado,'PARCIAL')
        registrar_entrega(self.company,self.user,order.pk,{str(line.pk):'20'},'Cliente')
        order.refresh_from_db();self.assertEqual(order.estado,'ENTREGUE')
        self.product.refresh_from_db();self.assertEqual(self.product.estoque_atual,70)
        sale.itens.get().refresh_from_db();self.assertEqual(sale.itens.get().custo_estoque_unitario,4)

    def test_returns_cap_quantity_credit_balance_and_new_purchase(self):
        sale,_=registrar_venda(empresa=self.company,usuario=self.user,dados={'idempotency_key':str(uuid.uuid4()),'caixa':self.cash.pk,'cliente':self.customer.pk,'itens':[{'produto':self.product.pk,'quantidade':'2'}],'pagamentos':[{'meio':'PIX','valor':'20'}]})
        item=sale.itens.get();key=uuid.uuid4()
        returned=devolver(self.company,self.user,item.pk,'1','ESTOQUE','CREDITO','Teste',key)
        self.assertEqual(returned.valor,10)
        self.assertEqual(devolver(self.company,self.user,item.pk,'1','ESTOQUE','CREDITO','Teste',key).pk,returned.pk)
        with self.assertRaises(Exception):devolver(self.company,self.user,item.pk,'2','ESTOQUE','CREDITO','Excesso',uuid.uuid4())
        payload={'idempotency_key':str(uuid.uuid4()),'caixa':self.cash.pk,'cliente':self.customer.pk,'itens':[{'produto':self.product.pk,'quantidade':'1'}],'pagamentos':[{'meio':'LOJA','valor':'10'}]}
        registrar_venda(empresa=self.company,usuario=self.user,dados=payload)
        self.assertEqual(CreditoLoja.objects.filter(cliente=self.customer).aggregate(n=Sum('valor'))['n'],0)
        payload['idempotency_key']=str(uuid.uuid4())
        with self.assertRaises(Exception):registrar_venda(empresa=self.company,usuario=self.user,dados=payload)

    def test_purchase_partial_receipts_and_xml_order_mismatch(self):
        supplier=Fornecedor.objects.create(empresa=self.company,nome='Fornecedor')
        order=PedidoCompra.objects.create(empresa=self.company,fornecedor=supplier,criado_por=self.user)
        ItemPedidoCompra.objects.create(pedido=order,produto=self.product,quantidade=10,custo=4)
        for number,q in [('A',4),('B',6)]:
            note=CompraEntrada.objects.create(empresa=self.company,fornecedor=supplier,numero_nf=number,criado_por=self.user)
            ItemCompraEntrada.objects.create(compra=note,produto=self.product,descricao_xml='Item XML',quantidade=q,custo_unitario=4,total=q*4)
            confirmar_compra_pedido(self.company,self.user,note.pk,order.pk)
        order.refresh_from_db();self.assertEqual(order.estado,'RECEBIDO')
        self.product.refresh_from_db();self.assertEqual(self.product.estoque_atual,110)
        self.assertEqual(order.notas.count(),2)

    def test_policy_and_terminal_heartbeat(self):
        PoliticaDesconto.objects.create(empresa=self.company,modo='AUTOMATICO',percentual=5,meio='PIX')
        payload={'idempotency_key':str(uuid.uuid4()),'caixa':self.cash.pk,'itens':[{'produto':self.product.pk,'quantidade':'10','desconto':'5'}],'pagamentos':[{'meio':'PIX','valor':'95'}]}
        sale= self.api.post('/api/v1/vendas/',payload,format='json',**self.headers)
        self.assertEqual(sale.status_code,201,sale.data)
        payload['idempotency_key']=str(uuid.uuid4());payload['itens'][0]['desconto']='0';payload['pagamentos'][0]['valor']='100'
        self.assertEqual(self.api.post('/api/v1/vendas/',payload,format='json',**self.headers).status_code,400)
        response=self.api.post('/api/v1/terminais/heartbeat/',{'identificador':'caixa1','pendentes':2,'erros':'Revisar preço'},format='json',**self.headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual(TerminalPDV.objects.get(empresa=self.company).pendentes,2)

    def test_cashier_cannot_edit_client_or_manage_stock(self):
        self.member.papel='CAIXA';self.member.save()
        for path in [f'clientes/{self.customer.pk}/editar','depositos','devolucoes','descontos','relatorios']:
            self.assertEqual(self.client.get(f'/empresa/{self.company.pk}/{path}/').status_code,403)
