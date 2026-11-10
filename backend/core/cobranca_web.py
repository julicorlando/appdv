"""Telas da cobrança da plataforma.

Master: planos, configuração do recebimento (Mercado Pago), assinatura de cada empresa (plano, teste,
isenção, valor especial, desconto, vencimento, carência), faturas (gerar, baixa manual, cancelar,
conferir no Mercado Pago) e "Entrar na empresa" com acesso total (auditado).
Empresa: tela "Assinatura" com o plano, as faturas e o botão de pagar por PIX ou cartão.
"""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from . import cobranca
from .access import mensagem_erro
from .models import Assinatura, ConfiguracaoMaster, Empresa, EventoGestao, Fatura, MembroEmpresa, Plano


def _master(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        raise PermissionDenied("Acesso exclusivo ao painel Master.")


# ------------------------------------------------------------------ formulários

class PlanoForm(forms.ModelForm):
    class Meta:
        model = Plano
        fields = ("nome", "descricao", "valor_mensal", "caixas_incluidos", "valor_caixa_adicional", "limite_usuarios", "ordem", "ativo")


class ConfiguracaoForm(forms.ModelForm):
    class Meta:
        model = ConfiguracaoMaster
        fields = ("dias_teste", "carencia_dias", "dias_antecedencia", "plano_padrao", "mp_access_token")
        widgets = {"mp_access_token": forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"})}
        help_texts = {"mp_access_token": "Access token de produção da SUA conta Mercado Pago (onde as mensalidades caem). "
                                         "Em branco mantém o atual; digite REMOVER para apagar."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["plano_padrao"].queryset = Plano.objects.filter(ativo=True)
        self.fields["mp_access_token"].required = False

    def clean_mp_access_token(self):
        valor = (self.cleaned_data.get("mp_access_token") or "").strip()
        if valor.upper() == "REMOVER":
            return ""
        return valor or self.instance.mp_access_token


class AssinaturaForm(forms.ModelForm):
    class Meta:
        model = Assinatura
        fields = ("plano", "teste_ate", "isenta", "isencao_ate", "motivo_isencao", "dia_vencimento", "carencia_dias",
                  "valor_especial", "desconto_percentual", "observacao")
        widgets = {"teste_ate": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "isencao_ate": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["plano"].queryset = Plano.objects.all()
        self.fields["plano"].required = False

    def clean_dia_vencimento(self):
        dia = self.cleaned_data["dia_vencimento"]
        if not 1 <= dia <= 28:
            raise forms.ValidationError("Escolha um dia entre 1 e 28.")
        return dia

    def clean_desconto_percentual(self):
        valor = self.cleaned_data["desconto_percentual"]
        if valor > 100:
            raise forms.ValidationError("O desconto vai até 100%.")
        return valor


# ------------------------------------------------------------------ master

@login_required
def master_cobranca(request):
    _master(request)
    config = ConfiguracaoMaster.atual()
    cobranca.processar()
    plano_editado = None
    if request.GET.get("plano", "").isdigit():
        plano_editado = get_object_or_404(Plano, pk=request.GET["plano"])
    assinaturas = list(Assinatura.objects.select_related("empresa", "plano").order_by("empresa__nome"))
    abertas = {}
    for fatura in Fatura.objects.filter(estado__in=(Fatura.Estado.ABERTA, Fatura.Estado.VENCIDA)).order_by("vencimento"):
        abertas.setdefault(fatura.empresa_id, fatura)
    for assinatura in assinaturas:
        assinatura.proxima = abertas.get(assinatura.empresa_id)
        assinatura.valor_previsto = cobranca.calcular(assinatura, timezone.localdate())[1]
    filtro = request.GET.get("estado", "")
    if filtro:
        assinaturas = [a for a in assinaturas if a.estado == filtro]
    resumo = cobranca.resumo_master()
    return render(request, "master_cobranca.html", {
        "config": config, "config_form": ConfiguracaoForm(instance=config),
        "plano_form": PlanoForm(instance=plano_editado), "plano_editado": plano_editado,
        "planos": Plano.objects.annotate(empresas=Count("assinatura")),
        "assinaturas": assinaturas, "resumo": resumo, "filtro": filtro,
        "estados": [(v, r, resumo["por_estado"].get(v, 0)) for v, r in Assinatura.Estado.choices], "recebimento_ok": bool(config.mp_access_token),
        "webhook": request.build_absolute_uri(reverse("cobranca-webhook")),
        "ultimas_pagas": Fatura.objects.filter(estado=Fatura.Estado.PAGA).select_related("empresa").order_by("-paga_em")[:10],
    })


@login_required
def master_configuracao(request):
    _master(request)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    form = ConfiguracaoForm(request.POST, instance=ConfiguracaoMaster.atual())
    if form.is_valid():
        form.save()
        messages.success(request, "Configuração da cobrança salva.")
    else:
        messages.error(request, "Confira a configuração: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in form.errors.items()))
    return redirect("master-cobranca")


@login_required
def master_plano(request):
    _master(request)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    plano = get_object_or_404(Plano, pk=request.POST["plano_id"]) if request.POST.get("plano_id", "").isdigit() else None
    form = PlanoForm(request.POST, instance=plano)
    if form.is_valid():
        plano = form.save()
        messages.success(request, f"Plano {plano.nome} salvo. Vale para as próximas faturas.")
    else:
        messages.error(request, "Confira o plano: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in form.errors.items()))
    return redirect("master-cobranca")


@login_required
def master_assinatura(request, empresa_id):
    _master(request)
    empresa = get_object_or_404(Empresa, pk=empresa_id)
    assinatura = cobranca.garantir_assinatura(empresa)
    if request.method == "POST":
        acao = request.POST.get("acao", "salvar")
        try:
            if acao == "salvar":
                form = AssinaturaForm(request.POST, instance=assinatura)
                if not form.is_valid():
                    raise ValidationError({k: v for k, v in form.errors.items()})
                assinatura = form.save()
                if assinatura.estado == Assinatura.Estado.CANCELADA and request.POST.get("reativar"):
                    assinatura.estado = Assinatura.Estado.ATIVA
                    assinatura.save(update_fields=("estado", "atualizado_em"))
                cobranca.processar_assinatura(assinatura)
                cobranca._registrar(empresa, "ASSINATURA_EDITADA", assinatura.pk, usuario=request.user,
                                    plano=assinatura.plano.nome if assinatura.plano else "", isenta=assinatura.isenta)
                messages.success(request, f"Assinatura de {empresa.nome} atualizada: {assinatura.get_estado_display()}.")
            elif acao == "gerar":
                fatura = cobranca.gerar_fatura(assinatura, cobranca.competencia(timezone.localdate()), timezone.localdate())
                messages.success(request, f"Fatura de R$ {fatura.valor:.2f} gerada (vence {fatura.vencimento:%d/%m/%Y})."
                                 if fatura else "Nada a cobrar: sem plano ou valor zero.")
            elif acao == "cancelar_assinatura":
                assinatura.estado = Assinatura.Estado.CANCELADA
                assinatura.save(update_fields=("estado", "atualizado_em"))
                cobranca._registrar(empresa, "ASSINATURA_CANCELADA", assinatura.pk, usuario=request.user)
                messages.success(request, "Assinatura cancelada: não gera mais faturas (o acesso continua até você suspender a empresa).")
            else:
                fatura = get_object_or_404(Fatura, pk=request.POST.get("fatura"), empresa=empresa)
                if acao == "baixa":
                    cobranca.baixa_manual(fatura, request.user, request.POST.get("meio", "MANUAL"), request.POST.get("observacao", ""))
                    messages.success(request, "Pagamento registrado; a empresa foi liberada.")
                elif acao == "cancelar":
                    fatura.estado = Fatura.Estado.CANCELADA
                    fatura.observacao = (request.POST.get("observacao") or f"Cancelada por {request.user.get_username()}")[:250]
                    fatura.save(update_fields=("estado", "observacao"))
                    cobranca._registrar(empresa, "FATURA_CANCELADA", fatura.pk, usuario=request.user)
                    cobranca.processar_assinatura(assinatura)
                    messages.success(request, "Fatura cancelada.")
                elif acao == "verificar":
                    fatura = cobranca.verificar_fatura(fatura)
                    messages.info(request, f"Fatura: {fatura.get_estado_display()}.")
                elif acao == "link":
                    link = cobranca.link_pagamento(fatura, request.build_absolute_uri(reverse("empresa-assinatura", args=[empresa.pk])),
                                                   request.build_absolute_uri(reverse("cobranca-webhook")))
                    messages.success(request, f"Link de pagamento: {link}")
        except ValidationError as exc:
            messages.error(request, mensagem_erro(exc))
        return redirect("master-assinatura", empresa_id=empresa.pk)
    hoje = timezone.localdate()
    itens, valor = cobranca.calcular(assinatura, hoje)
    return render(request, "master_assinatura.html", {
        "empresa": empresa, "assinatura": assinatura, "form": AssinaturaForm(instance=assinatura),
        "faturas": Fatura.objects.filter(empresa=empresa), "itens": itens, "valor_previsto": valor,
        "caixas": cobranca.caixas_ativos(empresa, hoje),
        "usuarios": MembroEmpresa.objects.filter(empresa=empresa, ativo=True).count(),
        "eventos": EventoGestao.objects.filter(empresa=empresa).filter(
            Q(acao__startswith="ASSINATURA") | Q(acao__startswith="FATURA") | Q(acao__startswith="MASTER")).order_by("-pk")[:20],
        "master_view": True,
    })


@login_required
def master_entrar(request, empresa_id):
    """Master entra no painel da empresa como administrador (vale até sair; fica na auditoria)."""
    _master(request)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    empresa = get_object_or_404(Empresa, pk=empresa_id)
    request.session["master_empresa"] = empresa.pk
    EventoGestao.objects.create(empresa=empresa, usuario=request.user, acao="MASTER_ENTROU", referencia=str(request.user.pk),
                                detalhes={"usuario": request.user.get_username()})
    messages.info(request, f"Você está no painel de {empresa.nome} com acesso total de Master.")
    return redirect("empresa-dashboard", empresa_id=empresa.pk)


@login_required
def master_sair_empresa(request):
    _master(request)
    empresa_id = request.session.pop("master_empresa", None)
    if empresa_id:
        EventoGestao.objects.create(empresa_id=empresa_id, usuario=request.user, acao="MASTER_SAIU", referencia=str(request.user.pk))
    return redirect("master")


# ------------------------------------------------------------------ empresa

@login_required
def empresa_assinatura(request, empresa_id):
    from .web import require_membership
    membro = require_membership(request, empresa_id)
    empresa = membro.empresa
    assinatura = cobranca.garantir_assinatura(empresa)
    if request.GET.get("payment_id"):
        cobranca.consultar_pagamento(request.GET["payment_id"])
        assinatura.refresh_from_db()
    hoje = timezone.localdate()
    itens, valor = cobranca.calcular(assinatura, hoje)
    return render(request, "empresa_assinatura.html", {
        "empresa": empresa, "membro": membro, "assinatura": assinatura, "faturas": Fatura.objects.filter(empresa=empresa)[:24],
        "itens": itens, "valor_previsto": valor, "pode_pagar": membro.papel in (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE),
        "online": cobranca.configurado(), "aviso": cobranca.aviso(empresa),
    })


@login_required
def empresa_assinatura_pagar(request, empresa_id, fatura_id):
    from .web import require_membership
    membro = require_membership(request, empresa_id, (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE))
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    fatura = get_object_or_404(Fatura, pk=fatura_id, empresa=membro.empresa)
    try:
        link = cobranca.link_pagamento(fatura, request.build_absolute_uri(reverse("empresa-assinatura", args=[membro.empresa_id])),
                                       request.build_absolute_uri(reverse("cobranca-webhook")))
    except ValidationError as exc:
        messages.error(request, mensagem_erro(exc))
        return redirect("empresa-assinatura", empresa_id=membro.empresa_id)
    return redirect(link)


@login_required
def empresa_assinatura_verificar(request, empresa_id, fatura_id):
    from .web import require_membership
    membro = require_membership(request, empresa_id)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    fatura = get_object_or_404(Fatura, pk=fatura_id, empresa=membro.empresa)
    try:
        fatura = cobranca.verificar_fatura(fatura)
        if fatura.estado == Fatura.Estado.PAGA:
            messages.success(request, "Pagamento confirmado. Obrigado! O acesso está liberado.")
        else:
            messages.info(request, "Ainda não encontramos o pagamento. PIX costuma confirmar em segundos; cartão, em até alguns minutos.")
    except ValidationError as exc:
        messages.error(request, mensagem_erro(exc))
    return redirect("empresa-assinatura", empresa_id=membro.empresa_id)


@csrf_exempt
@api_view(["POST", "GET"])
@authentication_classes([])
@permission_classes([])
def webhook(request):
    """Aviso do Mercado Pago (conta do master). Só dispara a consulta do pagamento com o token do master."""
    dados = request.data if isinstance(request.data, dict) else {}
    pagamento = str((dados.get("data") or {}).get("id") or request.query_params.get("data.id") or request.query_params.get("id") or "")
    tipo = str(dados.get("type") or request.query_params.get("type") or request.query_params.get("topic") or "payment")
    if tipo == "payment" and pagamento.isdigit():
        cobranca.consultar_pagamento(pagamento)
    return Response({"ok": True})


# ------------------------------------------------------------------ bloqueio e avisos

class AssinaturaMiddleware:
    """Empresa suspensa → tela de Assinatura; nas demais telas da empresa mostra a faixa de aviso."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view, args, kwargs):
        request.aviso_cobranca = None
        empresa_id = kwargs.get("empresa_id")
        if empresa_id and request.user.is_authenticated:
            try:
                empresa = Empresa.objects.select_related("assinatura").get(pk=empresa_id)
                request.aviso_cobranca = cobranca.aviso(empresa)
            except (Empresa.DoesNotExist, ValueError):
                pass
        return None

    def process_exception(self, request, exception):
        from .web import AssinaturaSuspensa
        if isinstance(exception, AssinaturaSuspensa):
            messages.error(request, str(exception))
            return redirect("empresa-assinatura", empresa_id=exception.empresa_id)
        return None


def contexto(request):
    return {
        "aviso_cobranca": getattr(request, "aviso_cobranca", None),
        "master_na_empresa": request.session.get("master_empresa") if getattr(request, "user", None) and request.user.is_authenticated
        and request.user.is_superuser else None,
    }
