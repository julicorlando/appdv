"""ApPdv — PDV instalado (Windows)."""

VERSION = "0.3.1"


def parse_version(value):
    """'0.3.0' -> (0, 3, 0). Partes inválidas contam como zero."""
    partes = []
    for parte in str(value or "0").split(".")[:3]:
        digitos = "".join(ch for ch in parte if ch.isdigit())
        partes.append(int(digitos) if digitos else 0)
    return tuple(partes + [0] * (3 - len(partes)))
