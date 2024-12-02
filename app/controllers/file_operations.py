import os
import time
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from controllers.audio_processing import obtener_duracion_audio


def select_files(list_view, file_paths):
    file_dialog = QFileDialog()
    selected_files, _ = file_dialog.getOpenFileNames(
        caption="Seleccionar archivos de audio",
        filter="Archivos de Audio (*.mp3 *.wav *.flac *.ogg *.m4a *.mp4 *.aac *.opus)"
    )

    if selected_files:
        model = list_view.model()  # Get the model for the list view
        if model is None:
            from PyQt6.QtCore import QStringListModel
            model = QStringListModel()
            list_view.setModel(model)

        existing_files = model.stringList()
        duplicate_files = []

        for file in selected_files:
            file_name = os.path.basename(file)
            duration = obtener_duracion_audio(file)
            duracion_str = time.strftime("%M:%S", time.gmtime(duration))
            item = f"{file_name} ({duracion_str})"
            print(item)
            if file_name not in file_paths:
                existing_files.append(item)
                file_paths[item] = file
                print(file)
                print(file_paths)
                
            else:
                duplicate_files.append(file_name)

        model.setStringList(existing_files)  # Update the model with new files

        if duplicate_files:
            QMessageBox.warning(
                None,
                "Archivos Duplicados",
                f"Los siguientes archivos ya estaban en la lista y no se añadieron:\n{', '.join(duplicate_files)}",
                QMessageBox.StandardButton.Ok,
            )


def delete_file(list_view, file_paths):
    selected_indexes = list_view.selectedIndexes()
    if selected_indexes:
        model = list_view.model()
        for index in selected_indexes:
            file_name = model.data(index)
            del file_paths[file_name]
            model.removeRow(index.row())


def export_transcripcion(transcription_text):
    from PyQt6.QtWidgets import QFileDialog
    output_file, _ = QFileDialog.getSaveFileName(
        caption="Guardar transcripción como",
        filter="Archivos de Texto (*.txt)"
    )
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(transcription_text)
