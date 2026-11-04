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
