"""Ciclo diário da cobrança: gera faturas, marca vencidas, suspende e libera empresas.

Agende no Coolify (Scheduled Tasks), por exemplo todo dia às 6h:  python manage.py processar_cobranca
O sistema também roda o ciclo sozinho no máximo uma vez por hora, puxado pelo uso.
"""
from django.core.management.base import BaseCommand

from core import cobranca


class Command(BaseCommand):
    help = "Gera faturas e suspende/libera empresas conforme os pagamentos."

    def handle(self, *args, **opcoes):
        resultado = cobranca.processar()
        resumo = ", ".join(f"{estado}: {total}" for estado, total in sorted(resultado.items())) or "nenhuma empresa"
        self.stdout.write(self.style.SUCCESS(f"Cobrança processada · {resumo}"))
