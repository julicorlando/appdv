"""Apaga todos os dados operacionais de UMA empresa, mantendo a empresa, os usuários e a assinatura.

Uso:
    python manage.py limpar_empresa --empresa "Armazém G4"            # só mostra o que seria apagado
    python manage.py limpar_empresa --empresa 12 --confirmar           # apaga de verdade (pede para digitar o nome)
    python manage.py limpar_empresa --empresa 12 --confirmar --sim     # apaga sem perguntar (para scripts)

Fica preservado: a Empresa (cadastro, dados fiscais e integrações), membros e perfis de usuários,
assinatura e faturas do Master, terminais PDV autorizados, sessões do PDV e a política de desconto.
Todo o resto da empresa (produtos, categorias, estoque, depósitos, clientes, fornecedores, vendas, caixas,
NFC-e, pedidos, orçamentos, compras, contas a pagar/receber, créditos, Pix, inventários e histórico) é apagado.
A operação é feita numa única transação: ou apaga tudo, ou nada.
"""
from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import ProtectedError, RestrictedError
from django.db.models.fields.related import ForeignKey, OneToOneField

from core.models import Empresa

MANTER = {
    "Empresa", "MembroEmpresa", "UsuarioPerfil", "Assinatura", "Fatura", "TerminalPDV",
    "SessaoDesktopTicket", "PoliticaDesconto", "Plano", "ConfiguracaoMaster", "Cupom",
}


def _querysets(empresa):
    """Para cada modelo do app core (fora de MANTER), o queryset dos registros que pertencem à empresa."""
    modelos = [m for m in apps.get_app_config("core").get_models() if m.__name__ not in MANTER]
    resolvidos = {}
    pendentes = list(modelos)
    while pendentes:
        progresso = False
        for modelo in list(pendentes):
            campos = {f.name: f for f in modelo._meta.get_fields() if isinstance(f, (ForeignKey, OneToOneField))}
            qs = None
            if "empresa" in campos and campos["empresa"].related_model is Empresa:
                qs = modelo.objects.filter(empresa=empresa)
            else:
                for nome, campo in campos.items():
                    pai = campo.related_model
                    if pai in resolvidos:
                        qs = modelo.objects.filter(**{f"{nome}__in": resolvidos[pai]})
                        break
            if qs is not None:
                resolvidos[modelo] = qs
                pendentes.remove(modelo)
                progresso = True
        if not progresso:
            nomes = ", ".join(m.__name__ for m in pendentes)
            raise CommandError(f"Não sei a que empresa pertencem estes modelos: {nomes}. Atualize limpar_empresa.py.")
    return resolvidos


def contar(empresa):
    return {m.__name__: qs.count() for m, qs in _querysets(empresa).items()}


def limpar(empresa):
    """Apaga tudo em ordem segura (os PROTECT obrigam apagar filhos antes dos pais). Retorna contagem apagada."""
    apagados = {}
    with transaction.atomic():
        restantes = dict(_querysets(empresa))
        for _ in range(len(restantes) + 5):
            if not restantes:
                break
            for modelo, qs in list(restantes.items()):
                if not qs.exists():
                    restantes.pop(modelo)
                    continue
                try:
                    with transaction.atomic():
                        _, por_modelo = qs.delete()
                except (ProtectedError, RestrictedError):
                    continue
                for rotulo, n in por_modelo.items():
                    nome = rotulo.split(".")[-1]
                    apagados[nome] = apagados.get(nome, 0) + n
                restantes.pop(modelo)
        if restantes:
            nomes = ", ".join(m.__name__ for m in restantes)
            raise CommandError(f"Não consegui apagar: {nomes}. Nada foi alterado.")
    return apagados


class Command(BaseCommand):
    help = "Apaga os dados de uma empresa (produtos, estoque, clientes, vendas...) mantendo empresa e usuários."

    def add_arguments(self, parser):
        parser.add_argument("--empresa", required=True, help="ID ou nome exato da empresa.")
        parser.add_argument("--confirmar", action="store_true", help="Apaga de verdade. Sem isto, só simula.")
        parser.add_argument("--sim", action="store_true", help="Não pede para digitar o nome da empresa.")

    def handle(self, *args, **opts):
        alvo = opts["empresa"].strip()
        if alvo.isdigit():
            empresa = Empresa.objects.filter(pk=int(alvo)).first()
        else:
            achadas = list(Empresa.objects.filter(nome__iexact=alvo))
            if len(achadas) > 1:
                lista = ", ".join(f"{e.pk} ({e.nome})" for e in achadas)
                raise CommandError(f"Mais de uma empresa com esse nome: {lista}. Use o ID.")
            empresa = achadas[0] if achadas else None
        if not empresa:
            raise CommandError(f"Empresa não encontrada: {alvo}")

        contagem = {k: v for k, v in contar(empresa).items() if v}
        self.stdout.write(f"Empresa #{empresa.pk} — {empresa.nome}")
        if not contagem:
            self.stdout.write(self.style.SUCCESS("Nada para apagar: a empresa já está limpa."))
            return
        for nome, n in sorted(contagem.items()):
            self.stdout.write(f"  {nome}: {n}")
        self.stdout.write(f"  Total: {sum(contagem.values())} registros")

        if not opts["confirmar"]:
            self.stdout.write(self.style.WARNING("Simulação: nada foi apagado. Rode de novo com --confirmar para apagar."))
            return
        if not opts["sim"]:
            digitado = input(f'Isto não pode ser desfeito. Digite o nome da empresa ("{empresa.nome}") para confirmar: ')
            if digitado.strip().lower() != empresa.nome.strip().lower():
                raise CommandError("Nome não confere. Nada foi apagado.")

        apagados = limpar(empresa)
        self.stdout.write(self.style.SUCCESS(f"Pronto: {sum(apagados.values())} registros apagados de {empresa.nome}."))
        self.stdout.write("Empresa, usuários, assinatura e terminais foram mantidos.")
