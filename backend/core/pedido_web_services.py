import uuid

from django.db import transaction
from django.utils import timezone

from rest_framework.exceptions import ValidationError

from .models import Caixa, ItemPedidoWeb, PedidoWeb, Produto
from .services import registrar_venda


@transaction.atomic
def finalizar_pedido_web(*, empresa, usuario, pedido_id, caixa_id, pagamentos):
    try:
        pedido = PedidoWeb.objects.select_for_update().get(pk=pedido_id, empresa=empresa)
    except (PedidoWeb.DoesNotExist, ValueError, TypeError):
        raise ValidationError({"pedido": "Pedido não encontrado nesta empresa."})
    if pedido.estado == PedidoWeb.Estado.CONCLUIDO and pedido.venda_id:
        return pedido, pedido.venda
    if pedido.estado not in (PedidoWeb.Estado.CONFIRMADO, PedidoWeb.Estado.SEPARANDO):
        raise ValidationError({"pedido": "O cliente precisa confirmar o pedido antes da finalização."})
    if pedido.expira_em <= timezone.now():
        raise ValidationError({"pedido": "O pedido expirou; crie uma nova cotação."})
    itens = list(pedido.itens.select_related("produto").select_for_update())
    for item in itens:
        if item.produto.empresa_id != empresa.pk or not item.produto.ativo:
            raise ValidationError({"itens": f"{item.nome_produto} não está mais disponível."})
        if item.produto.preco_venda != item.preco_unitario:
            raise ValidationError({"itens": f"O preço de {item.nome_produto} mudou. Atualize o pedido antes de cobrar."})
    try:
        caixa = Caixa.objects.get(pk=caixa_id, empresa=empresa, operador=usuario, estado=Caixa.Estado.ABERTO)
    except (Caixa.DoesNotExist, ValueError, TypeError):
        raise ValidationError({"caixa": "Abra seu caixa antes de finalizar o pedido."})
    payload = {
        "idempotency_key": str(pedido.codigo_publico),
        "caixa": caixa.pk,
        "observacao": f"Pedido web #{pedido.pk} — {pedido.cliente_nome} / {pedido.cliente_telefone}",
        "itens": [
            {"produto": item.produto_id, "quantidade": str(item.quantidade), "desconto": "0"}
            for item in itens
        ],
        "pagamentos": pagamentos,
    }
    venda, _ = registrar_venda(empresa=empresa, usuario=usuario, dados=payload, frete=pedido.frete)
    pedido.estado = PedidoWeb.Estado.CONCLUIDO
    pedido.venda = venda
    pedido.save(update_fields=("estado", "venda"))
    return pedido, venda
