from datetime import timedelta
from django.utils import timezone
from rest_framework import mixins, viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Categoria, Empresa, MembroEmpresa, Produto, TokenAutorizacao, Venda
from .serializers import CategoriaSerializer, ProdutoSerializer, VendaSerializer
from .services import cancelar_venda, registrar_venda


class TenantAPIView(APIView):
    permission_classes = [IsAuthenticated]
    def get_empresa(self, request):
        empresa_id = request.headers.get("X-Empresa-ID")
        if not empresa_id:
            raise ValidationError({"empresa": "Informe X-Empresa-ID."})
        try:
            membro = MembroEmpresa.objects.select_related("empresa").get(
                empresa_id=empresa_id, usuario=request.user, ativo=True, empresa__ativa=True
            )
        except (MembroEmpresa.DoesNotExist, ValueError):
            raise PermissionDenied("Acesso negado a esta empresa.")
        perfil = getattr(request.user, "perfil_armazem", None)
        if perfil and not perfil.ativo:
            raise PermissionDenied("Conta desativada.")
        return membro.empresa, membro


class MinhasEmpresasView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        empresas = MembroEmpresa.objects.filter(
            usuario=request.user, ativo=True, empresa__ativa=True
        ).select_related("empresa")
        return Response([{"id": m.empresa_id, "nome": m.empresa.nome,
                          "slug": m.empresa.slug, "papel": m.papel} for m in empresas])


class TenantViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    def get_empresa(self):
        empresa_id = self.request.headers.get("X-Empresa-ID")
        if not empresa_id:
            raise ValidationError({"empresa": "Informe X-Empresa-ID."})
        try:
            membro = MembroEmpresa.objects.select_related("empresa").get(
                empresa_id=empresa_id, usuario=self.request.user, ativo=True, empresa__ativa=True
            )
        except (MembroEmpresa.DoesNotExist, ValueError):
            raise PermissionDenied("Acesso negado a esta empresa.")
        return membro.empresa


class CategoriaViewSet(TenantViewSet):
    serializer_class = CategoriaSerializer
    def get_queryset(self):
        return Categoria.objects.filter(empresa=self.get_empresa(), ativa=True).order_by("nome")


class ProdutoViewSet(TenantViewSet):
    serializer_class = ProdutoSerializer
    search_fields = ("nome", "codigo", "codigo_barras")
    def get_queryset(self):
        return Produto.objects.filter(empresa=self.get_empresa(), ativo=True).select_related("categoria").order_by("nome")


class VendaViewSet(viewsets.GenericViewSet, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin):
    permission_classes = [IsAuthenticated]
    serializer_class = VendaSerializer
    def get_empresa(self):
        return TenantViewSet.get_empresa(self)
    def get_queryset(self):
        empresa = self.get_empresa()
        qs = Venda.objects.filter(empresa=empresa).select_related("caixa", "operador").prefetch_related("itens")
        membro = MembroEmpresa.objects.get(empresa=empresa, usuario=self.request.user, ativo=True)
        if membro.papel in (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE):
            return qs
        return qs.filter(operador=self.request.user)

    def create(self, request, *args, **kwargs):
        empresa = self.get_empresa()
        venda, criada = registrar_venda(empresa=empresa, usuario=request.user, dados=request.data)
        data = VendaSerializer(venda).data
        return Response(data, status=status.HTTP_201_CREATED if criada else status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="cancelar")
    def cancelar(self, request, pk=None):
        empresa = self.get_empresa()
        venda = cancelar_venda(
            empresa=empresa, usuario=request.user, venda_id=pk,
            codigo=request.data.get("token", ""),
            justificativa=request.data.get("justificativa", ""),
        )
        return Response(VendaSerializer(venda).data)

    @action(detail=True, methods=["post"], url_path="estornar")
    def estornar(self, request, pk=None):
        empresa = self.get_empresa()
        venda = cancelar_venda(
            empresa=empresa, usuario=request.user, venda_id=pk,
            codigo=request.data.get("token", ""),
            acao=TokenAutorizacao.Acao.ESTORNAR,
            justificativa=request.data.get("justificativa", ""),
        )
        return Response(VendaSerializer(venda).data)

    @action(detail=True, methods=["post"], url_path="trocar")
    def trocar(self, request, pk=None):
        from django.db import transaction
        empresa = self.get_empresa()
        with transaction.atomic():
            original = cancelar_venda(
                empresa=empresa, usuario=request.user, venda_id=pk,
                codigo=request.data.get("token", ""),
                acao=TokenAutorizacao.Acao.TROCAR,
                justificativa=request.data.get("justificativa", ""),
            )
            nova, criada = registrar_venda(
                empresa=empresa, usuario=request.user, dados=request.data.get("nova_venda", {}),
            )
        return Response({"venda_cancelada": VendaSerializer(original).data,
                         "nova_venda": VendaSerializer(nova).data},
                        status=status.HTTP_201_CREATED if criada else status.HTTP_200_OK)


class AbrirCaixaView(TenantAPIView):
    def post(self, request):
        from .models import Caixa
        empresa, membro = self.get_empresa(request)
        try:
            saldo = request.data.get("saldo_abertura", "0")
            dispositivo = str(request.data["dispositivo_id"])[:100]
            from decimal import Decimal
            saldo = Decimal(str(saldo))
            if not saldo.is_finite() or saldo < 0 or not dispositivo:
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise ValidationError({"caixa": "Informe dispositivo e saldo de abertura válidos."})
        existente = Caixa.objects.filter(
            empresa=empresa, operador=request.user, estado=Caixa.Estado.ABERTO
        ).first()
        if existente:
            if existente.dispositivo_id == dispositivo:
                return Response({"id": existente.pk, "estado": existente.estado})
            raise ValidationError({"caixa": "Este operador já possui caixa aberto em outro dispositivo."})
        caixa = Caixa.objects.create(
            empresa=empresa, operador=request.user, dispositivo_id=dispositivo, saldo_abertura=saldo,
        )
        return Response({"id": caixa.pk, "estado": caixa.estado}, status=status.HTTP_201_CREATED)


class FecharCaixaView(TenantAPIView):
    def post(self, request, caixa_id):
        from decimal import Decimal, InvalidOperation
        from django.db import transaction
        from .models import Caixa
        empresa, membro = self.get_empresa(request)
        try:
            informado = Decimal(str(request.data["saldo_fechamento"]))
            if not informado.is_finite() or informado < 0:
                raise ValueError
        except (KeyError, ValueError, TypeError, InvalidOperation):
            raise ValidationError({"saldo_fechamento": "Informe um saldo válido."})
        with transaction.atomic():
            try:
                caixa = Caixa.objects.select_for_update().get(
                    pk=caixa_id, empresa=empresa, operador=request.user, estado=Caixa.Estado.ABERTO
                )
            except Caixa.DoesNotExist:
                raise ValidationError({"caixa": "Caixa aberto não encontrado."})
            caixa.saldo_informado_fechamento = informado
            caixa.estado = Caixa.Estado.FECHADO
            caixa.fechado_em = timezone.now()
            caixa.save(update_fields=("saldo_informado_fechamento", "estado", "fechado_em"))
        return Response({"id": caixa.pk, "estado": caixa.estado, "fechado_em": caixa.fechado_em})


class CriarTokenView(TenantAPIView):
    def post(self, request):
        from .models import MembroEmpresa
        empresa, membro = self.get_empresa(request)
        if membro.papel not in (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE):
            raise PermissionDenied("Somente gerente ou administrador pode autorizar operações.")
        acao = request.data.get("acao")
        venda_id = request.data.get("venda")
        permitidas = TokenAutorizacao.Acao.values
        if acao not in permitidas:
            raise ValidationError({"acao": "Ação de autorização inválida."})
        if not venda_id:
            raise ValidationError({"venda": "A autorização precisa estar vinculada a uma venda."})
        venda = None
        if venda_id:
            try:
                venda = Venda.objects.get(pk=venda_id, empresa=empresa)
            except (Venda.DoesNotExist, ValueError, TypeError):
                raise ValidationError({"venda": "Venda não encontrada nesta empresa."})
        codigo = TokenAutorizacao.gerar_codigo()
        token = TokenAutorizacao.objects.create(
            empresa=empresa, token_hash=TokenAutorizacao.hash_token(codigo),
            acao=acao, venda=venda, criado_por=request.user,
            expira_em=timezone.now() + timedelta(minutes=10),
        )
        return Response({"token": codigo, "expira_em": token.expira_em}, status=status.HTTP_201_CREATED)
