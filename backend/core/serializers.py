from rest_framework import serializers
from .models import Categoria, Produto, Venda, ItemVenda

class CategoriaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Categoria
        fields = "__all__"

class ProdutoSerializer(serializers.ModelSerializer):
    categoria_nome = serializers.CharField(source="categoria.nome", read_only=True)
    class Meta:
        model = Produto
        fields = "__all__"
        read_only_fields = ("estoque_atual", "atualizado_em")

class ItemVendaSerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemVenda
        fields = ("produto", "quantidade", "preco_unitario", "desconto", "total")

class VendaSerializer(serializers.ModelSerializer):
    itens = ItemVendaSerializer(many=True, read_only=True)
    class Meta:
        model = Venda
        fields = "__all__"
        read_only_fields = ("operador", "estado", "subtotal", "total", "sincronizada_em")
