import pygame
import time
import os
import config
from tkinter import messagebox
from presentation.controllers.funcionalidad import obtener_duracion_audio, convertir_a_wav
import gc


class ReproductorAudio:
    def __init__(self):
        self.reproduciendo = False
        self.audio_actual = None
        # Ruta originalmente solicitada por el llamador (ej. historial_frame),
        # distinta de audio_actual cuando hay fallback a conversión WAV.
        # Se usa para comparar "es este el archivo que tengo seleccionado".
        self.ruta_solicitada = None
        self.tiempo_inicio = 0
        self.tiempo_pausa = 0
        self.duracion_total = 0
        self.posicion_actual = 0

    def iniciar(self, ruta_archivo):
        self.audio_actual = ruta_archivo
        self.ruta_solicitada = ruta_archivo
        self.duracion_total = obtener_duracion_audio(ruta_archivo)
        if pygame.mixer.get_init():
            try:
                pygame.mixer.music.load(ruta_archivo)
                pygame.mixer.music.play()
            except pygame.error:
                # Fallback to WAV conversion if pygame can't decode it directly
                try:
                    wav_path = convertir_a_wav(ruta_archivo)
                    self.audio_actual = wav_path
                    self.duracion_total = obtener_duracion_audio(wav_path)
                    pygame.mixer.music.load(wav_path)
                    pygame.mixer.music.play()
                except Exception as e:
                    config.logger.error(f"Error al reproducir audio tras fallback a WAV: {e}")
        self.reproduciendo = True
        self.tiempo_inicio = time.time()
        self.tiempo_pausa = 0
        self.posicion_actual = 0

    def pausar(self):
        if self.reproduciendo:
            if pygame.mixer.get_init():
                try:
                    pygame.mixer.music.pause()
                except pygame.error:
                    pass
            self.reproduciendo = False
            self.posicion_actual = self.obtener_tiempo_actual()

    def reanudar(self):
        if not self.reproduciendo:
            if pygame.mixer.get_init():
                try:
                    pygame.mixer.music.unpause()
                except pygame.error:
                    pass
            self.reproduciendo = True
            self.tiempo_inicio = time.time() - self.posicion_actual

    def detener(self):
        if pygame.mixer.get_init():
            try:
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
            except pygame.error:
                pass
        self.audio_actual = None  # was: del self.audio_actual (caused AttributeError on next check)
        self.ruta_solicitada = None
        self.reproduciendo = False
        self.tiempo_inicio = 0
        self.tiempo_pausa = 0
        self.duracion_total = 0
        self.posicion_actual = 0

    def adelantar(self, segundos):
        self.posicion_actual = min(
            self.duracion_total, self.obtener_tiempo_actual() + segundos
        )
        if pygame.mixer.get_init():
            try:
                if self.reproduciendo:
                    pygame.mixer.music.play(start=self.posicion_actual)
                else:
                    pygame.mixer.music.set_pos(self.posicion_actual)
            except pygame.error:
                pass
        if self.reproduciendo:
            self.tiempo_inicio = time.time() - self.posicion_actual

    def retroceder(self, segundos):
        self.posicion_actual = max(0, self.obtener_tiempo_actual() - segundos)
        if pygame.mixer.get_init():
            try:
                if self.reproduciendo:
                    pygame.mixer.music.play(start=self.posicion_actual)
                else:
                    pygame.mixer.music.set_pos(self.posicion_actual)
            except pygame.error:
                pass
        if self.reproduciendo:
            self.tiempo_inicio = time.time() - self.posicion_actual

    def obtener_tiempo_actual(self):
        if self.reproduciendo:
            return min(self.duracion_total, time.time() - self.tiempo_inicio)
        else:
            return self.posicion_actual

    def obtener_tiempo_formateado(self):
        tiempo_actual = int(self.obtener_tiempo_actual())
        tiempo_total = int(self.duracion_total)
        def _fmt(s):
            h = s // 3600
            m = (s % 3600) // 60
            sec = s % 60
            if h > 0:
                return f"{h}:{m:02d}:{sec:02d}"
            return f"{m:02d}:{sec:02d}"
        return f"{_fmt(tiempo_actual)} / {_fmt(tiempo_total)}"


reproductor = ReproductorAudio()


_after_id = None


def actualizar_tiempo(label, boton_pausar_reanudar, label_reproduccion, boton_adelantar, boton_retroceder, slider_progreso=None, slider_arrastrando=None, frame_slider=None):
    global _after_id
    if _after_id is not None:
        try:
            label.after_cancel(_after_id)
        except Exception:
            pass

    def actualizar():
        global _after_id
        mixer_busy = False
        if pygame.mixer.get_init():
            try:
                mixer_busy = pygame.mixer.music.get_busy()
            except pygame.error:
                pass

        should_stop = False
        if pygame.mixer.get_init():
            should_stop = not mixer_busy
        else:
            should_stop = reproductor.obtener_tiempo_actual() >= reproductor.duracion_total

        if reproductor.reproduciendo and should_stop:
            detener_reproduccion(
                boton_pausar_reanudar,
                label_reproduccion,
                label,
                boton_adelantar,
                boton_retroceder,
                slider_progreso,
                frame_slider=frame_slider,
            )
            return

        # CTkLabel uses .configure(), not .config()
        label.configure(text=reproductor.obtener_tiempo_formateado())

        if slider_progreso and not (slider_arrastrando and slider_arrastrando[0]):
            slider_progreso.config(to=reproductor.duracion_total)
            slider_progreso.set(reproductor.obtener_tiempo_actual())

        if reproductor.reproduciendo:
            _after_id = label.after(100, actualizar)

    actualizar()


def actualizar_label_reproduccion(label):
    if reproductor.reproduciendo and reproductor.audio_actual:
        label.configure(text=f"Reproduciendo: {os.path.basename(reproductor.audio_actual)}")
    else:
        label.configure(text="")


def reproducir(
    lista_archivos,
    lista_archivos_paths,
    boton_pausar_reanudar,
    label_reproduccion,
    label_tiempo,
    boton_adelantar,
    boton_retroceder,
    slider_progreso=None,
    slider_arrastrando=None,
    frame_slider=None,
):
    if config.transcripcion_en_curso:
        messagebox.showwarning(
            "Advertencia",
            "Hay una transcripción en curso. Por favor, espere a que termine.",
        )
        return

    seleccion = lista_archivos.curselection()
    if not seleccion:
        messagebox.showwarning(
            "Advertencia", "Por favor, seleccione un archivo de audio primero."
        )
        return

    indice_seleccionado = seleccion[0]
    item_seleccionado = lista_archivos.get(indice_seleccionado)
    ruta_archivo = next(
        key for key, value in lista_archivos_paths.items() if value == item_seleccionado
    )

    try:
        reproductor.iniciar(ruta_archivo)
    except Exception as e:
        messagebox.showerror(
            "Error de reproducción",
            f"No se pudo reproducir el archivo: {str(e)}",
        )
        return

    # CTkButton uses .configure(), not .config()
    boton_pausar_reanudar.configure(text="⏸", state="normal")
    boton_retroceder.configure(state="normal")
    boton_adelantar.configure(state="normal")
    actualizar_label_reproduccion(label_reproduccion)
    if frame_slider:
        import tkinter as tk
        frame_slider.pack(side=tk.TOP, fill=tk.X, pady=(0, 8))
    if slider_progreso:
        import tkinter as tk
        slider_progreso.pack(fill=tk.X, expand=True)
    actualizar_tiempo(
        label_tiempo,
        boton_pausar_reanudar,
        label_reproduccion,
        boton_adelantar,
        boton_retroceder,
        slider_progreso,
        slider_arrastrando,
        frame_slider=frame_slider,
    )


def pausar_reanudar(
    boton_pausar_reanudar,
    label_reproduccion,
    label_tiempo,
    boton_adelantar,
    boton_retroceder,
    slider_progreso=None,
    slider_arrastrando=None,
):
    if reproductor.reproduciendo:
        reproductor.pausar()
        boton_pausar_reanudar.configure(text="▶")
        label_tiempo.configure(text=reproductor.obtener_tiempo_formateado())
    else:
        reproductor.reanudar()
        boton_pausar_reanudar.configure(text="⏸")
        actualizar_tiempo(
            label_tiempo,
            boton_pausar_reanudar,
            label_reproduccion,
            boton_adelantar,
            boton_retroceder,
            slider_progreso,
            slider_arrastrando,
        )


def detener_reproduccion(
    boton_pausar_reanudar,
    label_reproduccion,
    label_tiempo,
    boton_adelantar,
    boton_retroceder,
    slider_progreso=None,
    frame_slider=None,
):
    global _after_id
    if _after_id is not None:
        try:
            label_tiempo.after_cancel(_after_id)
        except Exception:
            pass
        _after_id = None

    reproductor.detener()
    boton_pausar_reanudar.configure(text="⏸", state="disabled")
    boton_adelantar.configure(state="disabled")
    boton_retroceder.configure(state="disabled")
    actualizar_label_reproduccion(label_reproduccion)
    label_tiempo.configure(text="00:00 / 00:00")
    if slider_progreso:
        slider_progreso.set(0)
        slider_progreso.pack_forget()
    if frame_slider:
        frame_slider.pack_forget()
    if pygame.mixer.get_init():
        try:
            pygame.mixer.music.unload()
        except pygame.error:
            pass
    gc.collect()


def retroceder(label_tiempo):
    reproductor.retroceder(5)
    label_tiempo.configure(text=reproductor.obtener_tiempo_formateado())


def adelantar(label_tiempo):
    reproductor.adelantar(5)
    label_tiempo.configure(text=reproductor.obtener_tiempo_formateado())
