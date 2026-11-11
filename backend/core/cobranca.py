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
from .models import Assinatura, ConfiguracaoMaster, Cupom, Empresa, Fatura, MembroEmpresa, TerminalPDV

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
    if assinatura.cupom_id and assinatura.cupom_meses_restantes > 0:
        cupom = assinatura.cupom
        desconto = (total * cupom.desconto_percentual / 100).quantize(CENTAVO, ROUND_HALF_UP)
        if desconto:
            itens.append({"descricao": f"Cupom {cupom.codigo} ({cupom.desconto_percentual:g}%, faltam {assinatura.cupom_meses_restantes} mês(es))",
                          "valor": str(-desconto), "cupom": cupom.codigo})
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
    fatura, criada = Fatura.objects.get_or_create(empresa=assinatura.empresa, competencia=comp, defaults={
        "descricao": f"ApPdv {plano} — {comp:%m/%Y}", "itens": itens, "valor": valor,
        "vencimento": vencimento(assinatura, comp),
    })
    if criada:
        if any(item.get("cupom") for item in itens) and assinatura.cupom_meses_restantes:
            Assinatura.objects.filter(pk=assinatura.pk).update(cupom_meses_restantes=assinatura.cupom_meses_restantes - 1)
            assinatura.cupom_meses_restantes -= 1
        if assinatura.recorrente:
            atualizar_valor_recorrente(assinatura, valor)
        from . import cobranca_avisos
        cobranca_avisos.avisar_fatura(fatura, "gerada", hoje)
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
        if novo == Assinatura.Estado.SUSPENSA:
            from . import cobranca_avisos
            cobranca_avisos.avisar_suspensao(assinatura)
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
    from . import cobranca_avisos
    cobranca_avisos.processar_avisos(hoje, config)
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


def _quitar(fatura_id, pagamento_id, pago, meio):
    with transaction.atomic():
        fatura = Fatura.objects.select_for_update().select_related("empresa").filter(pk=fatura_id).first()
        if not fatura or fatura.estado in (Fatura.Estado.PAGA, Fatura.Estado.CANCELADA) or pago < fatura.valor:
            return fatura
        if pagamento_id and Fatura.objects.filter(mp_payment_id=pagamento_id).exclude(pk=fatura.pk).exists():
            return fatura  # o mesmo pagamento nunca quita duas faturas
        fatura.estado = Fatura.Estado.PAGA
        fatura.paga_em = timezone.now()
        fatura.mp_payment_id = pagamento_id[:40]
        fatura.meio = meio
        fatura.save(update_fields=("estado", "paga_em", "mp_payment_id", "meio"))
    _registrar(fatura.empresa, "FATURA_PAGA", fatura.pk, valor=str(fatura.valor), meio=fatura.meio)
    processar_assinatura(Assinatura.objects.get(empresa=fatura.empresa))
    from . import cobranca_avisos
    cobranca_avisos.avisar_pagamento(fatura)
    return fatura


def _aplicar_pagamento(pagamento):
    """Dá baixa se o pagamento consultado no Mercado Pago está aprovado e cobre a fatura.

    `fatura-N`: pagamento avulso do link; `assinatura-N`: cobrança do cartão recorrente, que quita
    a fatura em aberto mais antiga (ou a do mês, se o Mercado Pago cobrar antes de ela ser gerada).
    """
    referencia = str(pagamento.get("external_reference") or "")
    if pagamento.get("status") != "approved":
        return None
    try:
        pago = Decimal(str(pagamento.get("transaction_amount", "0"))).quantize(CENTAVO)
        alvo = int(referencia.split("-", 1)[1]) if "-" in referencia else 0
    except (ValueError, ArithmeticError):
        return None
    pagamento_id = str(pagamento.get("id", ""))
    if referencia.startswith("fatura-"):
        meio = "PIX" if pagamento.get("payment_method_id") == "pix" else "CARTAO"
        return _quitar(alvo, pagamento_id, pago, meio)
    if referencia.startswith("assinatura-"):
        assinatura = Assinatura.objects.filter(pk=alvo).select_related("empresa", "plano").first()
        if not assinatura:
            return None
        ja = Fatura.objects.filter(mp_payment_id=pagamento_id).first() if pagamento_id else None
        if ja:
            return ja
        fatura = Fatura.objects.filter(empresa=assinatura.empresa, estado__in=(Fatura.Estado.ABERTA, Fatura.Estado.VENCIDA),
                                       valor__lte=pago).order_by("vencimento").first()
        if not fatura:
            hoje = timezone.localdate()
            fatura = gerar_fatura(assinatura, competencia(hoje), hoje)
        return _quitar(fatura.pk, pagamento_id, pago, "CARTAO_RECORRENTE") if fatura else None
    return None


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
    from . import cobranca_avisos
    cobranca_avisos.avisar_pagamento(fatura)
    return fatura


def _registrar(empresa, acao, referencia, usuario=None, **detalhes):
    from .models import EventoGestao
    EventoGestao.objects.create(empresa=empresa, usuario=usuario, acao=acao[:60], referencia=str(referencia)[:80], detalhes=detalhes)


def resumo_master(hoje=None):
    from django.db.models import Sum
    from django.db.models.functions import TruncMonth
    from .models import EventoGestao
    hoje = hoje or timezone.localdate()
    mes = competencia(hoje)
    faturas = Fatura.objects.all()
    por_estado = {e: Assinatura.objects.filter(estado=e).count() for e, _ in Assinatura.Estado.choices}
    pagantes = [a for a in Assinatura.objects.select_related("empresa", "plano", "cupom").filter(
        estado__in=(Assinatura.Estado.ATIVA, Assinatura.Estado.ATRASADA))]
    mrr = sum((calcular(a, hoje)[1] for a in pagantes), Decimal("0"))
    cobraveis = por_estado["ATIVA"] + por_estado["ATRASADA"] + por_estado["SUSPENSA"]
    inadimplentes = por_estado["ATRASADA"] + por_estado["SUSPENSA"]
    # Conversão: empresas que já saíram do teste (sem isenção) e pagaram ao menos uma fatura.
    saidas = Assinatura.objects.filter(teste_ate__lt=hoje, isenta=False)
    convertidas = saidas.filter(empresa__faturas__estado=Fatura.Estado.PAGA).distinct().count()
    inicio_serie = _somar_meses(mes, -11)
    recebidos = {r["m"].date() if hasattr(r["m"], "date") else r["m"]: r["n"] for r in faturas.filter(
        estado=Fatura.Estado.PAGA, paga_em__date__gte=inicio_serie).annotate(m=TruncMonth("paga_em")).values("m").annotate(n=Sum("valor"))}
    serie = []
    for i in range(12):
        m = _somar_meses(inicio_serie, i)
        serie.append({"mes": m, "valor": recebidos.get(m, Decimal("0"))})
    maior = max((p["valor"] for p in serie), default=Decimal("0")) or Decimal("1")
    for ponto in serie:
        ponto["pct"] = int(ponto["valor"] * 100 / maior)
    return {
        "recebido_mes": faturas.filter(estado=Fatura.Estado.PAGA, paga_em__date__gte=mes).aggregate(n=Sum("valor"))["n"] or Decimal("0"),
        "em_aberto": faturas.filter(estado=Fatura.Estado.ABERTA).aggregate(n=Sum("valor"))["n"] or Decimal("0"),
        "vencido": faturas.filter(estado=Fatura.Estado.VENCIDA).aggregate(n=Sum("valor"))["n"] or Decimal("0"),
        "previsto_mes": faturas.filter(competencia=mes).exclude(estado=Fatura.Estado.CANCELADA).aggregate(n=Sum("valor"))["n"] or Decimal("0"),
        "por_estado": por_estado,
        "mrr": mrr.quantize(CENTAVO), "pagantes": len(pagantes),
        "ticket_medio": (mrr / len(pagantes)).quantize(CENTAVO) if pagantes else Decimal("0"),
        "inadimplencia": round(inadimplentes * 100 / cobraveis) if cobraveis else 0,
        "conversao": round(convertidas * 100 / saidas.count()) if saidas.exists() else 0,
        "em_teste": por_estado["TESTE"],
        "cancelamentos_mes": EventoGestao.objects.filter(acao="ASSINATURA_CANCELADA", criado_em__date__gte=mes).count(),
        "recorrentes": Assinatura.objects.filter(mp_preapproval_status="authorized").count(),
        "serie": serie,
    }


# ------------------------------------------------------------------ limites do plano

def _plano(empresa):
    try:
        return empresa.assinatura.plano
    except Assinatura.DoesNotExist:
        return None


def validar_novo_usuario(empresa):
    """Bloqueia usuário ativo além do limite do plano (o Master pode ignorar)."""
    plano = _plano(empresa)
    if plano and plano.limite_usuarios:
        ativos = MembroEmpresa.objects.filter(empresa=empresa, ativo=True).count()
        if ativos >= plano.limite_usuarios:
            raise ValidationError({"usuarios": f"O plano {plano.nome} permite {plano.limite_usuarios} usuário(s) ativo(s). "
                                               "Desative alguém ou troque de plano em Assinatura do ApPdv."})


def validar_terminal(empresa, identificador, hoje=None):
    """Caixa novo além do limite do plano não é registrado (os que já existem seguem funcionando)."""
    plano = _plano(empresa)
    if not plano or not plano.limite_caixas:
        return
    hoje = hoje or timezone.localdate()
    ativos = TerminalPDV.objects.filter(empresa=empresa, ultimo_contato__date__gte=hoje - timedelta(days=30))
    if ativos.filter(identificador=identificador).exists():
        return
    if ativos.count() >= plano.limite_caixas:
        raise ValidationError({"caixas": f"O plano {plano.nome} permite {plano.limite_caixas} caixa(s). "
                                         "Troque de plano em Assinatura do ApPdv para usar mais computadores."})


# ------------------------------------------------------------------ troca de plano e cupom

def trocar_plano(assinatura, plano, usuario=None):
    if not plano.ativo:
        raise ValidationError({"plano": "Plano indisponível."})
    anterior = assinatura.plano.nome if assinatura.plano else ""
    assinatura.plano = plano
    assinatura.save(update_fields=("plano", "atualizado_em"))
    _registrar(assinatura.empresa, "ASSINATURA_PLANO_TROCADO", assinatura.pk, usuario=usuario, de=anterior, para=plano.nome)
    if assinatura.recorrente:
        atualizar_valor_recorrente(assinatura, calcular(assinatura, timezone.localdate())[1])
    return assinatura


def aplicar_cupom(assinatura, codigo, usuario=None, hoje=None):
    hoje = hoje or timezone.localdate()
    codigo = (codigo or "").strip().upper()
    with transaction.atomic():
        cupom = Cupom.objects.select_for_update().filter(codigo=codigo, ativo=True).first()
        if not cupom or (cupom.valido_ate and cupom.valido_ate < hoje):
            raise ValidationError({"cupom": "Cupom inválido ou vencido."})
        if cupom.usos_maximos and cupom.usos >= cupom.usos_maximos:
            raise ValidationError({"cupom": "Este cupom já atingiu o limite de usos."})
        if assinatura.cupom_id == cupom.pk:
            raise ValidationError({"cupom": "Este cupom já está aplicado."})
        cupom.usos += 1
        cupom.save(update_fields=("usos",))
        assinatura.cupom = cupom
        assinatura.cupom_meses_restantes = cupom.meses
        assinatura.save(update_fields=("cupom", "cupom_meses_restantes", "atualizado_em"))
    _registrar(assinatura.empresa, "ASSINATURA_CUPOM", assinatura.pk, usuario=usuario, cupom=cupom.codigo)
    return cupom


# ------------------------------------------------------------------ cartão recorrente

def assinar_cartao(assinatura, email, url_retorno):
    """Cria a assinatura recorrente no Mercado Pago; o cliente autoriza o cartão no link devolvido."""
    token = ConfiguracaoMaster.atual().mp_access_token
    if not token:
        raise ValidationError({"cartao": "Pagamento online ainda não configurado pela plataforma."})
    if not email:
        raise ValidationError({"cartao": "Cadastre um e-mail na empresa ou no seu usuário para assinar no cartão."})
    valor = calcular(assinatura, timezone.localdate())[1]
    if valor <= 0:
        raise ValidationError({"cartao": "Não há mensalidade a cobrar nesta assinatura."})
    plano = assinatura.plano.nome if assinatura.plano else "ApPdv"
    corpo = {
        "reason": f"ApPdv {plano} — {assinatura.empresa.nome}"[:250],
        "external_reference": f"assinatura-{assinatura.pk}",
        "payer_email": email,
        "auto_recurring": {"frequency": 1, "frequency_type": "months", "transaction_amount": float(valor), "currency_id": "BRL"},
        "back_url": url_retorno,
        "status": "pending",
    }
    try:
        status_http, resposta = integracoes_http.enviar("POST", f"{API}/preapproval", corpo=corpo, token_bearer=token)
    except integracoes_http.ErroIntegracao as erro:
        raise ValidationError({"cartao": str(erro)})
    if status_http >= 400 or not resposta.get("init_point"):
        raise ValidationError({"cartao": f"Mercado Pago recusou a assinatura: {resposta.get('message') or status_http}"})
    assinatura.mp_preapproval_id = str(resposta.get("id", ""))[:80]
    assinatura.mp_preapproval_status = str(resposta.get("status", "pending"))[:20]
    assinatura.mp_valor_recorrente = valor
    assinatura.save(update_fields=("mp_preapproval_id", "mp_preapproval_status", "mp_valor_recorrente", "atualizado_em"))
    _registrar(assinatura.empresa, "ASSINATURA_CARTAO_CRIADA", assinatura.pk, valor=str(valor))
    return resposta["init_point"]


def consultar_preapproval(preapproval_id):
    token = ConfiguracaoMaster.atual().mp_access_token
    if not token or not preapproval_id:
        return None
    try:
        status_http, resposta = integracoes_http.enviar("GET", f"{API}/preapproval/{preapproval_id}", token_bearer=token)
    except integracoes_http.ErroIntegracao:
        return None
    referencia = str(resposta.get("external_reference") or "")
    if status_http >= 400 or not referencia.startswith("assinatura-"):
        return None
    assinatura = Assinatura.objects.filter(pk=referencia.split("-", 1)[1]).first() if referencia.split("-", 1)[1].isdigit() else None
    if not assinatura:
        return None
    status = str(resposta.get("status", ""))[:20]
    if assinatura.mp_preapproval_id != str(preapproval_id) or assinatura.mp_preapproval_status != status:
        assinatura.mp_preapproval_id = str(preapproval_id)[:80]
        assinatura.mp_preapproval_status = status
        assinatura.save(update_fields=("mp_preapproval_id", "mp_preapproval_status", "atualizado_em"))
        _registrar(assinatura.empresa, f"ASSINATURA_CARTAO_{status.upper()}", assinatura.pk)
    return assinatura


def consultar_cobranca_recorrente(autorizado_id):
    """Aviso de cobrança mensal do cartão: consulta e quita a fatura se o pagamento foi aprovado."""
    token = ConfiguracaoMaster.atual().mp_access_token
    if not token or not str(autorizado_id).isdigit():
        return None
    try:
        status_http, resposta = integracoes_http.enviar("GET", f"{API}/authorized_payments/{autorizado_id}", token_bearer=token)
    except integracoes_http.ErroIntegracao:
        return None
    if status_http >= 400:
        return None
    pagamento = resposta.get("payment") or {}
    assinatura = Assinatura.objects.filter(mp_preapproval_id=str(resposta.get("preapproval_id") or "")).first()
    if not assinatura or pagamento.get("status") != "approved":
        return None
    return _aplicar_pagamento({"id": pagamento.get("id"), "status": "approved", "external_reference": f"assinatura-{assinatura.pk}",
                               "transaction_amount": resposta.get("transaction_amount", 0)})


def atualizar_valor_recorrente(assinatura, valor):
    token = ConfiguracaoMaster.atual().mp_access_token
    if not token or not assinatura.mp_preapproval_id or valor <= 0 or valor == assinatura.mp_valor_recorrente:
        return False
    try:
        status_http, _ = integracoes_http.enviar("PUT", f"{API}/preapproval/{assinatura.mp_preapproval_id}", token_bearer=token,
                                                 corpo={"auto_recurring": {"transaction_amount": float(valor), "currency_id": "BRL"}})
    except integracoes_http.ErroIntegracao:
        return False
    if status_http < 400:
        Assinatura.objects.filter(pk=assinatura.pk).update(mp_valor_recorrente=valor)
        assinatura.mp_valor_recorrente = valor
        return True
    return False


def cancelar_cartao(assinatura, usuario=None):
    token = ConfiguracaoMaster.atual().mp_access_token
    if assinatura.mp_preapproval_id and token:
        try:
            integracoes_http.enviar("PUT", f"{API}/preapproval/{assinatura.mp_preapproval_id}", token_bearer=token,
                                    corpo={"status": "cancelled"})
        except integracoes_http.ErroIntegracao as erro:
            raise ValidationError({"cartao": str(erro)})
    assinatura.mp_preapproval_status = "cancelled"
    assinatura.save(update_fields=("mp_preapproval_status", "atualizado_em"))
    _registrar(assinatura.empresa, "ASSINATURA_CARTAO_CANCELADO", assinatura.pk, usuario=usuario)
    return assinatura
