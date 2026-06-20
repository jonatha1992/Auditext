import os
import platform
import subprocess
import threading
import time
import wave
import contextlib

import tkinter as tk
from tkinter import messagebox, filedialog

from mutagen import File

import transcriber
import diarizer
import config
import db
from config import (
    logger,
    report_error,
    idiomas,
    ffmpeg_path,
)


def convertir_a_wav(audio_path):
    """Convert an audio file to WAV with the bundled ffmpeg (local, offline).

    Used only as a playback fallback when pygame cannot decode a format
    directly. Transcription does not need this: faster-whisper decodes files
    on its own.
    """
    try:
        logger.info(f"Intentando convertir: {audio_path}")
        audio_format = audio_path.split(".")[-1]
        output_path = audio_path.replace(audio_format, "wav")

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
    except Exception as e:
        logger.error(f"Error al convertir archivo a WAV: {str(e)}")
        raise


def obtener_duracion_audio(ruta_archivo):
    try:
        audio = File(ruta_archivo)
        if audio is not None:
            return int(audio.info.length)
    except Exception:
        pass

    if ruta_archivo.lower().endswith(".wav"):
        try:
            with contextlib.closing(wave.open(ruta_archivo, "r")) as f:
                frames = f.getnframes()
                rate = f.getframerate()
                duration = frames / float(rate)
                return int(duration)
        except Exception:
            pass

    return 0


def seleccionar_archivos(lista_archivos, lista_archivos_paths):
    file_paths = filedialog.askopenfilenames(
        filetypes=[
            ("Archivos de Audio", "*.mp3 *.wav *.flac *.ogg *.m4a *.mp4 *.aac *.opus")
        ],
        title="Seleccionar archivos de audio",
    )
    logger.info(f"Archivos seleccionados por el diálogo: {file_paths} (tipo: {type(file_paths)})")
    
    if not file_paths:
        return

    # Si por alguna razón Tcl/Tk devuelve un string en vez de una lista/tupla,
    # lo convertimos de forma segura usando shlex.
    if isinstance(file_paths, str):
        import shlex
        try:
            file_paths = shlex.split(file_paths)
        except Exception:
            file_paths = [file_paths]

    archivos_no_agregados = []
    for file_path in file_paths:
        # Limpiar llaves de Tcl {} que a veces envuelven rutas con espacios en Windows
        file_path = file_path.strip("{}").strip()
        if not file_path:
            continue
            
        file_name = os.path.basename(file_path)
        duracion = obtener_duracion_audio(file_path)
        duracion_str = time.strftime("%M:%S", time.gmtime(duracion))
        item = f"{file_name} ({duracion_str})"
        
        logger.info(f"Procesando archivo para lista: path={file_path}, name={file_name}, duration={duracion_str}")
        
        if item not in lista_archivos.get(0, tk.END):
            lista_archivos.insert(tk.END, item)
            lista_archivos_paths[file_path] = item
        else:
            archivos_no_agregados.append(file_name)
            
    if archivos_no_agregados:
        messagebox.showwarning(
            "Archivos Duplicados",
            f"Los siguientes archivos ya estaban en la lista y no se añadieron nuevamente:\n{', '.join(archivos_no_agregados)}",
        )


def limpiar(text_area):
    text_area.delete("1.0", tk.END)


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


def contar_palabras_y_inaudibles(texto):
    palabras = texto.split()
    inaudibles = palabras.count("[inaudible]")
    palabras_sin_inaudibles = len(palabras) - inaudibles
    return palabras_sin_inaudibles, inaudibles


def exportar_transcripcion(transcripcion_resultado):
    if not transcripcion_resultado:
        messagebox.showwarning("Advertencia", "No hay transcripcion para exportar.")
        return
    output_file = filedialog.asksaveasfilename(
        defaultextension=".txt",
        filetypes=[("Archivo de texto", "*.txt")],
        title="Guardar transcripcion como",
    )
    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(transcripcion_resultado)
        messagebox.showinfo("Informacion", f"Transcripcion guardada en {output_file}.")
        logger.info(f"Transcripcion guardada en {output_file}.")

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


def borrar_archivo(lista_archivos, lista_archivos_paths):
    seleccion = lista_archivos.curselection()
    if seleccion:
        indice = seleccion[0]
        archivo = lista_archivos.get(indice)
        lista_archivos.delete(indice)
        lista_archivos_paths.pop(
            next(
                key for key, value in lista_archivos_paths.items() if value == archivo
            ),
            None,
        )
        return True
    return False


def iniciar_transcripcion_thread(
    lista_archivos,
    text_area,
    archivo_procesando,
    lista_archivos_paths,
    transcripcion_resultado,
    progress_bar,
    ventana,
    boton_transcribir,
    combobox_idioma_entrada,
    combobox_idioma_salida,
    diarizar_var=None,
    spinner=None,
    frame_progress=None,
):
    from reproductor import reproductor

    if reproductor.reproduciendo:
        messagebox.showwarning(
            "Advertencia",
            "Hay una reproduccion en curso. Por favor, detenga la reproduccion antes de transcribir.",
        )
        return

    seleccion = lista_archivos.curselection()
    if not seleccion:
        messagebox.showwarning(
            "Advertencia", "Seleccione un archivo de audio para transcribir."
        )
        config.transcripcion_en_curso = False
        return

    if config.transcripcion_activa:
        config.transcripcion_activa = False
        config.transcripcion_en_curso = False
        boton_transcribir.config(text="Transcribir")
        progress_bar["value"] = 0
        progress_bar.pack_forget()
        if spinner:
            spinner.stop()
            spinner.pack_forget()
        if frame_progress:
            frame_progress.pack_forget()
    else:
        config.transcripcion_activa = True
        config.transcripcion_en_curso = True
        boton_transcribir.config(text="Detener Transcripcion")
        progress_bar["value"] = 0
        if frame_progress:
            frame_progress.pack(side=tk.TOP, fill=tk.X, pady=(10, 0))
        progress_bar.pack(pady=5, padx=60, fill=tk.X)
        if spinner:
            spinner.start()
            spinner.pack(side=tk.LEFT, padx=5)
        threading.Thread(
            target=iniciar_transcripcion,
            args=(
                lista_archivos,
                text_area,
                archivo_procesando,
                lista_archivos_paths,
                transcripcion_resultado,
                progress_bar,
                ventana,
                boton_transcribir,
                combobox_idioma_entrada,
                combobox_idioma_salida,
                diarizar_var,
            ),
            kwargs={"spinner": spinner, "frame_progress": frame_progress},
            daemon=True,
        ).start()


def procesar_audio(audio_file, idioma_entrada, translate, progress_bar, ventana, diarizar=False):
    """Transcribe a single file offline with faster-whisper.

    Whisper decodes the file itself (no ffmpeg conversion) and applies its own
    voice activity detection (no manual band-pass filtering or chunking).

    When ``diarizar`` is True, the speaker-labeled diarization path is used
    instead (whisperx + pyannote).
    """
    filename = os.path.basename(audio_file)

    def progress_cb(fraction):
        progress_bar["value"] = fraction * 100
        ventana.update_idletasks()

    def should_continue():
        return config.transcripcion_activa

    if diarizar:
        transcripcion_final = diarizer.diarize_file(
            audio_file, language=idioma_entrada, progress_cb=progress_cb
        )
    else:
        transcripcion_final = transcriber.transcribe_file(
            audio_file,
            language=idioma_entrada,
            translate=translate,
            progress_cb=progress_cb,
            should_continue=should_continue,
        )

    if not transcripcion_final:
        transcripcion_final = "No se pudo transcribir ninguna parte del audio."

    palabras, inaudibles = contar_palabras_y_inaudibles(transcripcion_final)
    return {
        "filename": filename,
        "archivo": audio_file,
        "transcripcion": transcripcion_final,
        "inaudibles": inaudibles,
        "palabras": palabras,
    }


def iniciar_transcripcion(
    lista_archivos,
    text_area,
    archivo_procesando,
    lista_archivos_paths,
    transcripcion_resultado,
    progress_bar,
    ventana,
    boton_transcribir,
    combobox_idioma_entrada,
    combobox_idioma_salida,
    diarizar_var=None,
    spinner=None,
    frame_progress=None,
):

    seleccion = lista_archivos.curselection()
    if not seleccion:
        messagebox.showwarning(
            "Advertencia", "Por favor, seleccione al menos un archivo para transcribir."
        )
        return

    # Speaker diarization is optional and heavy. Fall back to plain transcription
    # when it is requested but unavailable (whisperx missing or no HF_TOKEN).
    diarizar = bool(diarizar_var.get()) if diarizar_var is not None else False
    if diarizar and not diarizer.is_available():
        messagebox.showinfo(
            "Diferenciar hablantes no disponible",
            "Falta whisperx o el token de HuggingFace (HF_TOKEN en .env). "
            "Se transcribira sin diferenciar hablantes.",
        )
        diarizar = False

    archivos_seleccionados = [lista_archivos.get(i) for i in seleccion]
    total_archivos = len(archivos_seleccionados)

    idioma_entrada = idiomas[combobox_idioma_entrada.get()]
    idioma_salida = idiomas[combobox_idioma_salida.get()]

    # Offline translation: Whisper's translate task only targets English.
    # When the requested output is English (and the source is not), translate;
    # otherwise transcribe in the source language.
    translate = idioma_salida == "en" and idioma_entrada != "en"
    if (
        idioma_salida is not None
        and idioma_salida != "en"
        and idioma_salida != idioma_entrada
    ):
        messagebox.showinfo(
            "Traduccion offline",
            "La traduccion offline solo soporta ingles como destino. "
            "El audio se transcribira en su idioma original.",
        )

    config.transcripcion_activa = True
    config.transcripcion_en_curso = True
    boton_transcribir.config(text="Detener Transcripcion")

    # Pre-cargar el modelo si no está en memoria y avisar al usuario
    if transcriber._model is None:
        archivo_procesando.set("Cargando modelo de transcripcion (solo la primera vez)...")
        try:
            transcriber.get_model()
        except Exception as e:
            logger.error(f"Error al cargar el modelo: {str(e)}")

    for index, archivo in enumerate(archivos_seleccionados):
        if not config.transcripcion_activa:
            break

        audio_file = next(
            key for key, value in lista_archivos_paths.items() if value == archivo
        )

        # Consultar si el archivo ya fue transcrito
        transcripcion_guardada = db.get_transcription(audio_file)
        if transcripcion_guardada:
            # Preguntar si desea cargar la transcripción existente o volver a procesar
            respuesta = messagebox.askyesnocancel(
                "Transcripción Guardada",
                f"El archivo '{archivo}' ya tiene una transcripción en el historial.\n\n"
                "¿Querés cargar la transcripción guardada?\n"
                "- Seleccioná SÍ para cargarla al instante.\n"
                "- Seleccioná NO para volver a transcribirla desde cero.\n"
                "- Seleccioná CANCELAR para abortar el proceso.",
                parent=ventana,
            )
            if respuesta is True:
                texto_transcrito = ajustar_texto_sencillo(transcripcion_guardada["transcription"])
                nuevo_texto = (
                    f"Transcripcion de {archivo} (Historial guardado): \n{texto_transcrito} \n\n"
                )
                text_area.insert(tk.END, nuevo_texto)
                text_area.see(tk.END)
                progress_bar["value"] = ((index + 1) / total_archivos) * 100
                ventana.update_idletasks()
                continue
            elif respuesta is None:
                # Cancelar toda la cola
                break

        archivo_procesando.set(f"Procesando: {archivo} ({index + 1}/{total_archivos})")
        logger.info(f"Procesando archivo: {audio_file}")

        try:
            resultado = procesar_audio(
                audio_file, idioma_entrada, translate, progress_bar, ventana, diarizar
            )

            if config.transcripcion_activa:
                texto_transcrito = ajustar_texto_sencillo(resultado["transcripcion"])
                nuevo_texto = (
                    f"Transcripcion de {archivo}: \n{texto_transcrito} \n\n"
                    f"Palabras: {resultado['palabras']}\n\n"
                )
                text_area.insert(tk.END, nuevo_texto)
                text_area.see(tk.END)
                
                # Guardar en base de datos
                db.save_transcription(
                    audio_file,
                    resultado["filename"],
                    archivo.split("(")[-1].replace(")", "").strip(),
                    resultado["transcripcion"],
                    summary="",
                    language=idioma_entrada
                )

            progress_bar["value"] = ((index + 1) / total_archivos) * 100
            ventana.update_idletasks()

        except diarizer.DiarizationError as e:
            messagebox.showerror(
                "Diferenciar hablantes",
                report_error(f"Diarizacion {archivo}", e, str(e)),
            )
        except Exception as e:
            messagebox.showerror(
                "Error",
                report_error(
                    f"Procesar {archivo}", e, f"No se pudo procesar el archivo {archivo}."
                ),
            )

        if not config.transcripcion_activa:
            break

    archivo_procesando.set("")

    if config.transcripcion_activa:
        messagebox.showinfo(
            "Informacion", f"Transcripcion completa para {total_archivos} archivo(s)."
        )

    boton_transcribir.config(text="Transcribir")
    config.transcripcion_activa = False
    progress_bar.pack_forget()
    progress_bar["value"] = 0
    config.transcripcion_en_curso = False
    if spinner:
        spinner.stop()
        spinner.pack_forget()
    if frame_progress:
        frame_progress.pack_forget()
