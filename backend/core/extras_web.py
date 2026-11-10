"""Telas do painel adicionadas na versão 0.3: produtos (edição, promoção, fiscal, etiquetas),
fornecedores e despesas, extrato do cliente, relatórios gerenciais, fechamento de caixa pelo
gerente, central fiscal (Focus NFe) e troca/redefinição de senha.

Todas as telas também aparecem no PDV instalado (módulos de gestão integrados).
"""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import io

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import ValidationError as DRFValidationError

from .access import mensagem_erro
from .codigo_barras import ean13_interno, ean13_svg, ean13_valido
from .management_services import audit
from .models import (
    Caixa, Cliente, ContaPagar, ContaReceber, CreditoLoja, DevolucaoItem, DocumentoFiscal, Fornecedor,
    ItemVenda, MembroEmpresa, PagamentoContaPagar, Produto, RecebimentoConta, Transacao, Venda,
)
from .web import require_membership

ZERO = Decimal("0.00")
GESTAO = (MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE)


def _gestor(request, empresa_id):
    return require_membership(request, empresa_id, GESTAO)


# ---------------------------------------------------------------- produtos

class ProdutoEdicaoForm(forms.ModelForm):
    class Meta:
        model = Produto
        fields = ("categoria", "codigo", "codigo_barras", "nome", "unidade", "unidade_estoque", "fator_conversao",
                  "setor", "endereco", "preco_venda", "custo_medio", "estoque_minimo", "ativo", "foto",
                  "preco_promocional", "promocao_inicio", "promocao_fim",
                  "ncm", "cest", "cfop", "csosn", "origem")
        widgets = {
            "preco_venda": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "preco_promocional": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "custo_medio": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "estoque_minimo": forms.NumberInput(attrs={"step": "0.001", "min": "0"}),
            "fator_conversao": forms.NumberInput(attrs={"step": "0.000001", "min": "0.000001"}),
            "promocao_inicio": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "promocao_fim": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "foto": forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/webp"}),
        }
        help_texts = {
            "ativo": "Produtos inativos somem do PDV, do caixa web e dos alertas, mas o histórico é mantido.",
            "preco_promocional": "Vale entre as datas informadas (em branco = sem limite). Precisa ser menor que o preço de venda.",
            "ncm": "8 dígitos, obrigatório para NFC-e.",
            "csosn": "Simples Nacional: CSOSN (ex.: 102). Regime normal: CST do ICMS (ex.: 00). Confirme com o contador.",
            "cfop": "Venda de mercadoria no estado: normalmente 5102.",
        }

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import Categoria
        self.empresa = empresa
        self.fields["categoria"].queryset = Categoria.objects.filter(empresa=empresa)

    def clean_codigo(self):
        codigo = self.cleaned_data["codigo"]
        if Produto.objects.filter(empresa=self.empresa, codigo=codigo).exclude(pk=self.instance.pk).exists():
            raise DjangoValidationError("Já existe produto com este código nesta empresa.")
        return codigo

    def clean_ncm(self):
        ncm = "".join(ch for ch in self.cleaned_data.get("ncm", "") if ch.isdigit())
        if ncm and len(ncm) != 8:
            raise DjangoValidationError("O NCM tem 8 dígitos.")
        return ncm

    def clean_cest(self):
        cest = "".join(ch for ch in self.cleaned_data.get("cest", "") if ch.isdigit())
        if cest and len(cest) != 7:
            raise DjangoValidationError("O CEST tem 7 dígitos.")
        return cest

    def clean_cfop(self):
        cfop = self.cleaned_data.get("cfop", "")
        if not (cfop.isdigit() and len(cfop) == 4):
            raise DjangoValidationError("O CFOP tem 4 dígitos.")
        return cfop

    def clean_foto(self):
        foto = self.cleaned_data.get("foto")
        if foto and hasattr(foto, "size") and foto.size > 2 * 1024 * 1024:
            raise DjangoValidationError("A foto pode ter no máximo 2 MB.")
        return foto

    def clean(self):
        dados = super().clean()
        promo, preco = dados.get("preco_promocional"), dados.get("preco_venda")
        inicio, fim = dados.get("promocao_inicio"), dados.get("promocao_fim")
        if promo is not None and preco is not None and promo >= preco:
            self.add_error("preco_promocional", "O preço promocional precisa ser menor que o preço de venda.")
        if inicio and fim and fim < inicio:
            self.add_error("promocao_fim", "A data final precisa ser igual ou posterior à inicial.")
        if (inicio or fim) and promo is None:
            self.add_error("preco_promocional", "Informe o preço promocional para usar as datas.")
        return dados


@login_required
def produto_editar(request, empresa_id, pk):
    membro = _gestor(request, empresa_id)
    produto = get_object_or_404(Produto, empresa=membro.empresa, pk=pk)
    antes = {"preco_venda": str(produto.preco_venda), "ativo": produto.ativo,
             "preco_promocional": str(produto.preco_promocional or "")}
    form = ProdutoEdicaoForm(request.POST or None, request.FILES or None, instance=produto, empresa=membro.empresa)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            produto = form.save()
            depois = {"preco_venda": str(produto.preco_venda), "ativo": produto.ativo,
                      "preco_promocional": str(produto.preco_promocional or "")}
            audit(membro.empresa, request.user, "PRODUTO_EDITADO", produto.pk, antes=antes, depois=depois)
        messages.success(request, f"Produto {produto.codigo} atualizado. O PDV recebe a mudança na próxima atualização do catálogo.")
        return redirect("empresa-produtos", empresa_id=empresa_id)
    return render(request, "extras_produto.html", {
        "empresa": membro.empresa, "membro": membro, "form": form, "produto": produto,
        "barcode_svg": ean13_svg(produto.codigo_barras),
    })


@login_required
def produtos_demo_inativar(request, empresa_id):
    membro = require_membership(request, empresa_id, (MembroEmpresa.Papel.ADMIN,))
    if request.method != "POST":
        return redirect("empresa-produtos", empresa_id=empresa_id)
    total = Produto.objects.filter(empresa=membro.empresa, codigo__startswith="DEMO-", ativo=True).update(ativo=False)
    audit(membro.empresa, request.user, "CATALOGO_DEMO_INATIVADO", total)
    messages.success(request, f"{total} produto(s) de teste inativados. O histórico das vendas de teste foi mantido.")
    return redirect("empresa-produtos", empresa_id=empresa_id)


@login_required
def etiquetas(request, empresa_id):
    membro = _gestor(request, empresa_id)
    if request.method == "POST" and request.POST.get("acao") == "gerar_codigos":
        gerados = 0
        with transaction.atomic():
            for produto in Produto.objects.select_for_update().filter(empresa=membro.empresa, ativo=True, codigo_barras=""):
                produto.codigo_barras = ean13_interno(membro.empresa.pk, produto.pk)
                produto.save(update_fields=("codigo_barras", "atualizado_em"))
                gerados += 1
        audit(membro.empresa, request.user, "CODIGOS_INTERNOS_GERADOS", gerados)
        messages.success(request, f"{gerados} código(s) de barras internos gerados (faixa 2xx, uso restrito da loja).")
        return redirect("empresa-etiquetas", empresa_id=empresa_id)
    produtos = Produto.objects.filter(empresa=membro.empresa, ativo=True).select_related("categoria").order_by("nome")
    busca = request.GET.get("q", "").strip()
    if busca:
        produtos = produtos.filter(Q(nome__icontains=busca) | Q(codigo__icontains=busca) | Q(codigo_barras=busca))
    selecionados = [int(v) for v in request.GET.getlist("p") if v.isdigit()]
    try:
        copias = max(1, min(int(request.GET.get("copias", "1")), 50))
    except ValueError:
        copias = 1
    etiquetas_impressao = []
    if selecionados:
        for produto in produtos.filter(pk__in=selecionados):
            etiquetas_impressao += [{"produto": produto, "svg": ean13_svg(produto.codigo_barras)}] * copias
    return render(request, "extras_etiquetas.html", {
        "empresa": membro.empresa, "membro": membro, "produtos": produtos[:500], "busca": busca,
        "etiquetas": etiquetas_impressao, "selecionados": selecionados, "copias": copias,
        "sem_codigo": Produto.objects.filter(empresa=membro.empresa, ativo=True, codigo_barras="").count(),
    })


# ------------------------------------------------------- fornecedores e despesas

class FornecedorForm(forms.ModelForm):
    class Meta:
        model = Fornecedor
        fields = ("nome", "cnpj", "email", "telefone", "ativo")

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa

    def clean_cnpj(self):
        cnpj = "".join(ch for ch in self.cleaned_data.get("cnpj", "") if ch.isdigit())
        if cnpj and len(cnpj) != 14:
            raise DjangoValidationError("Informe os 14 dígitos do CNPJ.")
        if cnpj and Fornecedor.objects.filter(empresa=self.empresa, cnpj=cnpj).exclude(pk=self.instance.pk).exists():
            raise DjangoValidationError("Já existe fornecedor com este CNPJ.")
        return cnpj


@login_required
def fornecedores(request, empresa_id):
    membro = _gestor(request, empresa_id)
    editando = None
    if request.GET.get("editar", "").isdigit():
        editando = get_object_or_404(Fornecedor, empresa=membro.empresa, pk=request.GET["editar"])
    form = FornecedorForm(request.POST or None, instance=editando, empresa=membro.empresa)
    if request.method == "POST" and form.is_valid():
        fornecedor = form.save(commit=False)
        fornecedor.empresa = membro.empresa
        fornecedor.save()
        audit(membro.empresa, request.user, "FORNECEDOR_SALVO", fornecedor.pk, nome=fornecedor.nome)
        messages.success(request, "Fornecedor salvo.")
        return redirect("empresa-fornecedores", empresa_id=empresa_id)
    lista = Fornecedor.objects.filter(empresa=membro.empresa).annotate(
        em_aberto=Sum("contas_pagar__saldo", filter=Q(contas_pagar__estado__in=("ABERTA", "PARCIAL")))
    ).order_by("-ativo", "nome")
    return render(request, "extras_fornecedores.html", {
        "empresa": membro.empresa, "membro": membro, "form": form, "editando": editando, "fornecedores": lista,
    })


@login_required
def conta_pagar_nova(request, empresa_id):
    membro = _gestor(request, empresa_id)
    if request.method != "POST":
        return redirect("empresa-contas-pagar", empresa_id=empresa_id)
    try:
        fornecedor = Fornecedor.objects.get(pk=request.POST.get("fornecedor"), empresa=membro.empresa, ativo=True)
        valor = Decimal(str(request.POST.get("valor", "0")).replace(",", "."))
        vencimento = date.fromisoformat(request.POST.get("vencimento", ""))
        descricao = request.POST.get("descricao", "").strip()[:240]
        categoria = request.POST.get("categoria", "OUTROS")
        if not valor.is_finite() or valor <= 0 or not descricao:
            raise ValueError("Informe descrição e valor positivo.")
        if categoria not in dict(ContaPagar._meta.get_field("categoria").choices):
            raise ValueError("Categoria inválida.")
        conta = ContaPagar.objects.create(
            empresa=membro.empresa, fornecedor=fornecedor, descricao=descricao, categoria=categoria,
            valor_original=valor.quantize(Decimal("0.01")), saldo=valor.quantize(Decimal("0.01")),
            vencimento=vencimento, criado_por=request.user,
        )
        audit(membro.empresa, request.user, "CONTA_PAGAR_LANCADA", conta.pk, valor=str(conta.valor_original))
        messages.success(request, "Conta lançada em contas a pagar.")
    except Fornecedor.DoesNotExist:
        messages.error(request, "Escolha um fornecedor ativo (cadastre em Fornecedores).")
    except (ValueError, InvalidOperation) as exc:
        messages.error(request, f"Não foi possível lançar: {exc}")
    return redirect("empresa-contas-pagar", empresa_id=empresa_id)


# --------------------------------------------------------------- extrato do cliente

@login_required
def cliente_extrato(request, empresa_id, pk):
    membro = _gestor(request, empresa_id)
    cliente = get_object_or_404(Cliente, empresa=membro.empresa, pk=pk)
    vendas = Venda.objects.filter(empresa=membro.empresa, cliente=cliente).order_by("-realizada_em")[:200]
    contas = ContaReceber.objects.filter(empresa=membro.empresa, cliente=cliente).select_related("venda").order_by("-criado_em")[:200]
    recebimentos = RecebimentoConta.objects.filter(empresa=membro.empresa, conta__cliente=cliente).select_related("conta").order_by("-recebido_em")[:200]
    creditos = CreditoLoja.objects.filter(empresa=membro.empresa, cliente=cliente).order_by("-criado_em")[:200]
    devolucoes = DevolucaoItem.objects.filter(empresa=membro.empresa, item__venda__cliente=cliente).select_related("item__produto").order_by("-criado_em")[:200]
    em_aberto = ContaReceber.objects.filter(empresa=membro.empresa, cliente=cliente, estado__in=("ABERTA", "PARCIAL")).aggregate(n=Sum("saldo"))["n"] or ZERO
    concluidas = Venda.objects.filter(empresa=membro.empresa, cliente=cliente, estado=Venda.Estado.CONCLUIDA)
    resumo = {
        "compras": concluidas.count(),
        "total_comprado": concluidas.aggregate(n=Sum("total"))["n"] or ZERO,
        "em_aberto": em_aberto,
        "vencido": ContaReceber.objects.filter(empresa=membro.empresa, cliente=cliente, estado__in=("ABERTA", "PARCIAL"), vencimento__lt=timezone.localdate()).aggregate(n=Sum("saldo"))["n"] or ZERO,
        "credito_loja": CreditoLoja.objects.filter(empresa=membro.empresa, cliente=cliente).aggregate(n=Sum("valor"))["n"] or ZERO,
        "limite_disponivel": max(cliente.limite_credito - em_aberto, ZERO),
    }
    return render(request, "extras_extrato.html", {
        "empresa": membro.empresa, "membro": membro, "cliente": cliente, "resumo": resumo, "vendas": vendas,
        "contas": contas, "recebimentos": recebimentos, "creditos": creditos, "devolucoes": devolucoes,
        "hoje": timezone.localdate(),
    })


# -------------------------------------------------------------- relatórios gerenciais

def _periodo(request):
    hoje = timezone.localdate()
    try:
        inicio = date.fromisoformat(request.GET.get("inicio", "")) if request.GET.get("inicio") else hoje.replace(day=1)
        fim = date.fromisoformat(request.GET.get("fim", "")) if request.GET.get("fim") else hoje
    except ValueError:
        inicio, fim = hoje.replace(day=1), hoje
    if fim < inicio:
        inicio, fim = fim, inicio
    if (fim - inicio).days > 366:
        inicio = fim - timedelta(days=366)
    return inicio, fim


def dados_relatorios(empresa, inicio, fim):
    vendas = Venda.objects.filter(empresa=empresa, estado=Venda.Estado.CONCLUIDA,
                                  realizada_em__date__gte=inicio, realizada_em__date__lte=fim)
    pagamentos = Transacao.objects.filter(empresa=empresa, venda__in=vendas, tipo=Transacao.Tipo.PAGAMENTO)
    rotulos = dict(Transacao.Meio.choices)
    por_meio = [{"meio": rotulos.get(l["meio"], l["meio"]), "total": l["total"] or ZERO, "quantidade": l["n"]}
                for l in pagamentos.values("meio").annotate(total=Sum("valor"), n=Count("id")).order_by("-total")]
    a_prazo = ContaReceber.objects.filter(empresa=empresa, venda__in=vendas).aggregate(n=Sum("valor_original"))["n"] or ZERO
    if a_prazo:
        por_meio.append({"meio": "A prazo (conta do cliente)", "total": a_prazo, "quantidade": ContaReceber.objects.filter(empresa=empresa, venda__in=vendas).count()})
    por_dia = [{"dia": l["dia"], "vendas": l["n"], "total": l["total"] or ZERO}
               for l in vendas.annotate(dia=TruncDate("realizada_em")).values("dia").annotate(n=Count("id"), total=Sum("total")).order_by("dia")]
    # Fluxo de caixa (regime de caixa): entradas e saídas efetivas por dia.
    fluxo = {}
    def somar(dia, campo, valor):
        linha = fluxo.setdefault(dia, {"dia": dia, "entradas": ZERO, "saidas": ZERO})
        linha[campo] += valor or ZERO
    for l in Transacao.objects.filter(empresa=empresa, criado_em__date__gte=inicio, criado_em__date__lte=fim).exclude(meio="LOJA").annotate(dia=TruncDate("criado_em")).values("dia", "tipo").annotate(total=Sum("valor")):
        somar(l["dia"], "entradas" if l["tipo"] == Transacao.Tipo.PAGAMENTO else "saidas", l["total"])
    for l in RecebimentoConta.objects.filter(empresa=empresa, recebido_em__date__gte=inicio, recebido_em__date__lte=fim).annotate(dia=TruncDate("recebido_em")).values("dia").annotate(total=Sum("valor")):
        somar(l["dia"], "entradas", l["total"])
    for l in PagamentoContaPagar.objects.filter(empresa=empresa, pago_em__date__gte=inicio, pago_em__date__lte=fim).annotate(dia=TruncDate("pago_em")).values("dia").annotate(total=Sum("valor")):
        somar(l["dia"], "saidas", l["total"])
    saldo = ZERO
    fluxo_linhas = []
    for dia in sorted(fluxo):
        linha = fluxo[dia]
        saldo += linha["entradas"] - linha["saidas"]
        fluxo_linhas.append(dict(linha, liquido=linha["entradas"] - linha["saidas"], acumulado=saldo))
    # Curva ABC por receita líquida de devoluções.
    itens = (ItemVenda.objects.filter(empresa=empresa, venda__in=vendas)
             .values("produto_id", "produto__codigo", "produto__nome")
             .annotate(receita=Sum("total"), quantidade=Sum("quantidade")).order_by("-receita"))
    devolvido = {l["item__produto_id"]: l["total"] or ZERO for l in DevolucaoItem.objects.filter(empresa=empresa, item__venda__in=vendas).values("item__produto_id").annotate(total=Sum("valor"))}
    abc = []
    for l in itens:
        abc.append({"codigo": l["produto__codigo"], "nome": l["produto__nome"], "quantidade": l["quantidade"],
                    "receita": (l["receita"] or ZERO) - devolvido.get(l["produto_id"], ZERO)})
    abc.sort(key=lambda l: l["receita"], reverse=True)
    total_abc = sum((l["receita"] for l in abc), ZERO)
    acumulado = ZERO
    for l in abc:
        acumulado += l["receita"]
        fracao = (acumulado / total_abc * 100) if total_abc else Decimal("100")
        l["participacao"] = (l["receita"] / total_abc * 100).quantize(Decimal("0.1")) if total_abc else ZERO
        l["acumulado"] = fracao.quantize(Decimal("0.1"))
        l["classe"] = "A" if fracao <= 80 or l is abc[0] else ("B" if fracao <= 95 else "C")
    # Estoque valorizado (posição atual).
    estoque = []
    for p in Produto.objects.filter(empresa=empresa, ativo=True, estoque_atual__gt=0).select_related("categoria").order_by("categoria__nome", "nome"):
        fator = p.fator_conversao or Decimal("1")
        estoque.append({"codigo": p.codigo, "nome": p.nome, "categoria": p.categoria.nome, "saldo": p.estoque_atual,
                        "custo": (p.estoque_atual * p.custo_medio).quantize(Decimal("0.01")),
                        "venda": (p.estoque_atual / fator * p.preco_atual).quantize(Decimal("0.01"))})
    totais = {
        "vendas": vendas.count(), "faturamento": vendas.aggregate(n=Sum("total"))["n"] or ZERO,
        "ticket_medio": ((vendas.aggregate(n=Sum("total"))["n"] or ZERO) / vendas.count()).quantize(Decimal("0.01")) if vendas.count() else ZERO,
        "estoque_custo": sum((l["custo"] for l in estoque), ZERO), "estoque_venda": sum((l["venda"] for l in estoque), ZERO),
        "entradas": sum((l["entradas"] for l in fluxo_linhas), ZERO), "saidas": sum((l["saidas"] for l in fluxo_linhas), ZERO),
        "contas_pagar_abertas": ContaPagar.objects.filter(empresa=empresa, estado__in=("ABERTA", "PARCIAL")).aggregate(n=Sum("saldo"))["n"] or ZERO,
        "contas_receber_abertas": ContaReceber.objects.filter(empresa=empresa, estado__in=("ABERTA", "PARCIAL")).aggregate(n=Sum("saldo"))["n"] or ZERO,
    }
    return {"por_meio": por_meio, "por_dia": por_dia, "fluxo": fluxo_linhas, "abc": abc, "estoque": estoque, "totais": totais}


def _planilha(empresa, inicio, fim, dados):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    livro = Workbook()
    abas = [
        ("Resumo", ["Indicador", "Valor"], [[k.replace("_", " ").capitalize(), v] for k, v in dados["totais"].items()]),
        ("Formas de pagamento", ["Forma", "Quantidade", "Total"], [[l["meio"], l["quantidade"], l["total"]] for l in dados["por_meio"]]),
        ("Vendas por dia", ["Dia", "Vendas", "Total"], [[l["dia"], l["vendas"], l["total"]] for l in dados["por_dia"]]),
        ("Fluxo de caixa", ["Dia", "Entradas", "Saídas", "Líquido", "Acumulado"], [[l["dia"], l["entradas"], l["saidas"], l["liquido"], l["acumulado"]] for l in dados["fluxo"]]),
        ("Curva ABC", ["Código", "Produto", "Quantidade", "Receita", "% do total", "% acumulado", "Classe"], [[l["codigo"], l["nome"], l["quantidade"], l["receita"], l["participacao"], l["acumulado"], l["classe"]] for l in dados["abc"]]),
        ("Estoque valorizado", ["Código", "Produto", "Categoria", "Saldo", "Valor a custo", "Valor de venda"], [[l["codigo"], l["nome"], l["categoria"], l["saldo"], l["custo"], l["venda"]] for l in dados["estoque"]]),
    ]
    for indice, (titulo, cabecalho, linhas) in enumerate(abas):
        aba = livro.active if indice == 0 else livro.create_sheet()
        aba.title = titulo
        aba.append([f"{empresa.nome} · {inicio:%d/%m/%Y} a {fim:%d/%m/%Y}"])
        aba.append(cabecalho)
        for celula in aba[2]:
            celula.font = Font(bold=True)
        for linha in linhas:
            aba.append([float(v) if isinstance(v, Decimal) else v for v in linha])
        aba.column_dimensions["A"].width = 18
        aba.column_dimensions["B"].width = 38
    saida = io.BytesIO()
    livro.save(saida)
    return saida.getvalue()


@login_required
def relatorios_gerenciais(request, empresa_id):
    membro = _gestor(request, empresa_id)
    inicio, fim = _periodo(request)
    dados = dados_relatorios(membro.empresa, inicio, fim)
    if request.GET.get("exportar") == "xlsx":
        resposta = HttpResponse(_planilha(membro.empresa, inicio, fim, dados),
                                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resposta["Content-Disposition"] = f'attachment; filename="relatorios-{inicio:%Y%m%d}-{fim:%Y%m%d}.xlsx"'
        return resposta
    return render(request, "extras_relatorios.html", {
        "empresa": membro.empresa, "membro": membro, "inicio": inicio, "fim": fim, **dados,
    })


# --------------------------------------------------------- fechamento pelo gerente

@login_required
def caixa_fechar(request, empresa_id, pk):
    from .api import _resumo_caixa
    membro = _gestor(request, empresa_id)
    if request.method != "POST":
        return redirect("gestao-caixa", empresa_id=empresa_id, pk=pk)
    try:
        informado = Decimal(str(request.POST.get("saldo_fechamento", "")).replace(",", "."))
        motivo = request.POST.get("motivo", "").strip()
        if not informado.is_finite() or informado < 0:
            raise ValueError("Informe o dinheiro contado na gaveta.")
        if len(motivo) < 5:
            raise ValueError("Explique por que o caixa está sendo fechado pela gerência.")
        with transaction.atomic():
            caixa = Caixa.objects.select_for_update().get(pk=pk, empresa=membro.empresa, estado=Caixa.Estado.ABERTO)
            esperado = Decimal(_resumo_caixa(caixa)["esperado_dinheiro"])
            caixa.saldo_informado_fechamento = informado
            caixa.saldo_esperado_fechamento = esperado
            caixa.diferenca_fechamento = (informado - esperado).quantize(Decimal("0.01"))
            caixa.estado = Caixa.Estado.FECHADO
            caixa.fechado_em = timezone.now()
            caixa.save(update_fields=("saldo_informado_fechamento", "saldo_esperado_fechamento",
                                      "diferenca_fechamento", "estado", "fechado_em"))
            audit(membro.empresa, request.user, "CAIXA_FECHADO_PELA_GERENCIA", caixa.pk,
                  operador=caixa.operador.username, contado=str(informado), esperado=str(esperado), motivo=motivo[:250])
        messages.success(request, f"Caixa #{caixa.pk} fechado. Diferença: R$ {caixa.diferenca_fechamento:.2f}.")
    except Caixa.DoesNotExist:
        messages.error(request, "Este caixa já está fechado.")
    except (ValueError, InvalidOperation) as exc:
        messages.error(request, str(exc))
    return redirect("gestao-caixa", empresa_id=empresa_id, pk=pk)


# ----------------------------------------------------------------- central fiscal

@login_required
def fiscal(request, empresa_id):
    from . import focus_nfe
    membro = _gestor(request, empresa_id)
    empresa = membro.empresa
    if request.method == "POST":
        acao = request.POST.get("acao")
        try:
            venda = get_object_or_404(Venda, empresa=empresa, pk=request.POST.get("venda"))
            if acao == "emitir":
                documento = focus_nfe.emitir(venda)
                audit(empresa, request.user, "NFCE_EMITIR", venda.pk, estado=documento.estado)
                messages.success(request, f"Venda #{venda.pk}: {documento.get_estado_display()}. {documento.mensagem}")
            elif acao == "consultar":
                documento = get_object_or_404(DocumentoFiscal, empresa=empresa, venda=venda)
                documento = focus_nfe.consultar(documento)
                messages.info(request, f"Venda #{venda.pk}: {documento.get_estado_display()}.")
            elif acao == "cancelar":
                documento = get_object_or_404(DocumentoFiscal, empresa=empresa, venda=venda)
                focus_nfe.cancelar(documento, request.POST.get("justificativa", ""))
                audit(empresa, request.user, "NFCE_CANCELADA", venda.pk, justificativa=request.POST.get("justificativa", "")[:255])
                messages.success(request, f"NFC-e da venda #{venda.pk} cancelada.")
        except DRFValidationError as exc:
            messages.error(request, mensagem_erro(exc))
        return redirect("gestao-fiscal", empresa_id=empresa_id)
    documentos = {d.venda_id: d for d in DocumentoFiscal.objects.filter(empresa=empresa)}
    vendas = list(Venda.objects.filter(empresa=empresa, estado=Venda.Estado.CONCLUIDA).select_related("cliente", "operador").order_by("-pk")[:200])
    for venda in vendas:
        venda.documento = documentos.get(venda.pk)
    sem_ncm = Produto.objects.filter(empresa=empresa, ativo=True, ncm="").count()
    return render(request, "extras_fiscal.html", {
        "empresa": empresa, "membro": membro, "vendas": vendas, "configurado": focus_nfe.configurado(empresa),
        "sem_ncm": sem_ncm, "ambiente": empresa.get_ambiente_fiscal_display(),
    })


# ------------------------------------------------------------------------- senhas

@login_required
def minha_senha(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        usuario = form.save()
        update_session_auth_hash(request, usuario)
        for membro in MembroEmpresa.objects.filter(usuario=usuario, ativo=True).select_related("empresa")[:20]:
            audit(membro.empresa, usuario, "SENHA_ALTERADA", usuario.pk, origem="painel")
        messages.success(request, "Senha alterada. Use a nova senha também no PDV instalado.")
        return redirect("/")
    return render(request, "extras_senha.html", {"form": form})


def email_configurado():
    return bool(getattr(settings, "EMAIL_HOST", "")) and settings.EMAIL_BACKEND.endswith("smtp.EmailBackend")


def redefinir_senha(request):
    """Esqueci minha senha. Sem servidor de e-mail configurado, orienta a pedir ao administrador."""
    from django.contrib.auth import views as auth_views
    if not email_configurado():
        return render(request, "extras_senha_sem_email.html")
    return auth_views.PasswordResetView.as_view(
        template_name="registration/redefinir_form.html",
        email_template_name="registration/redefinir_email.txt",
        subject_template_name="registration/redefinir_assunto.txt",
        success_url=reverse("redefinir-senha-enviado"),
    )(request)


@login_required
def equipe_redefinir_senha(request, empresa_id):
    """Administrador define uma senha provisória para um membro da própria empresa."""
    from django.contrib.auth.password_validation import validate_password
    membro = require_membership(request, empresa_id, (MembroEmpresa.Papel.ADMIN,))
    if request.method == "POST":
        try:
            alvo = MembroEmpresa.objects.select_related("usuario").get(pk=request.POST.get("membro"), empresa=membro.empresa)
            senha = request.POST.get("nova_senha", "")
            validate_password(senha, alvo.usuario)
            if alvo.usuario.is_superuser:
                raise DjangoValidationError("A senha do administrador master só pode ser alterada por ele.")
            alvo.usuario.set_password(senha)
            alvo.usuario.save(update_fields=["password"])
            audit(membro.empresa, request.user, "SENHA_REDEFINIDA_PELO_ADMIN", alvo.usuario.pk, usuario=alvo.usuario.username)
            messages.success(request, f"Senha provisória definida para {alvo.usuario.username}. Peça para trocá-la no primeiro acesso.")
        except MembroEmpresa.DoesNotExist:
            messages.error(request, "Membro não encontrado nesta empresa.")
        except DjangoValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    return redirect("gestao-equipe", empresa_id=empresa_id)
