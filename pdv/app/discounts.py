from decimal import Decimal, ROUND_HALF_UP


def distribute_discount(gross_values, mode, value):
    gross_values = [Decimal(str(v)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP) for v in gross_values]
    gross = sum(gross_values, Decimal('0'))
    value = Decimal(str(value))
    if not value.is_finite() or value < 0 or mode not in ('R$', '%'):
        raise ValueError('Desconto inválido.')
    if mode == '%' and value > 100:
        raise ValueError('Percentual não pode ultrapassar 100%.')
    discount = (gross * value / 100 if mode == '%' else value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    if discount > gross:
        raise ValueError('Desconto não pode ultrapassar o total.')
    if not gross:
        return [Decimal('0.00') for _ in gross_values]
    result, remaining = [], discount
    for index, amount in enumerate(gross_values):
        part = min(amount, remaining, (discount * amount / gross).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
        result.append(part)
        remaining -= part
    # Allocate any rounding cents within each line's remaining capacity.
    for index, amount in enumerate(gross_values):
        part = min(remaining, amount - result[index])
        result[index] += part
        remaining -= part
    return result
