from datetime import date
from io import BytesIO
from decimal import Decimal, InvalidOperation
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Sum, F
from django.http import HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from .forms import CategoriaSimpleForm, EmpresaForm, MembroForm, ProdutoForm
from .inventory import concluir_contagem, gerar_contagens_aleatorias, importar_planilha, iniciar_contagem
from .models import Categoria, Empresa, InventarioContagem, ItemContagem, MembroEmpresa, MovimentacaoEstoque, Produto, TokenAutorizacao, Venda

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
    empresas = Empresa.objects.annotate(
        total_membros=Count("membros", distinct=True),
        total_produtos=Count("produtos", distinct=True),
    ).order_by("nome")
    return render(request, "master_dashboard.html", {
        "empresas": empresas,
        "total_empresas": Empresa.objects.count(),
        "empresas_ativas": Empresa.objects.filter(ativa=True).count(),
        "total_usuarios": User.objects.filter(is_active=True).count(),
        "form": EmpresaForm(),
    })


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
    messages.success(request, f"Empresa {empresa.nome} e usuário administrador criados.")
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


def require_membership(request, empresa_id, papeis=None):
    if not request.user.is_authenticated:
        raise PermissionDenied("Faça login.")
    try:
        membro = MembroEmpresa.objects.select_related("empresa").get(
            empresa_id=empresa_id, usuario=request.user, ativo=True,
            empresa__ativa=True,
        )
    except MembroEmpresa.DoesNotExist:
        raise PermissionDenied("Você não possui acesso a esta empresa.")
    if papeis and membro.papel not in papeis:
        raise PermissionDenied("Seu perfil não permite esta ação.")
    return membro


@login_required
def empresa_dashboard(request, empresa_id):
    membro = require_membership(request, empresa_id)
    today = timezone.localdate()
    vendas = Venda.objects.filter(
        empresa=membro.empresa, estado=Venda.Estado.CONCLUIDA, realizada_em__date=today
    )
    return render(request, "empresa_dashboard.html", {
        "membro": membro, "empresa": membro.empresa,
        "produtos_count": Produto.objects.filter(empresa=membro.empresa, ativo=True).count(),
        "estoque_baixo": Produto.objects.filter(
            empresa=membro.empresa, ativo=True, estoque_atual__lte=F("estoque_minimo")
        ).count(),
        "vendas_count": vendas.count(),
        "faturamento_hoje": vendas.aggregate(total=Sum("total"))["total"] or Decimal("0.00"),
        "membros": MembroEmpresa.objects.filter(empresa=membro.empresa, ativo=True).select_related("usuario"),
        "now": timezone.now(),
    })


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
        "produtos": Produto.objects.filter(empresa=membro.empresa).select_related("categoria").order_by("nome"),
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
        produto.estoque_atual += quantidade
        produto.save(update_fields=("estoque_atual", "atualizado_em"))
        MovimentacaoEstoque.objects.create(
            empresa=membro.empresa, produto=produto,
            tipo=MovimentacaoEstoque.Tipo.ENTRADA, quantidade=quantidade,
            referencia="Entrada pelo painel web", operador=request.user,
        )
    messages.success(request, f"Entrada de {quantidade} {produto.unidade} registrada para {produto.nome}.")
    return redirect("empresa-produtos", empresa_id=empresa_id)


@login_required
def empresa_vendas(request, empresa_id):
    membro = require_membership(request, empresa_id)
    vendas = Venda.objects.filter(empresa=membro.empresa).select_related(
        "operador", "caixa"
    ).prefetch_related("itens__produto", "transacoes").order_by("-realizada_em")[:100]
    if membro.papel == MembroEmpresa.Papel.CAIXA:
        vendas = vendas.filter(operador=request.user)
    return render(request, "empresa_vendas.html", {
        "empresa": membro.empresa, "membro": membro, "vendas": vendas,
    })


@login_required
def baixar_modelo_planilha(request, empresa_id):
    require_membership(
        request, empresa_id, (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE)
    )
    from openpyxl import Workbook\n    from openpyxl.styles import Font
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Produtos"
    sheet.append([
        "codigo", "nome", "categoria", "unidade", "preco_venda",
        "estoque_atual", "estoque_minimo", "setor", "endereco", "codigo_barras",
    ])
    sheet.append([
        "MAT-001", "Cimento CP-II 50kg", "Cimento", "SC", 34.90, 120,
        20, "Depósito A", "A-03-02", "7890000000000",
    ])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
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
            if acao == "gerar":
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
            elif acao == "concluir":
                contagem = get_object_or_404(
                    InventarioContagem, pk=request.POST.get("contagem"),
                    empresa=membro.empresa,
                )
                valores = {
                    key.removeprefix("contado_"): value
                    for key, value in request.POST.items()
                    if key.startswith("contado_")
                }
                concluir_contagem(contagem=contagem, usuario=request.user, contagens=valores)
                messages.success(request, "Contagem concluída; diferenças aplicadas e registradas no histórico de estoque.")
            else:
                messages.error(request, "Ação de inventário inválida.")
        except Exception as exc:
            messages.error(request, str(exc))
        return redirect("empresa-inventario", empresa_id=empresa_id)

    contagens = InventarioContagem.objects.filter(empresa=membro.empresa).prefetch_related(
        "itens__produto"
    ).order_by("-data_programada", "-criado_em")
    return render(request, "empresa_inventario.html", {
        "empresa": membro.empresa, "membro": membro,
        "programadas": contagens.filter(estado=InventarioContagem.Estado.PROGRAMADO),
        "abertas": contagens.filter(estado=InventarioContagem.Estado.ABERTO),
        "concluidas": contagens.filter(estado=InventarioContagem.Estado.CONCLUIDO),
        "today": timezone.localdate(),
        "setores": list(Produto.objects.filter(empresa=membro.empresa, ativo=True)
                        .exclude(setor="").values_list("setor", flat=True).distinct()),
    })
