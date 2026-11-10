"""EAN-13 para etiquetas: validação, código interno (prefixo 2) e desenho em SVG."""

_L = ("0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011")
_G = tuple(padrao[::-1].translate(str.maketrans("01", "10")) for padrao in _L)
_R = tuple(padrao.translate(str.maketrans("01", "10")) for padrao in _L)
_PARIDADE = ("LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL")


def digito_verificador(doze):
    soma = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(doze))
    return str((10 - soma % 10) % 10)


def ean13_valido(codigo):
    codigo = str(codigo or "")
    return len(codigo) == 13 and codigo.isdigit() and digito_verificador(codigo[:12]) == codigo[12]


def ean13_interno(empresa_id, produto_id):
    """Código de uso interno da loja (faixa 2xx reservada pelo GS1 para uso restrito)."""
    doze = f"2{int(empresa_id) % 100:02d}{int(produto_id) % 10**9:09d}"
    return doze + digito_verificador(doze)


def ean13_svg(codigo, largura_modulo=2, altura=60):
    if not ean13_valido(codigo):
        return ""
    paridade = _PARIDADE[int(codigo[0])]
    bits = "101"
    for posicao, digito in enumerate(codigo[1:7]):
        bits += (_L if paridade[posicao] == "L" else _G)[int(digito)]
    bits += "01010"
    for digito in codigo[7:]:
        bits += _R[int(digito)]
    bits += "101"
    margem = 11 * largura_modulo
    total = len(bits) * largura_modulo + 2 * margem
    barras = []
    for indice, bit in enumerate(bits):
        if bit == "1":
            guarda = indice < 3 or 45 <= indice < 50 or indice >= 92
            barras.append(
                f'<rect x="{margem + indice * largura_modulo}" y="0" width="{largura_modulo}" '
                f'height="{altura + (8 if guarda else 0)}"/>'
            )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} {altura + 22}" '
        f'role="img" aria-label="Código de barras {codigo}">'
        f'<rect width="100%" height="100%" fill="#fff"/><g fill="#000">{"".join(barras)}</g>'
        f'<text x="{total / 2}" y="{altura + 20}" font-family="monospace" font-size="14" text-anchor="middle">{codigo}</text></svg>'
    )
