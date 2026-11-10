"""Regras de acesso compartilhadas pela API, pelo painel web e pela sessão desktop."""
from django.db import transaction
from rest_framework.exceptions import ValidationError


def conta_ativa(usuario):
    """Usuário Django ativo e sem bloqueio no perfil do ApPdv."""
    if not usuario or not usuario.is_active:
        return False
    perfil = getattr(usuario, "perfil_armazem", None)
    return not (perfil and not perfil.ativo)


def caixa_aberto(empresa, usuario, *, lock=True):
    """Caixa aberto do usuário nesta empresa, ou None."""
    from .models import Caixa
    qs = Caixa.objects.filter(empresa=empresa, operador=usuario, estado=Caixa.Estado.ABERTO).order_by("-pk")
    if lock and transaction.get_connection().in_atomic_block:
        qs = qs.select_for_update()
    return qs.first()


def caixa_para_movimento(empresa, usuario, meios, *, caixa_id=None, operacao="devolver dinheiro"):
    """Escolhe o caixa onde um estorno/recebimento será apurado.

    Dinheiro físico sempre passa por um caixa aberto de quem executa a operação.
    Para outros meios o caixa é registrado quando existir, mas não é obrigatório.
    """
    from .models import Caixa, Transacao
    if caixa_id not in (None, ""):
        try:
            caixa = Caixa.objects.select_for_update().get(
                pk=caixa_id, empresa=empresa, operador=usuario, estado=Caixa.Estado.ABERTO,
            )
        except (Caixa.DoesNotExist, ValueError, TypeError):
            raise ValidationError({"caixa": "Caixa aberto do operador não encontrado nesta empresa."})
        return caixa
    caixa = caixa_aberto(empresa, usuario)
    if caixa is None and Transacao.Meio.DINHEIRO in set(meios):
        raise ValidationError({
            "caixa": f"Abra o seu caixa para {operacao}. Valores em dinheiro são apurados no caixa de quem executa a operação."
        })
    return caixa


def mensagem_erro(exc):
    """Texto legível para mensagens do painel a partir de erros do DRF ou comuns."""
    detail = getattr(exc, "detail", None)
    if detail is None:
        return str(exc)
    partes = []

    def coletar(valor):
        if isinstance(valor, dict):
            for item in valor.values():
                coletar(item)
        elif isinstance(valor, (list, tuple)):
            for item in valor:
                coletar(item)
        else:
            partes.append(str(valor))
    coletar(detail)
    return " ".join(partes) or str(exc)
