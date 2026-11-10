import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from PySide6.QtWidgets import QApplication, QDialog

from app.local_store import LocalStore
from app.main import PendingSyncWorker, is_permanent_rejection
from app.pending import PendingSalesDialog, sale_total


def http_error(code, message='itens: Preço de A mudou'):
    response = Mock(status_code=code)
    return requests.HTTPError(message, response=response)


class RejectedOfflineSaleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = LocalStore(Path(self.folder.name) / 'store.db')
        self.payload = {'idempotency_key': 'k1', 'caixa': 7, 'operador_local': 'ana', 'credito': '0',
                        'itens': [{'produto': 1, 'quantidade': '1', 'preco_esperado': '10'}],
                        'pagamentos': [{'meio': 'DINHEIRO', 'valor': '10'}]}
        self.store.queue_sale('1', self.payload)

    def test_classifies_business_rejections(self):
        self.assertTrue(is_permanent_rejection(http_error(400)))
        self.assertTrue(is_permanent_rejection(http_error(404)))
        for code in (401, 403, 429, 500, 502):
            self.assertFalse(is_permanent_rejection(http_error(code)), code)

    def test_worker_marks_rejection_and_auto_sync_skips_it(self):
        api = Mock(username='ana', base_url='https://example.com', empresa_id='1', refresh_token=None)
        api.session.headers = {}
        clone = Mock()
        clone.registrar_venda.side_effect = http_error(400)
        with patch('app.main.ArmazemAPI', return_value=clone):
            worker = PendingSyncWorker(api, self.store.pending_sales('1'))
        rejected = []
        worker.rejected.connect(lambda key, error: rejected.append((key, error)))
        worker.run()
        self.assertEqual(rejected[0][0], 'k1')
        self.store.set_rejected(*rejected[0])
        self.assertEqual(self.store.pending_sales('1'), [])
        # Ainda bloqueia o fechamento do caixa até ser resolvida.
        self.assertEqual(len(self.store.pending_errors('1', 'ana')), 1)

    def test_discard_with_manager_clears_queue(self):
        self.store.set_rejected('k1', 'itens: Preço mudou')
        api = Mock()
        api.descartar_venda_offline.return_value = {'descartada': True}
        dialog = PendingSalesDialog(api, self.store, '1', 'ana')
        self.assertEqual(sale_total(dialog.sales[0]['payload']), 10)
        dialog.table.selectRow(0)
        self.assertTrue(dialog.discard_button.isEnabled())

        def fill(discard_dialog):
            discard_dialog.reason.setPlainText('Preço alterado, relançada')
            discard_dialog.manager.setText('gerente')
            discard_dialog.password.setText('segredo')
            return QDialog.DialogCode.Accepted
        with patch('app.pending.DiscardDialog.exec', autospec=True, side_effect=fill), \
                patch('app.pending.QMessageBox.information'):
            dialog._discard()
        args = api.descartar_venda_offline.call_args.args
        self.assertEqual(args[:4], ('k1', 'gerente', 'segredo', 'Preço alterado, relançada'))
        self.assertEqual(args[5]['itens'][0]['produto'], 1)
        self.assertEqual(self.store.pending_errors('1', 'ana'), [])
        dialog.close()

    def test_retry_returns_sale_to_queue(self):
        self.store.set_rejected('k1', 'itens: estoque insuficiente')
        dialog = PendingSalesDialog(Mock(), self.store, '1', 'ana')
        dialog.table.selectRow(0)
        with patch('app.pending.QMessageBox.information'):
            dialog._retry()
        self.assertEqual(len(self.store.pending_sales('1')), 1)
        dialog.close()


if __name__ == '__main__':
    unittest.main()
