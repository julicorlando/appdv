from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import uuid
import base64
import mimetypes
from django.utils import timezone
from django.db import transaction
from django.db.models import F, Sum
from django.urls import reverse
from rest_framework import mixins, viewsets, status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from .models import Caixa, Categoria, Empresa, ItemPedidoWeb, MembroEmpresa, MovimentacaoCaixa, PedidoWeb, Produto, TokenAutorizacao, Transacao, Venda
from .serializers import CategoriaSerializer, ProdutoSerializer, VendaSerializer
from .services import cancelar_venda, registrar_venda
from .pedido_web_services import finalizar_pedido_web
from .access import caixa_para_movimento, conta_ativa
from .throttles import (
    AutorizacaoAprovadorThrottle, AutorizacaoOperadorThrottle, LoginIPThrottle, LoginUsuarioThrottle,
)


def _checar_conta(usuario):
    if not conta_ativa(usuario):
        raise PermissionDenied("Conta desativada.")


def _checar_assinatura(empresa):
    """PDV e integrações param quando a empresa está suspensa (as vendas offline ficam guardadas no PC)."""
    from . import cobranca
    cobranca.processar_se_preciso()
    mensagem = cobranca.bloqueio(empresa)
    if mensagem:
        raise AssinaturaSuspensaAPI({"detail": mensagem, "codigo": "assinatura_suspensa"})


class AssinaturaSuspensaAPI(PermissionDenied):
    """403 com código próprio: o PDV mostra o aviso de suspensão em vez de tratar como queda de rede."""
    default_code = "assinatura_suspensa"


class TenantAPIView(APIView):
    permission_classes = [IsAuthenticated]
    def get_empresa(self, request):
        _checar_conta(request.user)
        empresa_id = request.headers.get("X-Empresa-ID")
        if not empresa_id:
            raise ValidationError({"empresa": "Informe X-Empresa-ID."})
        try:
            membro = MembroEmpresa.objects.select_related("empresa").get(
                empresa_id=empresa_id, usuario=request.user, ativo=True, empresa__ativa=True
            )
        except (MembroEmpresa.DoesNotExist, ValueError):
            raise PermissionDenied("Acesso negado a esta empresa.")
        _checar_assinatura(membro.empresa)
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
        from .models import PoliticaDesconto
        policy = PoliticaDesconto.objects.filter(empresa=empresa).first()
        return Response({
            "politica_desconto": {"modo": policy.modo, "limite_caixa": str(policy.limite_caixa), "percentual": str(policy.percentual), "meio": policy.meio, "minimo": str(policy.minimo)} if policy else {"modo":"MANUAL","limite_caixa":"5","percentual":"0","meio":"","minimo":"0"},
            "id": empresa.pk, **empresa.dados_documento(),
            "endereco_documento": empresa.endereco_documento,
            "logo_data_url": logo_data_url,
            "pix_configurado": bool(empresa.mp_access_token),
            "point_configurado": bool(empresa.mp_access_token and empresa.mp_point_dispositivo),
            "nfce_configurada": bool(empresa.focus_token),
            "nfce_automatica": bool(empresa.focus_token and empresa.nfce_automatica),
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
        _checar_conta(request.user)
        empresas = MembroEmpresa.objects.filter(
            usuario=request.user, ativo=True, empresa__ativa=True
        ).select_related("empresa")
        return Response([{"id": m.empresa_id, "nome": m.empresa.nome,
                          "slug": m.empresa.slug, "papel": m.papel} for m in empresas])


class TenantViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    def get_empresa(self):
        _checar_conta(self.request.user)
        empresa_id = self.request.headers.get("X-Empresa-ID")
        if not empresa_id:
            raise ValidationError({"empresa": "Informe X-Empresa-ID."})
        try:
            membro = MembroEmpresa.objects.select_related("empresa").get(
                empresa_id=empresa_id, usuario=self.request.user, ativo=True, empresa__ativa=True
            )
        except (MembroEmpresa.DoesNotExist, ValueError):
            raise PermissionDenied("Acesso negado a esta empresa.")
        _checar_assinatura(membro.empresa)
        return membro.empresa


class CategoriaViewSet(TenantViewSet):
    serializer_class = CategoriaSerializer
    def get_queryset(self):
        return Categoria.objects.filter(empresa=self.get_empresa(), ativa=True).order_by("nome")


class ProdutoViewSet(TenantViewSet):
    serializer_class = ProdutoSerializer
    search_fields = ("nome", "codigo", "codigo_barras")
    filter_backends = [SearchFilter]
    def get_queryset(self):
        qs = Produto.objects.filter(empresa=self.get_empresa()).select_related("categoria").order_by("nome")
        desde = self.request.query_params.get("desde") if self.request else None
        if desde:
            # Sincronização incremental do PDV: só o que mudou, incluindo inativados (para sair do caixa).
            from django.utils.dateparse import parse_datetime
            momento = parse_datetime(desde.replace(" ", "+"))
            if momento is None:
                raise ValidationError({"desde": "Data inválida (use ISO 8601)."})
            if timezone.is_naive(momento):
                momento = timezone.make_aware(momento)
            return qs.filter(atualizado_em__gt=momento)
        return qs.filter(ativo=True)


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
        if request.data.get("orcamento"):
            from .management_services import vender_orcamento
            venda, criada = vender_orcamento(empresa, request.user, request.data)
        else:
            venda, criada = registrar_venda(empresa=empresa, usuario=request.user, dados=request.data)
        data = dict(VendaSerializer(venda).data)
        data["nfce"] = _nfce_apos_venda(empresa, venda, criada)
        return Response(data, status=status.HTTP_201_CREATED if criada else status.HTTP_200_OK)

    @action(detail=True, methods=["get", "post"], url_path="nfce")
    def nfce(self, request, pk=None):
        """GET consulta a NFC-e da venda; POST emite (ou reenvia após rejeição)."""
        from . import focus_nfe
        from .models import DocumentoFiscal
        venda = self.get_object()
        if request.method == "POST":
            documento = focus_nfe.emitir(venda)
        else:
            documento = DocumentoFiscal.objects.filter(empresa=venda.empresa, venda=venda).first()
            if documento and documento.estado == "PROCESSANDO":
                documento = focus_nfe.consultar(documento)
        return Response({"venda": venda.pk, "nfce": focus_nfe.resumo(documento), "configurado": focus_nfe.configurado(venda.empresa)})

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


def _nfce_apos_venda(empresa, venda, criada):
    """Emissão automática opcional. Falhas fiscais nunca desfazem a venda já registrada."""
    from . import focus_nfe
    from .models import DocumentoFiscal
    if criada and empresa.nfce_automatica and focus_nfe.configurado(empresa):
        try:
            return focus_nfe.resumo(focus_nfe.emitir(venda))
        except ValidationError as erro:
            return {"estado": "CONFIGURAR", "estado_texto": "Não emitida", "mensagem": str(erro.detail), "danfe_url": ""}
    return focus_nfe.resumo(DocumentoFiscal.objects.filter(empresa=empresa, venda=venda).first())


class ClientesView(TenantAPIView):
    def get(self, request):
        empresa, membro = self.get_empresa(request)
        from .models import Cliente
        from .orcamentos import limite_disponivel
        clientes = Cliente.objects.filter(empresa=empresa, ativo=True).order_by("nome")[:500]
        return Response([{
            "id": c.pk, "nome": c.nome, "documento": c.documento,
            "telefone": c.telefone, "email": c.email,
            "limite_credito": str(c.limite_credito),
            "limite_disponivel": str(limite_disponivel(c)),
        } for c in clientes])

    def post(self, request):
        empresa, membro = self.get_empresa(request)
        if membro.papel not in (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE):
            raise PermissionDenied("Somente gerente ou administrador pode cadastrar clientes.")
        from .models import Cliente
        nome = str(request.data.get("nome", "")).strip()
        if not nome:
            raise ValidationError({"nome": "Informe o nome do cliente."})
        try:
            limite = Decimal(str(request.data.get("limite_credito", "0")))
            if not limite.is_finite() or limite < 0:
                raise ValueError
        except (ValueError, TypeError, InvalidOperation):
            raise ValidationError({"limite_credito": "Informe um limite válido."})
        cliente = Cliente.objects.create(
            empresa=empresa, nome=nome[:180],
            documento="".join(ch for ch in str(request.data.get("documento", "")) if ch.isdigit())[:14],
            telefone=str(request.data.get("telefone", ""))[:30],
            email=str(request.data.get("email", ""))[:254],
            endereco=str(request.data.get("endereco", ""))[:250],
            limite_credito=limite,
        )
        return Response({"id": cliente.pk, "nome": cliente.nome}, status=status.HTTP_201_CREATED)


class ContasReceberView(TenantAPIView):
    def get(self, request):
        empresa, membro = self.get_empresa(request)
        from .models import ContaReceber
        contas = ContaReceber.objects.filter(
            empresa=empresa, estado__in=(ContaReceber.Estado.ABERTA, ContaReceber.Estado.PARCIAL)
        ).select_related("cliente", "venda").order_by("vencimento")[:500]
        return Response([{
            "id": c.pk, "cliente": c.cliente.nome, "venda": c.venda_id,
            "valor_original": str(c.valor_original), "saldo": str(c.saldo),
            "vencimento": c.vencimento.isoformat(), "estado": c.estado,
        } for c in contas])

    @transaction.atomic
    def post(self, request):
        empresa, membro = self.get_empresa(request)
        from .models import ContaReceber, RecebimentoConta
        try:
            conta = ContaReceber.objects.select_for_update().get(
                pk=request.data.get("conta"), empresa=empresa,
                estado__in=(ContaReceber.Estado.ABERTA, ContaReceber.Estado.PARCIAL),
            )
            valor = Decimal(str(request.data.get("valor")))
            meio = str(request.data.get("meio"))
            if not valor.is_finite() or valor <= 0 or valor > conta.saldo or meio not in ("DINHEIRO","PIX","CARTAO","OUTRO"):
                raise ValueError
        except (ContaReceber.DoesNotExist, ValueError, TypeError, InvalidOperation):
            raise ValidationError({"recebimento": "Conta, valor ou forma de pagamento inválidos."})
        caixa = caixa_para_movimento(
            empresa, request.user, [meio], caixa_id=request.data.get("caixa"),
            operacao="receber em dinheiro",
        )
        RecebimentoConta.objects.create(
            conta=conta, empresa=empresa, operador=request.user, caixa=caixa,
            meio=meio, valor=valor, referencia=str(request.data.get("referencia", ""))[:120],
        )
        conta.saldo -= valor
        conta.estado = ContaReceber.Estado.QUITADA if conta.saldo == 0 else ContaReceber.Estado.PARCIAL
        conta.save(update_fields=("saldo", "estado"))
        return Response({"id": conta.pk, "saldo": str(conta.saldo), "estado": conta.estado,
                         "caixa": caixa.pk if caixa else None})


class ContasPagarView(TenantAPIView):
    def get(self, request):
        empresa, membro = self.get_empresa(request)
        if membro.papel not in (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE):
            raise PermissionDenied("A área financeira exige perfil de gerente ou administrador.")
        from .models import ContaPagar
        contas = ContaPagar.objects.filter(
            empresa=empresa, estado__in=(ContaPagar.Estado.ABERTA, ContaPagar.Estado.PARCIAL)
        ).select_related("fornecedor").order_by("vencimento")[:500]
        return Response([{
            "id": c.pk, "fornecedor": c.fornecedor.nome, "descricao": c.descricao,
            "valor_original": str(c.valor_original), "saldo": str(c.saldo),
            "vencimento": c.vencimento.isoformat(), "estado": c.estado,
        } for c in contas])

    @transaction.atomic
    def post(self, request):
        empresa, membro = self.get_empresa(request)
        if membro.papel not in (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE):
            raise PermissionDenied("Somente gerente ou administrador pode registrar pagamentos.")
        from .models import ContaPagar, PagamentoContaPagar
        try:
            conta = ContaPagar.objects.select_for_update().get(
                pk=request.data.get("conta"), empresa=empresa,
                estado__in=(ContaPagar.Estado.ABERTA, ContaPagar.Estado.PARCIAL),
            )
            valor = Decimal(str(request.data.get("valor")))
            meio = str(request.data.get("meio"))
            if not valor.is_finite() or valor <= 0 or valor > conta.saldo or meio not in ("DINHEIRO","PIX","CARTAO","OUTRO"):
                raise ValueError
        except (ContaPagar.DoesNotExist, ValueError, TypeError, InvalidOperation):
            raise ValidationError({"pagamento": "Conta, valor ou forma de pagamento inválidos."})
        PagamentoContaPagar.objects.create(
            conta=conta, empresa=empresa, operador=request.user,
            meio=meio, valor=valor, referencia=str(request.data.get("referencia", ""))[:120],
        )
        conta.saldo -= valor
        conta.estado = ContaPagar.Estado.QUITADA if conta.saldo == 0 else ContaPagar.Estado.PARCIAL
        conta.save(update_fields=("saldo", "estado"))
        return Response({"id": conta.pk, "saldo": str(conta.saldo), "estado": conta.estado})


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
    from .models import RecebimentoConta
    transacoes = list(
        Transacao.objects.filter(caixa=caixa)
        .values("meio", "tipo").annotate(total=Sum("valor"))
    )
    recebimentos = {
        item["meio"]: item["total"] or Decimal("0.00")
        for item in RecebimentoConta.objects.filter(caixa=caixa).values("meio").annotate(total=Sum("valor"))
    }
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
    recebido_dinheiro = recebimentos.get(Transacao.Meio.DINHEIRO, Decimal("0.00"))
    esperado = (
        caixa.saldo_abertura + dinheiro["pagamentos"] - dinheiro["estornos"]
        + recebido_dinheiro + suprimentos - sangrias
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
        "recebimentos_conta_por_meio": {
            meio: str(Decimal(valor).quantize(Decimal("0.01"))) for meio, valor in recebimentos.items()
        },
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
        chave = request.data.get("chave")
        if chave:
            # Reenvio do PDV offline: a mesma chave nunca gera uma segunda sangria/suprimento.
            try:
                chave = uuid.UUID(str(chave))
            except ValueError:
                raise ValidationError({"chave": "Chave inválida."})
            existente = MovimentacaoCaixa.objects.filter(empresa=empresa, chave=chave).select_related("caixa").first()
            if existente:
                if existente.operador_id != request.user.pk:
                    raise ValidationError({"chave": "Chave já usada."})
                return Response({"id": existente.pk, "tipo": existente.tipo, "valor": str(existente.valor),
                                 "motivo": existente.motivo, "criado_em": existente.criado_em,
                                 "resumo": _resumo_caixa(existente.caixa)})
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
            tipo=tipo, valor=valor, motivo=motivo, chave=chave or None,
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
                caixa = Caixa.objects.select_for_update().get(pk=caixa_id, empresa=empresa, operador=request.user)
            except Caixa.DoesNotExist:
                raise ValidationError({"caixa": "Caixa aberto não encontrado."})
            if caixa.estado == Caixa.Estado.FECHADO:
                # Reenvio do fechamento feito offline (ou já fechado pela gerência): devolve o que já vale.
                return Response({"id": caixa.pk, "estado": caixa.estado, "fechado_em": caixa.fechado_em,
                                 "ja_fechado": True, "resumo": _resumo_caixa(caixa)})
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
            "estado": pedido.estado, "subtotal": str(pedido.subtotal), "frete": str(pedido.frete), "total": str(pedido.total),
            "endereco_entrega": pedido.endereco_entrega,
            "entrega_solicitada_em": pedido.entrega_solicitada_em,
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
        endereco_entrega = str(request.data.get("endereco_entrega", "")).strip()[:250]
        raw_delivery = str(request.data.get("entrega_solicitada_em", "")).strip()
        try:
            entrega_solicitada_em = date.fromisoformat(raw_delivery) if raw_delivery else None
            if entrega_solicitada_em and entrega_solicitada_em < timezone.localdate():
                raise ValueError
        except ValueError:
            raise ValidationError({"entrega_solicitada_em": "A data de entrega deve ser hoje ou uma data futura."})
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
            fator = Decimal(str(produto.fator_conversao))
            if not fator.is_finite() or fator <= 0:
                raise ValidationError({"itens": "Fator de conversão inválido no catálogo."})
            produtos[produto.pk] = produtos.get(produto.pk, Decimal("0")) + (quantidade * fator)
            linha_total = (produto.preco_atual * quantidade).quantize(Decimal("0.01"))
            calculados.append((produto, quantidade, linha_total))
            subtotal += linha_total
        for produto_id, quantidade in produtos.items():
            produto = next(item[0] for item in calculados if item[0].pk == produto_id)
            if produto.estoque_atual < quantidade:
                raise ValidationError({"itens": f"Estoque insuficiente para {produto.nome}."})
        frete = empresa.frete_padrao if endereco_entrega else Decimal("0.00")
        pedido = PedidoWeb.objects.create(
            empresa=empresa, cliente_nome=nome, cliente_telefone=telefone,
            endereco_entrega=endereco_entrega, entrega_solicitada_em=entrega_solicitada_em,
            estado=PedidoWeb.Estado.AGUARDANDO_CLIENTE, subtotal=subtotal,
            total=subtotal + frete, frete=frete, desconto=Decimal("0"), criado_por=request.user,
            expira_em=timezone.now() + timedelta(days=7),
        )
        for produto, quantidade, linha_total in calculados:
            ItemPedidoWeb.objects.create(
                pedido=pedido, empresa=empresa, produto=produto,
                nome_produto=produto.nome, codigo_produto=produto.codigo,
                quantidade=quantidade, preco_unitario=produto.preco_atual, total=linha_total,
            )
        public_url = request.build_absolute_uri(
            reverse("pedido-publico", kwargs={"codigo": pedido.codigo_publico})
        )
        return Response({
            "id": pedido.pk, "codigo_publico": str(pedido.codigo_publico),
            "estado": pedido.estado, "frete": str(pedido.frete), "total": str(pedido.total), "public_url": public_url,
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


class ClienteCPFView(TenantAPIView):
    def get(self, request):
        from .models import Cliente
        from .orcamentos import normalizar_cpf, dados_cliente
        empresa, membro = self.get_empresa(request)
        cliente = Cliente.objects.filter(empresa=empresa, documento=normalizar_cpf(request.query_params.get('cpf', ''))).first()
        if cliente and not cliente.ativo:
            raise ValidationError({'cliente': 'Cadastro inativo. Solicite revisão ao administrador.'})
        if not cliente:
            from django.http import JsonResponse
            # Explicit JSON null stays compatible with installed desktop versions.
            return JsonResponse(None, safe=False)
        return Response(dados_cliente(cliente))

    def post(self, request):
        from .models import Cliente
        from .orcamentos import normalizar_cpf, dados_cliente
        empresa, membro = self.get_empresa(request)
        cpf = normalizar_cpf(request.data.get('cpf', ''))
        nome = str(request.data.get('nome', '')).strip()
        if not nome or len(nome) > 180:
            raise ValidationError({'nome': 'Informe o nome do cliente, até 180 caracteres.'})
        cliente, criado = Cliente.objects.get_or_create(empresa=empresa, documento=cpf,
            defaults={'nome': nome, 'telefone': str(request.data.get('telefone', ''))[:30], 'limite_credito': 0})
        if not cliente.ativo:
            raise ValidationError({'cliente': 'Cadastro inativo. Solicite revisão ao administrador.'})
        return Response(dados_cliente(cliente), status=201 if criado else 200)


class OrcamentosView(TenantAPIView):
    def get(self, request, orcamento_id=None):
        from .models import Orcamento
        from .orcamentos import normalizar_cpf, dados_orcamento
        empresa, membro = self.get_empresa(request)
        qs = Orcamento.objects.filter(empresa=empresa).select_related('cliente')
        if orcamento_id is not None:
            from django.shortcuts import get_object_or_404
            return Response(dados_orcamento(get_object_or_404(qs, pk=orcamento_id)))
        query = request.query_params.get('busca', '').strip()
        if not query:
            raise ValidationError({'busca': 'Informe número do orçamento ou CPF.'})
        if request.query_params.get('tipo') == 'cpf':
            qs = qs.filter(cliente__documento=normalizar_cpf(query))
        elif query.isascii() and query.isdigit():
            qs = qs.filter(pk=int(query))
        else:
            raise ValidationError({'busca': 'Número de orçamento inválido.'})
        return Response([dados_orcamento(obj) for obj in qs.order_by('-criado_em')[:100]])

    def post(self, request):
        from .orcamentos import salvar_orcamento, dados_orcamento
        empresa, membro = self.get_empresa(request)
        obj, criado = salvar_orcamento(empresa, membro, request.data)
        return Response(dados_orcamento(obj), status=201 if criado else 200)


class AutorizarDescontoView(TenantAPIView):
    throttle_classes = [AutorizacaoOperadorThrottle, AutorizacaoAprovadorThrottle]

    def post(self, request):
        import uuid
        from .models import AprovacaoDesconto
        from .discount_policy import fingerprint
        empresa, membro = self.get_empresa(request)
        approver = _autenticar_gerente(request, empresa)
        try:
            key = uuid.UUID(str(request.data.get('chave')))
        except (TypeError, ValueError):
            raise ValidationError({'chave': 'Informe a chave da operação.'})
        obj = AprovacaoDesconto.objects.create(empresa=empresa, operador=request.user, aprovador=approver,
            chave_operacao=key, resumo_itens=fingerprint(request.data.get('itens', [])))
        return Response({'codigo': str(obj.codigo)}, status=201)


class TerminalHeartbeatView(TenantAPIView):
    def post(self,request):
        from .models import TerminalPDV
        empresa,membro=self.get_empresa(request)
        name=str(request.data.get('identificador','')).strip()[:120]
        try:
            count=int(request.data.get('pendentes',0))
            if not name or not 0<=count<=100000:raise ValueError
        except (ValueError,TypeError):raise ValidationError('Terminal ou quantidade inválidos.')
        from . import cobranca
        cobranca.validar_terminal(empresa,name)
        TerminalPDV.objects.update_or_create(empresa=empresa,identificador=name,defaults={'operador':request.user,'pendentes':count,'erros':str(request.data.get('erros',''))[:2000],'versao':str(request.data.get('versao',''))[:20]})
        return Response({'ok':True,**_versoes_pdv()})


def _autenticar_gerente(request, empresa):
    from django.contrib.auth import authenticate
    approver = authenticate(request=request, username=request.data.get('usuario', ''), password=request.data.get('senha', ''))
    if not approver or not conta_ativa(approver) or not MembroEmpresa.objects.filter(
        empresa=empresa, usuario=approver, ativo=True, papel__in=['ADMIN', 'GERENTE']
    ).exists():
        raise PermissionDenied('Credenciais inválidas ou sem permissão nesta empresa.')
    return approver


class DescartarVendaOfflineView(TenantAPIView):
    """Encerra no PDV uma venda offline que o servidor recusou.

    A venda não é registrada: o descarte exige senha de gerente/administrador,
    justificativa e fica auditado com o conteúdo da venda, para que a loja
    possa lançá-la de novo ou cobrar a diferença.
    """
    throttle_classes = [AutorizacaoOperadorThrottle, AutorizacaoAprovadorThrottle]

    def post(self, request):
        import uuid
        from .management_services import audit
        empresa, membro = self.get_empresa(request)
        try:
            chave = uuid.UUID(str(request.data.get('chave')))
        except (TypeError, ValueError):
            raise ValidationError({'chave': 'Informe a chave da venda pendente.'})
        justificativa = str(request.data.get('justificativa', '')).strip()
        if len(justificativa) < 5:
            raise ValidationError({'justificativa': 'Descreva o motivo do descarte (mínimo de 5 caracteres).'})
        venda = Venda.objects.filter(empresa=empresa, idempotency_key=chave).first()
        if venda:
            # A venda chegou ao servidor; o PDV só precisa limpar a fila.
            return Response({'sincronizada': True, 'venda': venda.pk})
        approver = _autenticar_gerente(request, empresa)
        venda_local = request.data.get('venda')
        audit(empresa, request.user, 'VENDA_OFFLINE_DESCARTADA', str(chave),
              aprovador=approver.username, justificativa=justificativa[:500],
              erro=str(request.data.get('erro', ''))[:500],
              venda=venda_local if isinstance(venda_local, dict) else {})
        return Response({'descartada': True}, status=status.HTTP_201_CREATED)


class LoginView(TokenObtainPairView):
    """Login JWT com limite de tentativas e bloqueio de contas desativadas."""
    throttle_classes = [LoginUsuarioThrottle, LoginIPThrottle]

    def get_serializer_class(self):
        from rest_framework.exceptions import AuthenticationFailed
        from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

        class Serializer(TokenObtainPairSerializer):
            def validate(self, attrs):
                data = super().validate(attrs)
                if not conta_ativa(self.user):
                    raise AuthenticationFailed('Conta desativada.')
                return data
        return Serializer


def _versoes_pdv():
    import os
    from django.conf import settings as _settings
    atual = os.getenv("PDV_VERSAO_ATUAL", "0.4.0")
    # Instalador da versão anunciada (o PDV confere o .sha256 publicado junto antes de instalar).
    instalador = os.getenv("PDV_INSTALADOR_URL",
                           f"https://github.com/julicorlando/appdv/releases/download/pdv-v{atual}/ApPdv-Setup.exe")
    return {"instalador_url": instalador, "sha256_url": instalador + ".sha256",
            "versao_minima": os.getenv("PDV_VERSAO_MINIMA", "0.3.0"), "versao_atual": atual,
            "download_url": os.getenv("PDV_DOWNLOAD_URL", ""), "offline_dias": _settings.PDV_OFFLINE_DIAS,
            "agora": timezone.now().isoformat()}


class VersaoPdvView(APIView):
    """Versão mínima e atual do PDV instalado (o aplicativo avisa quando estiver desatualizado)."""
    permission_classes = []
    authentication_classes = []

    def get(self, request):
        return Response(_versoes_pdv())


class AlterarSenhaView(APIView):
    """Troca da própria senha (PDV instalado e integrações)."""
    throttle_classes = [AutorizacaoOperadorThrottle]

    def post(self, request):
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError
        _checar_conta(request.user)
        atual = str(request.data.get("senha_atual", ""))
        nova = str(request.data.get("nova_senha", ""))
        if not request.user.check_password(atual):
            raise ValidationError({"senha_atual": "Senha atual incorreta."})
        if nova == atual:
            raise ValidationError({"nova_senha": "Escolha uma senha diferente da atual."})
        try:
            validate_password(nova, request.user)
        except DjangoValidationError as erro:
            raise ValidationError({"nova_senha": list(erro.messages)})
        request.user.set_password(nova)
        request.user.save(update_fields=["password"])
        from .models import MembroEmpresa as _M, EventoGestao
        for membro in _M.objects.filter(usuario=request.user, ativo=True).select_related("empresa")[:20]:
            EventoGestao.objects.create(empresa=membro.empresa, usuario=request.user, acao="SENHA_ALTERADA", referencia=str(request.user.pk), detalhes={"origem": "api"})
        return Response({"ok": True})


class PixCobrancaView(TenantAPIView):
    def post(self, request):
        from . import pix_mp
        empresa, membro = self.get_empresa(request)
        webhook = request.build_absolute_uri(f"/api/v1/pix/webhook/{empresa.pk}/")
        cobranca = pix_mp.criar(
            empresa, request.user, request.data.get("valor"), request.data.get("chave"),
            email_pagador=str(request.data.get("email", ""))[:254], url_notificacao=webhook,
        )
        return Response(pix_mp.resumo(cobranca), status=status.HTTP_201_CREATED)

    def get(self, request):
        from . import pix_mp
        empresa, membro = self.get_empresa(request)
        return Response({"configurado": pix_mp.configurado(empresa)})


class PixCobrancaDetalheView(TenantAPIView):
    def get(self, request, cobranca_id):
        from . import pix_mp
        from .models import CobrancaPix
        empresa, membro = self.get_empresa(request)
        try:
            cobranca = CobrancaPix.objects.get(pk=cobranca_id, empresa=empresa, operador=request.user)
        except CobrancaPix.DoesNotExist:
            raise ValidationError({"pix": "Cobrança não encontrada."})
        return Response(pix_mp.resumo(pix_mp.atualizar(cobranca)))


class PointCobrancaView(TenantAPIView):
    """Cobrança na maquininha Mercado Pago Point (o PDV consulta até aprovar)."""
    def post(self, request):
        from . import point_mp
        empresa, membro = self.get_empresa(request)
        dados = point_mp.criar(empresa, request.data.get("valor"), str(request.data.get("tipo", "")).upper(), request.data.get("chave"))
        return Response(dados, status=status.HTTP_201_CREATED)

    def get(self, request):
        from . import point_mp
        empresa, membro = self.get_empresa(request)
        return Response({"configurado": point_mp.configurado(empresa)})


class PointCobrancaDetalheView(TenantAPIView):
    def get(self, request, intencao_id):
        from . import point_mp
        empresa, membro = self.get_empresa(request)
        return Response(point_mp.consultar(empresa, _intencao(intencao_id)))

    def delete(self, request, intencao_id):
        from . import point_mp
        empresa, membro = self.get_empresa(request)
        return Response(point_mp.cancelar(empresa, _intencao(intencao_id)))


def _intencao(valor):
    valor = str(valor)
    if not (0 < len(valor) <= 80) or not all(c.isalnum() or c in "-_" for c in valor):
        raise ValidationError({"point": "Cobrança inválida."})
    return valor


class PixWebhookView(APIView):
    """Aviso do Mercado Pago. O corpo não é confiável: só dispara uma consulta com o token da empresa."""
    permission_classes = []
    authentication_classes = []

    def post(self, request, empresa_id):
        from . import pix_mp
        from .models import CobrancaPix
        dados = request.data if isinstance(request.data, dict) else {}
        pagamento_id = str((dados.get("data") or {}).get("id") or request.query_params.get("data.id") or request.query_params.get("id") or "")[:40]
        if pagamento_id.isdigit():
            for cobranca in CobrancaPix.objects.filter(empresa_id=empresa_id, provedor_id=pagamento_id, estado=CobrancaPix.Estado.PENDENTE)[:1]:
                pix_mp.atualizar(cobranca)
        return Response({"ok": True})
