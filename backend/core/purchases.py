"""Importação e recebimento seguro de XML de NF-e de entrada."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import (
    CompraEntrada, ContaPagar, Fornecedor, InventarioContagem, ItemCompraEntrada,
    MovimentacaoEstoque, Produto,
)


MAX_XML_BYTES = 5 * 1024 * 1024
MAX_ITEMS = 5000


def _name(element):
    return element.tag.rsplit("}", 1)[-1]


def _child(element, name):
    if element is None:
        return None
    return next((child for child in element if _name(child) == name), None)


def _text(element, name, default=""):
    child = _child(element, name)
    return (child.text or "").strip() if child is not None else default


def _decimal(value, label, *, allow_zero=False):
    try:
        number = Decimal(str(value).replace(",", "."))
        if not number.is_finite() or number < 0 or (not allow_zero and number == 0):
            raise ValueError
        return number
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError({"xml": f"Valor inválido no XML: {label}."})


def _nfe_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        raise ValidationError({"xml": "Data de emissão inválida no XML da NF-e."})


def parse_nfe_xml(upload):
    """Lê somente campos comerciais da NF-e; rejeita DTD/entidades e arquivos grandes."""
    if getattr(upload, "size", 0) > MAX_XML_BYTES:
        raise ValidationError({"xml": "O XML deve ter no máximo 5 MB."})
    if not str(getattr(upload, "name", "")).lower().endswith(".xml"):
        raise ValidationError({"xml": "Envie o XML autorizado da NF-e (.xml)."})
    raw = upload.read(MAX_XML_BYTES + 1)
    if len(raw) > MAX_XML_BYTES:
        raise ValidationError({"xml": "O XML deve ter no máximo 5 MB."})
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValidationError({"xml": "O XML contém declarações não permitidas."})
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        raise ValidationError({"xml": "O arquivo não contém um XML válido."})

    info = next((node for node in root.iter() if _name(node) == "infNFe"), None)
    if info is None:
        raise ValidationError({"xml": "Não encontrei os dados de uma NF-e no XML."})
    identification = _child(info, "ide")
    issuer = _child(info, "emit")
    total_node = _child(info, "total")
    invoice_total_node = next(
        (node for node in total_node or [] if _name(node) == "ICMSTot"), None
    )
    tax_id = _text(issuer, "CNPJ") or _text(issuer, "CPF")
    tax_id = "".join(ch for ch in tax_id if ch.isdigit())
    access_key = (info.attrib.get("Id") or "").removeprefix("NFe")
    if access_key and (len(access_key) != 44 or not access_key.isdigit()):
        raise ValidationError({"xml": "A chave de acesso da NF-e deve conter 44 dígitos."})
    number = _text(identification, "nNF")
    if not number:
        raise ValidationError({"xml": "A NF-e não informa o número da nota."})

    item_rows = []
    for detail in (node for node in info.iter() if _name(node) == "det"):
        prod = _child(detail, "prod")
        if prod is None:
            continue
        code = _text(prod, "cProd")[:60]
        name = _text(prod, "xProd")[:240]
        quantity = _decimal(_text(prod, "qCom"), f"quantidade do item {code or name}")
        unit_price = _decimal(
            _text(prod, "vUnCom"), f"custo do item {code or name}", allow_zero=True
        )
        line_total_text = _text(prod, "vProd")
        line_total = (
            _decimal(line_total_text, f"total do item {code or name}", allow_zero=True)
            if line_total_text else (quantity * unit_price).quantize(Decimal("0.01"))
        )
        if not name:
            raise ValidationError({"xml": "Há produto sem descrição na NF-e."})
        item_rows.append({
            "codigo": code,
            "codigo_barras": _text(prod, "cEAN")[:50],
            "descricao": name,
            "unidade": _text(prod, "uCom")[:20],
            "quantidade": quantity,
            "custo_unitario": unit_price,
            "total": line_total,
        })
        if len(item_rows) > MAX_ITEMS:
            raise ValidationError({"xml": "A NF-e excede o limite de 5.000 itens."})
    if not item_rows:
        raise ValidationError({"xml": "A NF-e não possui itens de produto."})

    issuer_name = _text(issuer, "xNome")[:180] or "Fornecedor sem nome"
    emission = _text(identification, "dhEmi") or _text(identification, "dEmi")
    invoice_total = _text(invoice_total_node, "vNF")
    return {
        "numero": number[:20],
        "serie": _text(identification, "serie")[:10],
        "chave": access_key[:44],
        "cnpj": tax_id[:14],
        "nome": issuer_name,
        "data_emissao": _nfe_date(emission),
        "valor_total": _decimal(invoice_total, "valor total da NF-e", allow_zero=True)
        if invoice_total else sum((row["total"] for row in item_rows), Decimal("0")),
        "itens": item_rows,
    }


@transaction.atomic
def importar_nfe(*, empresa, usuario, documento):
    data = parse_nfe_xml(documento)
    if data["cnpj"]:
        fornecedor, _ = Fornecedor.objects.get_or_create(
            empresa=empresa, cnpj=data["cnpj"],
            defaults={"nome": data["nome"]},
        )
        if fornecedor.nome != data["nome"]:
            fornecedor.nome = data["nome"]
            fornecedor.save(update_fields=("nome",))
    else:
        fornecedor = Fornecedor.objects.create(empresa=empresa, nome=data["nome"])

    if CompraEntrada.objects.filter(
        empresa=empresa,
        emitente_cnpj=data["cnpj"],
        serie=data["serie"],
        numero_nf=data["numero"],
    ).exists() or (data["chave"] and CompraEntrada.objects.filter(
        empresa=empresa, chave_acesso=data["chave"]
    ).exists()):
        raise ValidationError({"xml": "Esta NF-e já foi importada para esta empresa."})

    compra = CompraEntrada.objects.create(
        empresa=empresa, fornecedor=fornecedor, numero_nf=data["numero"],
        serie=data["serie"], chave_acesso=data["chave"],
        emitente_cnpj=data["cnpj"], data_emissao=data["data_emissao"],
        valor_total=data["valor_total"], criado_por=usuario,
    )
    for row in data["itens"]:
        produto = Produto.objects.filter(empresa=empresa, codigo=row["codigo"]).first()
        if not produto and row["codigo_barras"]:
            produto = Produto.objects.filter(
                empresa=empresa, codigo_barras=row["codigo_barras"]
            ).first()
        ItemCompraEntrada.objects.create(
            compra=compra, produto=produto,
            codigo_xml=row["codigo"], descricao_xml=row["descricao"],
            unidade_xml=row["unidade"], quantidade=row["quantidade"],
            custo_unitario=row["custo_unitario"], total=row["total"],
        )
    return compra


@transaction.atomic
def confirmar_recebimento(*, compra_id, empresa, usuario, mapeamentos=None):
    compra = CompraEntrada.objects.select_for_update().get(pk=compra_id, empresa=empresa)
    if compra.estado != CompraEntrada.Estado.RASCUNHO:
        raise ValidationError({"compra": "Somente notas em conferência podem ser recebidas."})
    itens = list(compra.itens.select_for_update().order_by("pk"))
    mapeamentos = mapeamentos or {}
    if not itens:
        raise ValidationError({"compra": "A nota não possui itens para receber."})

    for item in itens:
        raw_id = mapeamentos.get(str(item.pk))
        if raw_id:
            try:
                item.produto = Produto.objects.get(pk=int(raw_id), empresa=empresa, ativo=True)
            except (Produto.DoesNotExist, ValueError, TypeError):
                raise ValidationError({"itens": "Selecione produtos ativos desta empresa."})
            item.save(update_fields=("produto",))
        if not item.produto_id:
            raise ValidationError({
                "itens": f"Associe o item '{item.descricao_xml}' a um produto do catálogo antes de receber."
            })
        produto = Produto.objects.select_for_update().get(pk=item.produto_id, empresa=empresa)
        em_contagem = InventarioContagem.objects.filter(
            empresa=empresa, estado=InventarioContagem.Estado.ABERTO,
            itens__produto=produto,
        ).exists()
        if em_contagem:
            raise ValidationError({
                "itens": f"O produto {produto.codigo} está em inventário aberto. Conclua a contagem antes de receber a NF."
            })

        saldo_anterior = produto.estoque_atual
        saldo_novo = saldo_anterior + item.quantidade
        custo_anterior = produto.custo_medio or Decimal("0")
        if saldo_novo > 0:
            produto.custo_medio = (
                (saldo_anterior * custo_anterior + item.quantidade * item.custo_unitario)
                / saldo_novo
            ).quantize(Decimal("0.01"))
        produto.estoque_atual = saldo_novo
        produto.save(update_fields=("estoque_atual", "custo_medio", "atualizado_em"))
        MovimentacaoEstoque.objects.create(
            empresa=empresa, produto=produto,
            tipo=MovimentacaoEstoque.Tipo.ENTRADA, quantidade=item.quantidade,
            referencia=f"Recebimento NF {compra.numero_nf}/{compra.serie}"[:120],
            operador=usuario,
        )

    compra.estado = CompraEntrada.Estado.RECEBIDA
    compra.recebido_por = usuario
    compra.recebido_em = timezone.now()
    if not compra.vencimento:
        compra.vencimento = timezone.localdate() + timedelta(days=30)
    compra.save(update_fields=("estado", "recebido_por", "recebido_em", "vencimento"))
    ContaPagar.objects.get_or_create(
        compra=compra,
        defaults={
            "empresa": empresa, "fornecedor": compra.fornecedor,
            "descricao": f"NF {compra.numero_nf}/{compra.serie}".strip("/"),
            "valor_original": compra.valor_total, "saldo": compra.valor_total,
            "vencimento": compra.vencimento, "criado_por": usuario,
        },
    )
    return compra



@transaction.atomic
def criar_compra_manual(*, empresa, usuario, dados):
    """Cria rascunho de entrada digitada manualmente; não movimenta estoque."""
    numero = str(dados.get("numero_nf", "")).strip()[:20]
    serie = str(dados.get("serie", "")).strip()[:10]
    nome = str(dados.get("fornecedor", "")).strip()[:180]
    cnpj = "".join(ch for ch in str(dados.get("cnpj", "")) if ch.isdigit())[:14]
    chave = "".join(ch for ch in str(dados.get("chave_acesso", "")) if ch.isdigit())[:44]
    if not numero or not nome:
        raise ValidationError({"nota": "Informe o número da NF e o nome do fornecedor."})
    if cnpj and len(cnpj) not in (11, 14):
        raise ValidationError({"cnpj": "Informe um CPF/CNPJ válido ou deixe em branco."})

    produto_ids = dados.getlist("produto")
    quantities = dados.getlist("quantidade")
    unit_costs = dados.getlist("custo_unitario")
    if not produto_ids or len(produto_ids) > 100:
        raise ValidationError({"itens": "Informe de 1 a 100 itens para a entrada manual."})
    if not (len(produto_ids) == len(quantities) == len(unit_costs)):
        raise ValidationError({"itens": "Revise os produtos, quantidades e custos informados."})
    if chave and len(chave) != 44:
        raise ValidationError({"chave_acesso": "A chave de acesso deve ter exatamente 44 dígitos."})
    duplicate = CompraEntrada.objects.filter(
        empresa=empresa, serie=serie, numero_nf=numero
    )
    if cnpj:
        duplicate = duplicate.filter(emitente_cnpj=cnpj)
    else:
        duplicate = duplicate.filter(
            emitente_cnpj="", fornecedor__nome__iexact=nome
        )
    if duplicate.exists() or (chave and CompraEntrada.objects.filter(
        empresa=empresa, chave_acesso=chave
    ).exists()):
        raise ValidationError({"nota": "Esta nota já foi lançada para esta empresa."})

    if cnpj:
        fornecedor, _ = Fornecedor.objects.get_or_create(
            empresa=empresa, cnpj=cnpj, defaults={"nome": nome}
        )
    else:
        fornecedor = Fornecedor.objects.create(empresa=empresa, nome=nome)

    emitted = None
    raw_date = str(dados.get("data_emissao", "")).strip()
    if raw_date:
        try:
            emitted = date.fromisoformat(raw_date)
        except ValueError:
            raise ValidationError({"data_emissao": "Informe uma data de emissão válida."})
    due_date = None
    raw_due = str(dados.get("vencimento", "")).strip()
    if raw_due:
        try:
            due_date = date.fromisoformat(raw_due)
        except ValueError:
            raise ValidationError({"vencimento": "Informe uma data de vencimento válida."})

    compra = CompraEntrada.objects.create(
        empresa=empresa, fornecedor=fornecedor, numero_nf=numero, serie=serie,
        chave_acesso=chave, emitente_cnpj=cnpj, data_emissao=emitted,
        vencimento=due_date, criado_por=usuario,
    )
    total = Decimal("0.00")
    seen = set()
    for raw_id, raw_quantity, raw_cost in zip(produto_ids, quantities, unit_costs):
        try:
            produto = Produto.objects.get(pk=int(raw_id), empresa=empresa, ativo=True)
            quantidade = Decimal(str(raw_quantity).replace(",", "."))
            custo = Decimal(str(raw_cost).replace(",", "."))
            if (not quantidade.is_finite() or quantidade <= 0
                    or not custo.is_finite() or custo < 0):
                raise ValueError
        except Produto.DoesNotExist:
            raise ValidationError({"itens": "Selecione produtos ativos desta empresa."})
        except (ValueError, TypeError, InvalidOperation):
            raise ValidationError({"itens": "Quantidade ou custo unitário inválido."})
        if produto.pk in seen:
            raise ValidationError({"itens": f"O produto {produto.codigo} está repetido. Some as quantidades em uma linha."})
        seen.add(produto.pk)
        line_total = (quantidade * custo).quantize(Decimal("0.01"))
        ItemCompraEntrada.objects.create(
            compra=compra, produto=produto, codigo_xml=produto.codigo,
            descricao_xml=produto.nome, unidade_xml=produto.unidade,
            quantidade=quantidade, custo_unitario=custo, total=line_total,
        )
        total += line_total
    compra.valor_total = total
    compra.save(update_fields=("valor_total",))
    return compra
