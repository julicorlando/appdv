from django.contrib import admin
from .models import (
    Caixa, Categoria, Empresa, ItemVenda, MembroEmpresa, Produto,
    TokenAutorizacao, Transacao, UsuarioPerfil, Venda,
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

admin.site.register([Categoria, Produto, Caixa, Venda, ItemVenda, Transacao, TokenAutorizacao, UsuarioPerfil])
