import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QMessageBox

from app import VERSION, parse_version
from app.api_client import ArmazemAPI
from app.integrated_management import MODULES, external_document
from app.payments import PaymentDialog, attach_pix


class VersionTests(unittest.TestCase):
    def test_parse_and_compare(self):
        self.assertEqual(parse_version('0.3.0'), (0, 3, 0))
        self.assertEqual(parse_version('1.2'), (1, 2, 0))
        self.assertLess(parse_version('0.2.9'), parse_version(VERSION))
        self.assertEqual(parse_version('x'), (0, 0, 0))

    def test_heartbeat_reports_version(self):
        api = ArmazemAPI('https://appdv.example', empresa_id='1')
        api.username = 'ana'
        response = Mock(ok=True, status_code=200)
        response.json.return_value = {'ok': True}
        with patch.object(api.session, 'request', return_value=response) as request:
            api.heartbeat(0)
        self.assertEqual(request.call_args.kwargs['json']['versao'], VERSION)


class ModulesTests(unittest.TestCase):
    def test_new_management_modules_and_fiscal_documents(self):
        areas = {area for _, area, _ in MODULES}
        self.assertTrue({'fornecedores', 'etiquetas', 'relatorios-gerenciais', 'fiscal'} <= areas)
        self.assertTrue(external_document('https://homologacao.focusnfe.com.br/arquivos/danfe.html'))
        self.assertFalse(external_document('https://evil.example/focusnfe.com.br'))
        self.assertFalse(external_document('http://api.focusnfe.com.br/x'))


class PixPaymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_attach_only_approved_same_value(self):
        payments = [{'meio': 'PIX', 'valor': '10.00'}]
        self.assertFalse(attach_pix(payments, {'id': 1, 'estado': 'PENDENTE', 'valor': '10.00'}))
        self.assertFalse(attach_pix(payments, {'id': 1, 'estado': 'APROVADO', 'valor': '9.00'}))
        self.assertTrue(attach_pix(payments, {'id': 1, 'estado': 'APROVADO', 'valor': '10.00'}))
        self.assertEqual(payments[0]['cobranca_pix'], 1)

    def test_dialog_links_approved_charge(self):
        dialog = PaymentDialog(Decimal('10'), {'nome': 'Cliente'})
        dialog.configure_pix(Mock())
        for method, _, widget in dialog.inputs:
            if method == 'PIX':
                widget.setValue(10)
        dialog.pix = {'id': 7, 'estado': 'APROVADO', 'valor': '10.00'}
        dialog.accept_payment()
        self.assertEqual(dialog.payments[0]['cobranca_pix'], 7)
        dialog.close()

    def test_dialog_asks_when_pix_not_confirmed(self):
        dialog = PaymentDialog(Decimal('10'), {'nome': 'Cliente'})
        dialog.configure_pix(Mock())
        for method, _, widget in dialog.inputs:
            if method == 'PIX':
                widget.setValue(10)
        with patch('app.payments.QMessageBox.question', return_value=QMessageBox.StandardButton.No):
            dialog.accept_payment()
        self.assertIsNone(dialog.payments)
        dialog.close()


if __name__ == '__main__':
    unittest.main()
