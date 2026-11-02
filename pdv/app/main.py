import sys
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget

class PDVWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Armazém PDV")
        self.resize(1100, 720)
        label = QLabel("Armazém PDV — frente de caixa\nConecte ao servidor para iniciar o turno.")
        label.setStyleSheet("font-size: 22px; padding: 32px;")
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.addWidget(label)
        self.setCentralWidget(container)

def main():
    app = QApplication(sys.argv)
    window = PDVWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
