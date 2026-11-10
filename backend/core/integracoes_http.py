"""Cliente HTTP mínimo (biblioteca padrão) usado pelas integrações fiscais e de PIX.

Fica isolado para que os testes troquem `enviar` por um dublê sem acessar a rede.
"""
import base64
import json
import urllib.error
import urllib.request


class ErroIntegracao(Exception):
    """Falha de comunicação ou resposta inesperada de um provedor externo."""

    def __init__(self, mensagem, status=None, corpo=None):
        super().__init__(mensagem)
        self.status = status
        self.corpo = corpo or {}


def enviar(metodo, url, *, corpo=None, token_basico=None, token_bearer=None, cabecalhos=None, timeout=30):
    """Envia JSON e devolve (status, dict). Erros HTTP com corpo JSON também são devolvidos."""
    dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    headers = {"Accept": "application/json", "User-Agent": "ApPdv/1.0"}
    if dados is not None:
        headers["Content-Type"] = "application/json"
    if token_basico:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{token_basico}:".encode()).decode()
    if token_bearer:
        headers["Authorization"] = f"Bearer {token_bearer}"
    headers.update(cabecalhos or {})
    requisicao = urllib.request.Request(url, data=dados, method=metodo, headers=headers)
    try:
        with urllib.request.urlopen(requisicao, timeout=timeout) as resposta:
            return resposta.status, _json(resposta.read())
    except urllib.error.HTTPError as erro:
        return erro.code, _json(erro.read())
    except (urllib.error.URLError, TimeoutError, OSError) as erro:
        raise ErroIntegracao(f"Não foi possível falar com o provedor: {erro}") from erro


def _json(bruto):
    if not bruto:
        return {}
    try:
        valor = json.loads(bruto.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"mensagem": bruto[:500].decode("utf-8", "replace")}
    return valor if isinstance(valor, dict) else {"dados": valor}
