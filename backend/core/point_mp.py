"""Maquininha Mercado Pago Point integrada ao PDV (API de integração Point, modo PDV).

O PDV pede a cobrança, o valor aparece na maquininha, o cliente passa o cartão e o PDV consulta
até o pagamento ser aprovado. A aprovação sempre é conferida em /v1/payments com o token da empresa.
"""
from decimal import Decimal, InvalidOperation
import uuid

from rest_framework.exceptions import ValidationError

from . import integracoes_http

API = "https://api.mercadopago.com"
TIPOS = {"CREDITO": "credit_card", "DEBITO": "debit_card"}
ESTADOS = {
    "OPEN": "PENDENTE", "ON_TERMINAL": "PENDENTE", "PROCESSING": "PENDENTE", "PROCESSED": "PENDENTE",
    "FINISHED": "FINALIZADO", "CANCELED": "CANCELADO", "ABANDONED": "CANCELADO", "ERROR": "ERRO",
}


def configurado(empresa):
    return bool(empresa.mp_access_token and empresa.mp_point_dispositivo)


def _chamar(empresa, metodo, caminho, corpo=None, cabecalhos=None):
    if not empresa.mp_access_token:
        raise ValidationError({"point": "Informe o access token do Mercado Pago em Dados da empresa."})
    try:
        status_http, resposta = integracoes_http.enviar(metodo, f"{API}{caminho}", corpo=corpo, token_bearer=empresa.mp_access_token,
                                                        cabecalhos=cabecalhos)
    except integracoes_http.ErroIntegracao as erro:
        raise ValidationError({"point": str(erro)})
    if status_http >= 400:
        raise ValidationError({"point": f"Mercado Pago: {resposta.get('message') or resposta.get('error') or status_http}"})
    return resposta


def dispositivos(empresa):
    resposta = _chamar(empresa, "GET", "/point/integration-api/devices?limit=50")
    return [{"id": d.get("id", ""), "operating_mode": d.get("operating_mode", ""), "pos_id": d.get("pos_id", "")}
            for d in (resposta.get("devices") or [])]


def ativar_modo_pdv(empresa):
    return _chamar(empresa, "PATCH", f"/point/integration-api/devices/{empresa.mp_point_dispositivo}",
                   corpo={"operating_mode": "PDV"})


def criar(empresa, valor, tipo, chave):
    if not configurado(empresa):
        raise ValidationError({"point": "Maquininha Point não vinculada: escolha o dispositivo em Dados da empresa."})
    try:
        valor = Decimal(str(valor)).quantize(Decimal("0.01"))
        chave = str(uuid.UUID(str(chave)))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError({"point": "Informe valor e chave válidos."})
    if not valor.is_finite() or valor < Decimal("1.00"):
        raise ValidationError({"point": "A maquininha aceita cobranças a partir de R$ 1,00."})
    if tipo not in TIPOS:
        raise ValidationError({"point": "Escolha crédito ou débito."})
    corpo = {
        "amount": int(valor * 100),
        "description": f"Venda {empresa.nome}"[:60],
        "payment": {"type": TIPOS[tipo], "installments": 1, "installments_cost": "seller"},
        "additional_info": {"external_reference": chave, "print_on_terminal": True},
    }
    resposta = _chamar(empresa, "POST", f"/point/integration-api/devices/{empresa.mp_point_dispositivo}/payment-intents",
                       corpo=corpo, cabecalhos={"X-Idempotency-Key": chave})
    if not resposta.get("id"):
        raise ValidationError({"point": "A maquininha não aceitou a cobrança. Confira se ela está ligada e no modo PDV."})
    return {"id": str(resposta["id"]), "estado": "PENDENTE", "valor": str(valor), "tipo": tipo, "pagamento": ""}


def consultar(empresa, intencao_id):
    intencao = _chamar(empresa, "GET", f"/point/integration-api/payment-intents/{intencao_id}")
    estado = ESTADOS.get(str(intencao.get("state", "")).upper(), "PENDENTE")
    try:
        valor = (Decimal(str(intencao.get("amount", 0))) / 100).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        valor = Decimal("0")
    resultado = {"id": str(intencao_id), "estado": estado, "valor": str(valor), "pagamento": ""}
    pagamento_id = str((intencao.get("payment") or {}).get("id") or "")
    if estado == "FINALIZADO":
        resultado["estado"] = "RECUSADO"
        if pagamento_id.isdigit():
            pagamento = _chamar(empresa, "GET", f"/v1/payments/{pagamento_id}")
            try:
                pago = Decimal(str(pagamento.get("transaction_amount", "0"))).quantize(Decimal("0.01"))
            except (InvalidOperation, ValueError):
                pago = Decimal("0")
            if pagamento.get("status") == "approved" and pago == valor:
                resultado.update(estado="APROVADO", pagamento=pagamento_id,
                                 bandeira=str(pagamento.get("payment_method_id", ""))[:20],
                                 autorizacao=str(pagamento.get("authorization_code") or "")[:20])
    return resultado


def cancelar(empresa, intencao_id):
    _chamar(empresa, "DELETE", f"/point/integration-api/devices/{empresa.mp_point_dispositivo}/payment-intents/{intencao_id}")
    return {"id": str(intencao_id), "estado": "CANCELADO"}
