from rest_framework import viewsets
from rest_framework.exceptions import NotAuthenticated, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Categoria, Empresa, MembroEmpresa, Produto, Venda
from .serializers import CategoriaSerializer, ProdutoSerializer, VendaSerializer


class TenantViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]

    def get_empresa(self):
        empresa_id = self.request.headers.get("X-Empresa-ID")
        if not empresa_id:
            raise ValidationError({"empresa": "Informe a empresa ativa no cabeçalho X-Empresa-ID."})
        try:
            membro = MembroEmpresa.objects.select_related("empresa").get(
                empresa_id=empresa_id, usuario=self.request.user, ativo=True, empresa__ativa=True
            )
        except (MembroEmpresa.DoesNotExist, ValueError):
            raise PermissionDenied("Você não tem acesso a esta empresa.")
        perfil = getattr(self.request.user, "perfil_armazem", None)
        if perfil and not perfil.ativo:
            raise NotAuthenticated("Conta desativada.")
        return membro.empresa


class MinhasEmpresasView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        empresas = MembroEmpresa.objects.filter(
            usuario=request.user, ativo=True, empresa__ativa=True
        ).select_related("empresa")
        return Response([{
            "id": m.empresa_id, "nome": m.empresa.nome, "slug": m.empresa.slug, "papel": m.papel
        } for m in empresas])


class CategoriaViewSet(TenantViewSet):
    serializer_class = CategoriaSerializer
    def get_queryset(self):
        return Categoria.objects.filter(empresa=self.get_empresa(), ativa=True).order_by("nome")


class ProdutoViewSet(TenantViewSet):
    serializer_class = ProdutoSerializer
    search_fields = ("nome", "codigo", "codigo_barras")
    def get_queryset(self):
        return Produto.objects.filter(empresa=self.get_empresa(), ativo=True).select_related("categoria").order_by("nome")


class VendaViewSet(TenantViewSet):
    serializer_class = VendaSerializer
    def get_queryset(self):
        empresa = self.get_empresa()
        qs = Venda.objects.filter(empresa=empresa).select_related("caixa", "operador").prefetch_related("itens")
        membership = MembroEmpresa.objects.get(empresa=empresa, usuario=self.request.user, ativo=True)
        if membership.papel in (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE):
            return qs
        return qs.filter(operador=self.request.user)
