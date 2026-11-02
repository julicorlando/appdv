from django.contrib import admin
from .models import Categoria, Produto, Caixa, Venda, ItemVenda, Transacao, TokenAutorizacao, UsuarioPerfil

admin.site.register([Categoria, Produto, Caixa, Venda, ItemVenda, Transacao, TokenAutorizacao, UsuarioPerfil])
