"""Emissão de NFC-e pela API da Focus NFe (https://focusnfe.com.br).

Fluxo: montar o JSON a partir da venda, enviar para /v2/nfce?ref=..., guardar o
retorno em DocumentoFiscal. Nada é simulado: sem token ou com dados fiscais
incompletos, a emissão é recusada com a lista do que falta.
"""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from . import integracoes_http
from .models import DocumentoFiscal, Transacao, Venda  # noqa: F401

URL_HOMOLOGACAO = "https://homologacao.focusnfe.com.br"
URL_PRODUCAO = "https://api.focusnfe.com.br"

ESTADOS = {
    "autorizado": "AUTORIZADO",
    "processando_autorizacao": "PROCESSANDO",
    "erro_autorizacao": "REJEITADO",
    "denegado": "REJEITADO",
    "cancelado": "CANCELADO",
}


def base_url(empresa):
    return URL_PRODUCAO if empresa.ambiente_fiscal == "PRODUCAO" else URL_HOMOLOGACAO


def configurado(empresa):
    return bool(empresa.focus_token)


def pendencias(venda):
    """Lista do que impede a emissão, em linguagem do usuário."""
    empresa = venda.empresa
    faltas = []
    if not empresa.focus_token:
        faltas.append("Token da Focus NFe em Dados da empresa.")
    if not (empresa.cnpj and len(empresa.cnpj) == 14):
        faltas.append("CNPJ da empresa.")
    if not empresa.pis_cofins_cst:
        faltas.append("CST de PIS/COFINS em Dados da empresa (confirme com o contador).")
    if venda.estado != Venda.Estado.CONCLUIDA:
        faltas.append("A venda precisa estar concluída.")
    for item in venda.itens.select_related("produto"):
        produto = item.produto
        if not (produto.ncm.isdigit() and len(produto.ncm) == 8):
            faltas.append(f"NCM (8 dígitos) do produto {produto.codigo} — {produto.nome}.")
        if not (produto.cfop.isdigit() and len(produto.cfop) == 4):
            faltas.append(f"CFOP do produto {produto.codigo}.")
    return faltas


def _forma_pagamento(transacao):
    referencia = (transacao.referencia_externa or "").lower()
    if transacao.meio == Transacao.Meio.DINHEIRO:
        return "01"
    if transacao.meio == Transacao.Meio.CARTAO:
        return "04" if "débito" in referencia or "debito" in referencia else "03"
    if transacao.meio == Transacao.Meio.PIX:
        return "17" if referencia.startswith("pix mercado pago") else "20"
    if transacao.meio == "LOJA":
        return "05"
    return "99"


def montar_nfce(venda):
    empresa = venda.empresa
    itens = []
    for numero, item in enumerate(venda.itens.select_related("produto").order_by("pk"), 1):
        produto = item.produto
        bruto = (item.preco_unitario * item.quantidade).quantize(Decimal("0.01"))
        linha = {
            "numero_item": numero,
            "codigo_produto": produto.codigo,
            "descricao": produto.nome[:120],
            "codigo_ncm": produto.ncm,
            "cfop": produto.cfop,
            "unidade_comercial": produto.unidade or "UN",
            "quantidade_comercial": f"{item.quantidade:.4f}",
            "valor_unitario_comercial": f"{item.preco_unitario:.2f}",
            "unidade_tributavel": produto.unidade or "UN",
            "quantidade_tributavel": f"{item.quantidade:.4f}",
            "valor_unitario_tributavel": f"{item.preco_unitario:.2f}",
            "valor_bruto": f"{bruto:.2f}",
            "icms_origem": produto.origem,
            "icms_situacao_tributaria": produto.csosn,
            "pis_situacao_tributaria": empresa.pis_cofins_cst,
            "cofins_situacao_tributaria": empresa.pis_cofins_cst,
        }
        if produto.cest:
            linha["cest"] = produto.cest
        if item.desconto:
            linha["valor_desconto"] = f"{item.desconto:.2f}"
        itens.append(linha)
    if venda.frete and itens:
        itens[0]["valor_outras_despesas"] = f"{venda.frete:.2f}"
    pagamentos = list(venda.transacoes.filter(tipo=Transacao.Tipo.PAGAMENTO))
    formas = [{"forma_pagamento": _forma_pagamento(t), "valor_pagamento": f"{t.valor_recebido or t.valor:.2f}"} for t in pagamentos]
    a_prazo = venda.total - sum((t.valor for t in pagamentos), Decimal("0"))
    if a_prazo > 0:
        formas.append({"forma_pagamento": "05", "valor_pagamento": f"{a_prazo:.2f}"})
    nota = {
        "cnpj_emitente": empresa.cnpj,
        "data_emissao": timezone.localtime().isoformat(timespec="seconds"),
        "natureza_operacao": "VENDA AO CONSUMIDOR",
        "presenca_comprador": "1",
        "consumidor_final": "1",
        "modalidade_frete": "9",
        "local_destino": "1",
        "items": itens,
        "formas_pagamento": formas,
    }
    troco = sum((t.troco for t in pagamentos), Decimal("0"))
    if troco:
        nota["valor_troco"] = f"{troco:.2f}"
    cliente = venda.cliente
    if cliente and cliente.documento:
        documento = "".join(ch for ch in cliente.documento if ch.isdigit())
        if len(documento) == 11:
            nota["cpf_destinatario"] = documento
        elif len(documento) == 14:
            nota["cnpj_destinatario"] = documento
        if len(documento) in (11, 14):
            nota["nome_destinatario"] = cliente.nome[:60]
    return nota


def _aplicar_retorno(documento, empresa, status_http, resposta):
    estado = ESTADOS.get(str(resposta.get("status", "")))
    if estado is None:
        estado = "REJEITADO" if status_http >= 400 else "PROCESSANDO"
    mensagens = [resposta.get("mensagem_sefaz") or resposta.get("mensagem") or ""]
    for erro in resposta.get("erros") or []:
        if isinstance(erro, dict):
            mensagens.append(erro.get("mensagem", ""))
    documento.estado = estado
    documento.mensagem = " ".join(m for m in mensagens if m)[:2000] or f"Retorno HTTP {status_http}."
    documento.chave = str(resposta.get("chave_nfe", documento.chave or ""))[-44:]
    documento.numero = str(resposta.get("numero", documento.numero or ""))[:20]
    documento.serie = str(resposta.get("serie", documento.serie or ""))[:5]
    documento.protocolo = str(resposta.get("protocolo", documento.protocolo or ""))[:80]
    if resposta.get("caminho_danfe"):
        documento.danfe_url = base_url(empresa) + str(resposta["caminho_danfe"])
    if resposta.get("caminho_xml_nota_fiscal"):
        documento.xml_url = base_url(empresa) + str(resposta["caminho_xml_nota_fiscal"])
    documento.save()
    return documento


def emitir(venda):
    """Emite a NFC-e da venda. Nunca gera nota duplicada: se já houve envio, consulta antes.

    - sem envio anterior: envia com referência nova;
    - envio anterior desconhecido pela Focus (falha de rede antes de chegar): reenvia a mesma referência;
    - envio anterior rejeitado: envia com a próxima referência;
    - nota autorizada, em processamento ou cancelada: apenas devolve o documento.
    """
    empresa = venda.empresa
    documento, _ = DocumentoFiscal.objects.get_or_create(empresa=empresa, venda=venda)
    if documento.estado in ("AUTORIZADO", "CANCELADO"):
        return documento
    faltas = pendencias(venda)
    if faltas:
        documento.estado = "CONFIGURAR"
        documento.mensagem = "Falta: " + " ".join(faltas)
        documento.save()
        raise ValidationError({"fiscal": faltas})
    nova_referencia = True
    if documento.referencia:
        status_http, resposta = _buscar(documento)
        if status_http == 404:
            nova_referencia = False
        else:
            _aplicar_retorno(documento, empresa, status_http, resposta)
            if documento.estado in ("AUTORIZADO", "PROCESSANDO", "CANCELADO"):
                return documento
    with transaction.atomic():
        documento = DocumentoFiscal.objects.select_for_update().get(pk=documento.pk)
        if nova_referencia:
            tentativa = int(documento.referencia.rsplit("-", 1)[-1]) + 1 if documento.referencia else 1
            documento.referencia = f"appdv-{empresa.pk}-venda-{venda.pk}-{tentativa}"
        documento.ambiente = empresa.ambiente_fiscal
        documento.estado = "PROCESSANDO"
        documento.mensagem = "Enviando para a SEFAZ."
        documento.save()
    nota = montar_nfce(venda)
    try:
        status_http, resposta = integracoes_http.enviar(
            "POST", f"{base_url(empresa)}/v2/nfce?ref={documento.referencia}",
            corpo=nota, token_basico=empresa.focus_token,
        )
    except integracoes_http.ErroIntegracao as erro:
        documento.estado = "PENDENTE"
        documento.mensagem = f"{erro} Tente emitir novamente; a mesma referência será consultada antes."
        documento.save()
        raise ValidationError({"fiscal": documento.mensagem})
    return _aplicar_retorno(documento, empresa, status_http, resposta)


def _buscar(documento):
    try:
        return integracoes_http.enviar(
            "GET", f"{base_url(documento.empresa)}/v2/nfce/{documento.referencia}",
            token_basico=documento.empresa.focus_token,
        )
    except integracoes_http.ErroIntegracao as erro:
        raise ValidationError({"fiscal": str(erro)})


def consultar(documento):
    if not documento.referencia:
        return documento
    status_http, resposta = _buscar(documento)
    if status_http == 404:
        return documento
    return _aplicar_retorno(documento, documento.empresa, status_http, resposta)


def cancelar(documento, justificativa):
    justificativa = (justificativa or "").strip()
    if not 15 <= len(justificativa) <= 255:
        raise ValidationError({"justificativa": "A SEFAZ exige justificativa entre 15 e 255 caracteres."})
    if documento.estado != "AUTORIZADO":
        raise ValidationError({"fiscal": "Só é possível cancelar uma NFC-e autorizada."})
    empresa = documento.empresa
    try:
        status_http, resposta = integracoes_http.enviar(
            "DELETE", f"{base_url(empresa)}/v2/nfce/{documento.referencia}",
            corpo={"justificativa": justificativa}, token_basico=empresa.focus_token,
        )
    except integracoes_http.ErroIntegracao as erro:
        raise ValidationError({"fiscal": str(erro)})
    if status_http >= 400 or resposta.get("status") not in ("cancelado",):
        mensagem = resposta.get("mensagem_sefaz") or resposta.get("mensagem") or f"Retorno HTTP {status_http}."
        raise ValidationError({"fiscal": f"Cancelamento recusado: {mensagem}"})
    documento.estado = "CANCELADO"
    documento.mensagem = (resposta.get("mensagem_sefaz") or "Cancelamento registrado.")[:2000]
    documento.save()
    return documento


def resumo(documento):
    if documento is None:
        return None
    return {
        "estado": documento.estado, "estado_texto": documento.get_estado_display(),
        "mensagem": documento.mensagem, "chave": documento.chave, "numero": documento.numero,
        "danfe_url": documento.danfe_url, "ambiente": documento.ambiente,
    }
