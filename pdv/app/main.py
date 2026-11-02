import json
import os
import socket
import sys
import uuid
from decimal import Decimal
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)
from app.api_client import ArmazemAPI
from app.local_store import LocalStore


class LoginDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Conectar ao Armazém PDV")
        self.api = None
        layout = QFormLayout(self)
        self.url = QLineEdit(os.getenv("APPDV_API_URL", "http://127.0.0.1:8000"))
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addRow("Endereço do servidor", self.url)
        layout.addRow("Usuário", self.username)
        layout.addRow("Senha", self.password)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.authenticate)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def authenticate(self):
        try:
            self.api = ArmazemAPI(self.url.text().strip())
            self.api.login(self.username.text().strip(), self.password.text())
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "Não foi possível entrar", str(exc))


class PDVWindow(QMainWindow):
    def __init__(self, api):
        super().__init__()
        self.api = api
        self.store = LocalStore()
        self.products = []
        self.cart = {}
        self.cash_id = None
        self.tenant_id = None
        self.setWindowTitle("Armazém PDV")
        self.resize(1120, 740)
        self._build()
        self._select_company()

    def _build(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        header = QHBoxLayout()
        self.company = QComboBox()
        self.status = QLabel("Conectando")
        self.refresh = QPushButton("Atualizar catálogo")
        self.refresh.clicked.connect(self._load_catalog)
        header.addWidget(QLabel("Empresa"))
        header.addWidget(self.company, 1)
        header.addWidget(self.status)
        header.addWidget(self.refresh)
        layout.addLayout(header)

        search = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Leia o código de barras ou pesquise pelo código")
        self.query.returnPressed.connect(self._add_by_code)
        add = QPushButton("Adicionar")
        add.clicked.connect(self._add_by_code)
        search.addWidget(self.query, 1)
        search.addWidget(add)
        layout.addLayout(search)

        self.catalog = QTableWidget(0, 4)
        self.catalog.setHorizontalHeaderLabels(["Código", "Produto", "Un.", "Preço"])
        self.catalog.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.catalog.cellDoubleClicked.connect(self._add_selected)
        layout.addWidget(self.catalog, 3)

        self.cart_table = QTableWidget(0, 4)
        self.cart_table.setHorizontalHeaderLabels(["Produto", "Qtd.", "Preço", "Total"])
        self.cart_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.cart_table, 2)

        footer = QHBoxLayout()
        self.total = QLabel("Total: R$ 0,00")
        self.total.setStyleSheet("font-size: 24px; font-weight: 800")
        self.payment = QComboBox()
        self.payment.addItems(["DINHEIRO", "PIX", "CARTAO"])
        self.pay = QPushButton("Finalizar venda")
        self.pay.clicked.connect(self._checkout)
        footer.addWidget(self.total, 1)
        footer.addWidget(QLabel("Pagamento"))
        footer.addWidget(self.payment)
        footer.addWidget(self.pay)
        layout.addLayout(footer)
        self.setCentralWidget(root)

    def _select_company(self):
        try:
            companies = self.api.minhas_empresas()
            self.company.clear()
            for company in companies:
                self.company.addItem(company["nome"], company["id"])
            self.company.currentIndexChanged.connect(self._company_changed)
            if companies:
                self._company_changed(0)
            else:
                QMessageBox.warning(self, "Sem acesso", "Sua conta não está vinculada a nenhuma empresa.")
        except Exception as exc:
            QMessageBox.critical(self, "Falha de conexão", str(exc))

    def _company_changed(self, index):
        if index < 0:
            return
        self.tenant_id = str(self.company.currentData())
        self.api.selecionar_empresa(self.tenant_id)
        self.cart.clear()
        try:
            caixa = self.api.abrir_caixa(socket.gethostname())
            self.cash_id = caixa["id"]
            self.status.setText("Caixa aberto · online")
        except Exception as exc:
            self.cash_id = None
            self.status.setText("Caixa indisponível")
            QMessageBox.warning(self, "Abertura de caixa", str(exc))
        self._load_catalog()

    def _load_catalog(self):
        if not self.tenant_id:
            return
        try:
            self.products = self.api.produtos()
            self.store.save_products(self.tenant_id, self.products)
            self.status.setText("Catálogo atualizado · online")
        except Exception:
            self.products = self.store.list_products(self.tenant_id)
            self.status.setText("Offline · catálogo em cache")
        self._render_catalog()
        self._sync_pending()

    def _render_catalog(self):
        self.catalog.setRowCount(len(self.products))
        for row, product in enumerate(self.products):
            values = [product.get("codigo", ""), product.get("nome", ""),
                      product.get("unidade", ""), f"R$ {Decimal(str(product.get('preco_venda', 0))):.2f}"]
            for col, value in enumerate(values):
                self.catalog.setItem(row, col, QTableWidgetItem(str(value)))
        self.catalog.resizeColumnsToContents()

    def _add_by_code(self):
        query = self.query.text().strip().lower()
        if not query:
            return
        product = next((p for p in self.products if query in
                        (str(p.get("codigo", "")).lower(), str(p.get("codigo_barras", "")).lower())), None)
        if product:
            self._add_product(product)
            self.query.clear()
        else:
            QMessageBox.information(self, "Produto não localizado", "Atualize o catálogo ou confira o código.")

    def _add_selected(self, row, _column):
        if row < len(self.products):
            self._add_product(self.products[row])

    def _add_product(self, product):
        pid = int(product["id"])
        available = Decimal(str(product.get("estoque_atual", "0")))
        qty = self.cart.get(pid, {}).get("quantidade", Decimal("0")) + Decimal("1")
        if qty > available:
            QMessageBox.warning(self, "Estoque", "A quantidade excede o saldo disponível no último catálogo.")
            return
        self.cart[pid] = {"produto": product, "quantidade": qty}
        self._render_cart()

    def _render_cart(self):
        rows = list(self.cart.values())
        self.cart_table.setRowCount(len(rows))
        total = Decimal("0")
        for row, item in enumerate(rows):
            product, qty = item["produto"], item["quantidade"]
            price = Decimal(str(product["preco_venda"]))
            line_total = qty * price
            total += line_total
            for col, value in enumerate([product["nome"], str(qty), f"R$ {price:.2f}", f"R$ {line_total:.2f}"]):
                self.cart_table.setItem(row, col, QTableWidgetItem(str(value)))
        self.total.setText(f"Total: R$ {total:.2f}")

    def _checkout(self):
        if not self.cash_id:
            QMessageBox.warning(self, "Caixa", "Abra o caixa com conexão antes de vender.")
            return
        if not self.cart:
            return
        total = sum((Decimal(str(item["produto"]["preco_venda"])) * item["quantidade"]
                     for item in self.cart.values()), Decimal("0")).quantize(Decimal("0.01"))
        key = str(uuid.uuid4())
        payload = {
            "idempotency_key": key, "caixa": self.cash_id, "observacao": "",
            "itens": [{"produto": pid, "quantidade": str(item["quantidade"]), "desconto": "0"}
                      for pid, item in self.cart.items()],
            "pagamentos": [{"meio": self.payment.currentText(), "valor": str(total)}],
        }
        try:
            response = self.api.registrar_venda(payload)
            QMessageBox.information(self, "Venda concluída", f"Venda #{response['id']} registrada.")
            self.cart.clear()
            self._render_cart()
            self._load_catalog()
        except Exception as exc:
            self.store.queue_sale(self.tenant_id, payload, str(exc))
            QMessageBox.warning(self, "Venda pendente",
                "O servidor não confirmou a venda. Ela foi guardada neste computador para sincronização. "
                "A sincronização só será aceita se caixa, estoque e valores continuarem válidos.\n\n" + str(exc))

    def _sync_pending(self):
        if not self.tenant_id or not self.cash_id:
            return
        for key, raw in self.store.pending_sales(self.tenant_id):
            payload = json.loads(raw)
            payload["caixa"] = self.cash_id
            try:
                self.api.registrar_venda(payload)
                self.store.mark_synced(key)
            except Exception as exc:
                self.store.set_error(key, exc)
                break


def main():
    app = QApplication(sys.argv)
    dialog = LoginDialog()
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    window = PDVWindow(dialog.api)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
