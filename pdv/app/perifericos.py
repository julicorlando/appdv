"""Periféricos do caixa: gaveta de dinheiro, balança e etiqueta de balança (EAN-13 com preço ou peso).

Sem dependência da interface: a configuração fica num dicionário salvo no QSettings do PDV.
"""
import socket
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# ESC p 0 25 250: pulso no conector da gaveta (padrão ESC/POS: Epson, Elgin, Bematech, Daruma, Tanca…).
PULSO_GAVETA = b"\x1b\x70\x00\x19\xfa"
UNIDADES_PESO = {"KG", "KGS", "KILO", "QUILO", "G", "GR"}

PADRAO = {
    "gaveta_modo": "",          # "" | windows | serial | rede
    "gaveta_destino": "",       # nome da impressora, COM3 ou 192.168.0.50:9100
    "gaveta_dinheiro": True,    # abre sozinha em venda com dinheiro
    "balanca_porta": "",        # COM4 (vazio = sem balança)
    "balanca_velocidade": 9600,
    "etiqueta_ativa": False,
    "etiqueta_prefixo": "2",
    "etiqueta_digitos_codigo": 4,
    "etiqueta_tipo": "preco",   # preco (R$ com 2 casas) | peso (kg com 3 casas)
}


class ErroPeriferico(Exception):
    pass


def carregar(settings):
    cfg = dict(PADRAO)
    for chave, padrao in PADRAO.items():
        valor = settings.value(f"perifericos/{chave}", padrao)
        if isinstance(padrao, bool):
            valor = valor in (True, "true", "True", "1", 1)
        elif isinstance(padrao, int):
            try:
                valor = int(valor)
            except (TypeError, ValueError):
                valor = padrao
        else:
            valor = str(valor or "")
        cfg[chave] = valor
    return cfg


def salvar(settings, cfg):
    for chave in PADRAO:
        settings.setValue(f"perifericos/{chave}", cfg.get(chave, PADRAO[chave]))


# ------------------------------------------------------------------ gaveta

def abrir_gaveta(cfg, serial_factory=None, conectar=socket.create_connection):
    modo, destino = cfg.get("gaveta_modo", ""), str(cfg.get("gaveta_destino", "")).strip()
    if not modo:
        return False
    if not destino:
        raise ErroPeriferico("Informe a impressora/porta da gaveta em Periféricos.")
    try:
        if modo == "rede":
            host, _, porta = destino.partition(":")
            with conectar((host, int(porta or 9100)), timeout=3) as conexao:
                conexao.sendall(PULSO_GAVETA)
        elif modo == "serial":
            factory = serial_factory or _serial()
            with factory(destino, 9600, timeout=2) as porta:
                porta.write(PULSO_GAVETA)
        elif modo == "windows":
            _imprimir_bruto_windows(destino, PULSO_GAVETA)
        else:
            raise ErroPeriferico("Modo de gaveta desconhecido.")
    except ErroPeriferico:
        raise
    except Exception as exc:  # porta ocupada, impressora desligada, rede…
        raise ErroPeriferico(f"Não consegui abrir a gaveta: {exc}") from exc
    return True


def _imprimir_bruto_windows(impressora, dados):
    try:
        import win32print  # pywin32, só no Windows
    except ImportError as exc:
        raise ErroPeriferico("Impressão direta disponível só no Windows.") from exc
    handle = win32print.OpenPrinter(impressora)
    try:
        win32print.StartDocPrinter(handle, 1, ("ApPdv gaveta", None, "RAW"))
        try:
            win32print.StartPagePrinter(handle)
            win32print.WritePrinter(handle, dados)
            win32print.EndPagePrinter(handle)
        finally:
            win32print.EndDocPrinter(handle)
    finally:
        win32print.ClosePrinter(handle)


def impressoras_windows():
    try:
        import win32print
        return [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
    except Exception:  # noqa: BLE001
        return []


# ------------------------------------------------------------------ balança

def _serial():
    try:
        import serial  # pyserial
    except ImportError as exc:
        raise ErroPeriferico("Comunicação serial indisponível neste computador.") from exc
    return serial.Serial


def balanca_configurada(cfg):
    return bool(str(cfg.get("balanca_porta", "")).strip())


def ler_peso(cfg, serial_factory=None):
    """Protocolo Toledo/Filizola/Urano (ENQ → STX peso ETX), o mais comum nas balanças de checkout."""
    porta = str(cfg.get("balanca_porta", "")).strip()
    if not porta:
        raise ErroPeriferico("Balança não configurada.")
    factory = serial_factory or _serial()
    try:
        with factory(porta, int(cfg.get("balanca_velocidade") or 9600), timeout=1.5) as conexao:
            conexao.reset_input_buffer()
            conexao.write(b"\x05")
            resposta = conexao.read_until(b"\x03", 32)
    except ErroPeriferico:
        raise
    except Exception as exc:
        raise ErroPeriferico(f"Não consegui ler a balança: {exc}") from exc
    return interpretar_peso(resposta)


def interpretar_peso(resposta):
    """"01250" → 1,250 kg; também aceita texto com ponto/vírgula ("1.250", "0,385 kg")."""
    texto = bytes(resposta or b"").decode("ascii", "ignore").strip("\x02\x03\r\n ").upper()
    if not texto:
        raise ErroPeriferico("A balança não respondeu. Confira o cabo e a porta.")
    if any(sinal in texto for sinal in ("I", "N", "S")):
        raise ErroPeriferico("Peso instável ou fora da faixa. Aguarde estabilizar.")
    numero = "".join(c for c in texto if c.isdigit() or c in ".,").replace(",", ".")
    try:
        if "." in numero:
            peso = Decimal(numero)
        elif len(numero) >= 5:
            peso = Decimal(numero[-5:]) / 1000
        else:
            raise ErroPeriferico("Resposta da balança não reconhecida.")
    except InvalidOperation as exc:
        raise ErroPeriferico("Resposta da balança não reconhecida.") from exc
    peso = peso.quantize(Decimal("0.001"))
    if peso <= 0:
        raise ErroPeriferico("Coloque o produto na balança.")
    return peso


def vende_por_peso(produto):
    return str(produto.get("unidade", "")).strip().upper() in UNIDADES_PESO


# ------------------------------------------------------------------ etiqueta de balança

def ean13_valido(codigo):
    if len(codigo) != 13 or not codigo.isdigit():
        return False
    soma = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(codigo[:12]))
    return (10 - soma % 10) % 10 == int(codigo[12])


def ler_etiqueta(codigo, cfg):
    """EAN-13 da balança: prefixo + código do produto + valor (5 dígitos antes do verificador).

    Ex. com 4 dígitos de código: 2 0123 00 01590 D → produto 123, R$ 15,90 (ou 1,590 kg).
    """
    codigo = str(codigo or "").strip()
    prefixo = str(cfg.get("etiqueta_prefixo") or "2")
    if not cfg.get("etiqueta_ativa") or not codigo.startswith(prefixo) or not ean13_valido(codigo):
        return None
    digitos = min(max(int(cfg.get("etiqueta_digitos_codigo") or 4), 4), 6)
    inicio = len(prefixo)
    if inicio + digitos > 7:
        return None
    produto = codigo[inicio:inicio + digitos].lstrip("0") or "0"
    bruto = Decimal(codigo[7:12])
    tipo = "peso" if cfg.get("etiqueta_tipo") == "peso" else "preco"
    valor = (bruto / 1000).quantize(Decimal("0.001")) if tipo == "peso" else (bruto / 100).quantize(Decimal("0.01"))
    return {"codigo": produto, "tipo": tipo, "valor": valor}


def produto_da_etiqueta(produtos, etiqueta):
    for produto in produtos:
        for campo in ("codigo", "codigo_barras"):
            valor = str(produto.get(campo, "")).strip()
            if valor and valor.isdigit() and (valor.lstrip("0") or "0") == etiqueta["codigo"]:
                return produto
    return None


def quantidade_da_etiqueta(produto, etiqueta):
    if etiqueta["tipo"] == "peso":
        return etiqueta["valor"]
    preco = Decimal(str(produto.get("preco_venda") or "0"))
    if preco <= 0:
        raise ErroPeriferico("Produto sem preço: não dá para calcular o peso da etiqueta.")
    return (etiqueta["valor"] / preco).quantize(Decimal("0.001"), ROUND_HALF_UP)
