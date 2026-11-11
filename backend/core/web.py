from datetime import date
import base64
import mimetypes
from io import BytesIO
from decimal import Decimal, InvalidOperation
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Sum, F
from django.http import Http404, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from rest_framework.exceptions import ValidationError
from .access import mensagem_erro
from .forms import CategoriaSimpleForm, EmpresaForm, EmpresaLogoForm, MembroForm, ProdutoForm
from .inventory import concluir_contagem, gerar_contagens_aleatorias, importar_planilha, iniciar_contagem
from .purchases import confirmar_recebimento, criar_compra_manual, importar_nfe
from .models import Cliente, ContaPagar, Fornecedor, PagamentoContaPagar, ContaReceber, RecebimentoConta, Categoria, CompraEntrada, Empresa, InventarioContagem, ItemContagem, MembroEmpresa, MovimentacaoEstoque, PedidoWeb, Produto, TokenAutorizacao, Venda

User = get_user_model()


def home(request):
    if not request.user.is_authenticated:
        return redirect("login")
    if request.user.is_superuser:
        return redirect("master")
    memberships = MembroEmpresa.objects.filter(
        usuario=request.user, ativo=True, empresa__ativa=True
    ).select_related("empresa")
    if memberships.count() == 1:
        return redirect("empresa-dashboard", empresa_id=memberships.first().empresa_id)
    if memberships.count() > 1:
        return render(request, "empresa_select.html", {"membros": memberships})
    return render(request, "sem_empresa.html")


@login_required
def master_dashboard(request):
    if not request.user.is_superuser:
        raise PermissionDenied("Acesso exclusivo ao painel Master.")
    empresas = Empresa.objects.select_related("assinatura", "assinatura__plano").annotate(
        total_membros=Count("membros", distinct=True),
        total_produtos=Count("produtos", distinct=True),
    ).order_by("nome")
    return render(request, "master_dashboard.html", {
        "empresas": empresas,
        "total_empresas": Empresa.objects.count(),
        "empresas_ativas": Empresa.objects.filter(ativa=True).count(),
        "total_usuarios": User.objects.filter(is_active=True).count(),
        "form": EmpresaForm(),
        "pdv": downloads_pdv(),
    })


def downloads_pdv():
    """Links do PDV para Windows (Releases do GitHub) e situação dos terminais conectados."""
    import os
    from .api import _versoes_pdv
    from .models import TerminalPDV
    base = os.getenv("PDV_RELEASES_URL", "https://github.com/julicorlando/appdv/releases/latest").rstrip("/")
    versoes = _versoes_pdv()
    terminais = list(TerminalPDV.objects.select_related("empresa").order_by("empresa__nome", "identificador")[:200])
    minima = _tupla_versao(versoes["versao_minima"])
    from datetime import timedelta
    limite_contato = timezone.now() - timedelta(hours=2)
    for terminal in terminais:
        terminal.desatualizado = _tupla_versao(terminal.versao) < minima
        # Caixa que não fala com o servidor há mais de 2 h pode estar acumulando vendas offline.
        terminal.sem_contato = terminal.ultimo_contato < limite_contato
    return {
        **versoes,
        "instalador_url": os.getenv("PDV_INSTALADOR_URL", f"{base}/download/ApPdv-Setup.exe"),
        "portatil_url": os.getenv("PDV_PORTATIL_URL", f"{base}/download/ApPdv-Portatil.zip"),
        "releases_url": base,
        "terminais": terminais,
        "desatualizados": sum(1 for t in terminais if t.desatualizado),
        "sem_contato": sum(1 for t in terminais if t.sem_contato),
    }


def _tupla_versao(valor):
    partes = []
    for parte in str(valor or "0").split(".")[:3]:
        digitos = "".join(ch for ch in parte if ch.isdigit())
        partes.append(int(digitos) if digitos else 0)
    return tuple(partes + [0] * (3 - len(partes)))


@login_required
def master_criar_empresa(request):
    if not request.user.is_superuser:
        raise PermissionDenied("Acesso exclusivo ao painel Master.")
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    form = EmpresaForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Confira os campos. " + " ".join(
            f"{label}: {', '.join(errors)}" for label, errors in form.errors.items()
        ))
        return redirect("master")
    data = form.cleaned_data
    with transaction.atomic():
        user = User.objects.create_user(
            username=data["username_admin"], email=data["email_admin"],
            password=data["senha_admin"],
        )
        empresa = Empresa.objects.create(
            nome=data["nome"], cnpj=data["cnpj"], slug=data["slug"], ativa=True
        )
        MembroEmpresa.objects.create(
            empresa=empresa, usuario=user, papel=MembroEmpresa.Papel.ADMIN
        )
        from . import cobranca
        assinatura = cobranca.garantir_assinatura(empresa)
    messages.success(request, f"Empresa {empresa.nome} e usuário administrador criados"
                     + (f" · teste grátis até {assinatura.teste_ate:%d/%m/%Y}." if assinatura.teste_ate else "."))
    return redirect("master")


@login_required
def master_status_empresa(request, empresa_id):
    if not request.user.is_superuser:
        raise PermissionDenied("Acesso exclusivo ao painel Master.")
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    empresa = get_object_or_404(Empresa, pk=empresa_id)
    empresa.ativa = not empresa.ativa
    empresa.save(update_fields=("ativa",))
    messages.success(request, f"Acesso à empresa {empresa.nome}: {'ativo' if empresa.ativa else 'suspenso'}.")
    return redirect("master")


@login_required
def master_adicionar_membro(request, empresa_id):
    if not request.user.is_superuser:
        raise PermissionDenied("Acesso exclusivo ao painel Master.")
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    empresa = get_object_or_404(Empresa, pk=empresa_id)
    form = MembroForm(request.POST)
    if not form.is_valid():
        messages.error(request, "; ".join(
            f"{label}: {', '.join(errors)}" for label, errors in form.errors.items()
        ))
        return redirect("master")
    membro, created = MembroEmpresa.objects.update_or_create(
        empresa=empresa, usuario=form.cleaned_data["usuario"],
        defaults={"papel": form.cleaned_data["papel"], "ativo": True},
    )
    messages.success(request, f"Membro vinculado a {empresa.nome}.")
    return redirect("master")


class AssinaturaSuspensa(PermissionDenied):
    """Empresa bloqueada por falta de pagamento: o middleware leva para a tela de Assinatura."""

    def __init__(self, empresa_id, mensagem):
        super().__init__(mensagem)
        self.empresa_id = empresa_id


# Telas que continuam abertas com a empresa suspensa (para pagar e sair).
TELAS_LIBERADAS_SUSPENSAO = {"empresa-assinatura", "empresa-assinatura-pagar", "empresa-assinatura-verificar",
                             "empresa-assinatura-acao"}


def require_membership(request, empresa_id, papeis=None):
    if not request.user.is_authenticated:
        raise PermissionDenied("Faça login.")
    from .access import conta_ativa
    if not conta_ativa(request.user):
        raise PermissionDenied("Conta desativada.")
    from . import cobranca
    cobranca.processar_se_preciso()
    if request.user.is_superuser and str(request.session.get("master_empresa")) == str(empresa_id):
        # Master dentro da empresa: acesso de administrador, sem bloqueio, registrado na auditoria ao entrar.
        empresa = Empresa.objects.filter(pk=empresa_id).first()
        if empresa is None:
            raise PermissionDenied("Empresa não encontrada.")
        return MembroEmpresa(empresa=empresa, usuario=request.user, papel=MembroEmpresa.Papel.ADMIN, ativo=True)
    try:
        membro = MembroEmpresa.objects.select_related("empresa").get(
            empresa_id=empresa_id, usuario=request.user, ativo=True,
            empresa__ativa=True,
        )
    except MembroEmpresa.DoesNotExist:
        raise PermissionDenied("Você não possui acesso a esta empresa.")
    tela = getattr(getattr(request, "resolver_match", None), "url_name", "")
    mensagem = cobranca.bloqueio(membro.empresa)
    if mensagem and tela not in TELAS_LIBERADAS_SUSPENSAO:
        raise AssinaturaSuspensa(membro.empresa_id, mensagem)
    if papeis and membro.papel not in papeis:
        raise PermissionDenied("Seu perfil não permite esta ação.")
    return membro


@login_required
def empresa_dashboard(request, empresa_id):
    membro = require_membership(request, empresa_id)
    if membro.papel == MembroEmpresa.Papel.CAIXA:
        return redirect("caixa-web", empresa_id=empresa_id)
    today = timezone.localdate()
    vendas = Venda.objects.filter(
        empresa=membro.empresa, estado=Venda.Estado.CONCLUIDA, realizada_em__date=today
    )
    vendas_mes = Venda.objects.filter(
        empresa=membro.empresa, estado=Venda.Estado.CONCLUIDA,
        realizada_em__year=today.year, realizada_em__month=today.month,
    )
    return render(request, "empresa_dashboard.html", {
        "membro": membro, "empresa": membro.empresa,
        "pedidos_confirmados": PedidoWeb.objects.filter(empresa=membro.empresa, estado=PedidoWeb.Estado.CONFIRMADO).count(),
        "produtos_count": Produto.objects.filter(empresa=membro.empresa, ativo=True).count(),
        "estoque_baixo": Produto.objects.filter(
            empresa=membro.empresa, ativo=True, estoque_atual__lte=F("estoque_minimo")
        ).count(),
        "vendas_count": vendas.count(),
        "faturamento_hoje": vendas.aggregate(total=Sum("total"))["total"] or Decimal("0.00"),
        "vendas_mes_count": vendas_mes.count(),
        "faturamento_mes": vendas_mes.aggregate(total=Sum("total"))["total"] or Decimal("0.00"),
        "membros": MembroEmpresa.objects.filter(empresa=membro.empresa, ativo=True).select_related("usuario"),
        "now": timezone.now(),
    })


@login_required
def caixa_web(request, empresa_id):
    from .models import Caixa, Transacao
    membro = require_membership(request, empresa_id)
    caixa = Caixa.objects.filter(
        empresa=membro.empresa, operador=request.user,
        dispositivo_id="web", estado=Caixa.Estado.ABERTO,
    ).first()
    return render(request, "caixa_web.html", {
        "loja": membro.empresa, "caixa": caixa,
        "meios": [(v,l) for v,l in Transacao.Meio.choices if v != "LOJA"],
        "pix_configurado": bool(membro.empresa.mp_access_token),
        "nfce_configurada": bool(membro.empresa.focus_token),
    })


@login_required
def empresa_configuracao(request, empresa_id):
    membro = require_membership(
        request, empresa_id, (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE)
    )
    form = EmpresaLogoForm(
        request.POST or None, request.FILES or None, instance=membro.empresa
    )
    if membro.papel != MembroEmpresa.Papel.ADMIN:
        for campo in list(form.fields):
            if campo != "logo":
                form.fields.pop(campo)
    if request.method == "POST":
        if form.is_valid():
            anterior = Empresa.objects.filter(pk=membro.empresa.pk).values_list("mp_point_dispositivo", flat=True).first()
            empresa = form.save()
            messages.success(request, "Configurações da empresa atualizadas.")
            if empresa.mp_point_dispositivo and empresa.mp_point_dispositivo != anterior:
                from . import point_mp
                try:
                    point_mp.ativar_modo_pdv(empresa)
                    messages.info(request, "Maquininha vinculada. Se ela estava em outro modo, reinicie-a para entrar no modo PDV.")
                except ValidationError as exc:
                    messages.warning(request, mensagem_erro(exc))
            return redirect("empresa-configuracao", empresa_id=empresa_id)
        messages.error(request, "Confira os campos da configuração.")
    logo_preview = ""
    if membro.empresa.logo:
        with membro.empresa.logo.open("rb") as arquivo:
            encoded = base64.b64encode(arquivo.read()).decode("ascii")
        media_type = mimetypes.guess_type(membro.empresa.logo.name)[0] or "image/png"
        logo_preview = f"data:{media_type};base64,{encoded}"
    maquininhas = None
    if request.GET.get("maquininhas") and membro.papel == MembroEmpresa.Papel.ADMIN:
        from . import point_mp
        try:
            maquininhas = point_mp.dispositivos(membro.empresa)
        except ValidationError as exc:
            messages.error(request, mensagem_erro(exc))
    return render(request, "empresa_configuracao.html", {
        "membro": membro, "empresa": membro.empresa, "form": form,
        "logo_preview": logo_preview, "maquininhas": maquininhas,
    })


@login_required
def empresa_baixa_perda(request, empresa_id, produto_id):
    membro = require_membership(
        request, empresa_id, (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE)
    )
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        quantidade = Decimal(str(request.POST.get("quantidade", "")).replace(",", "."))
        motivo = request.POST.get("motivo", "").strip()
        if not quantidade.is_finite() or quantidade <= 0 or not motivo:
            raise ValueError("Informe quantidade e motivo.")
        with transaction.atomic():
            produto = get_object_or_404(
                Produto.objects.select_for_update(), pk=produto_id, empresa=membro.empresa
            )
            if ItemContagem.objects.filter(
                contagem__empresa=membro.empresa,
                contagem__estado=InventarioContagem.Estado.ABERTO,
                produto=produto,
            ).exists():
                raise ValueError("Produto em inventário aberto; aguarde a conclusão.")
            if quantidade > produto.estoque_atual:
                raise ValueError("A perda informada excede o saldo disponível.")
            from .management_services import consumir_locais
            consumir_locais(produto, quantidade)
            produto.estoque_atual -= quantidade
            produto.save(update_fields=("estoque_atual", "atualizado_em"))
            MovimentacaoEstoque.objects.create(
                empresa=membro.empresa, produto=produto,
                tipo=MovimentacaoEstoque.Tipo.PERDA, quantidade=-quantidade,
                referencia=("Perda: " + motivo)[:120], operador=request.user,
            )
        messages.success(request, f"Baixa de perda registrada para {produto.nome}.")
    except Exception as exc:
        messages.error(request, f"Não foi possível registrar a perda: {exc}")
    return redirect("empresa-produtos", empresa_id=empresa_id)


@login_required
def empresa_produtos(request, empresa_id):
    membro = require_membership(
        request, empresa_id, (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE)
    )
    form = ProdutoForm(request.POST or None, empresa=membro.empresa)
    if request.method == "POST" and form.is_valid():
        produto = form.save(commit=False)
        produto.empresa = membro.empresa
        produto.save()
        messages.success(request, "Produto cadastrado.")
        return redirect("empresa-produtos", empresa_id=empresa_id)
    return render(request, "empresa_produtos.html", {
        "membro": membro, "empresa": membro.empresa, "form": form,
        "produtos": Produto.objects.filter(empresa=membro.empresa).select_related("categoria").order_by("-ativo", "nome"),
        "demo_ativos": Produto.objects.filter(empresa=membro.empresa, codigo__startswith="DEMO-", ativo=True).count(),
    })


@login_required
def empresa_autorizacao(request, empresa_id):
    membro = require_membership(
        request, empresa_id, (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE)
    )
    token_texto = None
    if request.method == "POST":
        acao = request.POST.get("acao")
        if acao not in TokenAutorizacao.Acao.values:
            messages.error(request, "Selecione uma operação válida.")
        else:
            raw_venda = request.POST.get("venda", "").strip()
            if not raw_venda:
                messages.error(request, "Informe a venda à qual o token ficará vinculado.")
                return redirect("empresa-autorizacao", empresa_id=empresa_id)
            try:
                venda = Venda.objects.get(pk=int(raw_venda), empresa=membro.empresa)
            except (ValueError, Venda.DoesNotExist):
                messages.error(request, "Venda não encontrada nesta empresa.")
                return redirect("empresa-autorizacao", empresa_id=empresa_id)
            token_texto = TokenAutorizacao.gerar_codigo()
            TokenAutorizacao.objects.create(
                empresa=membro.empresa,
                token_hash=TokenAutorizacao.hash_token(token_texto),
                acao=acao, venda=venda, criado_por=request.user,
                expira_em=timezone.now() + timezone.timedelta(minutes=10),
            )
            messages.success(request, "Token criado. Ele é exibido uma única vez e expira em 10 minutos.")
    tokens = TokenAutorizacao.objects.filter(
        empresa=membro.empresa
    ).select_related("criado_por", "venda").order_by("-criado_em")[:20]
    return render(request, "empresa_autorizacao.html", {
        "membro": membro, "empresa": membro.empresa, "tokens": tokens,
        "token_texto": token_texto, "acoes": TokenAutorizacao.Acao.choices,
    })

@login_required
def empresa_categoria(request, empresa_id):
    membro = require_membership(
        request, empresa_id, (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE)
    )
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    form = CategoriaSimpleForm(request.POST)
    if form.is_valid():
        categoria = form.save(commit=False)
        categoria.empresa = membro.empresa
        try:
            categoria.save()
            messages.success(request, "Categoria cadastrada.")
        except Exception:
            messages.error(request, "Já existe uma categoria com esse nome nesta empresa.")
    else:
        messages.error(request, "Informe um nome de categoria válido.")
    return redirect("empresa-produtos", empresa_id=empresa_id)


@login_required
def empresa_entrada_estoque(request, empresa_id, produto_id):
    from django.db import transaction
    from decimal import Decimal, InvalidOperation
    membro = require_membership(
        request, empresa_id, (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE)
    )
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        quantidade = Decimal(str(request.POST.get("quantidade", "")))
        if not quantidade.is_finite() or quantidade <= 0:
            raise ValueError
    except (ValueError, TypeError, InvalidOperation):
        messages.error(request, "Informe uma quantidade positiva válida.")
        return redirect("empresa-produtos", empresa_id=empresa_id)
    with transaction.atomic():
        produto = get_object_or_404(
            Produto.objects.select_for_update(), pk=produto_id, empresa=membro.empresa
        )
        if ItemContagem.objects.filter(
            contagem__empresa=membro.empresa,
            contagem__estado=InventarioContagem.Estado.ABERTO,
            produto=produto,
        ).exists():
            messages.error(request, "Este produto está em contagem física. Aguarde a conclusão.")
            return redirect("empresa-produtos", empresa_id=empresa_id)
        produto.estoque_atual += quantidade
        produto.save(update_fields=("estoque_atual", "atualizado_em"))
        MovimentacaoEstoque.objects.create(
            empresa=membro.empresa, produto=produto,
            tipo=MovimentacaoEstoque.Tipo.ENTRADA, quantidade=quantidade,
            referencia="Entrada pelo painel web", operador=request.user,
        )
    messages.success(request, f"Entrada de {quantidade} {produto.unidade} registrada para {produto.nome}.")
    return redirect("empresa-produtos", empresa_id=empresa_id)


def pedido_publico(request, codigo):
    pedido = get_object_or_404(
        PedidoWeb.objects.select_related("empresa").prefetch_related("itens"),
        codigo_publico=codigo,
    )
    if request.method == "POST":
        if pedido.estado != PedidoWeb.Estado.AGUARDANDO_CLIENTE:
            messages.info(request, "Este pedido não está mais aguardando confirmação.")
        elif pedido.expira_em <= timezone.now():
            messages.error(request, "O prazo deste pedido expirou. Fale com a loja para atualizar os valores.")
        elif request.POST.get("acao") == "confirmar":
            agora = timezone.now()
            aprovado = PedidoWeb.objects.filter(
                pk=pedido.pk, estado=PedidoWeb.Estado.AGUARDANDO_CLIENTE,
                expira_em__gt=agora,
            ).update(estado=PedidoWeb.Estado.CONFIRMADO, confirmado_em=agora)
            if aprovado:
                messages.success(request, "Orçamento aprovado. A loja pode preparar o pedido com os mesmos itens.")
            else:
                messages.info(request, "O orçamento expirou ou já foi atualizado.")
        return redirect("pedido-publico", codigo=pedido.codigo_publico)
    return render(request, "pedido_publico.html", {
        "pedido": pedido, "itens": pedido.itens.all(),
        "expirado": pedido.expira_em <= timezone.now(),
    })


@login_required
def empresa_pedidos_web(request, empresa_id):
    membro = require_membership(request, empresa_id)
    if request.method == "POST":
        pedido = get_object_or_404(PedidoWeb, pk=request.POST.get("pedido"), empresa=membro.empresa)
        convertido = request.POST.get("acao") == "separando" and PedidoWeb.objects.filter(
            pk=pedido.pk, empresa=membro.empresa,
            estado=PedidoWeb.Estado.CONFIRMADO, expira_em__gt=timezone.now(),
        ).update(estado=PedidoWeb.Estado.SEPARANDO)
        if convertido:
            messages.success(request, f"Orçamento #{pedido.pk} convertido em pedido para separação.")
        else:
            messages.error(request, "O orçamento precisa estar aprovado, dentro da validade e aguardando separação.")
        return redirect("empresa-pedidos-web", empresa_id=empresa_id)
    pedidos = PedidoWeb.objects.filter(empresa=membro.empresa).prefetch_related(
        "itens"
    ).order_by("-criado_em")[:100]
    return render(request, "empresa_pedidos_web.html", {
        "empresa": membro.empresa, "membro": membro, "pedidos": pedidos,
    })


@login_required
def empresa_vendas(request, empresa_id):
    membro = require_membership(request, empresa_id)
    vendas = Venda.objects.filter(empresa=membro.empresa).select_related(
        "operador", "caixa"
    ).prefetch_related("itens__produto", "transacoes").order_by("-realizada_em")
    if membro.papel == MembroEmpresa.Papel.CAIXA:
        vendas = vendas.filter(operador=request.user)
    vendas = vendas[:100]
    return render(request, "empresa_vendas.html", {
        "empresa": membro.empresa, "membro": membro, "vendas": vendas,
    })


@login_required
def baixar_modelo_planilha(request, empresa_id):
    require_membership(
        request, empresa_id, (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE)
    )
    from openpyxl import Workbook
    from openpyxl.styles import Font
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Produtos"
    sheet.append([
        "codigo", "nome", "categoria", "unidade", "unidade_estoque",
        "fator_conversao", "preco_venda", "estoque_atual", "estoque_minimo", "setor", "endereco", "codigo_barras",
    ])
    instructions = workbook.create_sheet("Instruções")
    instructions.append(["Campo", "Obrigatório", "Descrição", "Exemplo"])
    instructions.append(["codigo", "Sim", "Código único do produto nesta empresa; texto para manter zeros à esquerda.", "MAT-001"])
    instructions.append(["nome", "Sim", "Nome que aparecerá no catálogo e no PDV.", "Cimento CP-II 50kg"])
    instructions.append(["categoria", "Não", "Se vazio, será usado/criado Geral.", "Cimento"])
    instructions.append(["unidade", "Não", "Unidade de venda; padrão UN.", "SC, KG, M, M2"])
    instructions.append(["unidade_estoque", "Não", "Unidade em que o saldo físico é contado; vazio = unidade de venda.", "UN"])
    instructions.append(["fator_conversao", "Não", "Unidades de estoque consumidas por unidade vendida; padrão 1.", "12"])
    instructions.append(["preco_venda", "Sim", "Preço unitário em reais, sem símbolo.", "34,90"])
    instructions.append(["estoque_atual", "Sim", "Saldo absoluto na unidade de estoque; substitui o saldo atual.", "120"])
    instructions.append(["estoque_minimo", "Não", "Gatilho de alerta de reposição.", "20"])
    instructions.append(["setor", "Sim", "Área do armazém. Usada no sorteio preventivo.", "Depósito A"])
    instructions.append(["endereco", "Sim", "Posição física dentro do setor.", "A-03-02"])
    instructions.append(["codigo_barras", "Não", "Código de barras como texto.", "7890000000000"])
    instructions.column_dimensions["A"].width = 22
    instructions.column_dimensions["B"].width = 16
    instructions.column_dimensions["C"].width = 78
    instructions.column_dimensions["D"].width = 30
    for cell in instructions[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet["A2"].number_format = "@"
    sheet["J2"].number_format = "@"
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for column, width in {
        "A": 18, "B": 30, "C": 22, "D": 12, "E": 16,
        "F": 16, "G": 16, "H": 20, "I": 18, "J": 20,
    }.items():
        sheet.column_dimensions[column].width = width
    output = BytesIO()
    workbook.save(output)
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="modelo_importacao_armazem.xlsx"'
    return response


@login_required
def importar_produtos_excel(request, empresa_id):
    membro = require_membership(
        request, empresa_id, (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE)
    )
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    arquivo = request.FILES.get("arquivo")
    if not arquivo:
        messages.error(request, "Selecione um arquivo .xlsx.")
        return redirect("empresa-produtos", empresa_id=empresa_id)
    try:
        resultado = importar_planilha(empresa=membro.empresa, usuario=request.user, arquivo=arquivo)
    except Exception as exc:
        messages.error(request, str(exc))
        return redirect("empresa-produtos", empresa_id=empresa_id)
    messages.success(
        request,
        f"Importação {resultado['lote']} concluída: {resultado['criados']} novos e "
        f"{resultado['atualizados']} atualizados. Estoques e endereços foram aplicados.",
    )
    return redirect("empresa-produtos", empresa_id=empresa_id)


@login_required
def empresa_inventario(request, empresa_id):
    membro = require_membership(
        request, empresa_id, (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE)
    )
    if request.method == "POST":
        acao = request.POST.get("acao")
        try:
            if acao == "catalogo_demo":
                if membro.papel != MembroEmpresa.Papel.ADMIN:
                    raise PermissionDenied("Somente o administrador pode carregar produtos de teste.")
                from .catalogo_demo import carregar_catalogo_demo
                criados = carregar_catalogo_demo(membro.empresa)
                messages.success(request, f"Catálogo de teste: {criados} novos produtos cadastrados.")
            elif acao == "gerar":
                quantidade = int(request.POST.get("quantidade", "5"))
                por_contagem = int(request.POST.get("itens_por_contagem", "20"))
                if quantidade < 1 or por_contagem < 1 or quantidade > 100 or por_contagem > 200:
                    raise ValueError("Limite: até 100 sessões e 200 produtos por sessão.")
                geradas = gerar_contagens_aleatorias(
                    empresa=membro.empresa, usuario=request.user,
                    quantidade=quantidade, itens_por_contagem=por_contagem,
                )
                messages.success(
                    request, f"Foram sorteadas {len(geradas)} contagens entre setores e dias dos próximos 30 dias."
                )
            elif acao == "iniciar":
                contagem = get_object_or_404(
                    InventarioContagem, pk=request.POST.get("contagem"),
                    empresa=membro.empresa,
                )
                iniciar_contagem(contagem=contagem, usuario=request.user)
                messages.success(
                    request, f"Contagem do setor {contagem.setor} iniciada. As vendas desses itens ficam pausadas até concluir."
                )
            elif acao == "revisar":
                if membro.papel != MembroEmpresa.Papel.ADMIN:
                    raise PermissionDenied("A revisão é exclusiva do administrador.")
                InventarioContagem.objects.filter(
                    pk=request.POST.get("contagem"), empresa=membro.empresa,
                    estado=InventarioContagem.Estado.CONCLUIDO,
                    em_recontagem=True, revisada_em__isnull=True,
                ).update(revisada_em=timezone.now())
                messages.success(request, "Divergência marcada como revisada.")
            elif acao in ("concluir", "recontar"):
                contagem = get_object_or_404(
                    InventarioContagem, pk=request.POST.get("contagem"),
                    empresa=membro.empresa,
                )
                valores = {
                    key.removeprefix("contado_"): value
                    for key, value in request.POST.items()
                    if key.startswith("contado_")
                }
                resultado = concluir_contagem(
                    contagem=contagem, usuario=request.user, contagens=valores,
                    recontagem=acao == "recontar",
                )
                if resultado.estado == InventarioContagem.Estado.ABERTO:
                    messages.warning(request, "Reconte apenas os itens divergentes na próxima etapa.")
                else:
                    messages.success(request, "Contagem concluída e salva no histórico.")
            else:
                messages.error(request, "Ação de inventário inválida.")
        except Exception as exc:
            messages.error(request, str(exc))
        return redirect("empresa-inventario", empresa_id=empresa_id)

    contagens = InventarioContagem.objects.filter(empresa=membro.empresa).prefetch_related(
        "itens__produto"
    ).order_by("-data_programada", "-criado_em")
    alertas = contagens.none()
    if membro.papel == MembroEmpresa.Papel.ADMIN:
        alertas = contagens.filter(
            estado=InventarioContagem.Estado.CONCLUIDO, em_recontagem=True,
            revisada_em__isnull=True,
        ).filter(pk__in=ItemContagem.objects.filter(contagem__empresa=membro.empresa).exclude(diferenca=0).values("contagem_id"))
    return render(request, "empresa_inventario.html", {
        "alertas": alertas,
        "empresa": membro.empresa, "membro": membro,
        "programadas": contagens.filter(estado=InventarioContagem.Estado.PROGRAMADO),
        "abertas": contagens.filter(estado=InventarioContagem.Estado.ABERTO),
        "concluidas": contagens.filter(estado=InventarioContagem.Estado.CONCLUIDO),
        "today": timezone.localdate(),
        "setores": list(Produto.objects.filter(empresa=membro.empresa, ativo=True)
                        .exclude(setor="").values_list("setor", flat=True).distinct()),
    })


@login_required
def master_criar_usuario(request):
    if not request.user.is_superuser:
        raise PermissionDenied("Acesso exclusivo ao painel Master.")
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    username = request.POST.get("username", "").strip()
    email = request.POST.get("email", "").strip()
    password = request.POST.get("password", "")
    empresa_id = request.POST.get("empresa")
    papel = request.POST.get("papel")
    if not username or not email or len(password) < 10:
        messages.error(request, "Informe usuário, e-mail e senha com pelo menos 10 caracteres.")
        return redirect("master")
    if papel not in MembroEmpresa.Papel.values:
        messages.error(request, "Selecione um papel válido.")
        return redirect("master")
    empresa = get_object_or_404(Empresa, pk=empresa_id)
    if User.objects.filter(username=username).exists():
        messages.error(request, "Esse nome de usuário já está cadastrado. Use a opção de vincular usuário existente.")
        return redirect("master")
    with transaction.atomic():
        user = User.objects.create_user(username=username, email=email, password=password)
        MembroEmpresa.objects.create(empresa=empresa, usuario=user, papel=papel)
    messages.success(request, f"Usuário {username} criado e vinculado a {empresa.nome}.")
    return redirect("master")


@login_required
def empresa_movimentacoes(request, empresa_id):
    membro = require_membership(
        request, empresa_id, (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE)
    )
    movimentos = MovimentacaoEstoque.objects.filter(
        empresa=membro.empresa
    ).select_related("produto", "operador").order_by("-criado_em")[:200]
    return render(request, "empresa_movimentacoes.html", {
        "empresa": membro.empresa, "membro": membro, "movimentos": movimentos,
    })



@login_required
def empresa_compras(request, empresa_id):
    membro = require_membership(
        request, empresa_id,
        (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE),
    )
    if request.method == "POST":
        acao = request.POST.get("acao")
        try:
            if acao == "manual":
                compra = criar_compra_manual(
                    empresa=membro.empresa, usuario=request.user, dados=request.POST
                )
                messages.success(
                    request,
                    f"NF {compra.numero_nf}/{compra.serie} salva como rascunho manual. "
                    "Confira os itens antes de receber.",
                )
            elif acao == "importar_xml":
                arquivo = request.FILES.get("arquivo_xml")
                if not arquivo:
                    raise ValueError("Selecione o arquivo XML da NF-e.")
                compra = importar_nfe(
                    empresa=membro.empresa, usuario=request.user, documento=arquivo
                )
                correspondidos = compra.itens.filter(produto__isnull=False).count()
                messages.success(
                    request,
                    f"NF {compra.numero_nf}/{compra.serie} importada como rascunho. "
                    f"{correspondidos} de {compra.itens.count()} itens foram associados automaticamente; "
                    "confira os produtos antes de receber.",
                )
            elif acao == "receber":
                compra = confirmar_recebimento(
                    compra_id=request.POST.get("compra"),
                    empresa=membro.empresa,
                    usuario=request.user,
                    mapeamentos={
                        key.removeprefix("produto_"): value
                        for key, value in request.POST.items()
                        if key.startswith("produto_") and value
                    },
                )
                messages.success(
                    request,
                    f"NF {compra.numero_nf}/{compra.serie} recebida. "
                    "Estoque e custo médio atualizados.",
                )
            else:
                raise ValueError("Ação de compra inválida.")
        except Exception as exc:
            messages.error(request, str(exc))
        return redirect("empresa-compras", empresa_id=empresa_id)

    compras = CompraEntrada.objects.filter(
        empresa=membro.empresa
    ).select_related("fornecedor", "criado_por", "recebido_por").prefetch_related(
        "itens__produto"
    )[:100]
    produtos = Produto.objects.filter(
        empresa=membro.empresa, ativo=True
    ).order_by("nome")
    return render(request, "empresa_compras.html", {
        "empresa": membro.empresa, "membro": membro,
        "compras": compras, "produtos": produtos,
    })

@login_required
def empresa_clientes(request, empresa_id):
    membro = require_membership(
        request, empresa_id, (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE)
    )
    if request.method == "POST":
        nome = request.POST.get("nome", "").strip()
        try:
            limite = Decimal(request.POST.get("limite_credito", "0"))
            if not nome or not limite.is_finite() or limite < 0:
                raise ValueError
            documento = "".join(ch for ch in request.POST.get("documento", "") if ch.isdigit())[:14]
            cliente = Cliente.objects.create(
                empresa=membro.empresa, nome=nome[:180], documento=documento,
                telefone=request.POST.get("telefone", "")[:30],
                email=request.POST.get("email", "")[:254],
                limite_credito=limite,
            )
            messages.success(request, f"Cliente {cliente.nome} cadastrado.")
        except Exception as exc:
            messages.error(request, f"Não foi possível cadastrar o cliente: {exc}")
        return redirect("empresa-clientes", empresa_id=empresa_id)
    clientes = Cliente.objects.filter(empresa=membro.empresa).order_by("nome")
    return render(request, "empresa_clientes.html", {
        "empresa": membro.empresa, "membro": membro, "clientes": clientes,
    })


@login_required
def empresa_contas_receber(request, empresa_id):
    membro = require_membership(
        request, empresa_id, (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE)
    )
    if request.method == "POST":
        try:
            with transaction.atomic():
                conta = ContaReceber.objects.select_for_update().get(
                    pk=request.POST.get("conta"), empresa=membro.empresa,
                    estado__in=(ContaReceber.Estado.ABERTA, ContaReceber.Estado.PARCIAL),
                )
                valor = Decimal(request.POST.get("valor", "0"))
                meio = request.POST.get("meio")
                if not valor.is_finite() or valor <= 0 or valor > conta.saldo or meio not in ("DINHEIRO", "PIX", "CARTAO", "OUTRO"):
                    raise ValueError("Valor ou forma de pagamento inválidos.")
                from .access import caixa_para_movimento
                caixa = caixa_para_movimento(
                    membro.empresa, request.user, [meio], operacao="receber em dinheiro",
                )
                RecebimentoConta.objects.create(
                    conta=conta, empresa=membro.empresa, operador=request.user,
                    meio=meio, valor=valor, caixa=caixa,
                )
                conta.saldo -= valor
                conta.estado = ContaReceber.Estado.QUITADA if conta.saldo == 0 else ContaReceber.Estado.PARCIAL
                conta.save(update_fields=("saldo", "estado"))
            messages.success(request, "Recebimento registrado.")
        except Exception as exc:
            from .access import mensagem_erro
            messages.error(request, f"Não foi possível registrar: {mensagem_erro(exc)}")
        return redirect("empresa-contas-receber", empresa_id=empresa_id)
    contas = ContaReceber.objects.filter(
        empresa=membro.empresa, estado__in=(ContaReceber.Estado.ABERTA, ContaReceber.Estado.PARCIAL)
    ).select_related("cliente", "venda").order_by("vencimento")
    return render(request, "empresa_contas_receber.html", {
        "empresa": membro.empresa, "membro": membro, "contas": contas,
    })

@login_required
def empresa_contas_pagar(request, empresa_id):
    membro = require_membership(
        request, empresa_id, (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE)
    )
    if request.method == "POST":
        try:
            with transaction.atomic():
                conta = ContaPagar.objects.select_for_update().get(
                    pk=request.POST.get("conta"), empresa=membro.empresa,
                    estado__in=(ContaPagar.Estado.ABERTA, ContaPagar.Estado.PARCIAL),
                )
                valor = Decimal(request.POST.get("valor", "0"))
                meio = request.POST.get("meio")
                if not valor.is_finite() or valor <= 0 or valor > conta.saldo or meio not in ("DINHEIRO", "PIX", "CARTAO", "OUTRO"):
                    raise ValueError("Valor ou forma de pagamento inválidos.")
                PagamentoContaPagar.objects.create(
                    conta=conta, empresa=membro.empresa, operador=request.user,
                    meio=meio, valor=valor,
                )
                conta.saldo -= valor
                conta.estado = ContaPagar.Estado.QUITADA if conta.saldo == 0 else ContaPagar.Estado.PARCIAL
                conta.save(update_fields=("saldo", "estado"))
            messages.success(request, "Pagamento ao fornecedor registrado.")
        except Exception as exc:
            messages.error(request, f"Não foi possível registrar o pagamento: {exc}")
        return redirect("empresa-contas-pagar", empresa_id=empresa_id)
    contas = ContaPagar.objects.filter(
        empresa=membro.empresa, estado__in=(ContaPagar.Estado.ABERTA, ContaPagar.Estado.PARCIAL)
    ).select_related("fornecedor", "compra").order_by("vencimento")
    return render(request, "empresa_contas_pagar.html", {
        "empresa": membro.empresa, "membro": membro, "contas": contas,
        "fornecedores": Fornecedor.objects.filter(empresa=membro.empresa, ativo=True).order_by("nome"),
        "categorias": ContaPagar._meta.get_field("categoria").choices,
    })


def logo_empresa(request, path, pasta="empresas/logos"):
    """Serve somente imagens públicas: logos das empresas e fotos de produtos.

    O restante de MEDIA_ROOT (XML e DANFE fiscais) continua sem acesso público.
    """
    from django.conf import settings
    from django.views.static import serve
    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])
    if pasta not in ("empresas/logos", "produtos/fotos"):
        raise Http404()
    response = serve(request, path, document_root=settings.MEDIA_ROOT / pasta)
    response["Cache-Control"] = "public, max-age=3600"
    response["X-Content-Type-Options"] = "nosniff"
    return response


class LoginWebView(auth_views.LoginView):
    """Login do painel com o mesmo limite de tentativas do login da API."""
    template_name = "login.html"
    limite = 10
    janela = 60

    def _chaves(self):
        from rest_framework.settings import api_settings
        usuario = str(self.request.POST.get("username", "")).strip().lower()[:150]
        ip = self.request.META.get("REMOTE_ADDR", "")
        encaminhado = self.request.META.get("HTTP_X_FORWARDED_FOR", "")
        proxies = api_settings.NUM_PROXIES
        if encaminhado and proxies:
            enderecos = [a.strip() for a in encaminhado.split(",") if a.strip()]
            if enderecos:
                ip = enderecos[-min(proxies, len(enderecos))]
        return [f"login-web:u:{usuario}", f"login-web:ip:{ip}"]

    def post(self, request, *args, **kwargs):
        from django.core.cache import cache
        chaves = self._chaves()
        if any((cache.get(chave) or 0) >= self.limite * (3 if ":ip:" in chave else 1) for chave in chaves):
            from django.forms.utils import ErrorDict
            form = self.get_form()
            # Não valida o formulário: com o bloqueio ativo a senha nem é conferida.
            form._errors, form.cleaned_data = ErrorDict(), {}
            form.add_error(None, "Muitas tentativas de login. Aguarde um minuto e tente novamente.")
            return self.render_to_response(self.get_context_data(form=form))
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        from django.core.cache import cache
        if self.request.method == "POST":
            for chave in self._chaves():
                # Janela deslizante: cada falha renova o prazo de um minuto.
                cache.set(chave, (cache.get(chave) or 0) + 1, self.janela)
        return super().form_invalid(form)
