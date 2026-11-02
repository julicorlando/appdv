import random
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from openpyxl import load_workbook
from rest_framework.exceptions import ValidationError

from .models import (
    Categoria, InventarioContagem, ItemContagem, MovimentacaoEstoque,
    Produto,
)


REQUIRED_COLUMNS = (
    "codigo", "nome", "categoria", "unidade", "preco_venda",
    "estoque_atual", "estoque_minimo", "setor", "endereco", "codigo_barras",
)


def _decimal(value, field, row, *, minimum=Decimal("0")):
    try:
        number = Decimal(str(value).replace(",", ".") if isinstance(value, str) else str(value))
        if not number.is_finite() or number < minimum:
            raise ValueError
        return number
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError({"planilha": f"Linha {row}: valor inválido em {field}."})


@transaction.atomic
def importar_planilha(*, empresa, usuario, arquivo):
    if getattr(arquivo, "size", 0) > 5 * 1024 * 1024:
        raise ValidationError({"planilha": "O arquivo deve ter no máximo 5 MB."})
    if not str(getattr(arquivo, "name", "")).lower().endswith(".xlsx"):
        raise ValidationError({"planilha": "Envie uma planilha .xlsx."})
    try:
        workbook = load_workbook(arquivo, read_only=True, data_only=True)
        sheet = workbook.active
        headers = [str(cell.value or "").strip().lower() for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
    except Exception:
        raise ValidationError({"planilha": "Não foi possível ler a primeira aba do arquivo."})
    if tuple(headers) != REQUIRED_COLUMNS:
        raise ValidationError({"planilha": "Cabeçalhos incorretos. Baixe e use o modelo oficial."})

    rows = []
    seen = set()
    for row_number, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        if not values or all(value is None or str(value).strip() == "" for value in values):
            continue
        if row_number > 5001:
            raise ValidationError({"planilha": "A planilha pode conter até 5.000 produtos por envio."})
        data = dict(zip(headers, values))
        codigo = str(data.get("codigo") or "").strip()
        nome = str(data.get("nome") or "").strip()
        setor = str(data.get("setor") or "").strip()
        endereco = str(data.get("endereco") or "").strip()
        if not codigo or len(codigo) > 50 or not nome or len(nome) > 200:
            raise ValidationError({"planilha": f"Linha {row_number}: código e nome são obrigatórios."})
        if not setor or len(setor) > 80 or not endereco or len(endereco) > 100:
            raise ValidationError({"planilha": f"Linha {row_number}: setor e endereço são obrigatórios."})
        if codigo in seen:
            raise ValidationError({"planilha": f"Linha {row_number}: código duplicado ({codigo})."})
        seen.add(codigo)
        rows.append({
            "codigo": codigo, "nome": nome,
            "categoria": str(data.get("categoria") or "Geral").strip()[:120] or "Geral",
            "unidade": str(data.get("unidade") or "UN").strip()[:12],
            "preco_venda": _decimal(data.get("preco_venda"), "preco_venda", row_number),
            "estoque_atual": _decimal(data.get("estoque_atual"), "estoque_atual", row_number),
            "estoque_minimo": _decimal(data.get("estoque_minimo") or 0, "estoque_minimo", row_number),
            "setor": setor, "endereco": endereco,
            "codigo_barras": str(data.get("codigo_barras") or "").strip()[:50],
        })
    if not rows:
        raise ValidationError({"planilha": "Não há linhas de produtos para importar."})

    batch_id = uuid.uuid4().hex[:12]
    created = updated = 0
    for data in rows:
        category = Categoria.objects.filter(
            empresa=empresa, nome__iexact=data["categoria"]
        ).first()
        if category is None:
            category = Categoria.objects.create(empresa=empresa, nome=data["categoria"])
        product = Produto.objects.select_for_update().filter(
            empresa=empresa, codigo=data["codigo"]
        ).first()
        if product:
            previous_stock = product.estoque_atual
            for field in ("nome", "unidade", "preco_venda", "estoque_minimo",
                          "setor", "endereco", "codigo_barras"):
                setattr(product, field, data[field])
            product.categoria = category
            product.estoque_atual = data["estoque_atual"]
            product.save()
            updated += 1
        else:
            previous_stock = Decimal("0")
            product = Produto.objects.create(
                empresa=empresa, categoria=category, **data
            )
            created += 1
        difference = data["estoque_atual"] - previous_stock
        if difference:
            MovimentacaoEstoque.objects.create(
                empresa=empresa, produto=product,
                tipo=MovimentacaoEstoque.Tipo.AJUSTE, quantidade=difference,
                referencia=f"Importação Excel {batch_id}", operador=usuario,
            )
    return {"criados": created, "atualizados": updated, "lote": batch_id}


@transaction.atomic
def gerar_contagens_aleatorias(*, empresa, usuario, quantidade, itens_por_contagem):
    sectors = list(
        Produto.objects.filter(empresa=empresa, ativo=True)
        .exclude(setor="").values_list("setor", flat=True).distinct()
    )
    if not sectors:
        raise ValidationError({"inventario": "Cadastre/import products with sector and address first."})
    slots = []
    today = timezone.localdate()
    for day_offset in range(1, 31):
        scheduled_day = today + timedelta(days=day_offset)
        if scheduled_day.weekday() == 6:
            continue
        for sector in sectors:
            exists = InventarioContagem.objects.filter(
                empresa=empresa, data_programada=scheduled_day, setor=sector
            ).exists()
            if not exists:
                slots.append((scheduled_day, sector))
    random.SystemRandom().shuffle(slots)
    if quantidade < 1 or quantidade > len(slots):
        raise ValidationError({"inventario": f"Escolha de 1 a {len(slots)} contagens disponíveis nos próximos 30 dias."})
    generated = []
    for scheduled_day, sector in slots[:quantidade]:
        generated.append(InventarioContagem.objects.create(
            empresa=empresa, setor=sector, data_programada=scheduled_day,
            quantidade_itens=itens_por_contagem, criado_por=usuario,
        ))
    return generated


@transaction.atomic
def iniciar_contagem(*, contagem, usuario):
    contagem = InventarioContagem.objects.select_for_update().get(pk=contagem.pk)
    if contagem.estado != InventarioContagem.Estado.PROGRAMADO:
        raise ValidationError({"inventario": "Esta contagem não está aguardando início."})
    if contagem.data_programada > timezone.localdate():
        raise ValidationError({"inventario": "A contagem só pode ser iniciada na data sorteada."})
    products = list(Produto.objects.filter(
        empresa=contagem.empresa, setor=contagem.setor, ativo=True
    ).exclude(contagens__contagem__estado=InventarioContagem.Estado.ABERTO))
    if not products:
        raise ValidationError({"inventario": "Não há produtos disponíveis neste setor para contar."})
    rng = random.SystemRandom()
    selected = rng.sample(products, min(contagem.quantidade_itens, len(products)))
    for product in selected:
        product = Produto.objects.select_for_update().get(pk=product.pk)
        ItemContagem.objects.create(
            contagem=contagem, produto=product, saldo_sistema=product.estoque_atual,
        )
    contagem.estado = InventarioContagem.Estado.ABERTO
    contagem.iniciado_em = timezone.now()
    contagem.save(update_fields=("estado", "iniciado_em"))
    return contagem


@transaction.atomic
def concluir_contagem(*, contagem, usuario, contagens):
    contagem = InventarioContagem.objects.select_for_update().get(pk=contagem.pk)
    if contagem.estado != InventarioContagem.Estado.ABERTO:
        raise ValidationError({"inventario": "A contagem não está aberta."})
    items = list(contagem.itens.select_related("produto").order_by("pk"))
    if not items:
        raise ValidationError({"inventario": "A contagem está sem produtos."})
    checked = {}
    for item in items:
        raw = contagens.get(str(item.pk))
        if raw in (None, ""):
            raise ValidationError({"inventario": f"Informe a contagem física de {item.produto.nome}."})
        checked[item.pk] = _decimal(raw, "quantidade contada", item.pk, minimum=Decimal("0"))
    for item in items:
        product = Produto.objects.select_for_update().get(pk=item.produto_id, empresa=contagem.empresa)
        physical = checked[item.pk]
        system_balance = product.estoque_atual
        delta = physical - system_balance
        product.estoque_atual = physical
        product.save(update_fields=("estoque_atual", "atualizado_em"))
        item.saldo_sistema = system_balance
        item.quantidade_contada = physical
        item.diferenca = delta
        item.contado_por = usuario
        item.contado_em = timezone.now()
        item.save(update_fields=("saldo_sistema", "quantidade_contada", "diferenca", "contado_por", "contado_em"))
        if delta:
            MovimentacaoEstoque.objects.create(
                empresa=contagem.empresa, produto=product,
                tipo=MovimentacaoEstoque.Tipo.AJUSTE, quantidade=delta,
                referencia=f"Contagem preventiva #{contagem.pk}", operador=usuario,
            )
    contagem.estado = InventarioContagem.Estado.CONCLUIDO
    contagem.concluido_em = timezone.now()
    contagem.save(update_fields=("estado", "concluido_em"))
    return contagem
