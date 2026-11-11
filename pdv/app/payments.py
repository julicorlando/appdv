import base64
import uuid
from decimal import Decimal
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLabel, QDoubleSpinBox, QDialogButtonBox,
                               QPushButton, QLineEdit, QMessageBox)


class PixWorker(QThread):
    """Chamada ao servidor fora da tela, para o caixa não travar enquanto espera o PIX."""
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, call, parent=None):
        super().__init__(parent)
        self.call = call

    def run(self):
        try:
            self.done.emit(self.call())
        except Exception as exc:  # rede ou recusa do provedor
            self.failed.emit(str(exc))


def attach_pix(payments, pix):
    """Vincula a cobrança PIX aprovada ao pagamento PIX de mesmo valor. Devolve True se vinculou."""
    pix_payment = next((p for p in payments if p['meio'] == 'PIX'), None)
    if not pix_payment or not pix or pix.get('estado') != 'APROVADO':
        return False
    if Decimal(str(pix['valor'])).quantize(Decimal('.01')) != Decimal(pix_payment['valor']):
        return False
    pix_payment['cobranca_pix'] = pix['id']
    pix_payment['referencia_externa'] = 'PIX Mercado Pago'
    return True


def attach_point(payments, point):
    """Marca o pagamento em cartão aprovado na maquininha Point (mesmo valor). Devolve True se marcou."""
    if not point or point.get('estado') != 'APROVADO':
        return False
    valor = Decimal(str(point.get('valor', '0'))).quantize(Decimal('.01'))
    card = next((p for p in payments if p['meio'] == 'CARTAO' and Decimal(p['valor']) == valor), None)
    if not card:
        return False
    detalhes = ' '.join(v for v in (point.get('bandeira', ''), point.get('autorizacao', '')) if v)
    card['referencia_externa'] = f"Point MP {point.get('pagamento', '')} {detalhes}".strip()[:120]
    return True


def split_payment(total, amounts, credit=Decimal('0')):
    total, credit = Decimal(str(total)), Decimal(str(credit))
    values = [(method, label, Decimal(str(value))) for method, label, value in amounts]
    if any(not value.is_finite() or value < 0 or value != value.quantize(Decimal('.01')) for value in [total, credit] + [v for _, _, v in values]):
        raise ValueError('Informe valores positivos com até duas casas decimais.')
    other = sum((v for method, _, v in values if method != 'DINHEIRO'), Decimal('0'))
    cash = sum((v for method, _, v in values if method == 'DINHEIRO'), Decimal('0'))
    if other + credit > total:
        raise ValueError('Pix, cartão e crédito não podem ultrapassar o total.')
    remaining = total - other - credit
    if cash < remaining:
        raise ValueError(f'Falta distribuir R$ {remaining - cash:.2f}.')
    payments = []
    for method, label, value in values:
        if value <= 0:
            continue
        applied = remaining if method == 'DINHEIRO' else value
        if applied == 0:
            raise ValueError('Retire o dinheiro informado: a venda já está totalmente paga pelas outras formas.')
        payments.append({'meio': method, 'valor': str(applied.quantize(Decimal('.01'))),
            'valor_recebido': str(value.quantize(Decimal('.01'))),
            'troco': str((value - applied if method == 'DINHEIRO' else Decimal('0')).quantize(Decimal('.01'))),
            'referencia_externa': label})
    return payments


class PaymentDialog(QDialog):
    def __init__(self, total, customer, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Finalizar venda — formas de pagamento')
        self.setMinimumWidth(510)
        self.total = total
        self.payments = None
        self.credit = Decimal('0')
        self.policy = None
        self.policy_callback = None
        self.manual_discount = Decimal('0')
        self.policy_gross = total
        layout = QVBoxLayout(self)
        title = QLabel(f'Total da venda: R$ {total:.2f}')
        title.setStyleSheet('font-size:24px;font-weight:bold')
        self.title_label = title
        layout.addWidget(title)
        self.discount_summary = QLabel()
        layout.addWidget(self.discount_summary)
        self.discount_button = QPushButton("Desconto no total (R$ ou %)")
        self.discount_button.hide()
        layout.addWidget(self.discount_button)
        layout.addWidget(QLabel(f"Cliente: {customer['nome']}"))
        layout.addWidget(QLabel('Distribua o valor entre uma ou mais formas de pagamento.'))
        form = QFormLayout()
        self.inputs = []
        for method, label in [('DINHEIRO', 'Dinheiro recebido'), ('PIX', 'Pix'), ('CARTAO', 'Cartão de débito'), ('CARTAO', 'Cartão de crédito'), ('OUTRO', 'Outra forma')]:
            widget = QDoubleSpinBox()
            widget.setRange(0, 99999999)
            widget.setDecimals(2)
            widget.setPrefix('R$ ')
            widget.setAlignment(Qt.AlignmentFlag.AlignRight)
            widget.valueChanged.connect(self.update_summary)
            form.addRow(label, widget)
            self.inputs.append((method, label, widget))
        self.credit_input = QDoubleSpinBox()
        self.credit_input.setRange(0, float(total))
        self.credit_input.setDecimals(2)
        self.credit_input.setPrefix('R$ ')
        self.credit_input.valueChanged.connect(self.update_summary)
        form.addRow('A prazo / conta do cliente', self.credit_input)
        layout.addLayout(form)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.pix_api = None
        self.pix = None
        self.pix_worker = None
        self.pix_box = QVBoxLayout()
        layout.addLayout(self.pix_box)
        self.point_api = None
        self.point = None
        self.point_worker = None
        self.point_box = QVBoxLayout()
        layout.addLayout(self.point_box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.confirm = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.confirm.setText('Confirmar pagamento')
        buttons.accepted.connect(self.accept_payment)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.update_summary()

    def configure_discount(self, callback, allowed):
        self.discount_button.setVisible(allowed)
        def update():
            gross, discount = callback()
            self.manual_discount = discount
            self.policy_gross = gross
            self.total = gross - discount
            self.title_label.setText(f'Total a pagar: R$ {self.total:.2f}')
            self.discount_summary.setText(f'Subtotal: R$ {gross:.2f} · Desconto: R$ {discount:.2f}')
            self.credit_input.setMaximum(float(self.total))
            self.update_summary()
        self.discount_button.clicked.connect(update)
        # Show existing discount without prompting the user again.
        parent = self.parent()
        if parent and hasattr(parent, 'cart'):
            gross = sum(((Decimal(str(i['produto']['preco_venda'])) * i['quantidade']).quantize(Decimal('.01')) for i in parent.cart.values()), Decimal('0'))
            self.discount_summary.setText(f'Subtotal: R$ {gross:.2f} · Desconto: R$ {gross - self.total:.2f}')

    def configure_policy(self, policy, gross, manual, callback):
        self.policy = policy
        self.policy_gross = gross
        self.manual_discount = manual
        self.policy_callback = callback
        self.update_summary()

    def update_summary(self):
        if not hasattr(self, 'summary'):
            return
        amounts = [(m, label, Decimal(str(widget.value()))) for m, label, widget in self.inputs]
        if self.policy and self.policy_callback:
            methods={m for m,_,v in amounts if v>0}
            rate=Decimal('0')
            if self.policy.get('modo') in ('AUTOMATICO','MISTO') and self.policy_gross>=Decimal(self.policy.get('minimo','0')):
                if not self.policy.get('meio') or methods=={self.policy['meio']}:
                    rate=Decimal(self.policy.get('percentual','0'))
            auto=(self.policy_gross*rate/100).quantize(Decimal('.01'))
            discount=max(auto,self.manual_discount)
            self.total=self.policy_gross-discount
            self.policy_callback(discount,auto)
            self.title_label.setText(f'Total a pagar: R$ {self.total:.2f}')
            self.discount_summary.setText(f'Subtotal: R$ {self.policy_gross:.2f} · Desconto: R$ {discount:.2f}')
        credit = Decimal(str(self.credit_input.value()))
        count = sum(value > 0 for _, _, value in amounts) + (credit > 0)
        received = sum((value for _, _, value in amounts), Decimal('0')) + credit
        try:
            payments = split_payment(self.total, amounts, credit)
            change = sum((Decimal(p['troco']) for p in payments), Decimal('0'))
            self.summary.setText(f'{count} forma(s) · Total informado: R$ {received:.2f}\nFalta: R$ 0,00 · Troco: R$ {change:.2f}')
            self.confirm.setEnabled(True)
        except ValueError as exc:
            self.summary.setText(f'{count} forma(s) · Total informado: R$ {received:.2f}\n{exc}')
            self.confirm.setEnabled(False)

    # ------------------------------------------------------------------ PIX dinâmico
    def configure_pix(self, api):
        """Mostra o botão de QR Code PIX (Mercado Pago) quando a empresa tem o PIX automático ativo."""
        self.pix_api = api
        self.pix_button = QPushButton('Gerar QR Code PIX do valor informado')
        self.pix_button.clicked.connect(self._create_pix)
        self.pix_image = QLabel()
        self.pix_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pix_code = QLineEdit()
        self.pix_code.setReadOnly(True)
        self.pix_code.setPlaceholderText('PIX copia e cola')
        self.pix_code.hide()
        self.pix_status = QLabel()
        self.pix_status.setWordWrap(True)
        for widget in (self.pix_button, self.pix_image, self.pix_code, self.pix_status):
            self.pix_box.addWidget(widget)
        self.pix_timer = QTimer(self)
        self.pix_timer.setInterval(3000)
        self.pix_timer.timeout.connect(self._poll_pix)

    def _pix_value(self):
        return sum((Decimal(str(w.value())) for m, _, w in self.inputs if m == 'PIX'), Decimal('0')).quantize(Decimal('.01'))

    def _run_pix(self, call, on_done):
        if self.pix_worker and self.pix_worker.isRunning():
            return
        self.pix_worker = PixWorker(call, self)
        self.pix_worker.done.connect(on_done)
        self.pix_worker.failed.connect(lambda message: self.pix_status.setText('PIX: ' + message))
        self.pix_worker.start()

    def _create_pix(self):
        value = self._pix_value()
        if value <= 0:
            self.pix_status.setText('Informe o valor no campo Pix antes de gerar o QR Code.')
            return
        self.pix_timer.stop()
        self.pix = None
        self.pix_status.setText('Gerando cobrança…')
        key = str(uuid.uuid4())
        self._run_pix(lambda: self.pix_api.pix_criar(value, key), self._pix_created)

    def _pix_created(self, data):
        self.pix = data
        if data.get('qr_base64'):
            pixmap = QPixmap()
            pixmap.loadFromData(base64.b64decode(data['qr_base64']))
            self.pix_image.setPixmap(pixmap.scaled(220, 220, Qt.AspectRatioMode.KeepAspectRatio))
        self.pix_code.setText(data.get('qr_code', ''))
        self.pix_code.setVisible(bool(data.get('qr_code')))
        self._pix_status_text()
        self.pix_timer.start()

    def _poll_pix(self):
        if not self.pix or self.pix.get('estado') != 'PENDENTE':
            self.pix_timer.stop()
            return
        cobranca = self.pix['id']
        self._run_pix(lambda: self.pix_api.pix_consultar(cobranca), self._pix_polled)

    def _pix_polled(self, data):
        self.pix = data
        self._pix_status_text()
        if data.get('estado') != 'PENDENTE':
            self.pix_timer.stop()

    def _pix_status_text(self):
        estado = (self.pix or {}).get('estado')
        textos = {'PENDENTE': f"Aguardando pagamento de R$ {Decimal(str(self.pix['valor'])):.2f}…",
                  'APROVADO': 'PIX recebido ✓ — confirme o pagamento.'}
        self.pix_status.setText(textos.get(estado, f"Cobrança {(self.pix or {}).get('estado_texto', '').lower()}. Gere outro QR Code."))

    # ------------------------------------------------------------------ maquininha Point
    def configure_point(self, api):
        """Botão para cobrar o cartão direto na maquininha Mercado Pago Point vinculada à empresa."""
        self.point_api = api
        self.point_button = QPushButton('Cobrar cartão na maquininha (Mercado Pago Point)')
        self.point_button.clicked.connect(self._create_point)
        self.point_cancel = QPushButton('Cancelar cobrança na maquininha')
        self.point_cancel.clicked.connect(self._cancel_point)
        self.point_cancel.hide()
        self.point_status = QLabel()
        self.point_status.setWordWrap(True)
        for widget in (self.point_button, self.point_cancel, self.point_status):
            self.point_box.addWidget(widget)
        self.point_timer = QTimer(self)
        self.point_timer.setInterval(3000)
        self.point_timer.timeout.connect(self._poll_point)

    def _card_charge(self):
        """(valor, tipo) dos campos de cartão: só um deles (débito ou crédito) pode estar preenchido."""
        cards = [(label, Decimal(str(w.value())).quantize(Decimal('.01'))) for m, label, w in self.inputs if m == 'CARTAO']
        filled = [(label, value) for label, value in cards if value > 0]
        if len(filled) != 1:
            return None, None
        label, value = filled[0]
        return value, 'CREDITO' if 'crédito' in label.lower() else 'DEBITO'

    def _run_point(self, call, on_done):
        if self.point_worker and self.point_worker.isRunning():
            return
        self.point_worker = PixWorker(call, self)
        self.point_worker.done.connect(on_done)
        self.point_worker.failed.connect(lambda message: self.point_status.setText('Maquininha: ' + message))
        self.point_worker.start()

    def _create_point(self):
        value, kind = self._card_charge()
        if not value:
            self.point_status.setText('Preencha o valor em Cartão de débito OU Cartão de crédito (só um) antes de cobrar.')
            return
        self.point = None
        self.point_status.setText(f'Enviando R$ {value:.2f} ({kind.lower()}) para a maquininha…')
        key = str(uuid.uuid4())
        self._run_point(lambda: self.point_api.point_criar(value, kind, key), self._point_created)

    def _point_created(self, data):
        self.point = data
        self.point_cancel.show()
        self.point_status.setText(f"Passe o cartão na maquininha · R$ {Decimal(str(data['valor'])):.2f}…")
        self.point_timer.start()

    def _poll_point(self):
        if not self.point or self.point.get('estado') != 'PENDENTE':
            self.point_timer.stop()
            return
        intent = self.point['id']
        self._run_point(lambda: self.point_api.point_consultar(intent), self._point_polled)

    def _point_polled(self, data):
        self.point = {**(self.point or {}), **data}
        estado = data.get('estado')
        if estado == 'PENDENTE':
            return
        self.point_timer.stop()
        self.point_cancel.hide()
        textos = {'APROVADO': 'Cartão aprovado ✓ — confirme o pagamento.',
                  'RECUSADO': 'Pagamento recusado na maquininha. Tente outro cartão ou outra forma.',
                  'CANCELADO': 'Cobrança cancelada na maquininha.', 'ERRO': 'A maquininha informou erro. Tente de novo.'}
        self.point_status.setText(textos.get(estado, f'Maquininha: {estado}'))

    def _cancel_point(self):
        if not self.point:
            return
        self.point_timer.stop()
        intent = self.point['id']
        self._run_point(lambda: self.point_api.point_cancelar(intent), self._point_polled)

    def done(self, result):
        if hasattr(self, 'pix_timer'):
            self.pix_timer.stop()
        if self.pix_worker and self.pix_worker.isRunning():
            self.pix_worker.wait(5000)
        if hasattr(self, 'point_timer'):
            self.point_timer.stop()
        if self.point_worker and self.point_worker.isRunning():
            self.point_worker.wait(5000)
        super().done(result)

    def accept_payment(self):
        self.credit = Decimal(str(self.credit_input.value())).quantize(Decimal('.01'))
        self.payments = split_payment(self.total, [(m, label, Decimal(str(w.value()))) for m, label, w in self.inputs], self.credit)
        if self.pix_api and any(p['meio'] == 'PIX' for p in self.payments) and not attach_pix(self.payments, self.pix):
            answer = QMessageBox.question(
                self, 'PIX não confirmado',
                'O PIX deste valor não foi confirmado pelo QR Code. O valor já está na conta da loja?',
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.payments = None
                return
        if self.point and self.point.get('estado') == 'PENDENTE':
            QMessageBox.warning(self, 'Maquininha', 'Aguarde a maquininha concluir ou cancele a cobrança antes de confirmar.')
            self.payments = None
            return
        attach_point(self.payments, self.point)
        self.accept()
