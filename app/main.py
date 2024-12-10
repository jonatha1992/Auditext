import sys
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox
from ui.form_main import Ui_MainWindow  # Importa la clase de la UI
from controllers import file_operations, audio_processing
from PyQt6.QtGui import QPalette

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ui = Ui_MainWindow()  # Carga la interfaz de usuario
        self.ui.setupUi(self)

        self.lista_archivos_paths = {}  # Diccionario para almacenar rutas de archivos

        # Conecta las acciones de la interfaz gráfica a métodos específicos
        self.ui.actionImport_files.triggered.connect(self.select_files)
        self.ui.pushButton_delete_song.clicked.connect(self.delete_file)
        self.ui.pushButton_export.clicked.connect(self.export_transcription)
        self.ui.pushButton_transcribe.clicked.connect(self.process_transcription)
        self.ui.pushButton_clear_text_area.clicked.connect(self.clear_transcription)

    def select_files(self):
        """Selecciona archivos para transcribir y los agrega a la lista."""
        file_operations.select_files(self.ui.listView_songs, self.lista_archivos_paths)

    def clear_transcription(self):
        """Limpia el área de texto de transcripciones."""
        self.ui.textEdit_Transcipcions.clear()

    def delete_file(self):
        """Elimina un archivo seleccionado de la lista."""
        file_operations.delete_file(self.ui.listView_songs, self.lista_archivos_paths)

    def export_transcription(self):
        """Exporta la transcripción a un archivo de texto."""
        transcription_text = self.ui.textEdit_Transcipcions.toPlainText()
        if not transcription_text:
            QMessageBox.warning(self, "Advertencia", "No hay transcripciones para exportar.")
            return
        file_operations.export_transcription(transcription_text)

    def process_transcription(self):
        """Procesa los archivos seleccionados y muestra la transcripción."""
        if not self.lista_archivos_paths:
            QMessageBox.warning(self, "Advertencia", "No hay archivos seleccionados para transcribir.")
            return

        audio_processing.process_transcription(                                                  
            self.lista_archivos_paths,
            self.ui.textEdit_Transcipcions
        )


def main():
    app = QApplication(sys.argv)

    # Aplica el estilo del sistema operativo
    app.setStyle("Fusion")  # Alternativamente, prueba "WindowsVista", "Macintosh", etc.

    # Detecta el modo claro/oscuro del sistema (en sistemas compatibles)
    if app.palette().color(QPalette.ColorRole.Window).value() < 128:
        app.setStyleSheet("QMainWindow { background-color: #2b2b2b; color: #ffffff; }")
    else:
        app.setStyleSheet("QMainWindow { background-color: #ffffff; color: #000000; }")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())
    
    
if __name__ == "__main__":
    main()