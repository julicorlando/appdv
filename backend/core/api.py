from datetime import timedelta
from decimal import Decimal, InvalidOperation
import base64
import mimetypes
from django.utils import timezone
from django.db import transaction
from django.db.models import F, Sum
from django.urls import reverse
from rest_framework import mixins, viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Caixa, Categoria, Empresa, ItemPedidoWeb, MembroEmpresa, MovimentacaoCaixa, PedidoWeb, Produto, TokenAutorizacao, Transacao, Venda
from .serializers import CategoriaSerializer, ProdutoSerializer, VendaSerializer
from .services import cancelar_venda, registrar_venda
from .pedido_web_services import finalizar_pedido_web


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


class EmpresaInfoView(TenantAPIView):
    def get(self, request):
        empresa, membro = self.get_empresa(request)
        logo_data_url = ""
        if empresa.logo:
            with empresa.logo.open("rb") as arquivo:
                conteudo = base64.b64encode(arquivo.read()).decode("ascii")
            tipo = mimetypes.guess_type(empresa.logo.name)[0] or "image/png"
            logo_data_url = f"data:{tipo};base64,{conteudo}"
        return Response({
            "id": empresa.pk, "nome": empresa.nome, "cnpj": empresa.cnpj,
            "logo_data_url": logo_data_url,
        })


class DashboardEmpresaAPIView(TenantAPIView):
    def get(self, request):
        from .models import MembroEmpresa
        empresa, membro = self.get_empresa(request)
        if membro.papel not in (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE):
            raise PermissionDenied("A área administrativa exige perfil de gerente ou administrador.")
        hoje = timezone.localdate()
        vendas = Venda.objects.filter(
            empresa=empresa, estado=Venda.Estado.CONCLUIDA
        )
        dia = vendas.filter(realizada_em__date=hoje)
        mes = vendas.filter(realizada_em__year=hoje.year, realizada_em__month=hoje.month)
        return Response({
            "empresa": empresa.nome,
            "produtos_ativos": Produto.objects.filter(empresa=empresa, ativo=True).count(),
            "estoque_baixo": Produto.objects.filter(
                empresa=empresa, ativo=True, estoque_atual__lte=F("estoque_minimo")
            ).count(),
            "vendas_hoje": dia.count(),
            "faturamento_hoje": str(dia.aggregate(total=Sum("total"))["total"] or 0),
            "vendas_mes": mes.count(),
            "faturamento_mes": str(mes.aggregate(total=Sum("total"))["total"] or 0),
            "pedidos_confirmados": PedidoWeb.objects.filter(
                empresa=empresa, estado=PedidoWeb.Estado.CONFIRMADO
            ).count(),
        })


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
            from decimal import Decimal, InvalidOperation
            saldo = Decimal(str(saldo))
            if not saldo.is_finite() or saldo < 0 or not dispositivo:
                raise ValueError
        except (KeyError, ValueError, TypeError, InvalidOperation):
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


def _resumo_caixa(caixa):
    transacoes = list(
        Transacao.objects.filter(venda__caixa=caixa)
        .values("meio", "tipo").annotate(total=Sum("valor"))
    )
    meios = {}
    for item in transacoes:
        meio = item["meio"]
        meios.setdefault(meio, {"pagamentos": Decimal("0.00"), "estornos": Decimal("0.00")})
        chave = "pagamentos" if item["tipo"] == Transacao.Tipo.PAGAMENTO else "estornos"
        meios[meio][chave] += item["total"] or Decimal("0.00")
    movimentos = MovimentacaoCaixa.objects.filter(caixa=caixa).values("tipo").annotate(total=Sum("valor"))
    sangrias = sum((x["total"] or Decimal("0") for x in movimentos if x["tipo"] == MovimentacaoCaixa.Tipo.SANGRIA), Decimal("0.00"))
    suprimentos = sum((x["total"] or Decimal("0") for x in movimentos if x["tipo"] == MovimentacaoCaixa.Tipo.SUPRIMENTO), Decimal("0.00"))
    dinheiro = meios.get(Transacao.Meio.DINHEIRO, {"pagamentos": Decimal("0"), "estornos": Decimal("0")})
    esperado = (
        caixa.saldo_abertura + dinheiro["pagamentos"] - dinheiro["estornos"]
        + suprimentos - sangrias
    ).quantize(Decimal("0.01"))
    return {
        "caixa": caixa.pk,
        "estado": caixa.estado,
        "aberto_em": caixa.aberto_em,
        "fechado_em": caixa.fechado_em,
        "saldo_abertura": str(caixa.saldo_abertura),
        "esperado_dinheiro": str(esperado),
        "sangrias": str(sangrias),
        "suprimentos": str(suprimentos),
        "pagamentos_por_meio": {
            meio: {
                "pagamentos": str(valores["pagamentos"]),
                "estornos": str(valores["estornos"]),
                "liquido": str(valores["pagamentos"] - valores["estornos"]),
            } for meio, valores in meios.items()
        },
        "saldo_informado_fechamento": (
            str(caixa.saldo_informado_fechamento)
            if caixa.saldo_informado_fechamento is not None else None
        ),
        "saldo_esperado_fechamento": (
            str(caixa.saldo_esperado_fechamento)
            if caixa.saldo_esperado_fechamento is not None else None
        ),
        "diferenca_fechamento": (
            str(caixa.diferenca_fechamento)
            if caixa.diferenca_fechamento is not None else None
        ),
    }


class CaixaResumoView(TenantAPIView):
    def get(self, request, caixa_id):
        empresa, membro = self.get_empresa(request)
        try:
            caixa = Caixa.objects.get(pk=caixa_id, empresa=empresa)
        except Caixa.DoesNotExist:
            raise ValidationError({"caixa": "Caixa não encontrado nesta empresa."})
        if caixa.operador_id != request.user.pk and membro.papel not in (
            MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE
        ):
            raise PermissionDenied("Você não pode consultar o fechamento deste caixa.")
        resumo = _resumo_caixa(caixa)
        resumo["saldo_informado_fechamento"] = (
            str(caixa.saldo_informado_fechamento)
            if caixa.saldo_informado_fechamento is not None else None
        )
        resumo["diferenca_atual"] = (
            str(Decimal(str(caixa.saldo_informado_fechamento)) - Decimal(resumo["esperado_dinheiro"]))
            if caixa.saldo_informado_fechamento is not None else None
        )
        return Response(resumo)


class MovimentacaoCaixaView(TenantAPIView):
    @transaction.atomic
    def post(self, request):
        empresa, membro = self.get_empresa(request)
        try:
            caixa = Caixa.objects.select_for_update().get(
                pk=request.data.get("caixa"), empresa=empresa,
                operador=request.user, estado=Caixa.Estado.ABERTO,
            )
            tipo = str(request.data.get("tipo", ""))
            valor = Decimal(str(request.data.get("valor", "")))
            motivo = str(request.data.get("motivo", "")).strip()[:250]
            if tipo not in MovimentacaoCaixa.Tipo.values or not valor.is_finite() or valor <= 0 or not motivo:
                raise ValueError
        except Caixa.DoesNotExist:
            raise ValidationError({"caixa": "Caixa aberto do operador não encontrado."})
        except (ValueError, TypeError, InvalidOperation):
            raise ValidationError({"movimentacao": "Informe tipo, valor positivo e motivo."})
        movimento = MovimentacaoCaixa.objects.create(
            empresa=empresa, caixa=caixa, operador=request.user,
            tipo=tipo, valor=valor, motivo=motivo,
        )
        return Response({
            "id": movimento.pk, "tipo": movimento.tipo, "valor": str(movimento.valor),
            "motivo": movimento.motivo, "criado_em": movimento.criado_em,
            "resumo": _resumo_caixa(caixa),
        }, status=status.HTTP_201_CREATED)


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
            resumo = _resumo_caixa(caixa)
            esperado = Decimal(resumo["esperado_dinheiro"])
            caixa.saldo_informado_fechamento = informado
            caixa.saldo_esperado_fechamento = esperado
            caixa.diferenca_fechamento = (informado - esperado).quantize(Decimal("0.01"))
            caixa.estado = Caixa.Estado.FECHADO
            caixa.fechado_em = timezone.now()
            caixa.save(update_fields=(
                "saldo_informado_fechamento", "saldo_esperado_fechamento",
                "diferenca_fechamento", "estado", "fechado_em",
            ))
        return Response({"id": caixa.pk, "estado": caixa.estado, "fechado_em": caixa.fechado_em,
                         "resumo": _resumo_caixa(caixa)})


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


class PedidosWebView(TenantAPIView):
    """Pedidos públicos iniciados no PDV; a venda só é lançada ao finalizar no caixa."""
    def get(self, request):
        empresa, membro = self.get_empresa(request)
        pedidos = PedidoWeb.objects.filter(empresa=empresa).exclude(
            estado__in=(PedidoWeb.Estado.CONCLUIDO, PedidoWeb.Estado.CANCELADO)
        ).prefetch_related("itens").order_by("-criado_em")[:100]
        return Response([{
            "id": pedido.pk, "codigo_publico": str(pedido.codigo_publico),
            "cliente_nome": pedido.cliente_nome, "cliente_telefone": pedido.cliente_telefone,
            "estado": pedido.estado, "subtotal": str(pedido.subtotal), "total": str(pedido.total),
            "criado_em": pedido.criado_em, "expira_em": pedido.expira_em,
            "public_url": request.build_absolute_uri(
                reverse("pedido-publico", kwargs={"codigo": pedido.codigo_publico})
            ),
            "itens": [{
                "produto": item.produto_id, "nome": item.nome_produto, "codigo": item.codigo_produto,
                "quantidade": str(item.quantidade), "preco_unitario": str(item.preco_unitario),
                "total": str(item.total),
            } for item in pedido.itens.all()],
        } for pedido in pedidos])

    @transaction.atomic
    def post(self, request):
        from datetime import timedelta
        from decimal import Decimal, InvalidOperation
        import re
        from .models import Produto
        empresa, membro = self.get_empresa(request)
        nome = str(request.data.get("cliente_nome", "")).strip()[:160]
        telefone = re.sub(r"\D", "", str(request.data.get("cliente_telefone", "")))
        linhas = request.data.get("itens")
        if not nome or not (10 <= len(telefone) <= 15):
            raise ValidationError({"cliente": "Informe nome e telefone com DDD e código do país, se necessário."})
        if not isinstance(linhas, list) or not linhas or len(linhas) > 200:
            raise ValidationError({"itens": "Informe de 1 a 200 itens no pedido."})
        produtos = {}
        calculados = []
        subtotal = Decimal("0.00")
        for linha in linhas:
            try:
                produto = Produto.objects.select_for_update().get(
                    pk=linha["produto"], empresa=empresa, ativo=True
                )
                quantidade = Decimal(str(linha["quantidade"]))
                if not quantidade.is_finite() or quantidade <= 0:
                    raise ValueError
            except Produto.DoesNotExist:
                raise ValidationError({"itens": "Produto indisponível nesta empresa."})
            except (KeyError, ValueError, TypeError, InvalidOperation):
                raise ValidationError({"itens": "Produto ou quantidade inválidos."})
            produtos[produto.pk] = produtos.get(produto.pk, Decimal("0")) + quantidade
            linha_total = (produto.preco_venda * quantidade).quantize(Decimal("0.01"))
            calculados.append((produto, quantidade, linha_total))
            subtotal += linha_total
        for produto_id, quantidade in produtos.items():
            produto = next(item[0] for item in calculados if item[0].pk == produto_id)
            if produto.estoque_atual < quantidade:
                raise ValidationError({"itens": f"Estoque insuficiente para {produto.nome}."})
        pedido = PedidoWeb.objects.create(
            empresa=empresa, cliente_nome=nome, cliente_telefone=telefone,
            estado=PedidoWeb.Estado.AGUARDANDO_CLIENTE, subtotal=subtotal,
            total=subtotal, desconto=Decimal("0"), criado_por=request.user,
            expira_em=timezone.now() + timedelta(days=7),
        )
        for produto, quantidade, linha_total in calculados:
            ItemPedidoWeb.objects.create(
                pedido=pedido, empresa=empresa, produto=produto,
                nome_produto=produto.nome, codigo_produto=produto.codigo,
                quantidade=quantidade, preco_unitario=produto.preco_venda, total=linha_total,
            )
        public_url = request.build_absolute_uri(
            reverse("pedido-publico", kwargs={"codigo": pedido.codigo_publico})
        )
        return Response({
            "id": pedido.pk, "codigo_publico": str(pedido.codigo_publico),
            "estado": pedido.estado, "total": str(pedido.total), "public_url": public_url,
        }, status=status.HTTP_201_CREATED)


class FinalizarPedidoWebView(TenantAPIView):
    def post(self, request, pedido_id):
        from django.db import transaction
        empresa, membro = self.get_empresa(request)
        resultado = finalizar_pedido_web(
            empresa=empresa, usuario=request.user, pedido_id=pedido_id,
            caixa_id=request.data.get("caixa"), pagamentos=request.data.get("pagamentos"),
        )
        pedido, venda = resultado
        return Response({
            "pedido_id": pedido.pk, "estado": pedido.estado,
            "venda_id": venda.pk, "total": str(venda.total),
        })
