"""Cópia de segurança completa dos dados (JSON compactado), com rotação.

Uso:  python manage.py backup_dados            # grava em BACKUP_DIR e mantém BACKUP_MANTER cópias
      python manage.py backup_dados --listar
Restaurar em um banco vazio (depois de `migrate`):
      python manage.py loaddata backups/appdv-AAAAMMDD-HHMMSS.json.gz
"""
import gzip
import io
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

EXCLUIR = ("contenttypes", "auth.permission", "sessions", "admin.logentry")


class Command(BaseCommand):
    help = "Gera uma cópia de segurança dos dados em BACKUP_DIR e apaga as mais antigas."

    def add_arguments(self, parser):
        parser.add_argument("--destino", help="Pasta de destino (padrão: BACKUP_DIR).")
        parser.add_argument("--manter", type=int, help="Quantas cópias manter (padrão: BACKUP_MANTER).")
        parser.add_argument("--listar", action="store_true", help="Só lista as cópias existentes.")

    def handle(self, *args, **opcoes):
        pasta = Path(opcoes.get("destino") or settings.BACKUP_DIR)
        pasta.mkdir(parents=True, exist_ok=True)
        existentes = sorted(pasta.glob("appdv-*.json.gz"))
        if opcoes["listar"]:
            for arquivo in existentes:
                self.stdout.write(f"{arquivo.name}\t{arquivo.stat().st_size // 1024} KB")
            return
        nome = pasta / f"appdv-{timezone.localtime():%Y%m%d-%H%M%S}.json.gz"
        temporario = nome.with_suffix(".tmp")
        buffer = io.StringIO()
        call_command("dumpdata", natural_foreign=True, exclude=list(EXCLUIR), indent=None, stdout=buffer)
        with gzip.open(temporario, "wt", encoding="utf-8") as saida:
            saida.write(buffer.getvalue())
        temporario.rename(nome)
        manter = max(1, opcoes.get("manter") or settings.BACKUP_MANTER)
        antigas = sorted(pasta.glob("appdv-*.json.gz"))[:-manter]
        for arquivo in antigas:
            arquivo.unlink()
        self.stdout.write(self.style.SUCCESS(f"Backup gravado em {nome} ({nome.stat().st_size // 1024} KB). Cópias mantidas: {manter}."))
