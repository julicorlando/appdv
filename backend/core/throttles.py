"""Limites de tentativas para login e para senhas de gerente digitadas no PDV.

Os contadores ficam no cache padrão. Em produção ele é o cache em banco
(compartilhado entre os workers do gunicorn); veja CACHES em settings.py.
"""
from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle


class _PorCampoThrottle(SimpleRateThrottle):
    campo = "username"

    def get_cache_key(self, request, view):
        try:
            valor = str(request.data.get(self.campo, "")).strip().lower()
        except AttributeError:
            valor = ""
        if not valor:
            return None
        return self.cache_format % {"scope": self.scope, "ident": valor[:150]}


class LoginUsuarioThrottle(_PorCampoThrottle):
    """Impede tentar muitas senhas para o mesmo usuário, de qualquer origem."""
    scope = "login_usuario"
    campo = "username"


class LoginIPThrottle(SimpleRateThrottle):
    """Impede que um mesmo endereço teste muitos usuários diferentes."""
    scope = "login_ip"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class AutorizacaoOperadorThrottle(UserRateThrottle):
    """Tentativas de autorização (desconto/descarte) feitas por um operador."""
    scope = "autorizar_desconto"


class AutorizacaoAprovadorThrottle(_PorCampoThrottle):
    """Tentativas contra a senha de um mesmo gerente, vindas de qualquer operador."""
    scope = "aprovador"
    campo = "usuario"
