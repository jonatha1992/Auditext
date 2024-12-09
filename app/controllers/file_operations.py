import os
import platform
import subprocess
import time
from tkinter import filedialog, messagebox
from venv import logger
from PyQt6.QtWidgets import QFileDialog, QMessageBox
from controllers.audio_processing import obtener_duracion_audio
from config import ffmpeg_path


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


def convertir_a_wav(audio_path):
    try:
        logger.info(f"Intentando convertir: {audio_path}")
        audio_format = audio_path.split(".")[-1]
        logger.info(f"Formato de audio detectado: {audio_format}")
        output_path = audio_path.replace(audio_format, "wav")

        # Verificar si el archivo WAV ya existe
        if os.path.exists(output_path):
            logger.info(f"El archivo WAV ya existe: {output_path}")
            return output_path

        command = [ffmpeg_path, "-i", audio_path, output_path]

        startupinfo = subprocess.STARTUPINFO()
        if platform.system() == "Windows":
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        subprocess.run(
            command,
            startupinfo=startupinfo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        logger.info(f"Archivo convertido a WAV: {output_path}")
        return output_path
    except subprocess.CalledProcessError as e:
        logger.error(f"ffmpeg proceso devolvió un error: {e.stderr.decode('utf-8')}")
        raise
    except FileNotFoundError as e:
        logger.error(f"ffmpeg no encontrado: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Error al convertir archivo a WAV: {str(e)}")
        raise



def ajustar_texto_sencillo(texto, max_ancho=90):
    palabras = texto.split()
    lineas = []
    linea_actual = []
    longitud_actual = 0

    for palabra in palabras:
        if len(palabra) > max_ancho:
            if linea_actual:
                lineas.append(" ".join(linea_actual))
            for i in range(0, len(palabra), max_ancho):
                lineas.append(palabra[i: i + max_ancho])
            linea_actual = []
            longitud_actual = 0
        elif longitud_actual + len(palabra) + (1 if linea_actual else 0) <= max_ancho:
            linea_actual.append(palabra)
            longitud_actual += len(palabra) + (1 if linea_actual else 0)
        else:
            lineas.append(" ".join(linea_actual))
            linea_actual = [palabra]
            longitud_actual = len(palabra)

    if linea_actual:
        lineas.append(" ".join(linea_actual))

    if len(lineas) > 1 and len(lineas[-1]) < max_ancho // 2:
        penultima = lineas[-2].split()
        ultima = lineas[-1].split()
        while penultima and len(" ".join(penultima + [ultima[0]])) <= max_ancho:
            ultima.insert(0, penultima.pop())
        lineas[-2] = " ".join(penultima)
        lineas[-1] = " ".join(ultima)

    return "\n".join(lineas)



def exportar_transcripcion(transcripcion_resultado):
    if not transcripcion_resultado:
        messagebox.showwarning("Advertencia", "No hay transcripción para exportar.")
        return
    output_file = filedialog.asksaveasfilename(
        defaultextension=".txt",
        filetypes=[("Archivo de texto", "*.txt")],
        title="Guardar transcripción como",
    )
    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(transcripcion_resultado)
        messagebox.showinfo("Información", f"Transcripción guardada en {output_file}.")
        logger.info(f"Transcripción guardada en {output_file}.")

        try:
            if platform.system() == "Darwin":
                subprocess.call(("open", output_file))
            elif platform.system() == "Windows":
                os.startfile(output_file)
            else:
                subprocess.call(("xdg-open", output_file))

            logger.info(f"Archivo abierto: {output_file}")
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo abrir el archivo: {str(e)}")
            logger.error(f"Error al abrir el archivo: {str(e)}")
