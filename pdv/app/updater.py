"""Atualização do PDV pelo próprio aplicativo (botão "Atualizar ApPdv").

O servidor informa a versão atual e o endereço do instalador. O PDV baixa o instalador,
confere o SHA-256 publicado junto com ele, fecha e executa a instalação silenciosa; ao final
o instalador abre o ApPdv de novo. Vendas guardadas e o caixa aberto ficam na pasta de dados
do usuário, que a instalação não altera.
"""
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests
from PySide6.QtCore import QThread, Signal

# Downloads só do GitHub Releases (e redirecionamentos dele) ou do próprio servidor do ApPdv.
HOSTS_PERMITIDOS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")


def host_permitido(url, servidor=""):
    partes = urlparse(url or "")
    if partes.scheme != "https" or not partes.hostname or partes.username or partes.password:
        return False
    host = partes.hostname.lower()
    proprio = (urlparse(servidor).hostname or "").lower() if servidor else ""
    return host in HOSTS_PERMITIDOS or host.endswith(".githubusercontent.com") or (proprio and host == proprio)


def instalado():
    """True quando roda a partir da instalação (o Inno Setup deixa o desinstalador ao lado do .exe)."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return False
    return any(Path(sys.executable).parent.glob("unins*.exe"))


def iniciar_instalador(caminho):
    """Instalação silenciosa em segundo plano; o ApPdv deve fechar logo em seguida."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(caminho), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                     creationflags=flags, close_fds=True)


class DownloadWorker(QThread):
    progress = Signal(int, int)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, url, sha_url, versao, servidor="", parent=None, session=None):
        super().__init__(parent)
        self.url, self.sha_url, self.versao, self.servidor = url, sha_url, versao, servidor
        self.session = session or requests.Session()
        self.cancelado = False

    def run(self):
        try:
            self.done.emit(str(self.baixar()))
        except Exception as exc:
            self.failed.emit(str(exc))

    def baixar(self):
        if not host_permitido(self.url, self.servidor) or not host_permitido(self.sha_url, self.servidor):
            raise ValueError("Endereço de atualização não permitido.")
        esperado = self._sha_publicado()
        destino = Path(tempfile.gettempdir()) / f"ApPdv-Setup-{self.versao}.exe"
        parcial = destino.with_suffix(".part")
        digest = hashlib.sha256()
        with self.session.get(self.url, stream=True, timeout=30) as resposta:
            resposta.raise_for_status()
            if not host_permitido(resposta.url, self.servidor):
                raise ValueError("O download foi redirecionado para um endereço não permitido.")
            total = int(resposta.headers.get("Content-Length") or 0)
            recebido = 0
            with open(parcial, "wb") as arquivo:
                for bloco in resposta.iter_content(chunk_size=256 * 1024):
                    if self.cancelado:
                        raise InterruptedError("Download cancelado.")
                    if not bloco:
                        continue
                    arquivo.write(bloco)
                    digest.update(bloco)
                    recebido += len(bloco)
                    self.progress.emit(recebido, total)
        if digest.hexdigest() != esperado:
            parcial.unlink(missing_ok=True)
            raise ValueError("O arquivo baixado não confere com a assinatura publicada (SHA-256). Tente de novo.")
        os.replace(parcial, destino)
        return destino

    def _sha_publicado(self):
        resposta = self.session.get(self.sha_url, timeout=30)
        resposta.raise_for_status()
        texto = (resposta.text or "").strip().split()
        valor = texto[0].lower() if texto else ""
        if len(valor) != 64 or any(ch not in "0123456789abcdef" for ch in valor):
            raise ValueError("Assinatura SHA-256 da atualização indisponível.")
        return valor
