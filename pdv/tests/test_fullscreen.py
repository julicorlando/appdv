import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QMessageBox

from app.main import LoginDialog, PDVWindow


class TelaCheiaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self):
        api = Mock(username='ana', base_url='https://appdv.example', empresa_id='1', refresh_token='r', offline_login=False)
        api.session.headers = {'Authorization': 'Bearer x'}
        with patch.object(PDVWindow, '_select_company'), patch('app.main.LocalStore'):
            window = PDVWindow(api)
        window.sync_timer.stop()
        window.show()
        return window, api

    def test_sair_volta_para_login_e_esquece_o_acesso(self):
        window, api = self.window()
        window.cash_id = 10
        fechou = []
        window.closed.connect(lambda: fechou.append(True))
        with patch('app.main.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes) as pergunta:
            window._logout()
        self.assertIn('continua aberto', pergunta.call_args.args[2])
        self.assertTrue(window.logout_requested)
        self.assertNotIn('Authorization', api.session.headers)
        self.assertIsNone(api.refresh_token)
        self.assertEqual(fechou, [True])

    def test_cancelar_sair_mantem_sessao(self):
        window, api = self.window()
        with patch('app.main.QMessageBox.question', return_value=QMessageBox.StandardButton.No):
            window._logout()
        self.assertFalse(window.logout_requested)
        self.assertIn('Authorization', api.session.headers)
        self.assertEqual(window.logout_button.text(), 'Sair')
        window.close()

    def test_login_centralizado_em_tela_cheia(self):
        dialog = LoginDialog()
        card = dialog.findChild(type(dialog.username.parentWidget()), 'loginCard')
        self.assertIsNotNone(card)
        self.assertGreaterEqual(card.minimumWidth(), 440)
        dialog.close()


if __name__ == '__main__':
    unittest.main()
