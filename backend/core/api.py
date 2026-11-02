from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import Categoria, Produto, Venda
from .serializers import CategoriaSerializer, ProdutoSerializer, VendaSerializer

class CategoriaViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Categoria.objects.filter(ativa=True).order_by("nome")
    serializer_class = CategoriaSerializer
    permission_classes = [IsAuthenticated]

class ProdutoViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Produto.objects.filter(ativo=True).select_related("categoria").order_by("nome")
    serializer_class = ProdutoSerializer
    permission_classes = [IsAuthenticated]
    search_fields = ("nome", "codigo", "codigo_barras")

class VendaViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = VendaSerializer
    permission_classes = [IsAuthenticated]
    queryset = Venda.objects.select_related("caixa", "operador").prefetch_related("itens")
    def get_queryset(self):
        user = self.request.user
        if hasattr(user, "perfil_armazem") and user.perfil_armazem.papel in ("ADMIN", "GERENTE"):
            return self.queryset
        return self.queryset.filter(operador=user)
