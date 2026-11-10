from rest_framework import serializers
from .models import Categoria, Produto, Venda, ItemVenda

class CategoriaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Categoria
        fields = ("id", "nome", "ativa")

class ProdutoSerializer(serializers.ModelSerializer):
    categoria_nome = serializers.CharField(source="categoria.nome", read_only=True)
    # `preco_venda` é o preço cobrado hoje (já com promoção vigente), para que o PDV
    # instalado e o caixa web usem o mesmo valor que o servidor valida na venda.
    preco_venda = serializers.DecimalField(source="preco_atual", max_digits=12, decimal_places=2, read_only=True)
    preco_base = serializers.DecimalField(source="preco_venda", max_digits=12, decimal_places=2, read_only=True)
    em_promocao = serializers.SerializerMethodField()
    foto_url = serializers.SerializerMethodField()
    class Meta:
        model = Produto
        fields = ("id", "categoria", "categoria_nome", "codigo", "codigo_barras", "nome", "unidade", "unidade_estoque", "fator_conversao", "setor", "endereco",
                  "preco_venda", "preco_base", "em_promocao", "promocao_fim", "foto_url", "estoque_atual", "estoque_minimo", "ativo", "atualizado_em")

    def get_em_promocao(self, obj):
        return obj.promocao_ativa()

    def get_foto_url(self, obj):
        return obj.foto.url if obj.foto else ""
        read_only_fields = ("estoque_atual", "atualizado_em")

class ItemVendaSerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemVenda
        fields = ("produto", "quantidade", "preco_unitario", "desconto", "fator_conversao", "total")

class VendaSerializer(serializers.ModelSerializer):
    itens = ItemVendaSerializer(many=True, read_only=True)
    class Meta:
        model = Venda
        fields = ("id", "idempotency_key", "caixa", "operador", "cliente", "estado", "subtotal",
                  "desconto", "frete", "total", "realizada_em", "sincronizada_em", "itens")
        read_only_fields = fields
