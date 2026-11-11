"""Rotina diária da plataforma: cobrança (faturas, avisos por e-mail, suspensões) e backup.

Agende no Coolify (Scheduled Tasks), por exemplo todo dia às 6h:  python manage.py tarefas_diarias
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Processa a cobrança e gera o backup (com envio ao S3 quando configurado)."

    def handle(self, *args, **opcoes):
        falhas = []
        for comando in ("processar_cobranca", "backup_dados"):
            try:
                call_command(comando, stdout=self.stdout, stderr=self.stderr)
            except (Exception, SystemExit) as erro:  # noqa: BLE001 - uma etapa não impede a outra
                falhas.append(f"{comando}: {erro}")
                self.stderr.write(self.style.ERROR(f"{comando} falhou: {erro}"))
        if falhas:
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("Tarefas diárias concluídas."))
