from decimal import Decimal
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QLabel, QDoubleSpinBox, QDialogButtonBox, QPushButton


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

    def update_summary(self):
        if not hasattr(self, 'summary'):
            return
        amounts = [(m, label, Decimal(str(widget.value()))) for m, label, widget in self.inputs]
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

    def accept_payment(self):
        self.credit = Decimal(str(self.credit_input.value())).quantize(Decimal('.01'))
        self.payments = split_payment(self.total, [(m, label, Decimal(str(w.value()))) for m, label, w in self.inputs], self.credit)
        self.accept()
