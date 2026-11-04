import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from decimal import Decimal
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QDialog
from app.payments import PaymentDialog, split_payment
from app.discounts import distribute_discount
from app.quotes import quote_html
from app.main import PDVWindow


class DesktopCheckoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_split_payment_cash_change_and_non_cash_overpayment(self):
        payments = split_payment(Decimal('100'), [('PIX', 'Pix', 30), ('CARTAO', 'Débito', 20), ('DINHEIRO', 'Dinheiro', 60)])
        self.assertEqual(sum(Decimal(p['valor']) for p in payments), Decimal('100'))
        self.assertEqual(payments[-1]['troco'], '10.00')
        with self.assertRaises(ValueError):
            split_payment(Decimal('100'), [('PIX', 'Pix', 101)])
        with self.assertRaises(ValueError):
            split_payment(Decimal('100'), [('PIX', 'Pix', 99)])

    def test_total_percentage_discount_rounding(self):
        for values in ([Decimal('.01')] * 7, [Decimal('10'), Decimal('20'), Decimal('30')]):
            parts = distribute_discount(values, '%', Decimal('10'))
            self.assertEqual(sum(parts), (sum(values) * Decimal('.1')).quantize(Decimal('.01')))
            self.assertTrue(all(Decimal('0') <= p <= v for p, v in zip(parts, values)))
        self.assertEqual(sum(distribute_discount([10, 20], 'R$', 5)), 5)
        with self.assertRaises(ValueError):
            distribute_discount([10], '%', 101)

    def test_dialog_shows_split_and_blocks_incomplete_payment(self):
        dialog = PaymentDialog(Decimal('100'), {'nome': 'Cliente'})
        self.assertFalse(dialog.confirm.isEnabled())
        dialog.inputs[0][2].setValue(70)
        dialog.inputs[1][2].setValue(40)
        self.assertTrue(dialog.confirm.isEnabled())
        self.assertIn('Troco: R$ 10.00', dialog.summary.text())
        dialog.accept_payment()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(sum(Decimal(p['valor']) for p in dialog.payments), 100)

    def test_receipt_quote_render_preserves_identity_and_escapes(self):
        text = quote_html({'id': 10, 'cliente': {'nome': '<Cliente>', 'documento': '52998224725'}, 'itens': [{'codigo':'X','nome':'Produto','unidade':'UN','quantidade':'2','preco_venda':'10','desconto':'2','total':'18'}], 'criado_em':'2026-11-04', 'validade':'2026-11-10', 'estado':'ABERTO','total':'18'}, {'nome':'Empresa','cnpj':'123','logo_data_url':'data:image/png;base64,VEVTVEU='}, '/missing/logo.png')
        self.assertIn('&lt;Cliente&gt;', text)
        self.assertIn('VEVTVEU=', text)
        self.assertIn('Orçamento nº 10', text)

    def test_window_total_discount_applies_across_cart(self):
        with patch.object(PDVWindow, '_select_company'), patch('app.main.LocalStore'):
            window = PDVWindow(object())
        window.cart = {1:{'produto':{'nome':'A','preco_venda':'10'},'quantidade':Decimal('2')}, 2:{'produto':{'nome':'B','preco_venda':'30'},'quantidade':Decimal('1')}}
        window.total_discount = ('%', Decimal('10'))
        window._render_cart()
        self.assertIn('45.00', window.total.text())
        self.assertIn('5.00', window.subtotal.text())
        self.assertEqual(sum(item['desconto'] for item in window.cart.values()), Decimal('5'))
        window.close()


if __name__ == '__main__':
    unittest.main()


class PendingCheckoutStorageTests(unittest.TestCase):
    def test_pending_checkout_survives_restart_and_is_isolated(self):
        import tempfile
        from pathlib import Path
        from app.local_store import LocalStore
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'store.db'
            LocalStore(path).save_checkout_attempt('1', 'cashier', {'payload': {'idempotency_key': 'same-key'}})
            store = LocalStore(path)
            self.assertEqual(store.checkout_attempt('1', 'cashier')['payload']['idempotency_key'], 'same-key')
            self.assertIsNone(store.checkout_attempt('2', 'cashier'))
            self.assertIsNone(store.checkout_attempt('1', 'other'))
            store.clear_checkout_attempt('1', 'cashier')
            self.assertIsNone(store.checkout_attempt('1', 'cashier'))


class CustomerLookupResponseTests(unittest.TestCase):
    def test_empty_legacy_and_explicit_missing_customer_start_registration(self):
        from app.api_client import ArmazemAPI
        from unittest.mock import Mock
        api = ArmazemAPI('https://example.com')
        for content, data in [(b'', None), (b'null', None), (b'{"encontrado":false}', {'encontrado': False})]:
            response = Mock(content=content)
            response.json.return_value = data
            with patch.object(api, '_request', return_value=response):
                self.assertIsNone(api.cliente_cpf('52998224725'))
        response = Mock(content=b'{"id":1,"nome":"Cliente"}')
        response.json.return_value = {'id': 1, 'nome': 'Cliente'}
        with patch.object(api, '_request', return_value=response):
            self.assertEqual(api.cliente_cpf('52998224725')['id'], 1)

    def test_unknown_cpf_opens_name_and_phone_registration(self):
        from unittest.mock import Mock
        app = QApplication.instance() or QApplication([])
        api = Mock()
        api.cliente_cpf.return_value = None
        api.cadastrar_cliente_cpf.return_value = {'id': 42, 'nome': 'Cliente QA', 'documento': '52998224725'}
        with patch.object(PDVWindow, '_select_company'), patch('app.main.LocalStore'):
            window = PDVWindow(api)
        with patch('app.main.QInputDialog.getText', side_effect=[('52998224725', True), ('Cliente QA', True), ('', True)]) as inputs:
            customer = window._identify_customer()
        self.assertEqual(inputs.call_count, 3)
        self.assertEqual(customer['id'], 42)
        api.cadastrar_cliente_cpf.assert_called_once_with('52998224725', 'Cliente QA', '')
        window.close()


class OfflineCheckoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_outage_queues_sale_and_keeps_cash_and_key_on_reconnect(self):
        import json
        import tempfile
        import requests
        from pathlib import Path
        from unittest.mock import Mock
        from app.local_store import LocalStore
        from app.main import PendingSyncWorker
        api = Mock(username='cashier')
        api.registrar_venda.side_effect = requests.ConnectionError('offline')
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(PDVWindow, '_select_company'), patch('app.main.LocalStore'):
                window = PDVWindow(api)
            window.store = LocalStore(Path(folder) / 'store.db')
            window.tenant_id = '1'
            window.cash_id = 77
            product = {'id':1, 'nome':'A','codigo':'A','unidade':'UN','preco_venda':'10', 'estoque_atual':'10'}
            window.products = [product]
            window.cart = {1:{'produto':product,'quantidade':Decimal('1'),'desconto':Decimal('0')}}
            window.last_payments = [{'meio':'DINHEIRO','valor':'10'}]
            payload = {'idempotency_key':'same-operation','caixa':77,'operador_local':'cashier','credito':'0','itens':[{'produto':1,'quantidade':'1'}], 'pagamentos':window.last_payments}
            window.checkout_payload = payload
            with patch('app.main.QMessageBox.information'):
                window._submit_checkout(payload)
            rows = window.store.pending_sales('1')
            self.assertEqual(len(rows), 1)
            self.assertFalse(window.cart)
            self.assertEqual(window.store.list_products('1')[0]['estoque_atual'], '9')
            text = next((Path(folder) / 'comprovantes').glob('*.html')).read_text()
            self.assertIn('PENDENTE DE SINCRONIZAÇÃO', text)
            clone = Mock()
            clone.registrar_venda.return_value = {'id': 123}
            api.base_url = 'https://example.com'
            api.empresa_id = '1'
            api.session.headers = {}
            api.refresh_token = None
            with patch('app.main.ArmazemAPI', return_value=clone):
                worker = PendingSyncWorker(api, rows)
            received = []
            worker.result.connect(lambda key, error, response: received.append((key, error, response)))
            worker.run()
            sent = clone.registrar_venda.call_args.args[0]
            self.assertEqual(sent['caixa'], 77)
            self.assertEqual(sent['idempotency_key'], 'same-operation')
            self.assertEqual(received[0][2]['id'], 123)
            window.close()

    def test_offline_new_cpf_is_stored_locally_without_password(self):
        import tempfile
        import requests
        from pathlib import Path
        from unittest.mock import Mock
        from app.local_store import LocalStore
        api = Mock(username='cashier')
        api.cliente_cpf.side_effect = requests.ConnectionError('offline')
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(PDVWindow, '_select_company'), patch('app.main.LocalStore'):
                window = PDVWindow(api)
            window.store = LocalStore(Path(folder) / 'store.db')
            window.tenant_id = '1'
            with patch('app.main.QInputDialog.getText', side_effect=[('52998224725',True),('Cliente local',True),('',True)]):
                customer = window._identify_customer()
            self.assertTrue(customer['offline'])
            self.assertIsNone(customer['id'])
            self.assertEqual(window.store.customer('1','52998224725')['nome'], 'Cliente local')
            self.assertIsNone(window.store.customer('2','52998224725'))
            window.close()


class AutomaticPolicyDialogTests(unittest.TestCase):
    def test_pix_promotion_updates_due_and_payment_validation(self):
        app = QApplication.instance() or QApplication([])
        dialog = PaymentDialog(Decimal('100'), {'nome':'Cliente'})
        applied=[]
        dialog.configure_policy({'modo':'MISTO','percentual':'5','meio':'PIX','minimo':'50'},Decimal('100'),Decimal('0'),lambda discount,auto:applied.append((discount,auto)))
        dialog.inputs[1][2].setValue(95)
        self.assertEqual(dialog.total,Decimal('95'))
        self.assertTrue(dialog.confirm.isEnabled())
        dialog.inputs[1][2].setValue(0)
        dialog.inputs[0][2].setValue(100)
        self.assertEqual(dialog.total,Decimal('100'))
        self.assertTrue(dialog.confirm.isEnabled())
        dialog.close()
