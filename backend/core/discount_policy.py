import hashlib
import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from django.utils import timezone
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError
from .models import AprovacaoDesconto


def fingerprint(itens):
    try:
        data = sorted((int(i['produto']), str(Decimal(str(i['quantidade'])).normalize()), str(Decimal(str(i.get('desconto', '0'))).normalize())) for i in itens)
    except (ValueError, TypeError, KeyError, InvalidOperation):
        raise ValidationError({'itens': 'Itens inválidos.'})
    return hashlib.sha256(json.dumps(data).encode()).hexdigest()


def validar_desconto(empresa, usuario, dados, bruto, desconto, key_field='idempotency_key'):
    if desconto * 100 <= bruto * 5:
        return None
    try:
        approval = AprovacaoDesconto.objects.select_for_update().get(
            codigo=dados.get('autorizacao_desconto'), empresa=empresa, operador=usuario,
            chave_operacao=dados.get(key_field), resumo_itens=fingerprint(dados.get('itens', [])))
    except (AprovacaoDesconto.DoesNotExist, ValueError, TypeError, ValidationError, DjangoValidationError):
        raise ValidationError({'desconto': 'Acima de 5% é necessária autorização de gerente ou administrador para esta operação.'})
    if approval.venda_id or approval.criada_em < timezone.now() - timedelta(days=1):
        raise ValidationError({'desconto': 'Autorização expirada ou já utilizada.'})
    if not approval.aprovador.is_active or not approval.aprovador.empresas_vinculadas.filter(empresa=empresa, ativo=True, papel__in=['ADMIN', 'GERENTE']).exists():
        raise ValidationError({'desconto': 'O aprovador não possui mais permissão nesta empresa.'})
    return approval
