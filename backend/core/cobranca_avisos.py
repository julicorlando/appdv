"""Avisos de cobrança por e-mail para as empresas.

Cada fatura recebe no máximo um aviso de cada tipo (gerada, vencendo, vencida, paga), registrado em
`Fatura.avisos`; a suspensão é avisada na hora em que acontece. Destinatários: o e-mail da empresa e
os administradores e gerentes ativos. Sem servidor de e-mail configurado (EMAIL_HOST), nada é enviado
e os avisos continuam aparecendo no painel.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

from .models import Assinatura, ConfiguracaoMaster, Fatura, MembroEmpresa

log = logging.getLogger(__name__)


def destinatarios(empresa):
    emails = {empresa.email.strip().lower()} if empresa.email else set()
    for email in MembroEmpresa.objects.filter(
            empresa=empresa, ativo=True, papel__in=(MembroEmpresa.Papel.ADMIN, MembroEmpresa.Papel.GERENTE),
    ).exclude(usuario__email="").values_list("usuario__email", flat=True):
        emails.add(email.strip().lower())
    return sorted(e for e in emails if "@" in e)


def link_assinatura(empresa):
    base = getattr(settings, "SITE_URL", "").rstrip("/")
    caminho = reverse("empresa-assinatura", args=[empresa.pk])
    return f"{base}{caminho}" if base else caminho


def enviar(empresa, assunto, texto, config=None):
    config = config or ConfiguracaoMaster.atual()
    if not config.avisos_email or not getattr(settings, "EMAIL_HOST", ""):
        return False
    para = destinatarios(empresa)
    if not para:
        return False
    corpo = (f"Olá, {empresa.nome}!\n\n{texto}\n\nVer assinatura e pagar (PIX ou cartão): {link_assinatura(empresa)}\n\n"
             "— ApPdv\n(Mensagem automática. Em caso de dúvida, responda para o suporte.)")
    try:
        send_mail(f"ApPdv · {assunto}", corpo, settings.DEFAULT_FROM_EMAIL, para, fail_silently=False)
    except Exception:  # noqa: BLE001 - e-mail nunca derruba a cobrança
        log.exception("Falha ao enviar aviso de cobrança para %s", empresa.pk)
        return False
    from .cobranca import _registrar
    _registrar(empresa, "AVISO_EMAIL", assunto[:80], para=para)
    return True


def _marcar(fatura, tipo):
    fatura.avisos = list(fatura.avisos or []) + [tipo]
    Fatura.objects.filter(pk=fatura.pk).update(avisos=fatura.avisos)


def avisar_fatura(fatura, tipo, hoje, config=None):
    """tipo: gerada | vencendo | vencida. Devolve True se enviou."""
    if tipo in (fatura.avisos or []):
        return False
    textos = {
        "gerada": (f"Fatura de {fatura.competencia:%m/%Y} disponível",
                   f"A mensalidade do ApPdv de R$ {fatura.valor:.2f} está disponível e vence em {fatura.vencimento:%d/%m/%Y}."),
        "vencendo": (f"Mensalidade vence em {fatura.vencimento:%d/%m}",
                     f"Lembrete: a mensalidade de R$ {fatura.valor:.2f} vence em {fatura.vencimento:%d/%m/%Y}."),
        "vencida": ("Mensalidade em atraso",
                    f"A mensalidade de R$ {fatura.valor:.2f} venceu em {fatura.vencimento:%d/%m/%Y}. "
                    "Pague para evitar a suspensão do painel e dos caixas."),
    }
    assunto, texto = textos[tipo]
    try:
        if fatura.empresa.assinatura.recorrente and tipo != "vencida":
            texto += "\nA cobrança será feita automaticamente no cartão cadastrado."
    except Assinatura.DoesNotExist:
        pass
    enviado = enviar(fatura.empresa, assunto, texto, config)
    # Marca mesmo sem e-mail configurado: não dispara uma enxurrada atrasada quando o SMTP for ligado.
    _marcar(fatura, tipo)
    return enviado


def avisar_suspensao(assinatura):
    return enviar(assinatura.empresa, "Acesso suspenso por falta de pagamento",
                  "O acesso ao painel e aos caixas do ApPdv foi suspenso porque a mensalidade não foi paga. "
                  "As vendas feitas nos caixas ficam guardadas. Assim que o pagamento for confirmado, tudo volta na hora.")


def avisar_pagamento(fatura):
    if "paga" in (fatura.avisos or []):
        return False
    enviado = enviar(fatura.empresa, "Pagamento confirmado",
                     f"Recebemos o pagamento de R$ {fatura.valor:.2f} referente a {fatura.competencia:%m/%Y}. Obrigado!")
    _marcar(fatura, "paga")
    return enviado


def processar_avisos(hoje, config=None):
    config = config or ConfiguracaoMaster.atual()
    enviados = 0
    for fatura in Fatura.objects.filter(estado__in=(Fatura.Estado.ABERTA, Fatura.Estado.VENCIDA)).select_related("empresa"):
        if "gerada" not in (fatura.avisos or []):
            enviados += avisar_fatura(fatura, "gerada", hoje, config)
        if fatura.estado == Fatura.Estado.ABERTA and hoje <= fatura.vencimento <= hoje + timedelta(days=config.aviso_dias_antes) \
                and fatura.vencimento > fatura.criada_em.date():
            enviados += avisar_fatura(fatura, "vencendo", hoje, config)
        if fatura.estado == Fatura.Estado.VENCIDA:
            enviados += avisar_fatura(fatura, "vencida", hoje, config)
    return enviados
