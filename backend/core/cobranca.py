"""Cobrança das empresas pela plataforma (Painel Master).

Ciclo: teste grátis → fatura mensal (plano fixo + caixas adicionais) gerada alguns dias antes do
vencimento → pagamento por PIX ou cartão num link do Mercado Pago (conta do master) → baixa
automática por webhook/consulta → se não pagar, carência e depois bloqueio total (painel e PDV).
Pagou, desbloqueia na hora. Isenção e valor especial ficam por empresa.

A confirmação de pagamento sempre vem de consulta à API do Mercado Pago com o token do master;
o corpo do webhook só indica qual pagamento consultar.
"""
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from . import integracoes_http
from .models import Assinatura, ConfiguracaoMaster, Empresa, Fatura, TerminalPDV

API = "https://api.mercadopago.com"
CENTAVO = Decimal("0.01")


# ------------------------------------------------------------------ cálculo

def competencia(dia):
    return dia.replace(day=1)


def _somar_meses(dia, meses):
    ano, mes = divmod(dia.month - 1 + meses, 12)
    return date(dia.year + ano, mes + 1, 1)


def vencimento(assinatura, comp):
    dia = min(max(int(assinatura.dia_vencimento or 10), 1), 28)
    venc = comp.replace(day=dia)
    # Primeira fatura depois do teste/isenção nunca vence antes do fim do benefício.
    for limite in (assinatura.teste_ate, assinatura.isencao_ate if assinatura.isenta else None):
        if limite and venc <= limite:
            venc = limite + timedelta(days=3)
    return venc


def caixas_ativos(empresa, hoje):
    """Computadores de caixa que falaram com o servidor nos últimos 30 dias."""
    return TerminalPDV.objects.filter(empresa=empresa, ultimo_contato__date__gte=hoje - timedelta(days=30)).values(
        "identificador").distinct().count()


def calcular(assinatura, hoje):
    """Itens e total da mensalidade com o uso atual."""
    plano = assinatura.plano
    base = assinatura.valor_especial if assinatura.valor_especial is not None else (plano.valor_mensal if plano else Decimal("0"))
    itens = [{"descricao": f"Plano {plano.nome}" if plano else "Mensalidade", "valor": str(base)}]
    total = base
    if plano:
        caixas = caixas_ativos(assinatura.empresa, hoje)
        extras = max(caixas - plano.caixas_incluidos, 0)
        if extras and plano.valor_caixa_adicional:
            adicional = (plano.valor_caixa_adicional * extras).quantize(CENTAVO)
            itens.append({"descricao": f"{extras} caixa(s) adicional(is) × R$ {plano.valor_caixa_adicional:.2f}", "valor": str(adicional)})
            total += adicional
    if assinatura.desconto_percentual:
        desconto = (total * assinatura.desconto_percentual / 100).quantize(CENTAVO, ROUND_HALF_UP)
        itens.append({"descricao": f"Desconto {assinatura.desconto_percentual:g}%", "valor": str(-desconto)})
        total -= desconto
    return itens, max(total, Decimal("0")).quantize(CENTAVO)


# ------------------------------------------------------------------ ciclo

def garantir_assinatura(empresa, hoje=None):
    hoje = hoje or timezone.localdate()
    config = ConfiguracaoMaster.atual()
    assinatura, _ = Assinatura.objects.get_or_create(empresa=empresa, defaults={
        "plano": config.plano_padrao, "estado": Assinatura.Estado.TESTE,
        "teste_ate": hoje + timedelta(days=config.dias_teste), "dia_vencimento": min(hoje.day, 28),
    })
    return assinatura


def gerar_fatura(assinatura, comp, hoje):
    itens, valor = calcular(assinatura, hoje)
    if valor <= 0:
        return None
    plano = assinatura.plano.nome if assinatura.plano else "ApPdv"
    fatura, _ = Fatura.objects.get_or_create(empresa=assinatura.empresa, competencia=comp, defaults={
        "descricao": f"ApPdv {plano} — {comp:%m/%Y}", "itens": itens, "valor": valor,
        "vencimento": vencimento(assinatura, comp),
    })
    return fatura


def processar_assinatura(assinatura, hoje=None, config=None):
    hoje = hoje or timezone.localdate()
    config = config or ConfiguracaoMaster.atual()
    estado = assinatura.estado
    if estado == Assinatura.Estado.CANCELADA:
        return assinatura
    if assinatura.isencao_vigente(hoje):
        novo = Assinatura.Estado.ISENTA
    elif assinatura.teste_ate and hoje <= assinatura.teste_ate:
        novo = Assinatura.Estado.TESTE
    else:
        if assinatura.plano or assinatura.valor_especial is not None:
            # Gera a fatura do mês e, perto do fim do mês, já a do próximo.
            for comp in (competencia(hoje), _somar_meses(hoje, 1)):
                if hoje >= vencimento(assinatura, comp) - timedelta(days=config.dias_antecedencia):
                    if not Fatura.objects.filter(empresa=assinatura.empresa, competencia=comp).exists():
                        gerar_fatura(assinatura, comp, hoje)
        Fatura.objects.filter(empresa=assinatura.empresa, estado=Fatura.Estado.ABERTA, vencimento__lt=hoje).update(
            estado=Fatura.Estado.VENCIDA)
        carencia = assinatura.carencia_dias if assinatura.carencia_dias is not None else config.carencia_dias
        vencidas = Fatura.objects.filter(empresa=assinatura.empresa, estado=Fatura.Estado.VENCIDA)
        if vencidas.filter(vencimento__lt=hoje - timedelta(days=carencia)).exists():
            novo = Assinatura.Estado.SUSPENSA
        elif vencidas.exists():
            novo = Assinatura.Estado.ATRASADA
        else:
            novo = Assinatura.Estado.ATIVA
    if novo != estado:
        assinatura.estado = novo
        assinatura.suspensa_em = hoje if novo == Assinatura.Estado.SUSPENSA else None
        assinatura.save(update_fields=("estado", "suspensa_em", "atualizado_em"))
        _registrar(assinatura.empresa, f"ASSINATURA_{novo}", estado)
    return assinatura


def processar(hoje=None):
    """Roda o ciclo de todas as empresas (comando diário e verificação automática a cada hora)."""
    hoje = hoje or timezone.localdate()
    config = ConfiguracaoMaster.atual()
    for empresa in Empresa.objects.filter(assinatura__isnull=True):
        garantir_assinatura(empresa, hoje)
    resultado = {}
    for assinatura in Assinatura.objects.select_related("empresa", "plano"):
        processar_assinatura(assinatura, hoje, config)
        resultado[assinatura.estado] = resultado.get(assinatura.estado, 0) + 1
    return resultado


def processar_se_preciso():
    """Executa no máximo uma vez por hora, puxado pelo próprio uso do sistema."""
    if cache.add("cobranca:ultima-execucao", "1", 3600):
        try:
            processar()
        except Exception:
            cache.delete("cobranca:ultima-execucao")
            raise


def bloqueio(empresa):
    """Mensagem de bloqueio quando a empresa está suspensa por falta de pagamento."""
    try:
        assinatura = empresa.assinatura
    except Assinatura.DoesNotExist:
        return ""
    if assinatura.bloqueada:
        return ("Acesso suspenso por falta de pagamento da mensalidade do ApPdv. "
                "O administrador da empresa pode pagar em Assinatura e o acesso volta na hora.")
    return ""


def aviso(empresa, hoje=None):
    """Faixa exibida no painel: fatura perto do vencimento, vencida ou suspensa."""
    hoje = hoje or timezone.localdate()
    try:
        assinatura = empresa.assinatura
    except Assinatura.DoesNotExist:
        return None
    if assinatura.estado == Assinatura.Estado.TESTE and assinatura.teste_ate and (assinatura.teste_ate - hoje).days <= 5:
        return {"nivel": "info", "texto": f"Seu teste grátis termina em {assinatura.teste_ate:%d/%m/%Y}."}
    fatura = Fatura.objects.filter(empresa=empresa, estado__in=(Fatura.Estado.ABERTA, Fatura.Estado.VENCIDA)).order_by("vencimento").first()
    if not fatura:
        return None
    if assinatura.estado == Assinatura.Estado.SUSPENSA:
        return {"nivel": "erro", "texto": "Acesso suspenso por falta de pagamento.", "fatura": fatura}
    if fatura.estado == Fatura.Estado.VENCIDA:
        config = ConfiguracaoMaster.atual()
        carencia = assinatura.carencia_dias if assinatura.carencia_dias is not None else config.carencia_dias
        limite = fatura.vencimento + timedelta(days=carencia)
        return {"nivel": "erro", "texto": f"Mensalidade de R$ {fatura.valor:.2f} vencida em {fatura.vencimento:%d/%m}. "
                                          f"O acesso será suspenso em {limite + timedelta(days=1):%d/%m/%Y}.", "fatura": fatura}
    if (fatura.vencimento - hoje).days <= 5:
        return {"nivel": "info", "texto": f"Mensalidade de R$ {fatura.valor:.2f} vence em {fatura.vencimento:%d/%m/%Y}.", "fatura": fatura}
    return None


# ------------------------------------------------------------------ pagamento

def configurado():
    return bool(ConfiguracaoMaster.atual().mp_access_token)


def link_pagamento(fatura, url_retorno, url_webhook):
    """Cria (uma vez) o checkout do Mercado Pago com PIX e cartão para a fatura."""
    if fatura.estado not in (Fatura.Estado.ABERTA, Fatura.Estado.VENCIDA):
        raise ValidationError({"fatura": "Esta fatura não está em aberto."})
    if fatura.link_pagamento:
        return fatura.link_pagamento
    token = ConfiguracaoMaster.atual().mp_access_token
    if not token:
        raise ValidationError({"fatura": "Pagamento online ainda não configurado pela plataforma. Fale com o suporte do ApPdv."})
    corpo = {
        "items": [{"title": fatura.descricao[:250], "quantity": 1, "unit_price": float(fatura.valor), "currency_id": "BRL"}],
        "external_reference": f"fatura-{fatura.pk}",
        "back_urls": {"success": url_retorno, "pending": url_retorno, "failure": url_retorno},
        "auto_return": "approved",
        # PIX e cartão de crédito à vista; boleto fica de fora (compensação lenta desbloquearia tarde).
        "payment_methods": {"excluded_payment_types": [{"id": "ticket"}, {"id": "atm"}], "installments": 1},
        "statement_descriptor": "APPDV",
    }
    if fatura.empresa.email:
        corpo["payer"] = {"email": fatura.empresa.email}
    if url_webhook.startswith("https://"):
        corpo["notification_url"] = url_webhook
    try:
        status_http, resposta = integracoes_http.enviar("POST", f"{API}/checkout/preferences", corpo=corpo, token_bearer=token)
    except integracoes_http.ErroIntegracao as erro:
        raise ValidationError({"fatura": str(erro)})
    if status_http >= 400 or not resposta.get("init_point"):
        raise ValidationError({"fatura": f"Mercado Pago recusou o link de pagamento: {resposta.get('message') or status_http}"})
    fatura.mp_preference_id = str(resposta.get("id", ""))[:80]
    fatura.link_pagamento = resposta["init_point"]
    fatura.save(update_fields=("mp_preference_id", "link_pagamento"))
    return fatura.link_pagamento


def _aplicar_pagamento(pagamento):
    """Dá baixa se o pagamento consultado no Mercado Pago está aprovado e cobre a fatura."""
    referencia = str(pagamento.get("external_reference") or "")
    if pagamento.get("status") != "approved" or not referencia.startswith("fatura-"):
        return None
    try:
        fatura_id = int(referencia.split("-", 1)[1])
        pago = Decimal(str(pagamento.get("transaction_amount", "0"))).quantize(CENTAVO)
    except (ValueError, ArithmeticError):
        return None
    with transaction.atomic():
        fatura = Fatura.objects.select_for_update().select_related("empresa").filter(pk=fatura_id).first()
        if not fatura or fatura.estado == Fatura.Estado.PAGA or pago < fatura.valor:
            return fatura
        fatura.estado = Fatura.Estado.PAGA
        fatura.paga_em = timezone.now()
        fatura.mp_payment_id = str(pagamento.get("id", ""))[:40]
        fatura.meio = "PIX" if pagamento.get("payment_method_id") == "pix" else "CARTAO"
        fatura.save(update_fields=("estado", "paga_em", "mp_payment_id", "meio"))
    _registrar(fatura.empresa, "FATURA_PAGA", fatura.pk, valor=str(fatura.valor), meio=fatura.meio)
    processar_assinatura(Assinatura.objects.get(empresa=fatura.empresa))
    return fatura


def consultar_pagamento(pagamento_id):
    token = ConfiguracaoMaster.atual().mp_access_token
    if not token or not str(pagamento_id).isdigit():
        return None
    try:
        status_http, resposta = integracoes_http.enviar("GET", f"{API}/v1/payments/{pagamento_id}", token_bearer=token)
    except integracoes_http.ErroIntegracao:
        return None
    return _aplicar_pagamento(resposta) if status_http < 400 else None


def verificar_fatura(fatura):
    """Procura no Mercado Pago um pagamento aprovado desta fatura (caso o aviso automático não tenha chegado)."""
    token = ConfiguracaoMaster.atual().mp_access_token
    if not token or fatura.estado == Fatura.Estado.PAGA:
        return fatura
    try:
        status_http, resposta = integracoes_http.enviar(
            "GET", f"{API}/v1/payments/search?external_reference=fatura-{fatura.pk}&sort=date_created&criteria=desc",
            token_bearer=token)
    except integracoes_http.ErroIntegracao as erro:
        raise ValidationError({"fatura": str(erro)})
    for pagamento in (resposta.get("results") or []) if status_http < 400 else []:
        if _aplicar_pagamento(pagamento):
            break
    fatura.refresh_from_db()
    return fatura


def baixa_manual(fatura, usuario, meio, observacao=""):
    """Pagamento recebido fora do Mercado Pago (transferência, dinheiro…)."""
    if fatura.estado in (Fatura.Estado.PAGA, Fatura.Estado.CANCELADA):
        raise ValidationError({"fatura": "Fatura já paga ou cancelada."})
    fatura.estado = Fatura.Estado.PAGA
    fatura.paga_em = timezone.now()
    fatura.meio = (meio or "MANUAL")[:20]
    fatura.observacao = (observacao or f"Baixa manual por {usuario.get_username()}")[:250]
    fatura.save(update_fields=("estado", "paga_em", "meio", "observacao"))
    _registrar(fatura.empresa, "FATURA_BAIXA_MANUAL", fatura.pk, usuario=usuario, meio=fatura.meio)
    processar_assinatura(Assinatura.objects.get(empresa=fatura.empresa))
    return fatura


def _registrar(empresa, acao, referencia, usuario=None, **detalhes):
    from .models import EventoGestao
    EventoGestao.objects.create(empresa=empresa, usuario=usuario, acao=acao[:60], referencia=str(referencia)[:80], detalhes=detalhes)


def resumo_master(hoje=None):
    hoje = hoje or timezone.localdate()
    from django.db.models import Sum
    mes = competencia(hoje)
    faturas = Fatura.objects.all()
    return {
        "recebido_mes": faturas.filter(estado=Fatura.Estado.PAGA, paga_em__date__gte=mes).aggregate(n=Sum("valor"))["n"] or Decimal("0"),
        "em_aberto": faturas.filter(estado=Fatura.Estado.ABERTA).aggregate(n=Sum("valor"))["n"] or Decimal("0"),
        "vencido": faturas.filter(estado=Fatura.Estado.VENCIDA).aggregate(n=Sum("valor"))["n"] or Decimal("0"),
        "previsto_mes": faturas.filter(competencia=mes).exclude(estado=Fatura.Estado.CANCELADA).aggregate(n=Sum("valor"))["n"] or Decimal("0"),
        "por_estado": {e: Assinatura.objects.filter(estado=e).count() for e, _ in Assinatura.Estado.choices},
    }
