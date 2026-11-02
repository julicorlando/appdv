from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from core.api import (
    AbrirCaixaView, CategoriaViewSet, CriarTokenView, FecharCaixaView,
    MinhasEmpresasView, PedidosWebView, FinalizarPedidoWebView, ProdutoViewSet, VendaViewSet,
)
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
    path("empresa/<int:empresa_id>/", web.empresa_dashboard, name="empresa-dashboard"),
    path("empresa/<int:empresa_id>/produtos/", web.empresa_produtos, name="empresa-produtos"),
    path("empresa/<int:empresa_id>/produtos/modelo-xlsx/", web.baixar_modelo_planilha, name="modelo-planilha"),
    path("empresa/<int:empresa_id>/produtos/importar-xlsx/", web.importar_produtos_excel, name="importar-planilha"),
    path("empresa/<int:empresa_id>/inventario/", web.empresa_inventario, name="empresa-inventario"),
    path("empresa/<int:empresa_id>/movimentacoes/", web.empresa_movimentacoes, name="empresa-movimentacoes"),
    path("empresa/<int:empresa_id>/vendas/", web.empresa_vendas, name="empresa-vendas"),
    path("empresa/<int:empresa_id>/pedidos-web/", web.empresa_pedidos_web, name="empresa-pedidos-web"),
    path("pedido/<uuid:codigo>/", web.pedido_publico, name="pedido-publico"),
    path("empresa/<int:empresa_id>/categorias/criar/", web.empresa_categoria, name="empresa-categoria"),
    path("empresa/<int:empresa_id>/produtos/<int:produto_id>/entrada/", web.empresa_entrada_estoque, name="empresa-entrada-estoque"),
    path("empresa/<int:empresa_id>/autorizacoes/", web.empresa_autorizacao, name="empresa-autorizacao"),
    path("admin/", admin.site.urls),
    path("api/v1/auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/v1/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/v1/minhas-empresas/", MinhasEmpresasView.as_view(), name="minhas-empresas"),
    path("api/v1/caixas/abrir/", AbrirCaixaView.as_view(), name="abrir-caixa"),
    path("api/v1/caixas/<int:caixa_id>/fechar/", FecharCaixaView.as_view(), name="fechar-caixa"),
    path("api/v1/autorizacoes/", CriarTokenView.as_view(), name="criar-token"),
    path("api/v1/pedidos-web/", PedidosWebView.as_view(), name="pedidos-web"),
    path("api/v1/pedidos-web/<int:pedido_id>/finalizar/", FinalizarPedidoWebView.as_view(), name="pedido-web-finalizar"),
    path("api/v1/", include(router.urls)),
]
