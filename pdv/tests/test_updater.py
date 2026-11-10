import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import hashlib
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QMessageBox

from app import VERSION, updater
from app.main import PDVWindow

URL = 'https://github.com/julicorlando/appdv/releases/download/pdv-v9.9.9/ApPdv-Setup.exe'


class FakeResponse:
    def __init__(self, body=b'', text='', url=URL):
        self.body, self.text, self.url = body, text, url
        self.headers = {'Content-Length': str(len(body))}

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def fake_session(body, sha, final_url=URL):
    session = Mock()
    session.get.side_effect = lambda url, **kw: FakeResponse(text=sha) if url.endswith('.sha256') else FakeResponse(body, url=final_url)
    return session


class DownloadTests(unittest.TestCase):
    def test_hosts_permitidos(self):
        self.assertTrue(updater.host_permitido(URL))
        self.assertTrue(updater.host_permitido('https://objects.githubusercontent.com/x'))
        self.assertTrue(updater.host_permitido('https://appdv.example/x.exe', 'https://appdv.example'))
        self.assertFalse(updater.host_permitido('http://github.com/x'))
        self.assertFalse(updater.host_permitido('https://evil.example/github.com'))
        self.assertFalse(updater.host_permitido('https://user@github.com/x'))

    def test_baixa_e_confere_sha256(self):
        corpo = b'instalador'
        sha = hashlib.sha256(corpo).hexdigest() + '  ApPdv-Setup.exe'
        worker = updater.DownloadWorker(URL, URL + '.sha256', '9.9.9', session=fake_session(corpo, sha))
        caminho = worker.baixar()
        self.assertEqual(caminho.read_bytes(), corpo)
        caminho.unlink()

    def test_recusa_arquivo_adulterado_ou_redirecionado(self):
        sha = hashlib.sha256(b'outro').hexdigest()
        with self.assertRaises(ValueError):
            updater.DownloadWorker(URL, URL + '.sha256', '9.9.8', session=fake_session(b'instalador', sha)).baixar()
        ok = hashlib.sha256(b'x').hexdigest()
        with self.assertRaises(ValueError):
            updater.DownloadWorker(URL, URL + '.sha256', '9.9.7', session=fake_session(b'x', ok, 'https://evil.example/a.exe')).baixar()

    def test_fora_do_windows_nao_e_instalado(self):
        self.assertFalse(updater.instalado())


class BotaoAtualizarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self, versao_atual):
        api = Mock(username='ana', base_url='https://appdv.example', empresa_id='1', refresh_token='r', offline_login=False)
        api.session.headers = {}
        api.versao_pdv.return_value = {'versao_atual': versao_atual, 'versao_minima': '0.0.1', 'download_url': 'https://github.com/x',
                                       'instalador_url': URL, 'sha256_url': URL + '.sha256'}
        with patch.object(PDVWindow, '_select_company'), patch('app.main.LocalStore'):
            window = PDVWindow(api)
        window.sync_timer.stop()
        window.show()
        self.addCleanup(window.close)
        return window

    def test_botao_aparece_so_com_versao_nova(self):
        window = self.window(VERSION)
        window._check_version()
        self.assertFalse(window.update_button.isVisible())
        window = self.window('99.0.0')
        window._check_version()
        self.assertTrue(window.update_button.isVisible())
        self.assertEqual(window.update_button.text(), '⬆ Atualizar ApPdv')

    def test_portatil_abre_o_download_no_navegador(self):
        window = self.window('99.0.0')
        window._check_version()
        with patch('app.main.updater.instalado', return_value=False), \
                patch('app.main.QMessageBox.information'), patch('app.main.QDesktopServices.openUrl') as abrir:
            window._update_app()
        abrir.assert_called_once()

    def test_instalado_baixa_instala_e_fecha(self):
        window = self.window('99.0.0')
        window._check_version()
        fake = Mock()
        with patch('app.main.updater.instalado', return_value=True), \
                patch('app.main.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes), \
                patch('app.main.updater.DownloadWorker', return_value=fake):
            window._update_app()
        fake.start.assert_called_once()
        fechou = []
        window.closed.connect(lambda: fechou.append(True))
        with patch('app.main.updater.iniciar_instalador') as iniciar:
            window._install_update('C:/tmp/ApPdv-Setup-99.0.0.exe')
        iniciar.assert_called_once_with('C:/tmp/ApPdv-Setup-99.0.0.exe')
        self.assertEqual(fechou, [True])


if __name__ == '__main__':
    unittest.main()
