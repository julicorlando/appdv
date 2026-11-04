import re
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from .models import Cliente, Orcamento, Produto


def normalizar_cpf(valor):
    cpf = re.sub(r'[.\-\s]', '', str(valor))
    if len(cpf) != 11 or not cpf.isascii() or not cpf.isdigit() or len(set(cpf)) == 1:
        raise ValidationError({'cpf': 'Informe um CPF válido.'})
    for size in (9, 10):
        digit = (sum(int(cpf[i]) * (size + 1 - i) for i in range(size)) * 10 % 11) % 10
        if digit != int(cpf[size]):
            raise ValidationError({'cpf': 'CPF com dígitos verificadores inválidos.'})
    return cpf


def dados_cliente(cliente):
    return {'id': cliente.pk, 'nome': cliente.nome, 'documento': cliente.documento,
            'telefone': cliente.telefone, 'limite_credito': str(cliente.limite_credito)}


def dados_orcamento(obj):
    estado = 'CONVERTIDO' if obj.venda_id else 'CANCELADO' if obj.cancelado else 'VENCIDO' if obj.validade < timezone.localdate() else 'ABERTO'
    return {'id': obj.pk, 'cliente': dados_cliente(obj.cliente), 'itens': obj.itens,
            'total': str(obj.total), 'validade': obj.validade.isoformat(), 'estado': estado,
            'venda': obj.venda_id, 'criado_em': obj.criado_em.isoformat()}


@transaction.atomic
def salvar_orcamento(empresa, membro, dados):
    try:
        chave = uuid.UUID(str(dados.get('chave')))
        validade = date.fromisoformat(str(dados.get('validade')))
        cliente = Cliente.objects.get(pk=dados.get('cliente'), empresa=empresa, ativo=True)
    except (ValueError, TypeError, Cliente.DoesNotExist):
        raise ValidationError({'orcamento': 'Informe cliente, chave UUID e validade válidos.'})
    # Serialize same-company creates, including retry after a lost response.
    from .models import Empresa
    Empresa.objects.select_for_update().get(pk=empresa.pk)
    existente = Orcamento.objects.filter(empresa=empresa, chave=chave).first()
    if existente:
        if existente.operador_id != membro.usuario_id:
            raise ValidationError({'chave': 'Chave já utilizada por outro operador.'})
        return existente, False
    if validade < timezone.localdate():
        raise ValidationError({'validade': 'A validade deve ser hoje ou uma data futura.'})
    linhas = dados.get('itens')
    if not isinstance(linhas, list) or not linhas or len(linhas) > 500:
        raise ValidationError({'itens': 'Informe de 1 a 500 itens.'})
    itens, vistos, total = [], set(), Decimal('0')
    for linha in linhas:
        try:
            produto = Produto.objects.get(pk=linha['produto'], empresa=empresa, ativo=True)
            quantidade = Decimal(str(linha['quantidade']))
            desconto = Decimal(str(linha.get('desconto', '0')))
            if not quantidade.is_finite() or quantidade <= 0 or quantidade.as_tuple().exponent < -3 or not desconto.is_finite():
                raise ValueError
            bruto = (produto.preco_venda * quantidade).quantize(Decimal('.01'))
            if desconto < 0 or desconto > bruto or produto.pk in vistos or (desconto > 0 and membro.papel == 'CAIXA'):
                raise ValueError
            valor = bruto - desconto
            if valor > Decimal('9999999999.99'):
                raise ValueError
        except (KeyError, TypeError, ValueError, InvalidOperation, Produto.DoesNotExist):
            raise ValidationError({'itens': 'Produto, quantidade ou desconto inválido ou não autorizado.'})
        vistos.add(produto.pk)
        itens.append({'produto': produto.pk, 'codigo': produto.codigo, 'nome': produto.nome,
            'unidade': produto.unidade, 'quantidade': str(quantidade), 'preco_venda': str(produto.preco_venda),
            'desconto': str(desconto), 'total': str(valor)})
        total += valor
    if total > Decimal('9999999999.99'):
        raise ValidationError({'total': 'Total acima do limite permitido.'})
    obj = Orcamento.objects.create(empresa=empresa, cliente=cliente, operador=membro.usuario,
        chave=chave, validade=validade, itens=itens, total=total)
    return obj, True
