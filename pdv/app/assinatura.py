"""Aviso de assinatura suspensa no PDV.

O servidor responde 403 com {"codigo": "assinatura_suspensa"} quando a mensalidade da empresa não foi paga.
O caixa continua vendendo (as vendas ficam guardadas no computador) por alguns dias de tolerância e depois
trava novas vendas até o pagamento ser confirmado.
"""
import time

TOLERANCIA_DIAS = 3


def codigo_erro(exc):
    response = getattr(exc, "response", None)
    if response is None or getattr(response, "status_code", None) not in (400, 403):
        return ""
    try:
        dados = response.json()
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(dados, dict):
        return ""
    if dados.get("codigo") == "assinatura_suspensa":
        return "assinatura_suspensa"
    if "caixas" in dados:
        return "limite_caixas"
    return ""


def mensagem_erro(exc):
    response = getattr(exc, "response", None)
    try:
        dados = response.json()
        valor = dados.get("detail") or dados.get("caixas") or ""
        if isinstance(valor, list):
            valor = " ".join(str(v) for v in valor)
        return str(valor)
    except Exception:  # noqa: BLE001
        return str(exc)


class EstadoSuspensao:
    """Guarda quando a suspensão foi vista pela primeira vez (por empresa) no QSettings."""

    def __init__(self, settings, tolerancia_dias=TOLERANCIA_DIAS):
        self.settings = settings
        self.tolerancia = tolerancia_dias * 86400

    def _chave(self, empresa):
        return f"assinatura/suspensa_desde/{empresa}"

    def registrar(self, empresa, agora=None):
        if not self.desde(empresa):
            self.settings.setValue(self._chave(empresa), float(agora or time.time()))

    def limpar(self, empresa):
        self.settings.remove(self._chave(empresa))

    def desde(self, empresa):
        try:
            return float(self.settings.value(self._chave(empresa), 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    def dias_restantes(self, empresa, agora=None):
        inicio = self.desde(empresa)
        if not inicio:
            return None
        return max(0.0, (inicio + self.tolerancia - (agora or time.time())) / 86400)

    def travado(self, empresa, agora=None):
        restante = self.dias_restantes(empresa, agora)
        return restante is not None and restante <= 0
