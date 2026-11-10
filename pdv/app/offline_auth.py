"""Login sem internet no PDV instalado.

Depois de um login online bem-sucedido, o PDV guarda só uma verificação PBKDF2 da senha
(com sal aleatório), nunca a senha. Ela vale por OFFLINE_DIAS (30) e é renovada a cada
login online. Sem internet, o operador entra com a mesma senha; ao voltar a rede, o acesso
é renovado com o token de renovação guardado, e o servidor volta a validar tudo.
"""
import hashlib
import hmac
import os
import time

OFFLINE_DIAS = 30
ITERACOES = 200_000


def criar_registro(senha, refresh="", dias=OFFLINE_DIAS, agora=None):
    sal = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), sal, ITERACOES)
    return {"salt": sal.hex(), "hash": digest.hex(), "iterations": ITERACOES, "refresh": refresh or "",
            "valid_until": (agora or time.time()) + dias * 86400}


def conferir(registro, senha, agora=None):
    """Devolve (ok, motivo). Motivo explica a recusa em linguagem do operador."""
    if not registro:
        return False, "Este usuário ainda não entrou online neste computador."
    if (agora or time.time()) > float(registro["valid_until"]):
        return False, "O acesso sem internet venceu (30 dias sem entrar online). Conecte-se para renovar."
    digest = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), bytes.fromhex(registro["salt"]), int(registro["iterations"]))
    if not hmac.compare_digest(digest.hex(), registro["hash"]):
        return False, "Usuário ou senha inválidos."
    return True, ""
