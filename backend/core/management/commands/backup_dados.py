"""Cópia de segurança completa dos dados (JSON compactado), com rotação.

Uso:  python manage.py backup_dados            # grava em BACKUP_DIR e mantém BACKUP_MANTER cópias
      python manage.py backup_dados --listar
Com BACKUP_S3_* configurado, cada cópia também é enviada ao armazenamento externo (use --sem-s3 para pular).
Restaurar em um banco vazio (depois de `migrate`):
      python manage.py loaddata backups/appdv-AAAAMMDD-HHMMSS.json.gz
"""
import gzip
import io
import json
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
        parser.add_argument("--sem-s3", action="store_true", help="Não envia a cópia para o S3 mesmo se configurado.")

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
        from core import backup_s3
        estado = {"arquivo": nome.name, "kb": nome.stat().st_size // 1024, "em": timezone.now().isoformat(), "s3": ""}
        if backup_s3.configurado() and not opcoes.get("sem_s3"):
            try:
                estado["s3"] = backup_s3.enviar(nome)
                self.stdout.write(self.style.SUCCESS(f"Cópia externa enviada: {estado['s3']}"))
            except backup_s3.ErroBackupS3 as erro:
                estado["erro_s3"] = str(erro)
                self.stderr.write(self.style.ERROR(f"Falha no envio externo: {erro}"))
        (pasta / "ultimo_backup.json").write_text(json.dumps(estado), encoding="utf-8")
        if estado.get("erro_s3"):
            raise SystemExit(1)
