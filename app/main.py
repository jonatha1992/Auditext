import os
import sys
from tkinter.filedialog import FileDialog
from PyQt6 import uic
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox
from app.controllers.file_operations import formatear_duracion, obtener_duracion_audio
from app.ui.form_main import Ui_MainWindow  # Importa la interfaz generada

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # Cargar el archivo .ui
        self.ui = Ui_MainWindow()  # Instancia la interfaz
        self.ui.setupUi(self)   

        # Conectar botones a sus métodos
        self.ui.pushButton_clear_text_area.clicked.connect(self.clear_text_area)

    # Método para el botón "Delete"
    def seleccionar_archivos(self):
        file_paths, _ = FileDialog.getOpenFileNames(
            self,
            "Seleccionar archivos de audio",
            "",
            "Archivos de Audio (*.mp3 *.wav *.flac *.ogg *.m4a *.mp4 *.aac *.opus)"
        )
        archivos_no_agregados = []
        if file_paths:
            for file_path in file_paths:
                file_name = os.path.basename(file_path)
                duracion = obtener_duracion_audio(file_path)
                duracion_str = formatear_duracion(duracion)
                item = f"{file_name} ({duracion_str})"
                if item not in [self.ui.listView_songs.model().data(self.ui.listView_songs.model().index(i)) for i in range(self.ui.listView_songs.model().rowCount())]:
                    self.ui.listView_songs.model().insertRow(self.ui.listView_songs.model().rowCount())
                    self.ui.listView_songs.model().setData(self.ui.listView_songs.model().index(self.ui.listView_songs.model().rowCount() - 1), item)
                    self.lista_archivos_paths[file_path] = item
                else:
                    archivos_no_agregados.append(file_name)
            if archivos_no_agregados:
                QMessageBox.warning(
                    self,
                    "Archivos Duplicados",
                    f"Los siguientes archivos ya estaban en la lista y no se añadieron nuevamente:\n{', '.join(archivos_no_agregados)}",
                    QMessageBox.StandardButton.Ok
                )

    def eliminar_archivo(self):
        # Implementa la lógica para eliminar un archivo de la lista
        pass

    def limpiar_transcripcion(self):
        # Implementa la lógica para limpiar el área de transcripción
        pass

    def exportar_transcripcion(self):
        # Implementa la lógica para exportar la transcripción
        pass

    def procesar_transcripcion(self):
        # Implementa la lógica para transcribir y traducir el audio
        pass
    
    
    
def main():
    app = QApplication(sys.argv)
    ventana = MainWindow()    
    ventana.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
