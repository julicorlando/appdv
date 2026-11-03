import uuid
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import (
    Caixa, InventarioContagem, ItemContagem, ItemVenda, MovimentacaoEstoque, Produto, TokenAutorizacao,
    Transacao, Venda,
)


@transaction.atomic
def registrar_venda(*, empresa, usuario, dados):
    try:
        chave = uuid.UUID(str(dados.get("idempotency_key")))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError({"idempotency_key": "Informe um UUID válido."})

    existente = Venda.objects.filter(empresa=empresa, idempotency_key=chave).first()
    if existente:
        if existente.operador_id != usuario.pk:
            raise ValidationError({"idempotency_key": "Esta chave já foi usada por outro operador."})
        return existente, False

    try:
        caixa = Caixa.objects.select_for_update().get(
            pk=dados.get("caixa"), empresa=empresa, operador=usuario,
            estado=Caixa.Estado.ABERTO,
        )
    except (Caixa.DoesNotExist, ValueError, TypeError):
        raise ValidationError({"caixa": "Caixa aberto do operador não encontrado nesta empresa."})

    linhas = dados.get("itens")
    pagamentos = dados.get("pagamentos")
    if not isinstance(linhas, list) or not linhas:
        raise ValidationError({"itens": "A venda precisa conter ao menos um produto."})
    if not isinstance(pagamentos, list) or not pagamentos:
        raise ValidationError({"pagamentos": "Informe ao menos uma forma de pagamento."})

    subtotal = Decimal("0.00")
    itens_calculados = []
    estoque_solicitado = {}
    produtos_travados = {}
    for linha in linhas:
        try:
            produto = Produto.objects.select_for_update().get(
                pk=linha["produto"], empresa=empresa, ativo=True
            )
            if ItemContagem.objects.filter(
                contagem__empresa=empresa,
                contagem__estado=InventarioContagem.Estado.ABERTO,
                produto=produto,
            ).exists():
                raise ValidationError({
                    "itens": f"{produto.nome} está em contagem física. Aguarde a conclusão do inventário."
                })
            quantidade = Decimal(str(linha["quantidade"]))
            if not quantidade.is_finite() or quantidade <= 0:
                raise ValueError
            desconto = Decimal(str(linha.get("desconto", "0")))
            bruto = (produto.preco_venda * quantidade).quantize(Decimal("0.01"))
            if not desconto.is_finite() or desconto < 0 or desconto > bruto:
                raise ValueError
        except Produto.DoesNotExist:
            raise ValidationError({"itens": "Produto inexistente ou indisponível nesta empresa."})
        except (KeyError, ValueError, TypeError, InvalidOperation):
            raise ValidationError({"itens": "Produto, quantidade ou desconto inválido."})

        produtos_travados[produto.pk] = produto
        estoque_solicitado[produto.pk] = estoque_solicitado.get(
            produto.pk, Decimal("0")
        ) + quantidade
        total_linha = bruto - desconto
        subtotal += bruto
        itens_calculados.append((produto, quantidade, desconto, total_linha))

    for produto_id, quantidade_total in estoque_solicitado.items():
        produto = produtos_travados[produto_id]
        if produto.estoque_atual < quantidade_total:
            raise ValidationError({"itens": f"Estoque insuficiente para {produto.nome}."})

    desconto_total = sum((item[2] for item in itens_calculados), Decimal("0"))
    total = subtotal - desconto_total
    valores_pagamento = []
    soma_pagamentos = Decimal("0")
    for pagamento in pagamentos:
        try:
            meio = pagamento["meio"]
            valor = Decimal(str(pagamento["valor"]))
            recebido = Decimal(str(pagamento.get("valor_recebido", valor)))
            troco = Decimal(str(pagamento.get("troco", "0")))
            if (
                meio not in Transacao.Meio.values
                or not valor.is_finite() or valor <= 0
                or not recebido.is_finite() or recebido < valor
                or not troco.is_finite() or troco < 0
            ):
                raise ValueError
            if meio == Transacao.Meio.DINHEIRO:
                if (recebido - valor).quantize(Decimal("0.01")) != troco.quantize(Decimal("0.01")):
                    raise ValueError
            elif recebido != valor or troco != 0:
                raise ValueError
        except (KeyError, ValueError, TypeError, InvalidOperation):
            raise ValidationError({"pagamentos": "Forma, valor recebido ou troco inválido."})
        valores_pagamento.append(
            (meio, valor, recebido, troco, str(pagamento.get("referencia_externa", "")))
        )
        soma_pagamentos += valor
    if soma_pagamentos != total:
        raise ValidationError({"pagamentos": "A soma dos pagamentos deve ser igual ao total da venda."})

    venda = Venda.objects.create(
        empresa=empresa, idempotency_key=chave, caixa=caixa, operador=usuario,
        estado=Venda.Estado.CONCLUIDA, subtotal=subtotal, desconto=desconto_total,
        total=total, observacao=str(dados.get("observacao", ""))[:2000],
    )
    for produto, quantidade, desconto, total_linha in itens_calculados:
        ItemVenda.objects.create(
            empresa=empresa, venda=venda, produto=produto, quantidade=quantidade,
            preco_unitario=produto.preco_venda, desconto=desconto, total=total_linha,
        )
    for produto_id, quantidade_total in estoque_solicitado.items():
        produto = produtos_travados[produto_id]
        produto.estoque_atual -= quantidade_total
        produto.save(update_fields=("estoque_atual", "atualizado_em"))
        MovimentacaoEstoque.objects.create(
            empresa=empresa, produto=produto, tipo=MovimentacaoEstoque.Tipo.VENDA,
            quantidade=-quantidade_total, referencia=f"Venda #{venda.pk}", operador=usuario,
        )
    for meio, valor, recebido, troco, referencia in valores_pagamento:
        Transacao.objects.create(
            empresa=empresa, venda=venda, meio=meio, valor=valor,
            valor_recebido=recebido, troco=troco,
            referencia_externa=referencia[:120],
        )
    return venda, True


@transaction.atomic
def cancelar_venda(*, empresa, usuario, venda_id, codigo,
                   acao=TokenAutorizacao.Acao.CANCELAR_VENDA, justificativa=""):
    try:
        venda = Venda.objects.select_for_update().get(pk=venda_id, empresa=empresa)
    except (Venda.DoesNotExist, ValueError, TypeError):
        raise ValidationError({"venda": "Venda não encontrada nesta empresa."})
    if venda.estado != Venda.Estado.CONCLUIDA:
        raise ValidationError({"venda": "Somente vendas concluídas podem ser canceladas."})
    digest = TokenAutorizacao.hash_token(str(codigo))
    try:
        token = TokenAutorizacao.objects.select_for_update().get(
            empresa=empresa, venda=venda, acao=acao, token_hash=digest,
        )
    except TokenAutorizacao.DoesNotExist:
        raise ValidationError({"token": "Token inválido para esta operação."})
    if not token.esta_valido():
        raise ValidationError({"token": "Token expirado, revogado ou já utilizado."})

    token.usado_em = timezone.now()
    token.usado_por = usuario
    token.save(update_fields=("usado_em", "usado_por"))
    venda.estado = Venda.Estado.CANCELADA
    justificativa = str(justificativa).strip()[:500]
    if justificativa:
        venda.observacao = (venda.observacao + "\nMotivo da operação: " + justificativa).strip()[:2000]
        venda.save(update_fields=("estado", "observacao"))
    else:
        venda.save(update_fields=("estado",))
    itens = venda.itens.select_related("produto").select_for_update()
    for item in itens:
        item.produto.estoque_atual += item.quantidade
        item.produto.save(update_fields=("estoque_atual", "atualizado_em"))
        MovimentacaoEstoque.objects.create(
            empresa=empresa, produto=item.produto,
            tipo=MovimentacaoEstoque.Tipo.DEVOLUCAO,
            quantidade=item.quantidade, referencia=f"Estorno da venda #{venda.pk}",
            operador=usuario,
        )
    for pagamento in venda.transacoes.filter(tipo=Transacao.Tipo.PAGAMENTO):
        Transacao.objects.create(
            empresa=empresa, venda=venda, tipo=Transacao.Tipo.ESTORNO,
            meio=pagamento.meio, valor=pagamento.valor,
            referencia_externa=f"Estorno da venda #{venda.pk}"[:120],
        )
    return venda
