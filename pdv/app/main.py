import json
import base64
import html
import os
import re
from datetime import datetime
import socket
import sys
import uuid
from decimal import Decimal
from pathlib import Path

from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtCore import QUrl, QTimer, Qt, QSettings, QThread, Signal
import requests
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QInputDialog, QLineEdit, QLabel, QMainWindow, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.api_client import ArmazemAPI
from app.local_store import LocalStore
from app.payments import PaymentDialog
from app.quotes import quote_html
from app.discounts import distribute_discount
from app.customers import normalize_cpf


def asset_path(name):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return str(base / "assets" / name)


class LoginDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Conectar ao ApPdv")
        self.setWindowIcon(QIcon(asset_path("appdv-icon-256.png")))
        self.api = None
        self.setMinimumWidth(440)
        self.settings = QSettings("ApPdv", "PDV")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        logo = QLabel()
        logo.setPixmap(QPixmap(asset_path("appdv-logo-web.png")).scaled(
            240, 90, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo)
        heading = QLabel("Entre para iniciar o caixa")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)
        form = QFormLayout()
        self.url = QLineEdit(os.getenv("APPDV_API_URL") or self.settings.value(
            "server", "https://appdv.axionwebdigital.com.br"))
        self.username = QLineEdit(self.settings.value("username", ""))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        for field in (self.url, self.username, self.password):
            field.setMinimumHeight(36)
        form.addRow("Usuário", self.username)
        form.addRow("Senha", self.password)
        layout.addLayout(form)
        show_password = QCheckBox("Mostrar senha")
        show_password.toggled.connect(lambda checked: self.password.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password))
        layout.addWidget(show_password)
        self.remember = QCheckBox("Lembrar usuário")
        self.remember.setChecked(bool(self.username.text()))
        layout.addWidget(self.remember)
        advanced = QCheckBox("Configurações avançadas")
        layout.addWidget(advanced)
        server_panel = QWidget()
        server_form = QFormLayout(server_panel)
        server_form.addRow("Servidor", self.url)
        server_panel.hide()
        advanced.toggled.connect(server_panel.setVisible)
        layout.addWidget(server_panel)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Entrar")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Sair")
        self.buttons.accepted.connect(self.authenticate)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        (self.password if self.username.text() else self.username).setFocus()

    def authenticate(self):
        if not self.username.text().strip() or not self.password.text():
            self.error.setText("Informe o usuário e a senha.")
            return
        url = QUrl(self.url.text().strip())
        if url.scheme() != "https" or not url.host() or url.userInfo():
            self.error.setText("Informe um endereço HTTPS válido para o servidor.")
            return
        try:
            self.api = ArmazemAPI(self.url.text().strip())
            self.api.login(self.username.text().strip(), self.password.text())
            self.api.username = self.username.text().strip()
            self.settings.setValue("server", self.url.text().strip())
            self.settings.setValue("username", self.api.username if self.remember.isChecked() else "")
            self.password.clear()
            self.accept()
        except requests.HTTPError as exc:
            self.error.setText("Usuário ou senha inválidos." if exc.response is not None and
                               exc.response.status_code in (400, 401, 403) else
                               "O servidor não conseguiu concluir o acesso. Tente novamente.")
        except requests.RequestException:
            self.error.setText("Não foi possível conectar. Confira a internet e o servidor.")
        except (ValueError, KeyError):
            self.error.setText("Resposta de autenticação inválida. Confira o servidor.")


class AdminPanelDialog(QDialog):
    def __init__(self, api, empresa_id, company_name, cached_products, parent=None):
        super().__init__(parent)
        self.api = api
        self.empresa_id = str(empresa_id)
        self.setWindowTitle(f"Administração · {company_name}")
        self.resize(760, 500)
        layout = QVBoxLayout(self)
        heading = QLabel(f"Resumo administrativo — {company_name}")
        heading.setStyleSheet("font-size:22px;font-weight:800;color:palette(window-text)")
        layout.addWidget(heading)
        self.summary_label = QLabel("Carregando indicadores…")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("font-size:15px;padding:14px;background:palette(alternate-base);color:palette(text);border-radius:8px")
        layout.addWidget(self.summary_label)
        try:
            data = self.api.resumo_empresa()
            self.summary_label.setText(
                f"Produtos ativos: {data['produtos_ativos']}    ·    "
                f"Estoque baixo: {data['estoque_baixo']}\n"
                f"Vendas hoje: {data['vendas_hoje']}    ·    "
                f"Faturamento hoje: R$ {Decimal(str(data['faturamento_hoje'])):.2f}\n"
                f"Vendas no mês: {data['vendas_mes']}    ·    "
                f"Faturamento no mês: R$ {Decimal(str(data['faturamento_mes'])):.2f}\n"
                f"Pedidos confirmados aguardando: {data['pedidos_confirmados']}"
            )
        except Exception as exc:
            low_stock = sum(
                1 for product in cached_products
                if Decimal(str(product.get("estoque_atual", 0)))
                <= Decimal(str(product.get("estoque_minimo", 0)))
            )
            self.summary_label.setText(
                f"Sem conexão para carregar indicadores atualizados.\n"
                f"Catálogo local: {len(cached_products)} produtos · {low_stock} com estoque baixo.\n{exc}"
            )

        links = QHBoxLayout()
        base = self.api.base_url.rstrip("/")
        pages = [
            ("Painel Web", f"/empresa/{self.empresa_id}/"),
            ("Produtos e estoque", f"/empresa/{self.empresa_id}/produtos/"),
            ("Compras / NF-e", f"/empresa/{self.empresa_id}/compras/"),
            ("Inventário", f"/empresa/{self.empresa_id}/inventario/"),
            ("Autorizações", f"/empresa/{self.empresa_id}/autorizacoes/"),
            ("Vendas", f"/empresa/{self.empresa_id}/vendas/"),
        ]
        for label, path in pages:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, url=base + path: QDesktopServices.openUrl(QUrl(url)))
            links.addWidget(button)
        layout.addLayout(links)
        close = QPushButton("Fechar")
        close.clicked.connect(self.accept)
        layout.addWidget(close)


class PendingSyncWorker(QThread):
    result = Signal(str, str, object)

    def __init__(self, api, rows, parent=None):
        super().__init__(parent)
        self.rows = rows
        self.client = ArmazemAPI(api.base_url, empresa_id=api.empresa_id, timeout=4)
        self.client.session.headers.update(api.session.headers)
        self.client.refresh_token = api.refresh_token

    def run(self):
        for key, raw in self.rows:
            payload = json.loads(raw)
            try:
                if payload.get('cliente_offline'):
                    customer = payload['cliente_offline']
                    saved = self.client.cadastrar_cliente_cpf(customer['documento'], customer['nome'], customer.get('telefone', ''))
                    payload['cliente'] = saved['id']
                response = self.client.registrar_venda(payload)
                self.result.emit(key, '', response)
            except (requests.ConnectionError, requests.Timeout):
                self.result.emit(key, 'Sem conexão; nova tentativa automática.', None)
                break
            except Exception as exc:
                self.result.emit(key, str(exc), None)


class PDVWindow(QMainWindow):
    def __init__(self, api):
        super().__init__()
        self.api = api
        self.store = LocalStore()
        self.products = []
        self.cart = {}
        self.cash_id = None
        self.tenant_id = None
        self.company_info = {}
        self.selected_customer = None
        self.loaded_quote = None
        self.sale_key = str(uuid.uuid4())
        self.discount_approval = None
        self.online = True
        self.sync_worker = None
        self.total_discount = None
        self.quote_save_key = None
        self.checkout_payload = None
        self.current_product = None
        self.company_roles = {}
        self.tenant_role = "CAIXA"
        self.setWindowTitle("ApPdv — Frente de Caixa")
        self.setWindowIcon(QIcon(asset_path("appdv-icon-256.png")))
        self.resize(1120, 740)
        self._build()
        self._select_company()
        self.sync_timer = QTimer(self)
        self.sync_timer.timeout.connect(self._sync_pending)
        self.sync_timer.start(15000)

    def _build(self):
        self.setStyleSheet("""
            QLabel#brandTitle { color: palette(highlighted-text); font-size: 20px; font-weight: 800; }
            QLabel#operator, QLabel#clock, QLabel#status { font-weight: 700; }
            QLabel#productName, QLabel#total {
                background: palette(highlight);
                color: palette(highlighted-text);
                font-weight: 800;
                padding: 9px 14px;
                border-radius: 8px;
            }
            QLabel#productName { font-size: 22px; }
            QLabel#total { font-size: 32px; }
            QLabel#productValue {
                background: palette(base);
                color: palette(text);
                font-size: 16px;
                font-weight: 700;
                padding: 7px 9px;
                border: 1px solid palette(mid);
                border-radius: 6px;
            }
            QLabel#subtotal { font-size: 15px; font-weight: 700; }
            QFrame#panel {
                background: palette(window);
                border: 1px solid palette(mid);
                border-radius: 10px;
            }
            QLineEdit, QComboBox { min-height: 28px; }
            QPushButton { min-height: 34px; padding: 5px 10px; }
            QPushButton#primary { min-height: 44px; font-weight: 700; }
        """)
        root = QWidget()
        root.setObjectName("posRoot")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        brand_icon = QLabel()
        pixmap = QPixmap(asset_path("appdv-icon-256.png"))
        if not pixmap.isNull():
            brand_icon.setPixmap(pixmap.scaled(44, 44, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        brand_title = QLabel("ApPdv · Frente de Caixa")
        brand_title.setObjectName("brandTitle")
        brand_box = QWidget()
        brand_box.setStyleSheet("background:palette(highlight); border-radius:9px;")
        brand_layout = QHBoxLayout(brand_box)
        brand_layout.setContentsMargins(8, 4, 14, 4)
        brand_layout.addWidget(brand_icon)
        brand_layout.addWidget(brand_title)
        header.addWidget(brand_box)

        self.company = QComboBox()
        self.company.setMinimumWidth(150)
        self.status = QLabel("Conectando")
        self.status.setObjectName("status")
        self.operator_label = QLabel(f"Atendente: {getattr(self.api, 'username', '—')}")
        self.operator_label.setObjectName("operator")
        self.clock_label = QLabel()
        self.clock_label.setObjectName("clock")
        header.addWidget(QLabel("Empresa"))
        header.addWidget(self.company)
        header.addStretch(1)
        header.addWidget(self.operator_label)
        header.addWidget(self.clock_label)
        header.addWidget(self.status)
        layout.addLayout(header)

        self.product_banner = QLabel("Leia um código de barras ou pesquise um produto")
        self.product_banner.setObjectName("productName")
        layout.addWidget(self.product_banner)

        search = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Código, código de barras ou nome do produto")
        self.query.returnPressed.connect(self._add_by_code)
        add = QPushButton("Adicionar ao carrinho")
        add.setObjectName("primary")
        add.clicked.connect(self._add_by_code)
        search.addWidget(QLabel("Código / busca"))
        search.addWidget(self.query, 1)
        search.addWidget(add)
        layout.addLayout(search)

        work_area = QHBoxLayout()
        product_panel = QWidget()
        product_panel.setObjectName("panel")
        product_layout = QVBoxLayout(product_panel)
        product_layout.addWidget(QLabel("Consulta de estoque e preço"))
        detail_rows = QHBoxLayout()
        logo = QLabel()
        self.company_logo_label = logo
        logo_pixmap = QPixmap(asset_path("appdv-logo-web.png"))
        if not logo_pixmap.isNull():
            logo.setPixmap(logo_pixmap.scaled(170, 90, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail_rows.addWidget(logo, 1)
        facts = QVBoxLayout()
        self.detail_code = QLabel("Código: —")
        self.detail_quantity = QLabel("Quantidade no carrinho: —")
        self.detail_price = QLabel("Preço unitário: —")
        self.detail_line_total = QLabel("Preço total: —")
        self.detail_stock = QLabel("Saldo disponível: —")
        for label in (self.detail_code, self.detail_quantity, self.detail_price, self.detail_line_total, self.detail_stock):
            label.setObjectName("productValue")
            facts.addWidget(label)
        detail_rows.addLayout(facts, 2)
        product_layout.addLayout(detail_rows)

        self.catalog = QTableWidget(0, 6)
        self.catalog.setHorizontalHeaderLabels(["Código", "Produto", "Setor", "Endereço", "Preço", "Saldo"])
        self.catalog.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.catalog.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.catalog.cellDoubleClicked.connect(self._add_selected)
        product_layout.addWidget(self.catalog, 1)
        work_area.addWidget(product_panel, 5)

        cart_panel = QWidget()
        cart_panel.setObjectName("panel")
        cart_layout = QVBoxLayout(cart_panel)
        cart_layout.addWidget(QLabel("Itens da venda"))
        self.cart_table = QTableWidget(0, 5)
        self.cart_table.setHorizontalHeaderLabels(["Produto", "Qtd.", "Preço", "Desconto", "Total"])
        self.cart_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.cart_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        cart_layout.addWidget(self.cart_table, 1)
        cart_actions = QHBoxLayout()
        self.edit_cart_item = QPushButton("Quantidade")
        self.edit_cart_item.clicked.connect(self._edit_cart_item)
        self.remove_cart_item = QPushButton("Remover")
        self.remove_cart_item.clicked.connect(self._remove_cart_item)
        self.discount_cart_item = QPushButton("Desconto total (R$ / %)")
        self.discount_cart_item.clicked.connect(self._discount_cart_item)
        self.discount_cart_item.setEnabled(False)
        self.pending_button = QPushButton("Sincronização")
        self.pending_button.clicked.connect(self._show_pending)
        cart_actions.addWidget(self.pending_button)
        self.hold_cart = QPushButton("Suspender")
        self.hold_cart.clicked.connect(self._suspend_sale)
        self.resume_cart = QPushButton("Retomar")
        self.resume_cart.clicked.connect(self._resume_sale)
        for button in (self.edit_cart_item, self.remove_cart_item, self.discount_cart_item, self.hold_cart, self.resume_cart):
            cart_actions.addWidget(button)
        cart_layout.addLayout(cart_actions)
        work_area.addWidget(cart_panel, 7)
        layout.addLayout(work_area, 1)

        totals = QHBoxLayout()
        self.subtotal = QLabel("Subtotal: R$ 0,00")
        self.subtotal.setObjectName("subtotal")
        self.total = QLabel("TOTAL  R$ 0,00")
        self.total.setObjectName("total")
        totals.addWidget(self.subtotal, 1)
        totals.addWidget(self.total, 1)
        layout.addLayout(totals)

        actions = QHBoxLayout()
        self.pay = QPushButton("F2 · Finalizar venda")
        self.pay.setObjectName("primary")
        self.pay.clicked.connect(self._checkout)
        self.quote = QPushButton("F3 · Fazer / imprimir orçamento")
        self.quote.clicked.connect(self._print_quote)
        self.find_quote = QPushButton("Buscar orçamento")
        self.find_quote.clicked.connect(self._find_quote)
        self.web_order = QPushButton("Pedido via WhatsApp")
        self.web_order.clicked.connect(self._create_web_order)
        self.web_orders = QPushButton("Pedidos confirmados")
        self.web_orders.clicked.connect(self._review_web_orders)
        self.cash_move = QPushButton("Sangria / suprimento")
        self.cash_move.clicked.connect(self._cash_movement)
        self.close_cash = QPushButton("Fechar caixa")
        self.close_cash.clicked.connect(self._close_cash)
        self.cancel_sale = QPushButton("Cancelar venda")
        self.cancel_sale.clicked.connect(self._cancel_sale)
        self.admin_button = QPushButton("Área administrativa")
        self.admin_button.clicked.connect(self._open_admin_panel)
        self.admin_button.setVisible(False)
        for button in (self.pay, self.quote, self.find_quote, self.web_order, self.web_orders, self.cash_move, self.close_cash, self.cancel_sale, self.admin_button):
            actions.addWidget(button)
        layout.addLayout(actions)

        self.setCentralWidget(root)
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._update_clock()

    def _select_company(self):
        try:
            companies = self.api.minhas_empresas()
            for company in companies:
                self.company.addItem(company["nome"], company["id"])
                self.company_roles[str(company["id"])] = company.get("papel", "CAIXA")
            self.company.currentIndexChanged.connect(self._company_changed)
            if companies:
                self._company_changed(0)
            else:
                QMessageBox.warning(
                    self, "Sem acesso", "Sua conta não está vinculada a nenhuma empresa."
                )
        except Exception as exc:
            QMessageBox.critical(self, "Falha de conexão", str(exc))

    def _company_changed(self, index):
        if index < 0:
            return
        self.total_discount = None
        self.selected_customer = None
        self.loaded_quote = None
        self.checkout_payload = None
        self.tenant_id = str(self.company.currentData())
        self.tenant_role = self.company_roles.get(self.tenant_id, "CAIXA")
        self.discount_cart_item.setEnabled(True)
        self.admin_button.setVisible(self.tenant_role in ("ADMIN", "GERENTE"))
        self.api.selecionar_empresa(self.tenant_id)
        try:
            self.company_info = self.api.empresa_info()
            self.store.save_company(self.tenant_id, self.company_info)
        except Exception:
            self.company_info = self.store.company(self.tenant_id) or {"nome": self.company.currentText(), "logo_data_url": ""}
        self._update_company_logo()
        self.cart.clear()
        saldo, accepted = QInputDialog.getDouble(
            self, "Abertura do caixa", "Saldo inicial em dinheiro (R$):",
            0.00, 0.00, 10000000.00, 2,
        )
        if not accepted:
            return
        try:
            caixa = self.api.abrir_caixa(socket.gethostname(), saldo)
            self.cash_id = caixa["id"]
            self.status.setText("Caixa aberto · online")
        except Exception as exc:
            self.cash_id = None
            self.status.setText("Caixa indisponível")
            QMessageBox.warning(self, "Abertura de caixa", str(exc))
        self._load_catalog()
        attempt = self.store.checkout_attempt(self.tenant_id, self.api.username)
        if attempt:
            self.checkout_payload = attempt['payload']
            self.sale_key = self.checkout_payload["idempotency_key"]
            self.selected_customer = attempt.get('cliente')
            self.loaded_quote = self.checkout_payload.get('orcamento')
            self.last_payments = self.checkout_payload['pagamentos']
            self.cart = {int(pk): {'produto': item['produto'], 'quantidade': Decimal(item['quantidade']), 'desconto': Decimal(item['desconto'])} for pk, item in attempt['cart'].items()}
            self.company.setEnabled(False)
            self._render_cart()
            QMessageBox.information(self, "Venda pendente recuperada", "Há uma tentativa de venda pendente deste operador. Clique em Finalizar para confirmar a mesma operação sem duplicar.")

    def _load_catalog(self):
        if not self.tenant_id:
            return
        try:
            self.products = self.api.produtos()
            self.online = True
            self.store.save_products(self.tenant_id, self.products)
            self.status.setText("Catálogo atualizado · online")
        except Exception:
            self.online = False
            self.products = self.store.list_products(self.tenant_id)
            self.status.setText("Offline · catálogo em cache")
        self._render_catalog()
        self._sync_pending()

    def _render_catalog(self):
        self.catalog.setRowCount(len(self.products))
        for row, product in enumerate(self.products):
            values = [
                product.get("codigo", ""), product.get("nome", ""),
                product.get("setor", ""), product.get("endereco", ""),
                f"R$ {Decimal(str(product.get('preco_venda', 0))):.2f}",
                f"{product.get('estoque_atual', '0')} {product.get('unidade_estoque') or product.get('unidade', '')}",
            ]
            for col, value in enumerate(values):
                self.catalog.setItem(row, col, QTableWidgetItem(str(value)))
        self.catalog.resizeColumnsToContents()

    def _add_by_code(self):
        query = self.query.text().strip().lower()
        if not query:
            return
        exact = next(
            (p for p in self.products if query in (
                str(p.get("codigo", "")).strip().lower(),
                str(p.get("codigo_barras", "")).strip().lower(),
            )),
            None,
        )
        matches = [exact] if exact else [
            p for p in self.products if query in str(p.get("nome", "")).lower()
            or query in str(p.get("codigo", "")).lower()
        ]
        if len(matches) == 1:
            self._add_product(matches[0])
            self.query.clear()
        elif matches:
            choices = [
                f"{p.get('codigo','')} · {p.get('nome','')} · "
                f"R$ {Decimal(str(p.get('preco_venda',0))):.2f} · saldo {p.get('estoque_atual',0)}"
                for p in matches[:100]
            ]
            selected, accepted = QInputDialog.getItem(
                self, "Selecionar produto", "Foram encontrados vários resultados:",
                choices, 0, False,
            )
            if accepted:
                self._add_product(matches[choices.index(selected)])
                self.query.clear()
        else:
            QMessageBox.information(
                self, "Produto não localizado", "Atualize o catálogo ou confira o código."
            )

    def _add_selected(self, row, _column):
        if row < len(self.products):
            self._add_product(self.products[row])

    def _add_product(self, product):
        if self.checkout_payload:
            QMessageBox.warning(self, "Venda pendente", "Finalize a tentativa pendente antes de alterar o carrinho.")
            return
        product_id = int(product["id"])
        available = Decimal(str(product.get("estoque_atual", "0")))
        quantity = self.cart.get(product_id, {}).get("quantidade", Decimal("0")) + Decimal("1")
        if quantity * Decimal(str(product.get("fator_conversao", "1"))) > available:
            QMessageBox.warning(
                self, "Estoque", "A quantidade excede o saldo disponível no último catálogo."
            )
            return
        prior_discount = self.cart.get(product_id, {}).get("desconto", Decimal("0"))
        self.cart[product_id] = {
            "produto": product, "quantidade": quantity, "desconto": prior_discount,
        }
        self._show_product(product)
        self._render_cart()

    def _suspend_sale(self):
        if self.checkout_payload:
            QMessageBox.warning(self, "Venda pendente", "Finalize a tentativa pendente antes de alterar o carrinho.")
            return
        if not self.cart or not self.tenant_id:
            QMessageBox.information(self, "Suspender venda", "O carrinho está vazio.")
            return
        label, accepted = QInputDialog.getText(
            self, "Suspender venda", "Identificação para localizar depois:",
            text=f"Venda {datetime.now().strftime('%H:%M')}",
        )
        if not accepted:
            return
        payload = {
            "cliente": self.selected_customer,
            "orcamento": self.loaded_quote,
            "items": [
                {
                    "produto": product_id, "quantidade": str(item["quantidade"]),
                    "desconto": str(item.get("desconto", Decimal("0"))),
                }
                for product_id, item in self.cart.items()
            ]
        }
        self.store.hold_sale(self.tenant_id, label.strip() or "Venda suspensa", payload)
        self.total_discount = None
        self.loaded_quote = None
        self.selected_customer = None
        self.cart.clear()
        self._render_cart()
        QMessageBox.information(self, "Venda suspensa", "O carrinho foi salvo neste computador.")

    def _resume_sale(self):
        if self.checkout_payload:
            QMessageBox.warning(self, "Venda pendente", "Finalize a tentativa pendente antes de alterar o carrinho.")
            return
        if not self.tenant_id:
            return
        held = self.store.held_sales(self.tenant_id)
        if not held:
            QMessageBox.information(self, "Retomar venda", "Não há vendas suspensas neste computador.")
            return
        choices = [f"{row[1]} · {row[3]}" for row in held]
        selected, accepted = QInputDialog.getItem(
            self, "Retomar venda", "Selecione um carrinho suspenso:", choices, 0, False
        )
        if not accepted:
            return
        sale_id, _label, raw_payload, _created = held[choices.index(selected)]
        payload = json.loads(raw_payload)
        product_map = {int(product["id"]): product for product in self.products}
        restored = {}
        for line in payload.get("items", []):
            product_id = int(line["produto"])
            product = product_map.get(product_id)
            if not product:
                QMessageBox.warning(
                    self, "Produto indisponível",
                    f"O produto {product_id} não está no catálogo em cache. Atualize o catálogo.",
                )
                return
            restored[product_id] = {
                "produto": product,
                "quantidade": Decimal(str(line["quantidade"])),
                "desconto": Decimal(str(line.get("desconto", "0"))),
            }
        if self.cart:
            decision = QMessageBox.question(
                self, "Carrinho em uso",
                "Deseja suspender o carrinho atual antes de retomar o selecionado?",
            )
            if decision != QMessageBox.StandardButton.Yes:
                return
            self._suspend_sale()
            if self.cart:
                return
        self.total_discount = None
        self.loaded_quote = payload.get("orcamento")
        self.selected_customer = payload.get("cliente")
        self.cart = restored
        self.store.remove_held_sale(sale_id, self.tenant_id)
        self._render_cart()

    def _edit_cart_item(self):
        if self.checkout_payload:
            QMessageBox.warning(self, "Venda pendente", "Finalize a tentativa pendente antes de alterar o carrinho.")
            return
        row = self.cart_table.currentRow()
        entries = list(self.cart.items())
        if row < 0 or row >= len(entries):
            QMessageBox.information(self, "Carrinho", "Selecione um item do carrinho.")
            return
        product_id, item = entries[row]
        product = item["produto"]
        available = Decimal(str(product.get("estoque_atual", "0"))) / Decimal(str(product.get("fator_conversao", "1")))
        quantity, accepted = QInputDialog.getDouble(
            self, "Alterar quantidade", product["nome"],
            float(item["quantidade"]), 0.001, float(available), 3,
        )
        if not accepted:
            return
        if Decimal(str(quantity)) > available:
            QMessageBox.warning(self, "Estoque", "A quantidade excede o saldo disponível.")
            return
        new_quantity = Decimal(str(quantity)).quantize(Decimal("0.001"))
        gross = (Decimal(str(product["preco_venda"])) * new_quantity).quantize(Decimal("0.01"))
        self.cart[product_id]["quantidade"] = new_quantity
        if self.cart[product_id].get("desconto", Decimal("0")) > gross:
            self.cart[product_id]["desconto"] = Decimal("0")
        self._show_product(product)
        self._render_cart()

    def _discount_cart_item(self):
        if self.checkout_payload:
            QMessageBox.warning(self, "Venda pendente", "Finalize a tentativa pendente antes de alterar o carrinho.")
            return
        if not self.cart:
            return
        mode, ok = QInputDialog.getItem(self, "Desconto no total", "Tipo de desconto:", ["R$", "%"], 0, False)
        if not ok:
            return
        gross = sum(((Decimal(str(item['produto']['preco_venda'])) * item['quantidade']).quantize(Decimal('.01')) for item in self.cart.values()), Decimal('0'))
        value, ok = QInputDialog.getDouble(self, "Desconto no total", "Percentual (%):" if mode == '%' else "Valor do desconto (R$):", 0, 0, 100 if mode == '%' else float(gross), 2)
        if not ok:
            return
        previous_mode = self.total_discount
        previous_values = [item.get('desconto', Decimal('0')) for item in self.cart.values()]
        self.total_discount = (mode, Decimal(str(value)))
        self._render_cart()
        if not self._ensure_discount_approval(self.sale_key, self._cart_lines()):
            self.total_discount = previous_mode
            for item, discount in zip(self.cart.values(), previous_values):
                item['desconto'] = discount
            self._render_cart()
            return
        # A conversão exigirá salvar novo orçamento se suas condições forem alteradas.
        self._render_cart()

    def _remove_cart_item(self):
        if self.checkout_payload:
            QMessageBox.warning(self, "Venda pendente", "Finalize a tentativa pendente antes de alterar o carrinho.")
            return
        row = self.cart_table.currentRow()
        entries = list(self.cart)
        if row < 0 or row >= len(entries):
            QMessageBox.information(self, "Carrinho", "Selecione um item do carrinho.")
            return
        self.cart.pop(entries[row], None)
        self._render_cart()

    def _render_cart(self):
        rows = list(self.cart.values())
        if self.total_discount and rows:
            gross_values = [(Decimal(str(item['produto']['preco_venda'])) * item['quantidade']).quantize(Decimal('.01')) for item in rows]
            mode, value = self.total_discount
            if mode == 'R$':
                value = min(value, sum(gross_values, Decimal('0')))
            for item, discount in zip(rows, distribute_discount(gross_values, mode, value)):
                item['desconto'] = discount
        self.cart_table.setRowCount(len(rows))
        total = Decimal("0")
        for row, item in enumerate(rows):
            product, quantity = item["produto"], item["quantidade"]
            price = Decimal(str(product["preco_venda"]))
            discount = Decimal(str(item.get("desconto", "0")))
            line_total = (quantity * price - discount).quantize(Decimal("0.01"))
            total += line_total
            values = [
                product["nome"], str(quantity), f"R$ {price:.2f}",
                f"R$ {discount:.2f}", f"R$ {line_total:.2f}",
            ]
            for col, value in enumerate(values):
                self.cart_table.setItem(row, col, QTableWidgetItem(str(value)))
        gross = sum(((Decimal(str(item['produto']['preco_venda'])) * item['quantidade']).quantize(Decimal('.01')) for item in rows), Decimal('0'))
        self.subtotal.setText(f"Subtotal: R$ {gross:.2f} · Desconto: R$ {gross - total:.2f}")
        self.total.setText(f"Total: R$ {total:.2f}")

    def _update_clock(self):
        self.clock_label.setText(datetime.now().strftime("%H:%M:%S  |  %d/%m/%Y"))

    def _show_product(self, product):
        self.current_product = product
        product_id = int(product["id"])
        cart_item = self.cart.get(product_id, {})
        quantity = cart_item.get("quantidade", Decimal("0"))
        discount = cart_item.get("desconto", Decimal("0"))
        price = Decimal(str(product.get("preco_venda", "0")))
        line_total = (quantity * price - discount).quantize(Decimal("0.01"))
        self.product_banner.setText(str(product.get("nome", "Produto")))
        self.detail_code.setText(f"Código: {product.get('codigo', '—')} · EAN: {product.get('codigo_barras', '—')}")
        self.detail_quantity.setText(f"Quantidade no carrinho: {quantity if quantity else '—'} {product.get('unidade', '')}")
        self.detail_price.setText(f"Preço unitário: R$ {price:.2f}")
        self.detail_line_total.setText(f"Preço total: R$ {line_total:.2f}" if quantity else "Preço total: —")
        stock_unit = product.get("unidade_estoque") or product.get("unidade", "")
        factor = Decimal(str(product.get("fator_conversao", "1")))
        conversion = f" · 1 {product.get('unidade', '')} = {factor.normalize():g} {stock_unit}" if factor != 1 or stock_unit != product.get("unidade", "") else ""
        self.detail_stock.setText(
            f"Saldo disponível: {product.get('estoque_atual', 0)} {stock_unit}{conversion}  ·  "
            f"{product.get('setor', 'Sem setor')} / {product.get('endereco', 'Sem endereço')}"
        )

    def _open_admin_panel(self):
        if self.tenant_role not in ("ADMIN", "GERENTE"):
            QMessageBox.warning(self, "Acesso negado", "A área administrativa exige perfil autorizado.")
            return
        dialog = AdminPanelDialog(
            self.api, self.tenant_id, self.company.currentText(), self.products, self
        )
        dialog.exec()

    def _identify_customer(self):
        cpf, accepted = QInputDialog.getText(self, "Identificar cliente", "CPF do cliente:",
            text=(self.selected_customer or {}).get("documento", ""))
        if not accepted:
            return None
        try:
            cpf = normalize_cpf(cpf.strip())
            offline = False
            try:
                customer = self.api.cliente_cpf(cpf)
                self.online = True
            except (requests.ConnectionError, requests.Timeout):
                offline = True
                self.online = False
                customer = self.store.customer(self.tenant_id, cpf)
            if customer:
                if QMessageBox.question(self, "Cliente encontrado", f"Utilizar o cadastro de {customer['nome']}?" + (" (cache local)" if offline else "")) != QMessageBox.StandardButton.Yes:
                    return None
            else:
                name, accepted = QInputDialog.getText(self, "Novo cliente", "CPF não cadastrado. Nome completo:" + (" (cadastro local pendente)" if offline else ""))
                if not accepted or not name.strip():
                    return None
                phone, accepted = QInputDialog.getText(self, "Novo cliente", "Telefone (opcional):")
                if not accepted:
                    return None
                customer = {'id': None, 'nome': name.strip(), 'documento': cpf, 'telefone': phone.strip(), 'limite_credito': '0', 'offline': True}
                if not offline:
                    try:
                        customer = self.api.cadastrar_cliente_cpf(cpf, name.strip(), phone.strip())
                    except (requests.ConnectionError, requests.Timeout):
                        self.online = False
            self.store.save_customer(self.tenant_id, customer)
            self.selected_customer = customer
            return customer
        except Exception as exc:
            QMessageBox.warning(self, "Cliente", str(exc))
            return None

    def _show_saved_quote(self, quote):
        folder = Path(self.store.path).parent / "orcamentos"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"orcamento-{quote['id']}.html"
        path.write_text(quote_html(quote, self.company_info, asset_path("appdv-logo-web.png")), encoding="utf-8")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _print_quote(self):
        if self.checkout_payload:
            QMessageBox.warning(self, "Venda pendente", "Finalize a tentativa pendente antes de alterar o carrinho.")
            return
        if not self.cart:
            QMessageBox.information(self, "Orçamento", "Adicione produtos antes de salvar o orçamento.")
            return
        customer = self._identify_customer()
        if not customer:
            return
        validity, accepted = QInputDialog.getInt(self, "Validade", "Validade em dias:", 7, 1, 90)
        if not accepted:
            return
        from datetime import timedelta
        payload = {"cliente": customer['id'], "validade": (datetime.now().date() + timedelta(days=validity)).isoformat(),
            "itens": [{"produto": pk, "quantidade": str(item['quantidade']), "desconto": str(item.get('desconto', '0'))} for pk, item in self.cart.items()]}
        signature = json.dumps(payload, sort_keys=True)
        if not self.quote_save_key or self.quote_save_key[0] != signature:
            self.quote_save_key = (signature, str(uuid.uuid4()))
        payload['chave'] = self.quote_save_key[1]
        if not self._ensure_discount_approval(payload['chave'], payload['itens']):
            return
        if self.discount_approval:
            payload['autorizacao_desconto'] = self.discount_approval[1]
        try:
            quote = self.api.salvar_orcamento(payload)
            self.loaded_quote = quote["id"]
            self._show_saved_quote(quote)
            QMessageBox.information(self, "Orçamento salvo", f"Orçamento nº {quote['id']} salvo no sistema. Pesquise pelo número ou CPF.")
        except Exception as exc:
            QMessageBox.warning(self, "Orçamento", f"Não foi possível salvar/imprimir. Tente novamente; a tentativa utiliza a mesma chave para evitar duplicação.\n{exc}")

    def _find_quote(self):
        if self.checkout_payload:
            QMessageBox.warning(self, "Venda pendente", "Conclua a tentativa de venda antes de buscar outro orçamento.")
            return
        kind, ok = QInputDialog.getItem(self, "Buscar orçamento", "Pesquisar por:", ["Número", "CPF"], 0, False)
        if not ok:
            return
        query, ok = QInputDialog.getText(self, "Buscar orçamento", kind + ":")
        if not ok or not query.strip():
            return
        try:
            quotes = self.api.buscar_orcamentos(query.strip(), 'cpf' if kind == 'CPF' else 'numero')
            if not quotes:
                QMessageBox.information(self, "Orçamentos", "Nenhum orçamento encontrado nesta empresa.")
                return
            labels = [f"#{q['id']} · {q['cliente']['nome']} · R$ {q['total']} · {q['estado']} · até {q['validade']}" for q in quotes]
            choice, ok = QInputDialog.getItem(self, "Orçamentos", "Selecione:", labels, 0, False)
            if not ok:
                return
            quote = quotes[labels.index(choice)]
            action, ok = QInputDialog.getItem(self, "Orçamento", "Ação:", ['Imprimir novamente', 'Carregar para venda'], 0, False)
            if not ok:
                return
            if action == 'Imprimir novamente':
                self._show_saved_quote(quote)
                return
            if quote['estado'] != 'ABERTO':
                QMessageBox.warning(self, "Orçamento", "Somente orçamentos abertos e dentro da validade podem ser convertidos.")
                return
            if self.cart and QMessageBox.question(self, "Substituir carrinho", "Substituir os itens atuais pelos itens do orçamento?") != QMessageBox.StandardButton.Yes:
                return
            products = self.api.produtos()
            products = products.get('results', []) if isinstance(products, dict) else products
            catalog = {int(product['id']): product for product in products}
            cart, changed = {}, False
            for item in quote['itens']:
                product = catalog.get(int(item['produto']))
                if not product:
                    raise ValueError('Há produto indisponível no orçamento. Atualize o cadastro antes de converter.')
                changed = changed or Decimal(str(product['preco_venda'])) != Decimal(item['preco_venda'])
                cart[int(item['produto'])] = {'produto': product, 'quantidade': Decimal(item['quantidade']), 'desconto': Decimal(item['desconto'])}
            if changed and QMessageBox.question(self, "Preços atualizados", "Os preços mudaram desde o orçamento. Carregar os preços atuais para conferência antes do pagamento?") != QMessageBox.StandardButton.Yes:
                return
            self.total_discount = None
            self.cart = cart
            self.selected_customer = quote['cliente']
            self.loaded_quote = quote['id']
            self._render_cart()
            self.status.setText(f"Orçamento #{quote['id']} · {quote['cliente']['nome']} · confira e finalize com F2")
        except Exception as exc:
            QMessageBox.warning(self, "Orçamento", str(exc))

    def _checkout(self):
        if self.checkout_payload:
            self._submit_checkout(self.checkout_payload)
            return
        if not self.cash_id:
            QMessageBox.warning(self, "Caixa", "Abra o caixa com conexão antes de vender.")
            return
        if not self.cart:
            return
        for item in self.cart.values():
            product = item['produto']
            if item['quantidade'] * Decimal(str(product.get('fator_conversao', '1'))) > Decimal(str(product.get('estoque_atual', '0'))):
                QMessageBox.warning(self, 'Estoque local', f"Quantidade acima do saldo conhecido de {product['nome']}.")
                return
        total = sum(
            ((Decimal(str(item["produto"]["preco_venda"])) * item["quantidade"]).quantize(Decimal("0.01"))
             - Decimal(str(item.get("desconto", "0")))
             for item in self.cart.values()),
            Decimal("0"),
        ).quantize(Decimal("0.01"))
        customer = self._identify_customer()
        if not customer:
            return
        customer_id = customer['id']
        dialog = PaymentDialog(total, customer, self)
        def change_discount():
            self._discount_cart_item()
            gross = sum(((Decimal(str(item['produto']['preco_venda'])) * item['quantidade']).quantize(Decimal('.01')) for item in self.cart.values()), Decimal('0'))
            discount = sum((Decimal(str(item.get('desconto', '0'))) for item in self.cart.values()), Decimal('0'))
            return gross, discount
        dialog.configure_discount(change_discount, True)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payments, credit = dialog.payments, dialog.credit
        total = dialog.total
        self.last_payments = payments
        key = self.sale_key
        payload = {
            "idempotency_key": key,
            "caixa": self.cash_id,
            "observacao": "",
            "cliente": customer_id,
            "operador_local": self.api.username,
            "credito": str(credit),
            "itens": [
                {
                    "produto": product_id, "quantidade": str(item["quantidade"]),
                    "desconto": str(item.get("desconto", Decimal("0"))),
                }
                for product_id, item in self.cart.items()
            ],
            "pagamentos": payments,
        }
        if customer.get('offline'):
            payload['cliente_offline'] = customer
        if credit > 0 and not self.online:
            QMessageBox.warning(self, "Venda a prazo", "Venda a prazo exige conexão para verificar o limite de crédito.")
            return
        if not self._ensure_discount_approval(key, payload['itens']):
            return
        if self.discount_approval:
            payload['autorizacao_desconto'] = self.discount_approval[1]
        for line in payload['itens']:
            line['preco_esperado'] = str(self.cart[line['produto']]['produto']['preco_venda'])
        if self.loaded_quote:
            payload['orcamento'] = self.loaded_quote
            payload['total_confirmado'] = str(total)
        self.store.save_checkout_attempt(self.tenant_id, self.api.username, {
            'payload': payload, 'cliente': self.selected_customer,
            'cart': {str(pk): {'produto': item['produto'], 'quantidade': str(item['quantidade']), 'desconto': str(item.get('desconto', '0'))} for pk, item in self.cart.items()}})
        self.checkout_payload = payload
        self.company.setEnabled(False)
        self._submit_checkout(payload)

    def _submit_checkout(self, payload):
        try:
            if payload.get('cliente_offline'):
                local = payload['cliente_offline']
                customer = self.api.cadastrar_cliente_cpf(local['documento'], local['nome'], local.get('telefone', ''))
                payload['cliente'] = customer['id']
                self.store.save_customer(self.tenant_id, customer)
            response = self.api.registrar_venda(payload)
            receipt = self._build_receipt(response["id"])
            receipt_path = self.store.save_receipt(response["id"], receipt)
            box = QMessageBox(self)
            box.setWindowTitle("Venda concluída")
            box.setText(f"Venda #{response['id']} registrada. Cupom interno salvo.")
            box.setInformativeText(str(receipt_path))
            open_button = box.addButton("Abrir cupom", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Fechar", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() == open_button:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(receipt_path)))
            self.store.clear_checkout_attempt(self.tenant_id, self.api.username)
            self.checkout_payload = None
            self.sale_key = str(uuid.uuid4())
            self.discount_approval = None
            self.company.setEnabled(True)
            self.total_discount = None
            self.loaded_quote = None
            self.selected_customer = None
            self.cart.clear()
            self._render_cart()
            self._load_catalog()
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            self.online = False
            if Decimal(payload.get('credito', '0')) > 0:
                QMessageBox.warning(self, "Crédito pendente", "A confirmação da venda a prazo exige conexão. Finalize novamente ao reconectar.")
                return
            self.store.queue_sale(self.tenant_id, payload, "Aguardando conexão")
            path = self.store.save_receipt('PENDENTE-' + payload['idempotency_key'], self._build_receipt('PENDENTE — ' + payload['idempotency_key']))
            # Local quantities are provisional; the server validates again on sync.
            for line in payload['itens']:
                for product in self.products:
                    if int(product['id']) == int(line['produto']):
                        product['estoque_atual'] = str(max(Decimal('0'), Decimal(str(product['estoque_atual'])) - Decimal(line['quantidade']) * Decimal(str(product.get('fator_conversao', '1')))))
            self.store.save_products(self.tenant_id, self.products)
            self.store.clear_checkout_attempt(self.tenant_id, self.api.username)
            self.checkout_payload = None
            self.company.setEnabled(True)
            self.sale_key = str(uuid.uuid4())
            self.discount_approval = None
            self.total_discount = None
            self.loaded_quote = None
            self.selected_customer = None
            self.cart.clear()
            self._render_cart()
            self._render_catalog()
            self.status.setText('Offline · venda salva localmente · aguardando sincronização')
            QMessageBox.information(self, "Venda registrada localmente", f"Comprovante provisório: {path}\nA sincronização é automática quando a conexão voltar. Divergências ficarão pendentes de revisão.")
        except requests.HTTPError as exc:
            if exc.response is not None and 400 <= exc.response.status_code < 500:
                self.store.clear_checkout_attempt(self.tenant_id, self.api.username)
                self.checkout_payload = None
                self.company.setEnabled(True)
            QMessageBox.critical(self, "Venda recusada", str(exc))
        except Exception as exc:
            QMessageBox.critical(self, "Confirmação pendente", "Mantenha a tentativa e clique em Finalizar novamente. " + str(exc))

    def _create_web_order(self):
        if self.checkout_payload:
            QMessageBox.warning(self, "Venda pendente", "Finalize a tentativa pendente antes de alterar o carrinho.")
            return
        if not self.cart:
            QMessageBox.information(self, "Carrinho vazio", "Adicione produtos antes de criar o pedido.")
            return
        if any(item.get("desconto", Decimal("0")) > 0 for item in self.cart.values()):
            QMessageBox.warning(
                self, "Desconto",
                "Pedidos enviados pelo WhatsApp ainda não aceitam descontos. Remova os descontos antes de criar o pedido.",
            )
            return
        nome, ok = QInputDialog.getText(self, "Pedido via WhatsApp", "Nome do cliente:")
        if not ok or not nome.strip():
            return
        telefone, ok = QInputDialog.getText(
            self, "Pedido via WhatsApp", "WhatsApp com DDD (ex.: 81999998888):"
        )
        telefone = re.sub(r"\D", "", telefone)
        if not ok or len(telefone) < 10:
            QMessageBox.warning(self, "Telefone inválido", "Informe um número com DDD.")
            return
        if len(telefone) in (10, 11):
            telefone = "55" + telefone
        endereco_entrega, ok = QInputDialog.getText(
            self, "Entrega", "Endereço de entrega (opcional):"
        )
        if not ok:
            return
        entrega_data, ok = QInputDialog.getText(
            self, "Entrega", "Data desejada (AAAA-MM-DD; opcional):"
        )
        if not ok:
            return
        itens = [
            {"produto": product_id, "quantidade": str(item["quantidade"])}
            for product_id, item in self.cart.items()
        ]
        try:
            pedido = self.api.criar_pedido_web(
                nome.strip(), telefone, itens, endereco_entrega.strip(), entrega_data.strip()
            )
            total = Decimal(str(pedido["total"]))
            frete = Decimal(str(pedido.get("frete", "0")))
            resumo = "\n".join(
                f"• {item['quantidade']} x {item['produto']['nome']}"
                for item in self.cart.values()
            )
            mensagem = (
                f"Olá, {nome.strip()}! Seu pedido #{pedido['id']} da {self.company.currentText()}:"
                f"\n{resumo}\nFrete: R$ {frete:.2f}\nTotal: R$ {total:.2f}\n"
                f"Confira e confirme pelo link: {pedido['public_url']}"
            )
            from urllib.parse import quote
            link_whatsapp = f"https://wa.me/{telefone}?text={quote(mensagem)}"
            QDesktopServices.openUrl(QUrl(link_whatsapp))
            self.cart.clear()
            self._render_cart()
            QMessageBox.information(
                self, "Pedido criado",
                f"Pedido #{pedido['id']} criado. O WhatsApp foi aberto com a mensagem pronta. "
                "Revise e toque em Enviar para encaminhar ao cliente."
            )
        except Exception as exc:
            QMessageBox.critical(self, "Não foi possível criar o pedido", str(exc))

    def _build_receipt(self, sale_id):
        def data_uri(path):
            try:
                raw = Path(path).read_bytes()
                encoded = base64.b64encode(raw).decode("ascii")
                return f"data:image/png;base64,{encoded}"
            except OSError:
                return ""

        system_logo = data_uri(asset_path("appdv-icon-256.png"))
        company_logo = self.company_info.get("logo_data_url", "")
        company_name = html.escape(self.company_info.get("nome") or self.company.currentText())
        cnpj = html.escape(self.company_info.get("cnpj") or "")
        rows = []
        total = Decimal("0")
        for product_id, item in self.cart.items():
            product = item["produto"]
            quantity = item["quantidade"]
            price = Decimal(str(product["preco_venda"]))
            discount = Decimal(str(item.get("desconto", "0")))
            line_total = (quantity * price - discount).quantize(Decimal("0.01"))
            total += line_total
            rows.append(
                f"<tr><td><b>{html.escape(str(product['codigo']))}</b> "
                f"{html.escape(str(product['nome']))}<br>"
                f"<small>{quantity} x R$ {price:.2f} · desconto R$ {discount:.2f}</small></td>"
                f"<td class='right'>R$ {line_total:.2f}</td></tr>"
            )
        system_img = f"<img class='system-logo' src='{system_logo}' alt='ApPdv'>" if system_logo else ""
        company_img = f"<img class='company-logo' src='{company_logo}' alt='Logo {company_name}'>" if company_logo else ""
        identity = [self.company_info.get("razao_social"), "CNPJ: " + cnpj if cnpj else "",
                    self.company_info.get("inscricao_estadual"), self.company_info.get("endereco_documento"),
                    self.company_info.get("telefone"), self.company_info.get("email")]
        tax_id = "".join(f"<div>{html.escape(str(value))}</div>" for value in identity if value)
        if self.selected_customer:
            tax_id += "<div>Cliente: " + html.escape(self.selected_customer['nome']) + " · CPF: " + html.escape(self.selected_customer.get('documento', '')) + "</div>"
        payment_labels = {
            "DINHEIRO": "Dinheiro", "PIX": "PIX",
            "CARTAO": "Cartão", "OUTRO": "Outro",
        }
        payment_text = ", ".join(
            f"{p.get('referencia_externa') or payment_labels.get(p['meio'], p['meio'])}: R$ {Decimal(str(p['valor'])):.2f}"
            for p in self.last_payments
        )
        change = sum((Decimal(str(p.get("troco", "0"))) for p in self.last_payments), Decimal("0"))
        pending_notice = "<h2>VENDA LOCAL PENDENTE DE SINCRONIZAÇÃO</h2>" if str(sale_id).startswith('PENDENTE') else ""
        return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Cupom interno #{sale_id}</title>
<style>
@page {{ size: 80mm auto; margin: 4mm; }}
* {{ box-sizing: border-box; }}
body {{ width: 72mm; margin: 0 auto; color: #111; font: 12px Arial, sans-serif; }}
.brand {{ text-align: center; padding: 4px 0 8px; }}
.system-logo {{ display:block; width:145px; height:42px; object-fit:contain; margin:0 auto 4px; }}
.company-logo {{ display:block; max-width:62mm; max-height:22mm; object-fit:contain; margin:4px auto; }}
h1 {{ font-size:15px; margin:5px 0; }}
p {{ margin:3px 0; }}
.rule {{ border:0; border-top:1px dashed #333; margin:8px 0; }}
table {{ width:100%; border-collapse:collapse; }}
td {{ padding:4px 0; vertical-align:top; }}
.right {{ text-align:right; white-space:nowrap; }}
.total {{ font-size:16px; font-weight:bold; }}
.footer {{ text-align:center; font-size:10px; margin-top:10px; }}
button {{ width:100%; padding:10px; margin:12px 0; }}
@media print {{ button {{ display:none; }} body {{ width:auto; }} }}
</style></head><body>
<header class="brand">{system_img}{company_img}<h1>{company_name}</h1>{tax_id}
{pending_notice}<div>CUPOM DE COMPRA — NÃO FISCAL</div>
<div>Venda #{sale_id}</div><div>{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</div></header>
<hr class="rule"><table><tbody>{"".join(rows)}</tbody></table><hr class="rule">
<p class="total">TOTAL: R$ {total:.2f}</p>
<p>Pagamento: {html.escape(payment_text or "—")}</p>
<p>Troco: R$ {change:.2f}</p>
<div class="footer">Documento não fiscal. Válido apenas como comprovante interno.</div>
<button onclick="window.print()">Imprimir comprovante</button>
</body></html>"""

    def _cancel_sale(self):
        sale_id, ok = QInputDialog.getInt(self, "Cancelar venda", "Número da venda:")
        if not ok:
            return
        token, ok = QInputDialog.getText(
            self, "Autorização do gerente", "Token temporário:",
            QLineEdit.EchoMode.Password,
        )
        if not ok or not token:
            return
        reason, ok = QInputDialog.getText(
            self, "Justificativa", "Motivo do cancelamento:"
        )
        if not ok or not reason.strip():
            QMessageBox.warning(self, "Justificativa obrigatória", "Informe o motivo do cancelamento.")
            return
        confirm = QMessageBox.question(
            self, "Confirmar cancelamento",
            f"Cancelar a venda #{sale_id}? O estoque será devolvido e a operação ficará auditada.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            self.api.cancelar_venda(sale_id, token, reason)
            QMessageBox.information(self, "Venda cancelada", f"A venda #{sale_id} foi cancelada.")
            self._load_catalog()
        except Exception as exc:
            QMessageBox.critical(self, "Cancelamento recusado", str(exc))

    def _review_web_orders(self):
        if not self.tenant_id:
            return
        try:
            pedidos = [
                pedido for pedido in self.api.pedidos_web()
                if pedido.get("estado") in ("CONFIRMADO", "SEPARANDO")
            ]
        except Exception as exc:
            QMessageBox.critical(self, "Pedidos web", f"Não foi possível consultar os pedidos: {exc}")
            return
        if not pedidos:
            QMessageBox.information(self, "Pedidos web", "Não há pedidos confirmados aguardando atendimento.")
            return
        opcoes = [
            f"Pedido #{p['id']} · {p['cliente_nome']} · R$ {Decimal(str(p['total'])):.2f} · {p['estado']}"
            for p in pedidos
        ]
        selecionado, ok = QInputDialog.getItem(
            self, "Pedidos confirmados", "Selecione o pedido para concluir no caixa:", opcoes, 0, False
        )
        if not ok:
            return
        pedido = pedidos[opcoes.index(selecionado)]
        resumo = "\n".join(
            f"{item['quantidade']} × {item['nome']} — R$ {Decimal(str(item['total'])):.2f}"
            for item in pedido["itens"]
        )
        total = Decimal(str(pedido["total"]))
        if not self.cash_id:
            QMessageBox.warning(self, "Caixa fechado", "Abra o caixa para finalizar este pedido.")
            return
        payments = self._collect_payments(total)
        if payments is None:
            return
        payment_summary = ", ".join(
            f"{payment['meio']}: R$ {Decimal(str(payment['valor'])):.2f}"
            for payment in payments
        )
        confirm = QMessageBox.question(
            self, f"Finalizar pedido #{pedido['id']}",
            f"Cliente: {pedido['cliente_nome']}\n\n{resumo}\n\n"
            f"Total: R$ {total:.2f}\nPagamentos: {payment_summary}\n\n"
            "Confirmar recebimento e registrar a venda? O estoque será baixado.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self.api.finalizar_pedido_web(
                pedido["id"], self.cash_id, payments,
            )
            self.last_payments = payments
            QMessageBox.information(
                self, "Venda concluída",
                f"Pedido #{pedido['id']} finalizado como venda #{result['venda_id']}."
            )
            self._load_catalog()
        except Exception as exc:
            QMessageBox.critical(self, "Não foi possível finalizar", str(exc))

    def _cash_movement(self):
        if not self.cash_id:
            QMessageBox.warning(self, "Caixa", "Abra o caixa antes de registrar uma movimentação.")
            return
        kind, accepted = QInputDialog.getItem(
            self, "Movimentação do caixa", "Tipo:", ["Sangria", "Suprimento"], 0, False
        )
        if not accepted:
            return
        amount, accepted = QInputDialog.getDouble(
            self, kind, "Valor (R$):", 0.00, 0.01, 10000000.00, 2,
        )
        if not accepted:
            return
        reason, accepted = QInputDialog.getText(
            self, kind, "Motivo obrigatório:"
        )
        if not accepted or not reason.strip():
            return
        try:
            self.api.movimentar_caixa(
                self.cash_id, "SANGRIA" if kind == "Sangria" else "SUPRIMENTO",
                amount, reason.strip(),
            )
            QMessageBox.information(self, "Caixa", f"{kind} registrada e auditada.")
        except Exception as exc:
            QMessageBox.critical(self, "Falha na movimentação", str(exc))

    def _close_cash(self):
        if self.store.pending_errors(self.tenant_id, self.api.username):
            QMessageBox.warning(self, "Sincronização pendente", "Sincronize ou revise as vendas pendentes antes de fechar o caixa.")
            return
        if self.checkout_payload:
            QMessageBox.warning(self, "Venda pendente", "Confirme a venda pendente antes de fechar o caixa.")
            return
        if not self.cash_id:
            return
        try:
            summary = self.api.resumo_caixa(self.cash_id)
            expected = Decimal(str(summary["esperado_dinheiro"]))
        except Exception as exc:
            QMessageBox.critical(self, "Fechamento do caixa", f"Não foi possível consultar o resumo: {exc}")
            return
        amount, accepted = QInputDialog.getDouble(
            self, "Fechamento do caixa",
            f"Dinheiro esperado: R$ {expected:.2f}\nInforme o valor contado (R$):",
            float(expected), 0.00, 10000000.00, 2,
        )
        if not accepted:
            return
        difference = Decimal(str(amount)) - expected
        confirm = QMessageBox.question(
            self, "Confirmar fechamento",
            f"Esperado: R$ {expected:.2f}\nContado: R$ {Decimal(str(amount)):.2f}\n"
            f"Diferença: R$ {difference:.2f}\n\nFechar este caixa?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self.api.fechar_caixa(self.cash_id, amount)
            difference = Decimal(str(result.get("resumo", {}).get("diferenca_fechamento", difference)))
            QMessageBox.information(
                self, "Caixa fechado",
                f"Caixa encerrado. Diferença apurada: R$ {difference:.2f}.",
            )
            self.cash_id = None
            self.status.setText("Caixa fechado")
        except Exception as exc:
            QMessageBox.critical(self, "Falha ao fechar caixa", str(exc))

    def _show_pending(self):
        rows = self.store.pending_errors(self.tenant_id, self.api.username)
        QMessageBox.information(self, 'Sincronização', '\n'.join(f'{key[:8]} · {error or "aguardando envio"}' for key, error in rows) or 'Nenhuma venda pendente deste operador.')
        self._sync_pending()

    def _update_company_logo(self):
        pixmap = QPixmap()
        logo = self.company_info.get('logo_data_url', '')
        if logo and ',' in logo:
            try:
                pixmap.loadFromData(base64.b64decode(logo.split(',', 1)[1]))
            except (ValueError, TypeError):
                pass
        if pixmap.isNull():
            pixmap = QPixmap(asset_path('appdv-logo-web.png'))
        self.company_logo_label.setPixmap(pixmap.scaled(210, 130, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def _cart_lines(self):
        return [{'produto': pk, 'quantidade': str(item['quantidade']), 'desconto': str(item.get('desconto', '0'))} for pk, item in self.cart.items()]

    def _ensure_discount_approval(self, key, lines):
        gross = sum(((Decimal(str(item['produto']['preco_venda'])) * item['quantidade']).quantize(Decimal('.01')) for item in self.cart.values()), Decimal('0'))
        discount = sum((Decimal(str(line.get('desconto', '0'))) for line in lines), Decimal('0'))
        if discount * 100 <= gross * 5:
            return True
        signature = json.dumps([key, lines], sort_keys=True)
        if self.discount_approval and self.discount_approval[0] == signature:
            return True
        user, ok = QInputDialog.getText(self, "Autorizar desconto acima de 5%", "Usuário do gerente ou administrador:")
        if not ok:
            return False
        password, ok = QInputDialog.getText(self, "Autorizar desconto", "Senha:", QLineEdit.EchoMode.Password)
        if not ok:
            return False
        try:
            code = self.api.autorizar_desconto(key, lines, user.strip(), password)
            self.discount_approval = (signature, code)
            return True
        except Exception as exc:
            QMessageBox.warning(self, "Desconto não autorizado", "É necessário validar a autorização online. " + str(exc))
            return False
        finally:
            password = ''

    def _sync_pending(self):
        if not self.tenant_id or not self.cash_id or (self.sync_worker and self.sync_worker.isRunning()):
            return
        rows = [(key, raw) for key, raw in self.store.pending_sales(self.tenant_id) if json.loads(raw).get('operador_local') == self.api.username]
        if not rows:
            return
        tenant = self.tenant_id
        self.sync_worker = PendingSyncWorker(self.api, rows, self)
        def result(key, error, response):
            if error:
                self.store.set_error(key, error)
                if self.tenant_id == tenant:
                    self.status.setText('Sincronização pendente: ' + error[:180])
            else:
                path = Path(self.store.path).parent / 'comprovantes' / f'cupom-PENDENTE-{key}.html'
                if path.exists():
                    text = path.read_text(encoding='utf-8').replace('PENDENTE — ' + key, str(response['id'])).replace('VENDA LOCAL PENDENTE DE SINCRONIZAÇÃO', f"VENDA SINCRONIZADA #{response['id']}")
                    self.store.save_receipt(response['id'], text)
                    path.write_text(text, encoding='utf-8')
                self.store.mark_synced(key)
                if self.tenant_id == tenant:
                    self.online = True
                    self.status.setText(f"Venda sincronizada #{response['id']}")
        self.sync_worker.result.connect(result)
        self.sync_worker.start()

    def closeEvent(self, event):
        if self.sync_worker and self.sync_worker.isRunning():
            QMessageBox.information(self, 'Sincronização', 'Aguarde a tentativa de sincronização terminar antes de sair.')
            event.ignore()
            return
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(asset_path("appdv-icon-256.png")))
    app.setQuitOnLastWindowClosed(False)
    login = LoginDialog()
    if login.exec() != QDialog.DialogCode.Accepted:
        return
    window = PDVWindow(login.api)
    window.show()
    app.setQuitOnLastWindowClosed(True)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
