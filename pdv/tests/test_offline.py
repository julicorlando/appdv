import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
import tempfile
import time
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from PySide6.QtWidgets import QApplication, QMessageBox

from app import offline_auth
from app.local_store import LocalStore
from app.main import LoginDialog, PDVWindow, PendingSyncWorker, SYNC_INTERVAL_MS


class OfflineAuthTests(unittest.TestCase):
    def test_registro_vale_30_dias_e_nao_guarda_senha(self):
        registro = offline_auth.criar_registro('Senha#2026', 'refresh')
        self.assertNotIn('Senha#2026', json.dumps(registro))
        self.assertEqual(offline_auth.conferir(registro, 'Senha#2026'), (True, ''))
        self.assertFalse(offline_auth.conferir(registro, 'errada')[0])
        self.assertTrue(offline_auth.conferir(registro, 'Senha#2026', agora=time.time() + 29 * 86400)[0])
        self.assertFalse(offline_auth.conferir(registro, 'Senha#2026', agora=time.time() + 31 * 86400)[0])
        self.assertEqual(SYNC_INTERVAL_MS, 10_000)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = LocalStore(Path(self.folder.name) / 'store.db')


class LoginOfflineTests(Base):
    def dialog(self, password):
        dialog = LoginDialog()
        dialog.url.setText('https://appdv.example')
        dialog.username.setText('ana')
        dialog.password.setText(password)
        return dialog

    def test_login_online_guarda_acesso_e_offline_entra(self):
        with patch('app.main.LocalStore', return_value=self.store), \
                patch('app.main.ArmazemAPI.login', return_value={'access': 'a', 'refresh': 'r1'}):
            online = self.dialog('Senha#2026')
            online.authenticate()
        self.assertFalse(online.api.offline_login)
        self.store.save_offline_companies('https://appdv.example', 'ana', [{'id': 1, 'nome': 'Loja', 'papel': 'CAIXA'}])
        with patch('app.main.LocalStore', return_value=self.store), \
                patch('app.main.ArmazemAPI.login', side_effect=requests.ConnectionError('sem rede')), \
                patch('app.main.QMessageBox.information') as aviso:
            offline = self.dialog('Senha#2026')
            offline.authenticate()
        self.assertTrue(offline.api.offline_login)
        self.assertEqual(offline.api.refresh_token, 'r1')
        self.assertEqual(offline.api.cached_companies[0]['nome'], 'Loja')
        aviso.assert_called_once()
        with patch('app.main.LocalStore', return_value=self.store), \
                patch('app.main.ArmazemAPI.login', side_effect=requests.ConnectionError('sem rede')):
            errado = self.dialog('outra')
            errado.authenticate()
        self.assertIsNone(errado.api)
        self.assertIn('inválidos', errado.error.text())


class CaixaOfflineTests(Base):
    def window(self):
        api = Mock(username='ana', base_url='https://appdv.example', empresa_id='1', refresh_token='r', offline_login=True)
        api.session.headers = {}
        api.registrar_venda.side_effect = AssertionError('offline não deve chamar o servidor')
        with patch.object(PDVWindow, '_select_company'), patch('app.main.LocalStore'):
            window = PDVWindow(api)
        window.sync_timer.stop()
        window.store = self.store
        window.tenant_id = '1'
        self.addCleanup(window.close)
        return window

    def test_abre_vende_sangra_e_fecha_sem_internet(self):
        window = self.window()
        self.assertFalse(window.online)
        with patch('app.main.QInputDialog.getDouble', return_value=(100.0, True)), patch.object(PDVWindow, '_load_catalog'):
            window._open_cash_mode()
        self.assertTrue(window.cash_id.startswith('l:'))
        self.assertEqual(self.store.current_cash('1', 'ana'), window.cash_id)
        product = {'id': 1, 'nome': 'A', 'codigo': 'A', 'unidade': 'UN', 'preco_venda': '10', 'estoque_atual': '10'}
        window.products = [product]
        window.cart = {1: {'produto': product, 'quantidade': Decimal('1'), 'desconto': Decimal('0')}}
        window.last_payments = [{'meio': 'DINHEIRO', 'valor': '10'}]
        payload = {'idempotency_key': 'v-off', 'caixa': None, 'caixa_local': window.cash_id[2:], 'operador_local': 'ana',
                   'credito': '0', 'itens': [{'produto': 1, 'quantidade': '1'}], 'pagamentos': window.last_payments}
        with patch('app.main.QMessageBox.information'):
            window._submit_checkout(payload)
        self.assertEqual(len(self.store.pending_sales('1')), 1)
        self.assertEqual(self.store.list_products('1')[0]['estoque_atual'], '9')
        with patch('app.main.QInputDialog.getItem', return_value=('Sangria', True)), \
                patch('app.main.QInputDialog.getDouble', return_value=(5.0, True)), \
                patch('app.main.QInputDialog.getText', return_value=('Banco', True)), \
                patch('app.main.QMessageBox.information'):
            window._cash_movement()
        with patch('app.main.QInputDialog.getDouble', return_value=(95.0, True)), \
                patch('app.main.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes), \
                patch('app.main.QMessageBox.information'):
            window._close_cash()
        self.assertIsNone(window.cash_id)
        self.assertIsNone(self.store.current_cash('1', 'ana'))
        self.assertEqual([op['tipo'] for op in self.store.pending_cash_ops('1', 'ana')], ['ABRIR', 'MOVIMENTO', 'FECHAR'])
        self.assertIn('Offline', window.connection_label.text())

    def test_caixa_reaberto_depois_de_reiniciar_offline(self):
        window = self.window()
        self.store.set_current_cash('1', 'ana', 'l:abc')
        self.assertEqual(window._resume_cash(), 'l:abc')
        self.store.map_cash('1', 'abc', 42)
        self.assertEqual(window._resume_cash(), 42)

    def test_prazo_offline_respeita_limite_conhecido(self):
        window = self.window()
        self.store.save_customer('1', {'id': 3, 'nome': 'C', 'documento': '52998224725', 'limite_disponivel': '50.00'})
        cliente = {'id': 3, 'documento': '52998224725'}
        with patch('app.main.QMessageBox.warning') as aviso:
            self.assertTrue(window._offline_credit_ok(cliente, Decimal('50')))
            self.assertFalse(window._offline_credit_ok(cliente, Decimal('50.01')))
            self.assertFalse(window._offline_credit_ok({'id': None, 'documento': '1'}, Decimal('1')))
        self.assertEqual(aviso.call_count, 2)


class SincronizacaoTests(Base):
    def test_sobe_na_ordem_e_baixa_catalogo(self):
        self.store.queue_cash_op('1', 'ana', 'ABRIR', {'local_id': 'L1', 'dispositivo': 'pc', 'saldo': '50.00'})
        self.store.queue_sale('1', {'idempotency_key': 'v1', 'operador_local': 'ana', 'caixa': None, 'caixa_local': 'L1',
                                    'credito': '0', 'itens': [], 'pagamentos': []})
        self.store.queue_cash_op('1', 'ana', 'FECHAR', {'caixa_local': 'L1', 'saldo': '50.00'})
        api = Mock(username='ana', base_url='https://x', empresa_id='1', refresh_token='r')
        api.session.headers = {}
        clone = Mock()
        ordem = []
        clone.abrir_caixa.side_effect = lambda d, s: ordem.append('abrir') or {'id': 99}
        clone.registrar_venda.side_effect = lambda p: ordem.append(('venda', p['caixa'])) or {'id': 500}
        clone.fechar_caixa.side_effect = lambda c, s: ordem.append(('fechar', c)) or {}
        clone.produtos.side_effect = lambda desde=None: [{'id': 1, 'ativo': True, 'atualizado_em': '2026-11-10T10:00:00-03:00'}]
        clone.clientes.return_value = []
        clone.session.headers = {'Authorization': 'Bearer novo'}
        clone.refresh_token = 'r'
        with patch('app.main.ArmazemAPI', return_value=clone):
            worker = PendingSyncWorker(api, self.store.pending_sales('1'), store=self.store, tenant_id='1', operator='ana')
        mapeado = []
        worker.cash_mapped.connect(lambda local, server: mapeado.append((local, server)))
        worker.run()
        self.assertEqual(ordem, ['abrir', ('venda', 99), ('fechar', 99)])
        self.assertEqual(mapeado, [('L1', 99)])
        self.assertEqual(self.store.pending_count('1', 'ana'), 0)
        self.assertEqual(self.store.sync_state('1')['cursor'], '2026-11-10T10:00:00-03:00')
        with patch('app.main.ArmazemAPI', return_value=clone):
            worker = PendingSyncWorker(api, [], store=self.store, tenant_id='1', operator='ana')
        worker.run()
        clone.produtos.assert_called_with(desde='2026-11-10T10:00:00-03:00')


if __name__ == '__main__':
    unittest.main()
