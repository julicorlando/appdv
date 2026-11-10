"""Revisão das vendas locais que ainda não foram registradas no servidor."""
from decimal import Decimal, InvalidOperation

import requests
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

STATUS_LABEL = {'PENDENTE': 'Aguardando envio', 'REJEITADA': 'Recusada pelo servidor'}


def sale_total(payload):
    total = Decimal('0')
    try:
        for payment in payload.get('pagamentos') or []:
            total += Decimal(str(payment.get('valor', '0')))
        total += Decimal(str(payload.get('credito', '0') or '0'))
    except (InvalidOperation, TypeError, ValueError, AttributeError):
        return None
    return total


def sale_summary(payload):
    """Resumo enviado para a auditoria do descarte (sem dados de sessão)."""
    return {
        'caixa': payload.get('caixa'),
        'cliente': payload.get('cliente'),
        'cliente_offline': payload.get('cliente_offline'),
        'credito': payload.get('credito'),
        'itens': payload.get('itens', []),
        'pagamentos': payload.get('pagamentos', []),
    }


class DiscardDialog(QDialog):
    def __init__(self, sale, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Descartar venda recusada')
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            'A venda não será registrada no servidor. O descarte fica auditado com os itens, '
            'os pagamentos e o motivo, para a gerência lançar a venda de novo ou acertar a diferença.'
        ))
        layout.addWidget(QLabel('Recusa do servidor: ' + (sale.get('erro') or '—')))
        form = QFormLayout()
        self.reason = QPlainTextEdit()
        self.reason.setPlaceholderText('Ex.: preço alterado; venda relançada como #1234')
        self.manager = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow('Motivo', self.reason)
        form.addRow('Gerente/administrador', self.manager)
        form.addRow('Senha', self.password)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Descartar')
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self):
        if len(self.reason.toPlainText().strip()) < 5 or not self.manager.text().strip() or not self.password.text():
            QMessageBox.warning(self, 'Descarte', 'Informe o motivo e as credenciais do gerente ou administrador.')
            return
        self.accept()


class PendingSalesDialog(QDialog):
    def __init__(self, api, store, tenant_id, operator, parent=None):
        super().__init__(parent)
        self.api, self.store, self.tenant_id, self.operator = api, store, tenant_id, operator
        self.setWindowTitle('Sincronização de vendas locais')
        self.resize(820, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            'Vendas aguardando envio são reenviadas automaticamente. Vendas recusadas exigem revisão: '
            'reenvie depois de corrigir a causa (ex.: estoque) ou descarte com autorização do gerente.'
        ))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['Venda', 'Criada em', 'Total', 'Situação', 'Motivo'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        self.retry_button = QPushButton('Reenviar selecionada')
        self.discard_button = QPushButton('Descartar recusada…')
        close_button = QPushButton('Fechar')
        self.retry_button.clicked.connect(self._retry)
        self.discard_button.clicked.connect(self._discard)
        close_button.clicked.connect(self.accept)
        for button in (self.retry_button, self.discard_button):
            actions.addWidget(button)
        actions.addStretch()
        actions.addWidget(close_button)
        layout.addLayout(actions)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self._load()

    def _load(self):
        self.sales = self.store.pending_details(self.tenant_id, self.operator)
        self.table.setRowCount(len(self.sales))
        for row, sale in enumerate(self.sales):
            total = sale_total(sale['payload'])
            values = [
                sale['chave'][:8], str(sale['criada_em'] or ''),
                f'R$ {total:.2f}' if total is not None else '—',
                STATUS_LABEL.get(sale['status'], sale['status']), sale['erro'] or '',
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self._update_buttons()

    def selected(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return self.sales[rows[0].row()] if rows else None

    def _update_buttons(self):
        sale = self.selected()
        self.retry_button.setEnabled(bool(sale))
        self.discard_button.setEnabled(bool(sale) and sale['status'] == 'REJEITADA')

    def _retry(self):
        sale = self.selected()
        if not sale:
            return
        self.store.retry_sale(sale['chave'])
        self.store.set_error(sale['chave'], '')
        QMessageBox.information(self, 'Sincronização', 'A venda voltou para a fila e será enviada agora.')
        self._load()

    def _discard(self):
        sale = self.selected()
        if not sale or sale['status'] != 'REJEITADA':
            return
        dialog = DiscardDialog(sale, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            result = self.api.descartar_venda_offline(
                sale['chave'], dialog.manager.text().strip(), dialog.password.text(),
                dialog.reason.toPlainText().strip(), sale['erro'], sale_summary(sale['payload']),
            )
        except (requests.ConnectionError, requests.Timeout):
            QMessageBox.warning(self, 'Descarte', 'O descarte exige conexão com o servidor.')
            return
        except Exception as exc:
            QMessageBox.critical(self, 'Descarte não autorizado', str(exc))
            return
        finally:
            dialog.password.clear()
        self.store.mark_synced(sale['chave'])
        if result.get('sincronizada'):
            QMessageBox.information(self, 'Sincronização', f"Esta venda já estava registrada no servidor (#{result.get('venda')}). A pendência local foi removida.")
        else:
            QMessageBox.information(self, 'Descarte registrado', 'A venda foi descartada e o registro ficou na auditoria da empresa.')
        self._load()
