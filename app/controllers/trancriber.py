
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
):
    global transcripcion_activa, transcripcion_en_curso
    from Reproductor import reproductor

    if reproductor.reproduciendo:
        messagebox.showwarning(
            "Advertencia",
            "Hay una reproducción en curso. Por favor, detenga la reproducción antes de transcribir.",
        )
        return

    seleccion = lista_archivos.curselection()
    if not seleccion:
        messagebox.showwarning(
            "Advertencia", "Seleccione un archivo de audio para transcribir."
        )
        transcripcion_en_curso = False
        return

    if transcripcion_activa:
        transcripcion_activa = False
        transcripcion_en_curso = False
        boton_transcribir.config(text="Transcribir")
        progress_bar["value"] = 0
        progress_bar.pack_forget()
    else:
        transcripcion_activa = True
        transcripcion_en_curso = True
        boton_transcribir.config(text="Detener Transcripción")
        progress_bar["value"] = 0
        progress_bar.pack(pady=5, padx=60, fill=tk.X)
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
            ),
            daemon=True,
        ).start()


def transcribir_archivo_grande(audio_path, idioma_entrada, progress_bar, ventana, transcripcion_activa):
    transcripcion_completa = []
    progreso_actual = 0

    try:
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"El archivo {audio_path} no existe")

        audio = AudioSegment.from_file(audio_path)
        if len(audio) == 0:
            raise ValueError("El archivo de audio está vacío")

        duracion_total = len(audio)

        # Mejorar el audio
        audio = mejorar_audio(audio)

        # Dividir el audio en fragmentos basados en silencio usando VAD
        chunks = vad_segmentacion(audio)

        if not chunks:
            logger.warning("No se pudieron crear chunks de audio. Procesando el archivo completo.")
            chunks = [audio]

        recognizer = sr.Recognizer()
        recognizer.energy_threshold = 300
        recognizer.dynamic_energy_threshold = True

        chunks_numerados = list(enumerate(chunks))
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for i, chunk in chunks_numerados:
                if not transcripcion_activa:
                    break
                logger.info(f"Transcribiendo chunk {i} con duración {len(chunk) / 1000} segundos")
                futures.append(executor.submit(transcribir_chunk, recognizer, chunk, idioma_entrada, i))

            resultados = []
            for future in as_completed(futures):
                if not transcripcion_activa:
                    break
                i, texto = future.result()
                if texto and not texto.startswith("[error:"):
                    resultados.append((i, texto))

                progreso_actual += len(chunks[i])
                progress_bar["value"] = min(progreso_actual, duracion_total)
                ventana.update_idletasks()

            # Ordenar los resultados por el índice original
            resultados.sort(key=lambda x: x[0])
            transcripcion_completa = [texto for i, texto in resultados]

        # Si no se reconoció nada, intentar con el archivo completo
        if not transcripcion_completa:
            logger.warning("Intentando transcribir el archivo completo...")
            _, texto_completo = transcribir_chunk(recognizer, audio, idioma_entrada, 0)
            if texto_completo and not texto_completo.startswith("[error:"):
                transcripcion_completa.append(texto_completo)

        transcripcion_final = " ".join(transcripcion_completa).strip()
        transcripcion_final = transcripcion_final.capitalize()

        if not transcripcion_final:
            return "No se pudo transcribir ninguna parte del audio."

        palabras_sin_inaudibles, inaudibles = contar_palabras_y_inaudibles(transcripcion_final)
        logger.info(f"Palabras reconocidas: {palabras_sin_inaudibles}, Inaudibles: {inaudibles}")

        return transcripcion_final

    except FileNotFoundError as e:
        logger.error(f"Error de archivo: {e}")
        return f"Error: {str(e)}"
    except ValueError as e:
        logger.error(f"Error de valor: {e}")
        return f"Error: {str(e)}"
    except Exception as e:
        logger.error(f"Error al procesar el archivo: {e}")
        return f"Error al procesar el archivo: {e}"





def contar_palabras_y_inaudibles(texto):
    palabras = texto.split()
    inaudibles = palabras.count("[inaudible]")
    palabras_sin_inaudibles = len(palabras) - inaudibles
    return palabras_sin_inaudibles, inaudibles





def vad_segmentacion(audio, min_silence_len=1000, silence_thresh=-40, keep_silence=300):
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
    # audio = AudioSegment.from_file(audio_file)
    # Convertir a WAV
    wav_file = convertir_a_wav(audio_file)

    # Procesar el archivo WAV
    audio = AudioSegment.from_file(wav_file)

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
    palabras, inaudibles = contar_palabras_y_inaudibles(transcripcion_final)
    return {
        "filename": filename,
        "archivo": audio_file,
        "transcripcion": transcripcion_final,
        "num_chunks": len(chunks),
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
):
    global transcripcion_activa, transcripcion_en_curso

    seleccion = lista_archivos.curselection()
    if not seleccion:
        messagebox.showwarning("Advertencia", "Por favor, seleccione al menos un archivo para transcribir.")
        return

    archivos_seleccionados = [lista_archivos.get(i) for i in seleccion]
    total_archivos = len(archivos_seleccionados)

    idioma_entrada = idiomas[combobox_idioma_entrada.get()]
    idioma_salida = idiomas[combobox_idioma_salida.get()]

    transcripcion_activa = True
    transcripcion_en_curso = True
    boton_transcribir.config(text="Detener Transcripción")

    for index, archivo in enumerate(archivos_seleccionados):
        if not transcripcion_activa:
            break

        audio_file = next(key for key, value in lista_archivos_paths.items() if value == archivo)

        archivo_procesando.set(f"Procesando: {archivo} ({index + 1}/{total_archivos})")
        logger.info(f"Procesando archivo: {audio_file}")

        try:
            resultado = procesar_audio(audio_file, idioma_entrada, text_area, progress_bar, ventana)

            if transcripcion_activa and idioma_entrada != idioma_salida:
                resultado['transcripcion'] = traducir_texto(resultado['transcripcion'], idioma_salida)

            if transcripcion_activa:
                texto_transcrito = ajustar_texto_sencillo(resultado['transcripcion'])
                nuevo_texto = f"Transcripción de {archivo}: \n{texto_transcrito} \n\nPalabras: {resultado['palabras']} \nInaudibles: {resultado['inaudibles']}\n\n"
                text_area.insert(tk.END, nuevo_texto)
                text_area.see(tk.END)

            # Actualizar la barra de progreso general
            progress_bar["value"] = ((index + 1) / total_archivos) * 100
            ventana.update_idletasks()

        except Exception as e:
            logger.error(f"Error al procesar el archivo {archivo}: {e}")
            messagebox.showerror("Error", f"Error al procesar el archivo {archivo}: {e}")

        if not transcripcion_activa:
            break

    archivo_procesando.set("")

    if transcripcion_activa:
        messagebox.showinfo("Información", f"Transcripción completa para {total_archivos} archivo(s).")

    boton_transcribir.config(text="Transcribir")
    transcripcion_activa = False
    progress_bar.pack_forget()
    progress_bar["value"] = 0
    transcripcion_en_curso = False
