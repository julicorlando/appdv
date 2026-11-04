import re


def normalize_cpf(value):
    cpf = re.sub(r'[.\-\s]', '', value)
    if len(cpf) != 11 or not cpf.isascii() or not cpf.isdigit() or len(set(cpf)) == 1:
        raise ValueError('Informe um CPF válido.')
    for size in (9, 10):
        if (sum(int(cpf[i]) * (size + 1 - i) for i in range(size)) * 10 % 11) % 10 != int(cpf[size]):
            raise ValueError('CPF com dígitos verificadores inválidos.')
    return cpf
