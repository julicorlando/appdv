from django.contrib import admin
from .models import (
    Caixa, Categoria, Cliente, ContaPagar, PagamentoContaPagar, ContaReceber, RecebimentoConta, CompraEntrada, Empresa, Fornecedor, ItemCompraEntrada, ItemVenda, MembroEmpresa, MovimentacaoCaixa, Produto,
    TokenAutorizacao, Transacao, UsuarioPerfil, Venda, MovimentacaoEstoque, InventarioContagem, ItemContagem,
)

@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ("nome", "cnpj", "slug", "ativa", "criada_em")
    search_fields = ("nome", "cnpj", "slug")
    list_filter = ("ativa",)

@admin.register(MembroEmpresa)
class MembroEmpresaAdmin(admin.ModelAdmin):
    list_display = ("usuario", "empresa", "papel", "ativo")
    list_filter = ("empresa", "papel", "ativo")
    search_fields = ("usuario__username", "usuario__email", "empresa__nome")

admin.site.register([Categoria, Produto, Caixa, Venda, ItemVenda, Transacao, TokenAutorizacao, UsuarioPerfil, InventarioContagem, ItemContagem, Fornecedor, CompraEntrada, ItemCompraEntrada, MovimentacaoCaixa, Cliente, ContaPagar, PagamentoContaPagar, ContaReceber, RecebimentoConta])

@admin.register(MovimentacaoEstoque)
class MovimentacaoEstoqueAdmin(admin.ModelAdmin):
    list_display = ("empresa", "produto", "tipo", "quantidade", "operador", "criado_em")
    list_filter = ("empresa", "tipo")
    search_fields = ("produto__nome", "referencia")
    readonly_fields = ("empresa", "produto", "tipo", "quantidade", "referencia", "operador", "criado_em")
