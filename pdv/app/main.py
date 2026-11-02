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

from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtCore import QUrl
import requests
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
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
        layout = QFormLayout(self)
        self.url = QLineEdit(os.getenv("APPDV_API_URL", "http://127.0.0.1:8000"))
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addRow("Endereço do servidor", self.url)
        layout.addRow("Usuário", self.username)
        layout.addRow("Senha", self.password)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
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
        self.company_info = {}
        self.setWindowTitle("ApPdv — Frente de Caixa")
        self.setWindowIcon(QIcon(asset_path("appdv-icon-256.png")))
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
        self.web_orders = QPushButton("Pedidos confirmados")
        self.web_orders.clicked.connect(self._review_web_orders)
        self.close_cash = QPushButton("Fechar caixa")
        self.close_cash.clicked.connect(self._close_cash)
        self.cancel_sale = QPushButton("Cancelar venda…")
        self.cancel_sale.clicked.connect(self._cancel_sale)
        header.addWidget(QLabel("Empresa"))
        header.addWidget(self.company, 1)
        header.addWidget(self.status)
        header.addWidget(self.refresh)
        header.addWidget(self.web_orders)
        header.addWidget(self.close_cash)
        header.addWidget(self.cancel_sale)
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

        self.catalog = QTableWidget(0, 5)
        self.catalog.setHorizontalHeaderLabels(
            ["Código", "Produto", "Setor", "Endereço", "Preço"]
        )
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
        self.web_order = QPushButton("Pedido via WhatsApp")
        self.web_order.clicked.connect(self._create_web_order)
        footer.addWidget(self.total, 1)
        footer.addWidget(QLabel("Pagamento"))
        footer.addWidget(self.payment)
        footer.addWidget(self.pay)
        footer.addWidget(self.web_order)
        layout.addLayout(footer)
        self.setCentralWidget(root)

    def _select_company(self):
        try:
            companies = self.api.minhas_empresas()
            for company in companies:
                self.company.addItem(company["nome"], company["id"])
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
            ]
            for col, value in enumerate(values):
                self.catalog.setItem(row, col, QTableWidgetItem(str(value)))
        self.catalog.resizeColumnsToContents()

    def _add_by_code(self):
        query = self.query.text().strip().lower()
        if not query:
            return
        product = next(
            (p for p in self.products if query in (
                str(p.get("codigo", "")).lower(),
                str(p.get("codigo_barras", "")).lower(),
            )),
            None,
        )
        if product:
            self._add_product(product)
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
        self.cart[product_id] = {"produto": product, "quantidade": quantity}
        self._render_cart()

    def _render_cart(self):
        rows = list(self.cart.values())
        self.cart_table.setRowCount(len(rows))
        total = Decimal("0")
        for row, item in enumerate(rows):
            product, quantity = item["produto"], item["quantidade"]
            price = Decimal(str(product["preco_venda"]))
            line_total = quantity * price
            total += line_total
            values = [
                product["nome"], str(quantity), f"R$ {price:.2f}",
                f"R$ {line_total:.2f}",
            ]
            for col, value in enumerate(values):
                self.cart_table.setItem(row, col, QTableWidgetItem(str(value)))
        self.total.setText(f"Total: R$ {total:.2f}")

    def _checkout(self):
        if not self.cash_id:
            QMessageBox.warning(self, "Caixa", "Abra o caixa com conexão antes de vender.")
            return
        if not self.cart:
            return
        total = sum(
            (Decimal(str(item["produto"]["preco_venda"])) * item["quantidade"]
             for item in self.cart.values()),
            Decimal("0"),
        ).quantize(Decimal("0.01"))
        key = str(uuid.uuid4())
        payload = {
            "idempotency_key": key,
            "caixa": self.cash_id,
            "observacao": "",
            "itens": [
                {"produto": product_id, "quantidade": str(item["quantidade"]), "desconto": "0"}
                for product_id, item in self.cart.items()
            ],
            "pagamentos": [
                {"meio": self.payment.currentText(), "valor": str(total)}
            ],
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
        itens = [
            {"produto": product_id, "quantidade": str(item["quantidade"])}
            for product_id, item in self.cart.items()
        ]
        try:
            pedido = self.api.criar_pedido_web(nome.strip(), telefone, itens)
            total = Decimal(str(pedido["total"]))
            resumo = "\n".join(
                f"• {item['quantidade']} x {item['produto']['nome']}"
                for item in self.cart.values()
            )
            mensagem = (
                f"Olá, {nome.strip()}! Seu pedido #{pedido['id']} da {self.company.currentText()}:"
                f"\n{resumo}\nTotal: R$ {total:.2f}\n"
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
            line_total = (quantity * price).quantize(Decimal("0.01"))
            total += line_total
            rows.append(
                f"<tr><td><b>{html.escape(str(product['codigo']))}</b> "
                f"{html.escape(str(product['nome']))}<br>"
                f"<small>{quantity} x R$ {price:.2f}</small></td>"
                f"<td class='right'>R$ {line_total:.2f}</td></tr>"
            )
        system_img = f"<img class='system-logo' src='{system_logo}' alt='ApPdv'>" if system_logo else ""
        company_img = f"<img class='company-logo' src='{company_logo}' alt='Logo {company_name}'>" if company_logo else ""
        tax_id = f"<div>{cnpj}</div>" if cnpj else ""
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
<p>Pagamento: {html.escape(self.payment.currentText())}</p>
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
        confirm = QMessageBox.question(
            self, f"Finalizar pedido #{pedido['id']}",
            f"Cliente: {pedido['cliente_nome']}\n\n{resumo}\n\n"
            f"Total: R$ {total:.2f}\nPagamento: {self.payment.currentText()}\n\n"
            "Confirmar recebimento e registrar a venda? O estoque será baixado.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        if not self.cash_id:
            QMessageBox.warning(self, "Caixa fechado", "Abra o caixa para finalizar este pedido.")
            return
        try:
            result = self.api.finalizar_pedido_web(
                pedido["id"], self.cash_id,
                [{"meio": self.payment.currentText(), "valor": str(total)}],
            )
            QMessageBox.information(
                self, "Venda concluída",
                f"Pedido #{pedido['id']} finalizado como venda #{result['venda_id']}."
            )
            self._load_catalog()
        except Exception as exc:
            QMessageBox.critical(self, "Não foi possível finalizar", str(exc))

    def _close_cash(self):
        if not self.cash_id:
            return
        amount, accepted = QInputDialog.getDouble(
            self, "Fechamento do caixa", "Valor contado em dinheiro (R$):",
            0.00, 0.00, 10000000.00, 2,
        )
        if not accepted:
            return
        try:
            self.api.fechar_caixa(self.cash_id, amount)
            QMessageBox.information(self, "Caixa fechado", "O caixa foi encerrado no servidor.")
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
    login = LoginDialog()
    if login.exec() != QDialog.DialogCode.Accepted:
        return
    window = PDVWindow(login.api)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
