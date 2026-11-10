import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import (
    Caixa, Cliente, ContaReceber, InventarioContagem, ItemContagem, ItemVenda, MembroEmpresa, MovimentacaoEstoque, Produto, TokenAutorizacao,
    Transacao, Venda,
)


@transaction.atomic
def registrar_venda(*, empresa, usuario, dados, frete=Decimal("0.00")):
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

    # Another retry may have committed while this request waited for the cash lock.
    existente = Venda.objects.filter(empresa=empresa, idempotency_key=chave).first()
    if existente:
        if existente.operador_id != usuario.pk:
            raise ValidationError({"idempotency_key": "Esta chave já foi usada por outro operador."})
        return existente, False

    linhas = dados.get("itens")
    pagamentos = dados.get("pagamentos")
    try:
        credito = Decimal(str(dados.get("credito", "0")))
        if not credito.is_finite() or credito < 0:
            raise ValueError
    except (ValueError, TypeError, InvalidOperation):
        raise ValidationError({"credito": "Informe um valor de crédito válido."})
    if not isinstance(linhas, list) or not linhas:
        raise ValidationError({"itens": "A venda precisa conter ao menos um produto."})
    if not isinstance(pagamentos, list):
        raise ValidationError({"pagamentos": "Informe pagamento ou valor lançado a crédito."})

    cliente = None
    cliente_id = dados.get("cliente")
    if cliente_id:
        try:
            cliente = Cliente.objects.get(pk=cliente_id, empresa=empresa, ativo=True)
        except (Cliente.DoesNotExist, ValueError, TypeError):
            raise ValidationError({"cliente": "Cliente ativo não encontrado nesta empresa."})
    if credito > 0 and not cliente:
        raise ValidationError({"cliente": "Selecione um cliente para registrar venda a prazo."})

    try:
        membro = MembroEmpresa.objects.get(empresa=empresa, usuario=usuario, ativo=True)
    except MembroEmpresa.DoesNotExist:
        raise ValidationError({"empresa": "Usuário sem vínculo ativo com esta empresa."})

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
            fator = Decimal(str(produto.fator_conversao))
            if not quantidade.is_finite() or quantidade <= 0 or not fator.is_finite() or fator <= 0:
                raise ValueError
            quantidade_estoque = (quantidade * fator).quantize(Decimal("0.001"))
            desconto = Decimal(str(linha.get("desconto", "0")))
            if "preco_esperado" in linha and Decimal(str(linha['preco_esperado'])) != produto.preco_atual:
                raise ValidationError({'itens': f'Preço de {produto.nome} mudou. Venda pendente de revisão.'})
            bruto = (produto.preco_atual * quantidade).quantize(Decimal("0.01"))
            if not desconto.is_finite() or desconto < 0 or desconto > bruto:
                raise ValueError
        except Produto.DoesNotExist:
            raise ValidationError({"itens": "Produto inexistente ou indisponível nesta empresa."})
        except (KeyError, ValueError, TypeError, InvalidOperation):
            raise ValidationError({"itens": "Produto, quantidade ou desconto inválido."})

        produtos_travados[produto.pk] = produto
        estoque_solicitado[produto.pk] = estoque_solicitado.get(
            produto.pk, Decimal("0")
        ) + quantidade_estoque
        total_linha = bruto - desconto
        subtotal += bruto
        itens_calculados.append((produto, quantidade, desconto, total_linha, fator))

    for produto_id, quantidade_total in estoque_solicitado.items():
        produto = produtos_travados[produto_id]
        if produto.estoque_atual < quantidade_total:
            raise ValidationError({"itens": f"Estoque insuficiente para {produto.nome}."})

    desconto_total = sum((item[2] for item in itens_calculados), Decimal("0"))
    from .discount_policy import validar_desconto
    aprovacao = validar_desconto(empresa, usuario, dados, subtotal, desconto_total)
    total = subtotal - desconto_total + frete
    valores_pagamento = []
    cobrancas_pix = []
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
        referencia = str(pagamento.get("referencia_externa", ""))
        if pagamento.get("cobranca_pix") not in (None, ""):
            # PIX dinâmico: só aceita cobrança paga, da mesma empresa, ainda não usada e do mesmo valor.
            from .models import CobrancaPix
            try:
                cobranca = CobrancaPix.objects.select_for_update().get(
                    pk=pagamento["cobranca_pix"], empresa=empresa, venda__isnull=True,
                )
            except (CobrancaPix.DoesNotExist, ValueError, TypeError):
                raise ValidationError({"pagamentos": "Cobrança PIX não encontrada ou já usada em outra venda."})
            if meio != Transacao.Meio.PIX or cobranca.estado != CobrancaPix.Estado.APROVADO or cobranca.valor != valor:
                raise ValidationError({"pagamentos": "A cobrança PIX ainda não foi paga ou não corresponde ao valor informado."})
            cobrancas_pix.append(cobranca)
            referencia = f"PIX Mercado Pago {cobranca.provedor_id}"
        valores_pagamento.append((meio, valor, recebido, troco, referencia))
        soma_pagamentos += valor
    if (soma_pagamentos + credito).quantize(Decimal("0.01")) != total:
        raise ValidationError({"pagamentos": "Pagamentos e crédito precisam corresponder ao total da venda."})
    credito_loja=sum((valor for meio,valor,_,_,_ in valores_pagamento if meio=='LOJA'),Decimal('0'))
    if credito_loja:
        from .models import CreditoLoja
        if not cliente:raise ValidationError({'cliente':'Identifique o cliente para usar crédito em loja.'})
        Cliente.objects.select_for_update().get(pk=cliente.pk,empresa=empresa)
        saldo=CreditoLoja.objects.filter(empresa=empresa,cliente=cliente).aggregate(n=Sum('valor'))['n'] or Decimal('0')
        if credito_loja>saldo:raise ValidationError({'credito_loja':'Saldo de crédito em loja insuficiente.'})
    if credito > 0:
        saldo_aberto = ContaReceber.objects.filter(
            empresa=empresa, cliente=cliente,
            estado__in=(ContaReceber.Estado.ABERTA, ContaReceber.Estado.PARCIAL),
        ).aggregate(total=Sum("saldo"))["total"] or Decimal("0")
        limite = cliente.limite_credito
        if limite <= 0 or saldo_aberto + credito > limite:
            raise ValidationError({
                "credito": f"Crédito recusado. Limite: R$ {limite:.2f}; saldo em aberto: R$ {saldo_aberto:.2f}."
            })
        raw_due = str(dados.get("vencimento") or (timezone.localdate() + timedelta(days=30)).isoformat())
        try:
            vencimento = timezone.datetime.fromisoformat(raw_due).date()
        except (ValueError, TypeError):
            raise ValidationError({"vencimento": "Informe uma data de vencimento válida."})
        if vencimento < timezone.localdate():
            raise ValidationError({"vencimento": "A data de vencimento não pode estar no passado."})

    venda = Venda.objects.create(
        empresa=empresa, idempotency_key=chave, caixa=caixa, operador=usuario,
        cliente=cliente, estado=Venda.Estado.CONCLUIDA, subtotal=subtotal, desconto=desconto_total,
        total=total, frete=frete, observacao=str(dados.get("observacao", ""))[:2000],
    )
    for produto, quantidade, desconto, total_linha, fator in itens_calculados:
        ItemVenda.objects.create(
            empresa=empresa, venda=venda, produto=produto, quantidade=quantidade,
            custo_estoque_unitario=produto.custo_medio,
            preco_unitario=produto.preco_atual, desconto=desconto, fator_conversao=fator, total=total_linha,
        )
    for produto_id, quantidade_total in estoque_solicitado.items():
        produto = produtos_travados[produto_id]
        from .management_services import consumir_locais
        consumir_locais(produto, quantidade_total, dados.get('depositos_reserva',{}).get(str(produto.pk)))
        produto.estoque_atual -= quantidade_total
        produto.save(update_fields=("estoque_atual", "atualizado_em"))
        MovimentacaoEstoque.objects.create(
            empresa=empresa, produto=produto, tipo=MovimentacaoEstoque.Tipo.VENDA,
            quantidade=-quantidade_total, referencia=f"Venda #{venda.pk}", operador=usuario,
        )
    for meio, valor, recebido, troco, referencia in valores_pagamento:
        Transacao.objects.create(
            empresa=empresa, venda=venda, caixa=caixa, meio=meio, valor=valor,
            valor_recebido=recebido, troco=troco,
            referencia_externa=referencia[:120],
        )
    if credito > 0:
        ContaReceber.objects.create(
            empresa=empresa, cliente=cliente, venda=venda,
            valor_original=credito, saldo=credito, vencimento=vencimento,
            criado_por=usuario,
        )
    if credito_loja:
        CreditoLoja.objects.create(empresa=empresa,cliente=cliente,venda=venda,valor=-credito_loja)
    for cobranca in cobrancas_pix:
        cobranca.venda = venda
        cobranca.save(update_fields=("venda", "atualizada_em"))
    if aprovacao:
        aprovacao.venda = venda
        aprovacao.save(update_fields=['venda'])
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

    from .models import DevolucaoItem, OrdemEntrega
    if DevolucaoItem.objects.filter(empresa=empresa,item__venda=venda).exists():
        raise ValidationError('Venda com devolução parcial: trate os itens restantes pela central de devoluções.')
    if OrdemEntrega.objects.filter(empresa=empresa,venda=venda,itens__entregue__gt=0).exists():
        raise ValidationError('Venda com entrega registrada: utilize devolução por item.')
    from .access import caixa_para_movimento
    from .models import RecebimentoConta
    conta = ContaReceber.objects.select_for_update().filter(empresa=empresa, venda=venda).first()
    recebimentos = list(RecebimentoConta.objects.filter(conta=conta)) if conta else []
    pagamentos = list(venda.transacoes.filter(tipo=Transacao.Tipo.PAGAMENTO))
    # O dinheiro devolvido sai do caixa de quem cancela, não do caixa original da venda.
    caixa_estorno = caixa_para_movimento(
        empresa, usuario, [p.meio for p in pagamentos] + [r.meio for r in recebimentos],
        operacao="estornar pagamentos em dinheiro",
    )
    OrdemEntrega.objects.filter(empresa=empresa,venda=venda).update(estado='CANCELADO')
    from .models import CreditoLoja
    for debit in CreditoLoja.objects.filter(empresa=empresa,venda=venda,valor__lt=0):
        Cliente.objects.select_for_update().get(pk=debit.cliente_id)
        CreditoLoja.objects.create(empresa=empresa,cliente=debit.cliente,venda=venda,valor=-debit.valor)
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
        item.produto.estoque_atual += (item.quantidade * item.fator_conversao).quantize(Decimal("0.001"))
        item.produto.save(update_fields=("estoque_atual", "atualizado_em"))
        MovimentacaoEstoque.objects.create(
            empresa=empresa, produto=item.produto,
            tipo=MovimentacaoEstoque.Tipo.DEVOLUCAO,
            quantidade=(item.quantidade * item.fator_conversao).quantize(Decimal("0.001")), referencia=f"Estorno da venda #{venda.pk}",
            operador=usuario,
        )
    for pagamento in pagamentos:
        Transacao.objects.create(
            empresa=empresa, venda=venda, caixa=caixa_estorno, tipo=Transacao.Tipo.ESTORNO,
            meio=pagamento.meio, valor=pagamento.valor,
            referencia_externa=f"Estorno da venda #{venda.pk}"[:120],
        )
    if conta and conta.estado != ContaReceber.Estado.CANCELADA:
        # Parcelas já pagas pelo cliente são devolvidas; o saldo restante deixa de ser cobrado.
        for recebimento in recebimentos:
            Transacao.objects.create(
                empresa=empresa, venda=venda, caixa=caixa_estorno, tipo=Transacao.Tipo.ESTORNO,
                meio=recebimento.meio, valor=recebimento.valor,
                referencia_externa=f"Estorno do recebimento #{recebimento.pk} da venda #{venda.pk}"[:120],
            )
        conta.saldo = Decimal("0.00")
        conta.estado = ContaReceber.Estado.CANCELADA
        conta.save(update_fields=("saldo", "estado"))
    return venda
