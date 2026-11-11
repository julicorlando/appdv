"""Tela de configuração dos periféricos deste computador (gaveta, balança, etiqueta de balança)."""
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout)

from app import perifericos

MODOS_GAVETA = [("", "Sem gaveta"), ("windows", "Ligada na impressora do Windows"),
                ("serial", "Porta serial / USB-serial (COM)"), ("rede", "Impressora de rede (IP:porta)")]


class PerifericosDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Periféricos deste caixa")
        self.setMinimumWidth(520)
        self.config = dict(config)
        layout = QVBoxLayout(self)

        gaveta = QGroupBox("Gaveta de dinheiro")
        form = QFormLayout(gaveta)
        self.gaveta_modo = QComboBox()
        for valor, rotulo in MODOS_GAVETA:
            self.gaveta_modo.addItem(rotulo, valor)
        self.gaveta_modo.setCurrentIndex(max(0, self.gaveta_modo.findData(self.config.get("gaveta_modo", ""))))
        self.gaveta_destino = QComboBox()
        self.gaveta_destino.setEditable(True)
        self.gaveta_destino.addItems(perifericos.impressoras_windows())
        self.gaveta_destino.setCurrentText(self.config.get("gaveta_destino", ""))
        self.gaveta_destino.lineEdit().setPlaceholderText("Nome da impressora, COM3 ou 192.168.0.50:9100")
        self.gaveta_dinheiro = QCheckBox("Abrir sozinha ao finalizar venda com dinheiro")
        self.gaveta_dinheiro.setChecked(bool(self.config.get("gaveta_dinheiro", True)))
        testar_gaveta = QPushButton("Testar gaveta")
        testar_gaveta.clicked.connect(self._testar_gaveta)
        form.addRow("Conexão", self.gaveta_modo)
        form.addRow("Impressora / porta", self.gaveta_destino)
        form.addRow("", self.gaveta_dinheiro)
        form.addRow("", testar_gaveta)
        layout.addWidget(gaveta)

        balanca = QGroupBox("Balança (produtos vendidos por kg)")
        form = QFormLayout(balanca)
        self.balanca_porta = QLineEdit(self.config.get("balanca_porta", ""))
        self.balanca_porta.setPlaceholderText("COM4 (em branco = sem balança)")
        self.balanca_velocidade = QComboBox()
        for velocidade in ("2400", "4800", "9600", "19200"):
            self.balanca_velocidade.addItem(velocidade)
        self.balanca_velocidade.setCurrentText(str(self.config.get("balanca_velocidade", 9600)))
        linha = QHBoxLayout()
        testar_balanca = QPushButton("Ler peso agora")
        testar_balanca.clicked.connect(self._testar_balanca)
        self.peso_lido = QLabel()
        linha.addWidget(testar_balanca)
        linha.addWidget(self.peso_lido, 1)
        form.addRow("Porta", self.balanca_porta)
        form.addRow("Velocidade", self.balanca_velocidade)
        form.addRow("", linha)
        form.addRow(QLabel("Protocolo Toledo/Filizola/Urano. Ao adicionar um produto em KG, o PDV lê o peso sozinho."))
        layout.addWidget(balanca)

        etiqueta = QGroupBox("Etiqueta de balança (código de barras com preço ou peso)")
        form = QFormLayout(etiqueta)
        self.etiqueta_ativa = QCheckBox("Ler etiquetas impressas pela balança")
        self.etiqueta_ativa.setChecked(bool(self.config.get("etiqueta_ativa")))
        self.etiqueta_prefixo = QLineEdit(self.config.get("etiqueta_prefixo", "2"))
        self.etiqueta_prefixo.setMaxLength(2)
        self.etiqueta_digitos = QSpinBox()
        self.etiqueta_digitos.setRange(4, 6)
        self.etiqueta_digitos.setValue(int(self.config.get("etiqueta_digitos_codigo", 4)))
        self.etiqueta_tipo = QComboBox()
        self.etiqueta_tipo.addItem("Preço total (R$)", "preco")
        self.etiqueta_tipo.addItem("Peso (kg)", "peso")
        self.etiqueta_tipo.setCurrentIndex(max(0, self.etiqueta_tipo.findData(self.config.get("etiqueta_tipo", "preco"))))
        self.etiqueta_teste = QLineEdit()
        self.etiqueta_teste.setPlaceholderText("Leia uma etiqueta aqui para testar")
        self.etiqueta_teste.textChanged.connect(self._testar_etiqueta)
        self.etiqueta_resultado = QLabel()
        form.addRow("", self.etiqueta_ativa)
        form.addRow("Prefixo", self.etiqueta_prefixo)
        form.addRow("Dígitos do código do produto", self.etiqueta_digitos)
        form.addRow("A etiqueta traz", self.etiqueta_tipo)
        form.addRow("Teste", self.etiqueta_teste)
        form.addRow("", self.etiqueta_resultado)
        form.addRow(QLabel("O código do produto na etiqueta deve ser o mesmo do campo Código do cadastro."))
        layout.addWidget(etiqueta)

        botoes = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        botoes.accepted.connect(self._salvar)
        botoes.rejected.connect(self.reject)
        layout.addWidget(botoes)

    def _ler(self):
        return {
            "gaveta_modo": self.gaveta_modo.currentData() or "",
            "gaveta_destino": self.gaveta_destino.currentText().strip(),
            "gaveta_dinheiro": self.gaveta_dinheiro.isChecked(),
            "balanca_porta": self.balanca_porta.text().strip(),
            "balanca_velocidade": int(self.balanca_velocidade.currentText()),
            "etiqueta_ativa": self.etiqueta_ativa.isChecked(),
            "etiqueta_prefixo": self.etiqueta_prefixo.text().strip() or "2",
            "etiqueta_digitos_codigo": self.etiqueta_digitos.value(),
            "etiqueta_tipo": self.etiqueta_tipo.currentData(),
        }

    def _testar_gaveta(self):
        try:
            if perifericos.abrir_gaveta(self._ler()):
                QMessageBox.information(self, "Gaveta", "Comando enviado. A gaveta abriu?")
            else:
                QMessageBox.information(self, "Gaveta", "Escolha a conexão da gaveta.")
        except perifericos.ErroPeriferico as exc:
            QMessageBox.warning(self, "Gaveta", str(exc))

    def _testar_balanca(self):
        try:
            self.peso_lido.setText(f"{perifericos.ler_peso(self._ler())} kg")
        except perifericos.ErroPeriferico as exc:
            self.peso_lido.setText(str(exc))

    def _testar_etiqueta(self, texto):
        cfg = {**self._ler(), "etiqueta_ativa": True}
        etiqueta = perifericos.ler_etiqueta(texto.strip(), cfg)
        if not etiqueta:
            self.etiqueta_resultado.setText("Não é uma etiqueta de balança válida." if len(texto.strip()) >= 13 else "")
            return
        valor = f"R$ {etiqueta['valor']:.2f}" if etiqueta["tipo"] == "preco" else f"{etiqueta['valor']} kg"
        self.etiqueta_resultado.setText(f"Produto código {etiqueta['codigo']} · {valor}")

    def _salvar(self):
        self.config = self._ler()
        self.accept()
