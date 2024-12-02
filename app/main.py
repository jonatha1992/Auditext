import sys
from PyQt6.QtWidgets import QApplication, QMainWindow
from ui.form_main import Ui_MainWindow  # Import the UI class
from controllers import file_operations, audio_processing


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ui = Ui_MainWindow()  # Load the UI
        self.ui.setupUi(self)

        self.lista_archivos_paths = {}  # Dictionary to store file paths
      
        # Connect UI actions to methods
        self.ui.actionImport_files.triggered.connect(self.select_files)
        self.ui.pushButton_delete_song.clicked.connect(self.delete_file)
        self.ui.pushButton_export.clicked.connect(self.export_transcripcion)
        self.ui.pushButton_trancript.clicked.connect(self.processing_transcripcion)
        self.ui.pushButton_clear_text_area.clicked.connect(self.clear_transcripcion)

    def select_files(self):
        file_operations.select_files(self.ui.listView_songs, self.lista_archivos_paths)

    def clear_transcripcion(self):
        self.ui.textEdit_Transcipcions.clear()

    def delete_file(self):
        file_operations.delete_file(self.ui.listView_songs, self.lista_archivos_paths)

    def export_transcripcion(self):
        file_operations.export_transcripcion(self.ui.textEdit_Transcipcions.toPlainText())

    def processing_transcripcion(self):
        audio_processing.c(
            self.lista_archivos_paths,
            self.ui.textEdit_Transcipcions
        )


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
