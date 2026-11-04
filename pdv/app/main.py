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
from PySide6.QtCore import QUrl, QTimer, Qt, QSettings
import requests
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QInputDialog, QLineEdit, QLabel, QMainWindow, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.api_client import ArmazemAPI
from app.local_store import LocalStore


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
        self.current_product = None
        self.company_roles = {}
        self.tenant_role = "CAIXA"
        self.setWindowTitle("ApPdv — Frente de Caixa")
        self.setWindowIcon(QIcon(asset_path("appdv-icon-256.png")))
        self.resize(1120, 740)
        self._build()
        self._select_company()

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
        self.discount_cart_item = QPushButton("Desconto")
        self.discount_cart_item.clicked.connect(self._discount_cart_item)
        self.discount_cart_item.setEnabled(False)
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
        for button in (self.pay, self.quote, self.web_order, self.web_orders, self.cash_move, self.close_cash, self.cancel_sale, self.admin_button):
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
        self.tenant_id = str(self.company.currentData())
        self.tenant_role = self.company_roles.get(self.tenant_id, "CAIXA")
        self.discount_cart_item.setEnabled(self.tenant_role in ("ADMIN", "GERENTE"))
        self.admin_button.setVisible(self.tenant_role in ("ADMIN", "GERENTE"))
        self.api.selecionar_empresa(self.tenant_id)
        try:
            self.company_info = self.api.empresa_info()
        except Exception:
            self.company_info = {"nome": self.company.currentText(), "logo_data_url": ""}
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
        product_id = int(product["id"])
        available = Decimal(str(product.get("estoque_atual", "0")))
        quantity = self.cart.get(product_id, {}).get("quantidade", Decimal("0")) + Decimal("1")
        if quantity > available:
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
            "items": [
                {
                    "produto": product_id, "quantidade": str(item["quantidade"]),
                    "desconto": str(item.get("desconto", Decimal("0"))),
                }
                for product_id, item in self.cart.items()
            ]
        }
        self.store.hold_sale(self.tenant_id, label.strip() or "Venda suspensa", payload)
        self.cart.clear()
        self._render_cart()
        QMessageBox.information(self, "Venda suspensa", "O carrinho foi salvo neste computador.")

    def _resume_sale(self):
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
        self.cart = restored
        self.store.remove_held_sale(sale_id, self.tenant_id)
        self._render_cart()

    def _edit_cart_item(self):
        row = self.cart_table.currentRow()
        entries = list(self.cart.items())
        if row < 0 or row >= len(entries):
            QMessageBox.information(self, "Carrinho", "Selecione um item do carrinho.")
            return
        product_id, item = entries[row]
        product = item["produto"]
        available = Decimal(str(product.get("estoque_atual", "0")))
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
        if self.tenant_role not in ("ADMIN", "GERENTE"):
            QMessageBox.warning(self, "Desconto", "Descontos exigem perfil de gerente ou administrador.")
            return
        row = self.cart_table.currentRow()
        entries = list(self.cart.items())
        if row < 0 or row >= len(entries):
            QMessageBox.information(self, "Desconto", "Selecione um item do carrinho.")
            return
        product_id, item = entries[row]
        product = item["produto"]
        gross = (Decimal(str(product["preco_venda"])) * item["quantidade"]).quantize(Decimal("0.01"))
        discount, accepted = QInputDialog.getDouble(
            self, "Desconto no item", "Desconto em reais (R$):",
            float(item.get("desconto", Decimal("0"))), 0, float(gross), 2,
        )
        if not accepted:
            return
        self.cart[product_id]["desconto"] = Decimal(str(discount)).quantize(Decimal("0.01"))
        self._show_product(product)
        self._render_cart()

    def _remove_cart_item(self):
        row = self.cart_table.currentRow()
        entries = list(self.cart)
        if row < 0 or row >= len(entries):
            QMessageBox.information(self, "Carrinho", "Selecione um item do carrinho.")
            return
        self.cart.pop(entries[row], None)
        self._render_cart()

    def _render_cart(self):
        rows = list(self.cart.values())
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

    def _print_quote(self):
        if not self.cart:
            QMessageBox.information(self, "Orçamento", "Adicione produtos antes de gerar o orçamento.")
            return
        customer, accepted = QInputDialog.getText(
            self, "Orçamento", "Nome do cliente (opcional):"
        )
        if not accepted:
            return
        validity, accepted = QInputDialog.getInt(
            self, "Validade do orçamento", "Validade em dias:",
            7, 1, 90, 1,
        )
        if not accepted:
            return
        quote_id = uuid.uuid4().hex[:10].upper()
        path = Path(self.store.path).parent / "orcamentos"
        path.mkdir(parents=True, exist_ok=True)
        file_path = path / f"orcamento-{quote_id}.html"
        rows = []
        subtotal = Decimal("0.00")
        discounts = Decimal("0.00")
        for item in self.cart.values():
            product = item["produto"]
            quantity = item["quantidade"]
            price = Decimal(str(product["preco_venda"]))
            discount = Decimal(str(item.get("desconto", "0")))
            line_total = (quantity * price - discount).quantize(Decimal("0.01"))
            subtotal += (quantity * price).quantize(Decimal("0.01"))
            discounts += discount
            rows.append(
                f"<tr><td>{html.escape(str(product.get('codigo','')))} · "
                f"{html.escape(str(product.get('nome','')))}</td>"
                f"<td>{quantity} {html.escape(str(product.get('unidade','')))}</td>"
                f"<td>R$ {price:.2f}</td><td>R$ {discount:.2f}</td>"
                f"<td>R$ {line_total:.2f}</td></tr>"
            )
        total = subtotal - discounts
        company = html.escape(self.company.currentText())
        customer_html = f"<p><b>Cliente:</b> {html.escape(customer.strip())}</p>" if customer.strip() else ""
        logo_path = Path(asset_path("appdv-logo-web.png"))
        # Embed images so saved budgets remain printable after PyInstaller cleanup.
        system_logo = ("data:image/png;base64," + base64.b64encode(
            logo_path.read_bytes()).decode("ascii")) if logo_path.exists() else ""
        company_logo = html.escape(str(self.company_info.get("logo_data_url") or ""), quote=True)
        system_img = f"<img src='{system_logo}' alt='ApPdv' style='max-width:180px;max-height:60px'>" if system_logo else "<b>ApPdv</b>"
        company_img = f"<img src='{company_logo}' alt='Logo {company}' style='max-width:200px;max-height:90px'>" if company_logo else ""
        logo_html = f"<div style='display:flex;align-items:center;gap:20px;flex-wrap:wrap'>{system_img}{company_img}</div>"
        quote = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>Orçamento {quote_id}</title>
<style>body{{font:14px Arial,sans-serif;margin:28px;color:#17202a}}header{{display:flex;justify-content:space-between;align-items:center;border-bottom:3px solid #203879;padding-bottom:14px}}h1{{color:palette(window-text)}}table{{width:100%;border-collapse:collapse;margin-top:22px}}th,td{{padding:10px;border-bottom:1px solid #d9dee8;text-align:left}}th{{background:#edf1f7}}.summary{{text-align:right;font-size:17px;line-height:1.8;margin-top:18px}}.note{{background:#f4f6f9;padding:12px;border-radius:8px;margin-top:20px}}button{{padding:12px 20px;background:#203879;color:white;border:0;border-radius:6px;font-weight:bold}}@media print{{button{{display:none}}body{{margin:0}}}}</style></head>
<body><header><div>{logo_html}<p>{company}</p></div><div><h1>ORÇAMENTO</h1><b>Nº {quote_id}</b><br>{datetime.now().strftime('%d/%m/%Y %H:%M')}</div></header>
{customer_html}<p>Válido por {validity} dias · Vencimento: {(datetime.now() + __import__('datetime').timedelta(days=validity)).strftime('%d/%m/%Y')}</p>
<table><thead><tr><th>Produto</th><th>Quantidade</th><th>Preço unitário</th><th>Desconto</th><th>Total</th></tr></thead><tbody>{"".join(rows)}</tbody></table>
<div class="summary">Subtotal: R$ {subtotal:.2f}<br>Descontos: R$ {discounts:.2f}<br><strong>TOTAL: R$ {total:.2f}</strong></div>
<div class="note">Este documento é um orçamento e não representa venda, reserva ou baixa de estoque. Preços e disponibilidade deverão ser confirmados no momento da compra.</div>
<p style="text-align:center"><button onclick="window.print()">Imprimir orçamento</button></p></body></html>"""
        file_path.write_text(quote, encoding="utf-8")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(file_path)))
        QMessageBox.information(self, "Orçamento gerado", f"Orçamento {quote_id} salvo em:\n{file_path}")

    def _collect_payments(self, total):
        """Collect one or more payment methods and calculate cash change."""
        methods = ["DINHEIRO", "PIX", "CARTAO", "OUTRO"]
        labels = ["Dinheiro", "PIX", "Cartão", "Outro"]
        remaining = total.quantize(Decimal("0.01"))
        payments = []
        while remaining > 0:
            if len(payments) >= 10:
                QMessageBox.warning(self, "Pagamento", "Limite de 10 formas de pagamento por venda.")
                return None
            method, accepted = QInputDialog.getItem(
                self, "Forma de pagamento",
                f"Falta pagar R$ {remaining:.2f}. Selecione a próxima forma:",
                labels, 0, False,
            )
            if not accepted:
                return None
            payment_method = methods[labels.index(method)]
            if payment_method == "DINHEIRO":
                received_value, accepted = QInputDialog.getDouble(
                    self, "Dinheiro recebido",
                    f"Valor recebido em dinheiro (R$). Falta pagar R$ {remaining:.2f}:",
                    float(remaining), 0.01, 10000000.00, 2,
                )
                if not accepted:
                    return None
                received = Decimal(str(received_value)).quantize(Decimal("0.01"))
                applied = min(received, remaining)
                change = max(received - remaining, Decimal("0")).quantize(Decimal("0.01"))
            else:
                applied_value, accepted = QInputDialog.getDouble(
                    self, "Valor do pagamento",
                    f"Valor pago em {method} (R$). Falta pagar R$ {remaining:.2f}:",
                    float(remaining), 0.01, float(remaining), 2,
                )
                if not accepted:
                    return None
                applied = Decimal(str(applied_value)).quantize(Decimal("0.01"))
                received = applied
                change = Decimal("0.00")
            if applied <= 0:
                QMessageBox.warning(self, "Pagamento", "O valor pago precisa ser maior que zero.")
                continue
            payments.append({
                "meio": payment_method,
                "valor": str(applied),
                "valor_recebido": str(received),
                "troco": str(change),
            })
            remaining = (remaining - applied).quantize(Decimal("0.01"))
            if remaining > 0:
                result = QMessageBox.question(
                    self, "Pagamento parcial",
                    f"Restam R$ {remaining:.2f}. Adicionar outra forma de pagamento?",
                )
                if result != QMessageBox.StandardButton.Yes:
                    return None
        return payments

    def _checkout(self):
        if not self.cash_id:
            QMessageBox.warning(self, "Caixa", "Abra o caixa com conexão antes de vender.")
            return
        if not self.cart:
            return
        total = sum(
            (Decimal(str(item["produto"]["preco_venda"])) * item["quantidade"]
             - Decimal(str(item.get("desconto", "0")))
             for item in self.cart.values()),
            Decimal("0"),
        ).quantize(Decimal("0.01"))
        credit = Decimal("0.00")
        customer_id = None
        credit_value, ok = QInputDialog.getDouble(
            self, "Venda a prazo", "Valor a lançar em conta do cliente (R$):",
            0.00, 0.00, float(total), 2,
        )
        if not ok:
            return
        credit = Decimal(str(credit_value)).quantize(Decimal("0.01"))
        if credit > 0:
            try:
                customers = self.api.clientes()
            except Exception as exc:
                QMessageBox.critical(self, "Venda a prazo", f"É necessário estar conectado para consultar clientes: {exc}")
                return
            if not customers:
                QMessageBox.warning(self, "Venda a prazo", "Cadastre o cliente no painel web antes de lançar crédito.")
                return
            choices = [f"{item['nome']} · limite R$ {Decimal(str(item['limite_credito'])):.2f}" for item in customers]
            selected, accepted = QInputDialog.getItem(self, "Venda a prazo", "Cliente:", choices, 0, False)
            if not accepted:
                return
            customer_id = customers[choices.index(selected)]["id"]
        payments = self._collect_payments(total - credit)
        if payments is None:
            return
        self.last_payments = payments
        key = str(uuid.uuid4())
        payload = {
            "idempotency_key": key,
            "caixa": self.cash_id,
            "observacao": "",
            "cliente": customer_id,
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
        try:
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
            self.cart.clear()
            self._render_cart()
            self._load_catalog()
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            self.store.queue_sale(self.tenant_id, payload, str(exc))
            QMessageBox.warning(
                self, "Venda pendente",
                "Sem conexão. A venda foi guardada neste computador para sincronização. "
                "A sincronização exige caixa, estoque e valores válidos.\n\n" + str(exc),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Venda recusada", str(exc))

    def _create_web_order(self):
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
        tax_id = f"<div>{cnpj}</div>" if cnpj else ""
        payment_labels = {
            "DINHEIRO": "Dinheiro", "PIX": "PIX",
            "CARTAO": "Cartão", "OUTRO": "Outro",
        }
        payment_text = ", ".join(
            f"{payment_labels.get(p['meio'], p['meio'])}: R$ {Decimal(str(p['valor'])):.2f}"
            for p in self.last_payments
        )
        change = sum((Decimal(str(p.get("troco", "0"))) for p in self.last_payments), Decimal("0"))
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
<div>CUPOM DE COMPRA — NÃO FISCAL</div>
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
