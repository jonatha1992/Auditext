import sys
from PyQt6 import uic
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # Cargar el archivo .ui
        uic.loadUi("app/ui/main.ui", self)  # Asegúrate de que la ruta sea correcta

        # Conectar botones a sus métodos
        self.pushButton_delete_song.clicked.connect(self.delete_song)
        self.pushButton_export.clicked.connect(self.export_data)
        self.pushButton_clear_text_area.clicked.connect(self.clear_text_area)

    # Método para el botón "Delete"
    def delete_song(self):
        QMessageBox.information(self, "Delete", "Función de eliminar canción aún no implementada")

    # Método para el botón "Export"
    def export_data(self):
        QMessageBox.information(self, "Export", "Función de exportar aún no implementada")

    # Método para el botón "Clear"
    def clear_text_area(self):
        self.textEdit_Transcipcions.clear()

def main():
    app = QApplication(sys.argv)
    ventana = MainWindow()
    ventana.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
