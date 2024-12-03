import contextlib
import wave
from PyQt6.QtWidgets import QMessageBox
from click import File
from mutagen import File
import numpy as np
from pydub import AudioSegment
from scipy.signal import butter, lfilter
from config import logger

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





def butter_bandpass(lowcut, highcut, fs, order=5):
    nyq = 0.5 * fs
    low = lowcut / nyq
    high = highcut / nyq
    b, a = butter(order, [low, high], btype='band')
    return b, a


def butter_bandpass_filter(data, lowcut, highcut, fs, order=5):
    b, a = butter_bandpass(lowcut, highcut, fs, order=order)
    y = lfilter(b, a, data)
    return y


def mejorar_audio(audio, lowcut=300, highcut=3000):
    try:
        # Convertir a mono
        audio = audio.set_channels(1)

        # Aplicar filtro paso banda
        samples = np.array(audio.get_array_of_samples())
        filtered = butter_bandpass_filter(samples, lowcut, highcut, audio.frame_rate)

        # Normalizar
        filtered = np.int16(filtered / np.max(np.abs(filtered)) * 32767)

        # Crear nuevo AudioSegment
        mejorado = AudioSegment(
            filtered.tobytes(),
            frame_rate=audio.frame_rate,
            sample_width=2,
            channels=1
        )

        return mejorado
    except Exception as e:
        logger.error(f"Error en mejorar_audio: {e}")
        return audio
