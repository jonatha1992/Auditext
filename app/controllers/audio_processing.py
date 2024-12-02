import contextlib
import wave
from PyQt6.QtWidgets import QMessageBox
from click import File
from mutagen import File
from pydub import AudioSegment

def processing_transcripcion(file_paths, text_area):
    if not file_paths:
        QMessageBox.warning(None, "Advertencia", "No hay archivos para procesar.")
        return

    # Simulate transcription logic
    try:
        transcriptions = []
        for file_name, file_path in file_paths.items():
            # Example transcription logic
            transcriptions.append(f"{file_name}: Transcripción de ejemplo\n")
        
        # Update the text area with the transcription results
        text_area.setPlainText("\n".join(transcriptions))
    except Exception as e:
        QMessageBox.critical(None, "Error", f"Error al procesar los archivos: {str(e)}")




def obtener_duracion_audio(ruta_archivo):
    try:
        # Intentar con Mutagen
        audio = File(ruta_archivo)
        if audio is not None and hasattr(audio.info, 'length'):
            return int(audio.info.length)

        # Intentar con Wave para archivos WAV
        if ruta_archivo.lower().endswith(".wav"):
            with contextlib.closing(wave.open(ruta_archivo, "r")) as f:
                frames = f.getnframes()
                rate = f.getframerate()
                duration = frames / float(rate)
                print(f"Duración del audio (WAV): {duration} segundos")
                return int(duration)

        # Respaldo con pydub
        audio = AudioSegment.from_file(ruta_archivo)
        duration = len(audio) / 1000  # Duración en segundos
        print(f"Duración del audio (Pydub): {duration} segundos")
        return int(duration)

    except Exception as e:
        print(f"Error al obtener duración de {ruta_archivo}: {e}")
        return 0
