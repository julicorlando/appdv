"""PIX dinâmico pelo Mercado Pago (API /v1/payments).

A confirmação do pagamento vem sempre de uma consulta à API do Mercado Pago com o
token da empresa; o corpo do webhook é tratado só como aviso para consultar.
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import uuid

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from . import integracoes_http
from .models import CobrancaPix

API = "https://api.mercadopago.com"
VALIDADE_MINUTOS = 15
ESTADOS = {
    "approved": CobrancaPix.Estado.APROVADO,
    "pending": CobrancaPix.Estado.PENDENTE,
    "in_process": CobrancaPix.Estado.PENDENTE,
    "cancelled": CobrancaPix.Estado.CANCELADO,
    "rejected": CobrancaPix.Estado.CANCELADO,
    "expired": CobrancaPix.Estado.EXPIRADO,
}


def configurado(empresa):
    return bool(empresa.mp_access_token)


def criar(empresa, operador, valor, chave, email_pagador="", url_notificacao=""):
    if not configurado(empresa):
        raise ValidationError({"pix": "PIX automático não configurado: informe o access token do Mercado Pago em Dados da empresa."})
    try:
        valor = Decimal(str(valor)).quantize(Decimal("0.01"))
        chave = uuid.UUID(str(chave))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError({"pix": "Informe valor e chave válidos."})
    if not valor.is_finite() or valor <= 0:
        raise ValidationError({"pix": "O valor do PIX precisa ser maior que zero."})
    existente = CobrancaPix.objects.filter(chave=chave).first()
    if existente:
        if existente.empresa_id != empresa.pk or existente.operador_id != operador.pk:
            raise ValidationError({"pix": "Esta chave já foi usada."})
        return existente
    email = email_pagador or empresa.email
    if not email:
        raise ValidationError({"pix": "Cadastre o e-mail da empresa: o Mercado Pago exige um e-mail do pagador."})
    expira = timezone.now() + timedelta(minutes=VALIDADE_MINUTOS)
    corpo = {
        "transaction_amount": float(valor),
        "description": f"Venda {empresa.nome}"[:200],
        "payment_method_id": "pix",
        "payer": {"email": email},
        "external_reference": str(chave),
        "date_of_expiration": expira.isoformat(timespec="milliseconds"),
    }
    if url_notificacao.startswith("https://"):
        corpo["notification_url"] = url_notificacao
    try:
        status_http, resposta = integracoes_http.enviar(
            "POST", f"{API}/v1/payments", corpo=corpo, token_bearer=empresa.mp_access_token,
            cabecalhos={"X-Idempotency-Key": str(chave)},
        )
    except integracoes_http.ErroIntegracao as erro:
        raise ValidationError({"pix": str(erro)})
    if status_http >= 400 or not resposta.get("id"):
        raise ValidationError({"pix": f"Mercado Pago recusou a cobrança: {resposta.get('message') or status_http}"})
    dados = (resposta.get("point_of_interaction") or {}).get("transaction_data") or {}
    return CobrancaPix.objects.create(
        empresa=empresa, operador=operador, chave=chave, provedor_id=str(resposta["id"]),
        valor=valor, estado=ESTADOS.get(resposta.get("status"), CobrancaPix.Estado.PENDENTE),
        qr_code=dados.get("qr_code", ""), qr_base64=dados.get("qr_code_base64", ""), expira_em=expira,
    )


def atualizar(cobranca):
    """Consulta o Mercado Pago e grava o estado. Cobrança paga nunca volta a pendente."""
    if cobranca.estado != CobrancaPix.Estado.PENDENTE or not cobranca.provedor_id:
        return cobranca
    try:
        status_http, resposta = integracoes_http.enviar(
            "GET", f"{API}/v1/payments/{cobranca.provedor_id}", token_bearer=cobranca.empresa.mp_access_token,
        )
    except integracoes_http.ErroIntegracao:
        return cobranca
    if status_http >= 400:
        return cobranca
    estado = ESTADOS.get(resposta.get("status"), CobrancaPix.Estado.PENDENTE)
    try:
        pago = Decimal(str(resposta.get("transaction_amount", "0"))).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        pago = Decimal("0")
    if estado == CobrancaPix.Estado.APROVADO and pago != cobranca.valor:
        return cobranca
    if estado == CobrancaPix.Estado.PENDENTE and cobranca.expira_em <= timezone.now():
        estado = CobrancaPix.Estado.EXPIRADO
    with transaction.atomic():
        atual = CobrancaPix.objects.select_for_update().get(pk=cobranca.pk)
        if atual.estado == CobrancaPix.Estado.PENDENTE and estado != atual.estado:
            atual.estado = estado
            atual.save(update_fields=("estado", "atualizada_em"))
    cobranca.refresh_from_db()
    return cobranca


def resumo(cobranca):
    return {
        "id": cobranca.pk, "estado": cobranca.estado, "estado_texto": cobranca.get_estado_display(),
        "valor": str(cobranca.valor), "qr_code": cobranca.qr_code, "qr_base64": cobranca.qr_base64,
        "expira_em": cobranca.expira_em, "venda": cobranca.venda_id,
    }
