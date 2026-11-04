from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from core.api import (
    AbrirCaixaView, CaixaResumoView, ClientesView, ContasPagarView, ContasReceberView, CategoriaViewSet, CriarTokenView, DashboardEmpresaAPIView, EmpresaInfoView, FecharCaixaView, MovimentacaoCaixaView,
    MinhasEmpresasView, PedidosWebView, FinalizarPedidoWebView, ProdutoViewSet, VendaViewSet,
)
from core.api import ClienteCPFView, OrcamentosView, AutorizarDescontoView, TerminalHeartbeatView
from core import management_web as management
from core import web

router = DefaultRouter()
router.register("categorias", CategoriaViewSet, basename="categoria")
router.register("produtos", ProdutoViewSet, basename="produto")
router.register("vendas", VendaViewSet, basename="venda")

urlpatterns = [
    path("", web.home, name="home"),
    path("accounts/login/", auth_views.LoginView.as_view(template_name="login.html"), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("master/", web.master_dashboard, name="master"),
    path("master/empresas/criar/", web.master_criar_empresa, name="master-criar-empresa"),
    path("master/usuarios/criar/", web.master_criar_usuario, name="master-criar-usuario"),
    path("master/empresas/<int:empresa_id>/status/", web.master_status_empresa, name="master-status-empresa"),
    path("master/empresas/<int:empresa_id>/membros/adicionar/", web.master_adicionar_membro, name="master-adicionar-membro"),
    path("empresa/<int:empresa_id>/clientes/<int:pk>/editar/",management.cliente_editar,name="cliente-editar"),
    path("empresa/<int:empresa_id>/orcamentos/",management.orcamentos,name="gestao-orcamentos"),
    path("empresa/<int:empresa_id>/orcamentos/novo/",management.orcamento_editar,name="gestao-orcamento-novo"),
    path("empresa/<int:empresa_id>/orcamentos/<int:pk>/",management.orcamento,name="gestao-orcamento"),
    path("empresa/<int:empresa_id>/orcamentos/<int:pk>/revisar/",management.orcamento_editar,name="gestao-orcamento-editar"),
    path("empresa/<int:empresa_id>/entregas/",management.entregas,name="gestao-entregas"),
    path("empresa/<int:empresa_id>/depositos/",management.depositos,name="gestao-depositos"),
    path("empresa/<int:empresa_id>/devolucoes/",management.devolucoes,name="gestao-devolucoes"),
    path("empresa/<int:empresa_id>/caixas/",management.caixas,name="gestao-caixas"),
    path("empresa/<int:empresa_id>/caixas/<int:pk>/",management.caixas,name="gestao-caixa"),
    path("empresa/<int:empresa_id>/sincronizacao/",management.sincronizacao,name="gestao-sincronizacao"),
    path("empresa/<int:empresa_id>/descontos/",management.descontos,name="gestao-descontos"),
    path("empresa/<int:empresa_id>/relatorios/",management.relatorios,name="gestao-relatorios"),
    path("empresa/<int:empresa_id>/reposicao/",management.reposicao,name="gestao-reposicao"),
    path("empresa/<int:empresa_id>/fiscal/",management.fiscal,name="gestao-fiscal"),
    path("empresa/<int:empresa_id>/", web.empresa_dashboard, name="empresa-dashboard"),
    path("empresa/<int:empresa_id>/caixa/", web.caixa_web, name="caixa-web"),
    path("empresa/<int:empresa_id>/produtos/", web.empresa_produtos, name="empresa-produtos"),
    path("empresa/<int:empresa_id>/compras/", web.empresa_compras, name="empresa-compras"),
    path("empresa/<int:empresa_id>/clientes/", web.empresa_clientes, name="empresa-clientes"),
    path("empresa/<int:empresa_id>/contas-receber/", web.empresa_contas_receber, name="empresa-contas-receber"),
    path("empresa/<int:empresa_id>/contas-pagar/", web.empresa_contas_pagar, name="empresa-contas-pagar"),
    path("empresa/<int:empresa_id>/configuracao/", web.empresa_configuracao, name="empresa-configuracao"),
    path("empresa/<int:empresa_id>/produtos/modelo-xlsx/", web.baixar_modelo_planilha, name="modelo-planilha"),
    path("empresa/<int:empresa_id>/produtos/importar-xlsx/", web.importar_produtos_excel, name="importar-planilha"),
    path("empresa/<int:empresa_id>/inventario/", web.empresa_inventario, name="empresa-inventario"),
    path("empresa/<int:empresa_id>/movimentacoes/", web.empresa_movimentacoes, name="empresa-movimentacoes"),
    path("empresa/<int:empresa_id>/vendas/", web.empresa_vendas, name="empresa-vendas"),
    path("empresa/<int:empresa_id>/pedidos-web/", web.empresa_pedidos_web, name="empresa-pedidos-web"),
    path("pedido/<uuid:codigo>/", web.pedido_publico, name="pedido-publico"),
    path("empresa/<int:empresa_id>/categorias/criar/", web.empresa_categoria, name="empresa-categoria"),
    path("empresa/<int:empresa_id>/produtos/<int:produto_id>/entrada/", web.empresa_entrada_estoque, name="empresa-entrada-estoque"),
    path("empresa/<int:empresa_id>/produtos/<int:produto_id>/perda/", web.empresa_baixa_perda, name="empresa-baixa-perda"),
    path("empresa/<int:empresa_id>/autorizacoes/", web.empresa_autorizacao, name="empresa-autorizacao"),
    path("admin/", admin.site.urls),
    path("api/v1/auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/v1/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/v1/minhas-empresas/", MinhasEmpresasView.as_view(), name="minhas-empresas"),
    path("api/v1/empresa/", EmpresaInfoView.as_view(), name="empresa-info"),
    path("api/v1/empresa/resumo/", DashboardEmpresaAPIView.as_view(), name="empresa-resumo"),
    path("api/v1/terminais/heartbeat/", TerminalHeartbeatView.as_view()),
    path("api/v1/descontos/autorizar/", AutorizarDescontoView.as_view(), name="desconto-autorizar"),
    path("api/v1/clientes/cpf/", ClienteCPFView.as_view(), name="cliente-cpf"),
    path("api/v1/orcamentos/", OrcamentosView.as_view(), name="orcamentos"),
    path("api/v1/orcamentos/<int:orcamento_id>/", OrcamentosView.as_view(), name="orcamento-detalhe"),
    path("api/v1/clientes/", ClientesView.as_view(), name="clientes"),
    path("api/v1/contas-receber/", ContasReceberView.as_view(), name="contas-receber"),
    path("api/v1/contas-pagar/", ContasPagarView.as_view(), name="contas-pagar"),
    path("api/v1/caixas/abrir/", AbrirCaixaView.as_view(), name="abrir-caixa"),
    path("api/v1/caixas/<int:caixa_id>/fechar/", FecharCaixaView.as_view(), name="fechar-caixa"),
    path("api/v1/caixas/<int:caixa_id>/resumo/", CaixaResumoView.as_view(), name="resumo-caixa"),
    path("api/v1/caixas/movimentacoes/", MovimentacaoCaixaView.as_view(), name="movimentacao-caixa"),
    path("api/v1/autorizacoes/", CriarTokenView.as_view(), name="criar-token"),
    path("api/v1/pedidos-web/", PedidosWebView.as_view(), name="pedidos-web"),
    path("api/v1/pedidos-web/<int:pedido_id>/finalizar/", FinalizarPedidoWebView.as_view(), name="pedido-web-finalizar"),
    path("api/v1/", include(router.urls)),
]
