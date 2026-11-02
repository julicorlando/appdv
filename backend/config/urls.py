from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from core.api import CategoriaViewSet, MinhasEmpresasView, ProdutoViewSet, VendaViewSet

router = DefaultRouter()
router.register("categorias", CategoriaViewSet, basename="categoria")
router.register("produtos", ProdutoViewSet, basename="produto")
router.register("vendas", VendaViewSet, basename="venda")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/v1/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/v1/minhas-empresas/", MinhasEmpresasView.as_view(), name="minhas-empresas"),
    path("api/v1/", include(router.urls)),
]
