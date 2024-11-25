import sys
import os
from PyQt6 import uic
from PyQt6.QtWidgets import QApplication, QMainWindow

class VentanaPrincipal(QMainWindow):
    def __init__(self):
        super().__init__()
        ruta_ui = os.path.join(os.path.dirname(__file__), 'ui/main.ui')
        uic.loadUi(ruta_ui, self)

def main():
    app = QApplication(sys.argv)
    ventana = VentanaPrincipal()
    ventana.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
 
 