import tkinter as tk
from tkinter import messagebox, filedialog, ttk
import csv
import os
import speech_recognition as sr
from pydub import AudioSegment
from pydub.silence import split_on_silence, detect_nonsilent
import numpy as np
from scipy.signal import butter, lfilter
import io
from Config import logger, ffmpeg_path, ffprobe_path

# Inicializar el reconocedor
recognizer = sr.Recognizer()

# Configuración de AudioSegment
AudioSegment.converter = ffmpeg_path
AudioSegment.ffmpeg = ffmpeg_path
AudioSegment.ffprobe = ffprobe_path


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


def vad_segmentacion(audio, min_silence_len=1000, silence_thresh=-40, keep_silence=300):
    """
    Segmenta el audio usando un algoritmo de Detección de Actividad de Voz (VAD) mejorado.

    :param audio: AudioSegment a segmentar
    :param min_silence_len: Duración mínima del silencio para considerarlo una pausa (en ms)
    :param silence_thresh: Umbral de dB para considerar un segmento como silencio
    :param keep_silence: Cantidad de silencio a mantener al inicio y final de los segmentos no silenciosos (en ms)
    :return: Lista de segmentos de audio (AudioSegment)
    """
    not_silence_ranges = detect_nonsilent(audio, min_silence_len=min_silence_len, silence_thresh=silence_thresh)

    chunks = []
    for start_i, end_i in not_silence_ranges:
        start_i = max(0, start_i - keep_silence)
        end_i = min(len(audio), end_i + keep_silence)
        chunks.append(audio[start_i:end_i])

    return chunks


def transcribir_chunk(recognizer, audio_chunk, idioma_entrada, indice):
    try:
        if len(audio_chunk) == 0:
            return indice, "[chunk vacío]"

        buffer = io.BytesIO()
        audio_chunk.export(buffer, format="wav")
        buffer.seek(0)

        with sr.AudioFile(buffer) as source:
            audio_data = recognizer.record(source)

        if not audio_data or len(audio_data.frame_data) == 0:
            return indice, "[datos de audio vacíos]"

        texto = recognizer.recognize_google(audio_data, language=idioma_entrada)
        duracion_chunk = len(audio_chunk) / 1000  # Duración en segundos
        logger.info(f"Chunk {indice} transcrito: {texto}... (Duración: {duracion_chunk} segundos)")
        return indice, texto
    except sr.UnknownValueError:
        logger.warning(
            f"Audio no reconocido en chunk {indice} - Duración: {len(audio_chunk) / 1000} segundos"
        )
        return indice, "[inaudible]"
    except sr.RequestError as e:
        logger.error(f"Error en el servicio de reconocimiento para chunk {indice}: {e}")
        return indice, "[error de reconocimiento]"
    except Exception as e:
        logger.error(f"Error inesperado al procesar chunk {indice}: {e}")
        return indice, f"[error: {str(e)}]"


def procesar_audio(audio_file, idioma_entrada, text_area, progress_bar, ventana):
    audio = AudioSegment.from_file(audio_file)
    filename = os.path.basename(audio_file)

    # Mejorar audio
    audio_mejorado = mejorar_audio(audio)

    # Segmentación usando VAD mejorado
    chunks = vad_segmentacion(audio_mejorado)

    # Transcribir los chunks
    transcripcion_completa = []
    for i, chunk in enumerate(chunks):
        if len(chunk) < 1000:  # Ignorar chunks menores a 1 segundo
            continue
        _, texto = transcribir_chunk(recognizer, chunk, idioma_entrada, i)
        transcripcion_completa.append(texto)

        # Actualizar progreso
        progress_bar["value"] = (i + 1) / len(chunks) * 100
        ventana.update_idletasks()

    transcripcion_final = " ".join(transcripcion_completa).strip()

    # Mostrar resultados
    text_area.insert(tk.END, f"Archivo: {filename}\n")
    text_area.insert(tk.END, f"Transcripción:\n{transcripcion_final}\n\n")
    text_area.see(tk.END)

    return {
        "filename": filename,
        "archivo": audio_file,
        "transcripcion": transcripcion_final,
        "num_chunks": len(chunks)
    }


def guardar_resultados_csv(resultados, filename):
    keys = resultados[0].keys()
    with open(filename, 'w', newline='', encoding='utf-8') as output_file:
        dict_writer = csv.DictWriter(output_file, fieldnames=keys)
        dict_writer.writeheader()
        dict_writer.writerows(resultados)


def on_click_procesar():
    audio_files = filedialog.askopenfilenames(filetypes=[("Audio Files", "*.wav *.mp3 *.ogg *.flac")])
    if not audio_files:
        return

    idioma_entrada = idiomas[combobox_idioma.get()]

    progress_bar["maximum"] = 100
    text_area.delete("1.0", tk.END)

    resultados = []
    for audio_file in audio_files:
        text_area.insert(tk.END, f"\nProcesando archivo: {os.path.basename(audio_file)}\n")
        text_area.see(tk.END)
        resultado = procesar_audio(audio_file, idioma_entrada, text_area, progress_bar, ventana)
        resultados.append(resultado)

    # Guardar resultados
    output_file = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV Files", "*.csv")])
    if output_file:
        guardar_resultados_csv(resultados, output_file)

    messagebox.showinfo("Procesamiento Completo", f"La transcripción ha finalizado para {len(audio_files)} archivos.")


# Crear ventana principal
ventana = tk.Tk()
ventana.title("Sistema Avanzado de Transcripción de Audio")

# Crear combobox para selección de idioma
idiomas = {
    "Español": "es-ES",
    "Inglés": "en-US",
    "Francés": "fr-FR",
    "Alemán": "de-DE",
    "Italiano": "it-IT"
}
label_idioma = tk.Label(ventana, text="Seleccione el idioma:")
label_idioma.pack()
combobox_idioma = ttk.Combobox(ventana, values=list(idiomas.keys()))
combobox_idioma.set("Español")
combobox_idioma.pack()

# Crear área de texto para mostrar resultados
text_area = tk.Text(ventana, wrap="word", height=20, width=80)
text_area.pack(pady=10)

# Crear barra de progreso
progress_bar = ttk.Progressbar(ventana, orient="horizontal", mode="determinate")
progress_bar.pack(fill=tk.X, padx=10, pady=5)

# Crear botón para iniciar el procesamiento
boton_procesar = tk.Button(ventana, text="Procesar Audio", command=on_click_procesar)
boton_procesar.pack(pady=10)

# Ejecutar el bucle principal de la interfaz
ventana.mainloop()
