from rest_framework import serializers
from .models import Categoria, Produto, Venda, ItemVenda

class CategoriaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Categoria
        fields = ("id", "nome", "ativa")

class ProdutoSerializer(serializers.ModelSerializer):
    categoria_nome = serializers.CharField(source="categoria.nome", read_only=True)
    class Meta:
        model = Produto
        fields = ("id", "categoria", "categoria_nome", "codigo", "codigo_barras", "nome", "unidade", "setor", "endereco",
                  "preco_venda", "estoque_atual", "estoque_minimo", "ativo", "atualizado_em")
        read_only_fields = ("estoque_atual", "atualizado_em")

class ItemVendaSerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemVenda
        fields = ("produto", "quantidade", "preco_unitario", "desconto", "total")

class VendaSerializer(serializers.ModelSerializer):
    itens = ItemVendaSerializer(many=True, read_only=True)
    class Meta:
        model = Venda
        fields = ("id", "idempotency_key", "caixa", "operador", "estado", "subtotal",
                  "desconto", "total", "realizada_em", "sincronizada_em", "itens")
        read_only_fields = fields
