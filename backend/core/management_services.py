from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.db.models import Sum, F
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from .models import *

ZERO = Decimal('0')


def quantity(raw):
    try:
        value = Decimal(str(raw))
        if not value.is_finite() or value <= 0 or value.as_tuple().exponent < -3:
            raise ValueError
        return value
    except (ValueError, TypeError, InvalidOperation):
        raise ValidationError('Informe uma quantidade positiva, com até três casas decimais.')


def audit(empresa, usuario, acao, referencia, **details):
    EventoGestao.objects.create(empresa=empresa, usuario=usuario, acao=acao, referencia=str(referencia), detalhes=details)


def principal(empresa):
    return Deposito.objects.get_or_create(empresa=empresa, principal=True, defaults={'nome':'Principal'})[0]


def reservado(produto, deposito=None):
    qs = ItemEntrega.objects.filter(produto=produto, ordem__venda__isnull=True, ordem__orcamento__validade__gte=timezone.localdate(), ordem__estado__in=['AGUARDANDO','PRONTO','EM_ROTA','PARCIAL'])
    if deposito:
        qs = qs.filter(deposito=deposito)
    return sum(((i.quantidade - i.entregue) * i.fator for i in qs), ZERO)


def saldo_local(produto, deposito):
    if deposito.empresa_id != produto.empresa_id:
        raise ValidationError('Depósito de outra empresa.')
    if deposito.principal:
        others = SaldoDeposito.objects.filter(produto=produto, deposito__empresa=produto.empresa).aggregate(n=Sum('quantidade'))['n'] or ZERO
        return produto.estoque_atual - others
    obj = SaldoDeposito.objects.filter(produto=produto, deposito=deposito).first()
    return obj.quantidade if obj else ZERO


def consumir_locais(produto, amount, deposito_id=None):
    if produto.estoque_atual - reservado(produto) < amount:
        raise ValidationError(f'Estoque disponível insuficiente: {produto.nome}. Há reservas ou saldo insuficiente.')
    if deposito_id:
        location=Deposito.objects.get(pk=deposito_id,empresa=produto.empresa)
        if saldo_local(produto,location)-reservado(produto,location)<amount:raise ValidationError('Saldo do depósito reservado insuficiente.')
        if not location.principal:
            row=SaldoDeposito.objects.select_for_update().get(produto=produto,deposito=location)
            row.quantidade-=amount;row.save(update_fields=['quantidade'])
        return
    remaining = amount
    locations = list(Deposito.objects.filter(empresa=produto.empresa).order_by('-principal','pk'))
    if not locations:
        locations = [principal(produto.empresa)]
    for location in locations:
        available = max(ZERO, saldo_local(produto, location) - reservado(produto, location))
        taken = min(remaining, available)
        if taken and not location.principal:
            row = SaldoDeposito.objects.select_for_update().get(produto=produto, deposito=location)
            row.quantidade -= taken
            row.save(update_fields=['quantidade'])
        remaining -= taken
        if remaining <= 0:
            return
    if remaining > 0:
        raise ValidationError('Saldo por depósito inconsistente. Revise os saldos antes de movimentar.')


@transaction.atomic
def transferir(empresa, usuario, produto_id, origem_id, destino_id, raw):
    produto = Produto.objects.select_for_update().get(pk=produto_id, empresa=empresa)
    origem = Deposito.objects.get(pk=origem_id, empresa=empresa)
    destino = Deposito.objects.get(pk=destino_id, empresa=empresa)
    amount = quantity(raw)
    if origem.pk == destino.pk or saldo_local(produto, origem) - reservado(produto, origem) < amount:
        raise ValidationError('Depósitos iguais ou saldo livre insuficiente na origem.')
    for location, delta in [(origem,-amount),(destino,amount)]:
        if not location.principal:
            row, _ = SaldoDeposito.objects.get_or_create(produto=produto, deposito=location)
            row.quantidade += delta
            row.save()
    audit(empresa,usuario,'TRANSFERENCIA',produto.pk,origem=origem.nome,destino=destino.nome,quantidade=str(amount))


@transaction.atomic
def reservar_orcamento(empresa, usuario, quote_id, deposito_id, endereco='', prevista=None, responsavel=''):
    quote = Orcamento.objects.select_for_update().get(pk=quote_id,empresa=empresa)
    if quote.cancelado or quote.validade < timezone.localdate():
        raise ValidationError('Orçamento cancelado ou vencido.')
    existing = OrdemEntrega.objects.filter(orcamento=quote).first()
    if existing:
        return existing
    location = Deposito.objects.get(pk=deposito_id, empresa=empresa)
    order = OrdemEntrega.objects.create(empresa=empresa,orcamento=quote,venda=quote.venda,endereco=endereco,prevista=prevista,responsavel=responsavel,frete=empresa.frete_padrao if endereco else ZERO)
    for line in sorted(quote.itens, key=lambda l:l['produto']):
        product = Produto.objects.select_for_update().get(pk=line['produto'],empresa=empresa,ativo=True)
        q = quantity(line['quantidade'])
        if not quote.venda_id and saldo_local(product,location) - reservado(product,location) < q * product.fator_conversao:
            raise ValidationError(f'Saldo livre insuficiente em {location.nome} para {product.nome}.')
        ItemEntrega.objects.create(ordem=order,produto=product,deposito=location,quantidade=q,fator=product.fator_conversao)
    audit(empresa,usuario,'RESERVA_ENTREGA',order.pk,orcamento=quote.pk)
    return order


@transaction.atomic
def registrar_entrega(empresa, usuario, order_id, items, recebido_por):
    order = OrdemEntrega.objects.select_for_update().get(pk=order_id,empresa=empresa)
    if not order.venda_id or order.venda.estado != Venda.Estado.CONCLUIDA or order.estado in ['ENTREGUE','CANCELADO'] or not recebido_por.strip():
        raise ValidationError('Confirme o pagamento/venda, o estado do pedido e o nome de quem recebeu.')
    changed = False
    for item in order.itens.select_for_update():
        raw = items.get(str(item.pk), '')
        if raw in ('', '0', '0.000'):
            continue
        q = quantity(raw)
        if q > item.quantidade - item.entregue:
            raise ValidationError('Quantidade maior que o saldo a entregar.')
        item.entregue += q
        item.save(update_fields=['entregue'])
        RegistroEntrega.objects.create(item=item,quantidade=q,recebido_por=recebido_por,usuario=usuario)
        changed = True
    if not changed:
        raise ValidationError('Informe pelo menos uma quantidade para entrega.')
    order.estado = 'ENTREGUE' if not order.itens.filter(entregue__lt=F('quantidade')).exists() else 'PARCIAL'
    order.save(update_fields=['estado'])
    audit(empresa,usuario,'ENTREGA_PARCIAL',order.pk,recebido_por=recebido_por)
    return order


@transaction.atomic
def devolver(empresa, usuario, item_id, raw, destino, financeiro, motivo, chave, meio="OUTRO"):
    existing = DevolucaoItem.objects.filter(empresa=empresa,chave=chave).first()
    if existing:
        return existing
    item = ItemVenda.objects.select_for_update().select_related('venda','produto').get(pk=item_id,empresa=empresa)
    existing = DevolucaoItem.objects.filter(empresa=empresa,chave=chave).first()
    if existing:
        return existing
    q = quantity(raw)
    prior = item.devolucoes.aggregate(q=Sum('quantidade'),v=Sum('valor'))
    remaining = item.quantidade - (prior['q'] or ZERO)
    if item.venda.estado != Venda.Estado.CONCLUIDA or q > remaining or not motivo.strip() or destino not in ['ESTOQUE','PERDA'] or financeiro not in ['CREDITO','ESTORNO','ABATIMENTO']:
        raise ValidationError('Venda, quantidade, motivo ou destino inválido.')
    amount = item.total - (prior['v'] or ZERO) if q == remaining else (item.total * q / item.quantidade).quantize(Decimal('.01'))
    if financeiro == 'CREDITO' and not item.venda.cliente_id:
        raise ValidationError('A venda precisa ter cliente identificado para gerar crédito.')
    result = DevolucaoItem.objects.create(empresa=empresa,item=item,quantidade=q,valor=amount,destino=destino,financeiro=financeiro,motivo=motivo,usuario=usuario,chave=chave,meio_estorno=meio)
    if financeiro == 'CREDITO':
        Cliente.objects.select_for_update().get(pk=item.venda.cliente_id)
        CreditoLoja.objects.create(empresa=empresa,cliente=item.venda.cliente,devolucao=result,valor=amount)
    elif financeiro == 'ABATIMENTO':
        account = ContaReceber.objects.select_for_update().filter(empresa=empresa,venda=item.venda,estado__in=['ABERTA','PARCIAL']).first()
        if not account or amount > account.saldo:
            raise ValidationError('Saldo a receber insuficiente para o abatimento. Escolha outro destino financeiro.')
        account.saldo -= amount
        account.estado = 'QUITADA' if account.saldo == 0 else 'PARCIAL'
        account.save(update_fields=['saldo','estado'])
    if financeiro=='ESTORNO':
        if meio not in ('DINHEIRO','PIX','CARTAO','OUTRO'):raise ValidationError('Meio de estorno inválido.')
        from .access import caixa_para_movimento
        caixa = caixa_para_movimento(empresa, usuario, [meio], operacao='devolver dinheiro ao cliente')
        Transacao.objects.create(empresa=empresa,venda=item.venda,caixa=caixa,tipo='ESTORNO',meio=meio,valor=amount,referencia_externa=f'Devolução #{result.pk} — estorno manual')
    if destino == 'ESTOQUE':
        product = Produto.objects.select_for_update().get(pk=item.produto_id,empresa=empresa)
        stock = (q * item.fator_conversao).quantize(Decimal('.001'))
        product.estoque_atual += stock
        product.save(update_fields=['estoque_atual','atualizado_em'])
        MovimentacaoEstoque.objects.create(empresa=empresa,produto=product,tipo='DEVOLUCAO',quantidade=stock,referencia=f'Devolução #{result.pk}',operador=usuario)
    audit(empresa,usuario,'DEVOLUCAO',result.pk,valor=str(amount),financeiro=financeiro,motivo=motivo)
    return result


@transaction.atomic
def confirmar_compra_pedido(empresa, usuario, nota_id, pedido_id, aceitar_custo=False):
    from .purchases import confirmar_recebimento
    pedido = PedidoCompra.objects.select_for_update().get(pk=pedido_id,empresa=empresa)
    nota = CompraEntrada.objects.select_for_update().get(pk=nota_id,empresa=empresa)
    if nota.estado == 'RECEBIDA':
        if nota.pedido_compra_id == pedido.pk:
            return nota
        raise ValidationError('Esta nota já foi recebida fora deste pedido.')
    if pedido.estado not in ['ABERTO','PARCIAL'] or nota.fornecedor_id != pedido.fornecedor_id:
        raise ValidationError('Pedido encerrado ou fornecedor da nota diferente do pedido.')
    totals = {}
    for item in nota.itens.all():
        if not item.produto_id:
            raise ValidationError('Associe todos os produtos do XML antes de conferir com o pedido.')
        totals[item.produto_id] = totals.get(item.produto_id,ZERO) + item.quantidade
    for pk, q in totals.items():
        row = pedido.itens.select_for_update().filter(produto_id=pk).first()
        if not row or q > row.quantidade - row.recebido:
            raise ValidationError('Nota contém produto não pedido ou quantidade acima do saldo. Revise o pedido antes de receber.')
    for item in nota.itens.all():
        expected=pedido.itens.get(produto_id=item.produto_id)
        if item.custo_unitario!=expected.custo and not aceitar_custo:
            raise ValidationError('Custo do XML difere do pedido. Confira a diferença e marque a aceitação do custo para receber.')
    confirmar_recebimento(compra_id=nota.pk,empresa=empresa,usuario=usuario)
    for pk,q in totals.items():
        row = pedido.itens.get(produto_id=pk)
        row.recebido += q
        row.save(update_fields=['recebido'])
    nota.pedido_compra = pedido
    nota.save(update_fields=['pedido_compra'])
    pedido.estado = 'RECEBIDO' if not pedido.itens.filter(recebido__lt=F('quantidade')).exists() else 'PARCIAL'
    pedido.save(update_fields=['estado'])
    audit(empresa,usuario,'RECEBIMENTO_PEDIDO',pedido.pk,nota=nota.pk)
    return nota


@transaction.atomic
def vender_orcamento(empresa,usuario,dados):
    import uuid
    from .services import registrar_venda
    try:
        key=uuid.UUID(str(dados.get('idempotency_key')))
        quote=Orcamento.objects.select_for_update().get(pk=dados.get('orcamento'),empresa=empresa)
    except (ValueError,TypeError,Orcamento.DoesNotExist):
        raise ValidationError('Orçamento ou chave inválidos.')
    if quote.venda_id:
        if quote.venda.operador_id==usuario.pk and quote.venda.idempotency_key==key:return quote.venda,False
        raise ValidationError('Orçamento já convertido.')
    if quote.cancelado or quote.validade<timezone.localdate():raise ValidationError('Orçamento vencido ou cancelado.')
    from .discount_policy import fingerprint
    if str(dados.get('cliente'))!=str(quote.cliente_id) or fingerprint(dados.get('itens',[]))!=fingerprint(quote.itens):
        raise ValidationError('Cliente ou itens diferem do orçamento. Salve uma revisão.')
    if Venda.objects.filter(empresa=empresa,idempotency_key=key).exists():raise ValidationError('Chave já usada por outra venda.')
    try:Caixa.objects.select_for_update().get(pk=dados.get('caixa'),empresa=empresa,operador=usuario,estado='ABERTO')
    except (Caixa.DoesNotExist,ValueError,TypeError):raise ValidationError('Abra seu caixa antes de vender.')
    order=OrdemEntrega.objects.select_for_update().filter(orcamento=quote).first()
    frete=order.frete if order else ZERO
    products={p.pk:p for p in Produto.objects.select_for_update().filter(empresa=empresa,ativo=True,pk__in=[i['produto'] for i in quote.itens]).order_by('pk')}
    if len(products)!=len(quote.itens):raise ValidationError('Há produto indisponível no orçamento.')
    current=sum(((products[i['produto']].preco_atual*Decimal(i['quantidade'])).quantize(Decimal('.01'))-Decimal(i['desconto']) for i in quote.itens),ZERO)+frete
    if str(dados.get('total_confirmado'))!=str(current.quantize(Decimal('.01'))):raise ValidationError('Preços ou frete alterados. Reabra o orçamento antes de cobrar.')
    payload=dict(dados)
    if order:
        if any(i.fator != products[i.produto_id].fator_conversao for i in order.itens.all()):raise ValidationError('Conversão de unidade mudou. Revise o pedido reservado.')
        if order.estado=='CANCELADO':raise ValidationError('A entrega foi cancelada. Revise o orçamento.')
        order.estado='PAGANDO';order.save(update_fields=['estado'])
        payload['depositos_reserva']={str(i.produto_id):i.deposito_id for i in order.itens.all()}
    sale,created=registrar_venda(empresa=empresa,usuario=usuario,dados=payload,frete=frete)
    quote.venda=sale;quote.save(update_fields=['venda'])
    if order:order.venda=sale;order.estado='AGUARDANDO';order.save(update_fields=['venda','estado'])
    audit(empresa,usuario,'ORCAMENTO_CONVERTIDO',quote.pk,venda=sale.pk)
    return sale,created
