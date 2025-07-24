import pygame
import time
import os
from Config import *
from tkinter import messagebox
from Funcionalidad import obtener_duracion_audio, convertir_a_wav
import gc


class ReproductorAudio:
    def __init__(self):
        self.reproduciendo = False
        self.audio_actual = None
        self.tiempo_inicio = 0
        self.tiempo_pausa = 0
        self.duracion_total = 0
        self.posicion_actual = 0

    def iniciar(self, ruta_archivo):
        self.audio_actual = ruta_archivo
        self.duracion_total = obtener_duracion_audio(ruta_archivo)
        pygame.mixer.music.load(ruta_archivo)
        pygame.mixer.music.play()
        self.reproduciendo = True
        self.tiempo_inicio = time.time()
        self.tiempo_pausa = 0
        self.posicion_actual = 0

    def pausar(self):
        if self.reproduciendo:
            pygame.mixer.music.pause()
            self.reproduciendo = False
            self.posicion_actual = self.obtener_tiempo_actual()

    def reanudar(self):
        if not self.reproduciendo:
            pygame.mixer.music.unpause()
            self.reproduciendo = True
            self.tiempo_inicio = time.time() - self.posicion_actual

    def detener(self):
        pygame.mixer.music.stop()
        pygame.mixer.music.unload()
        del self.audio_actual
        self.reproduciendo = False
        self.audio_actual = None
        self.tiempo_inicio = 0
        self.tiempo_pausa = 0
        self.duracion_total = 0
        self.posicion_actual = 0

    def adelantar(self, segundos):
        self.posicion_actual = min(
            self.duracion_total, self.obtener_tiempo_actual() + segundos
        )
        if self.reproduciendo:
            pygame.mixer.music.play(start=self.posicion_actual)
            self.tiempo_inicio = time.time() - self.posicion_actual
        else:
            pygame.mixer.music.set_pos(self.posicion_actual)

    def retroceder(self, segundos):
        self.posicion_actual = max(0, self.obtener_tiempo_actual() - segundos)
        if self.reproduciendo:
            pygame.mixer.music.play(start=self.posicion_actual)
            self.tiempo_inicio = time.time() - self.posicion_actual
        else:
            pygame.mixer.music.set_pos(self.posicion_actual)

    def obtener_tiempo_actual(self):
        if self.reproduciendo:
            return min(self.duracion_total, time.time() - self.tiempo_inicio)
        else:
            return self.posicion_actual

    def obtener_tiempo_formateado(self):
        tiempo_actual = int(self.obtener_tiempo_actual())
        tiempo_total = int(self.duracion_total)
        return f"{time.strftime('%M:%S', time.gmtime(tiempo_actual))} / {time.strftime('%M:%S', time.gmtime(tiempo_total))}"


reproductor = ReproductorAudio()


def actualizar_tiempo(label):
    def actualizar():
        label.config(text=reproductor.obtener_tiempo_formateado())
        label.after(100, actualizar)

    actualizar()


def actualizar_label_reproduccion(label):
    if reproductor.reproduciendo and reproductor.audio_actual:
        label.config(
            text=f"Reproduciendo: {os.path.basename(reproductor.audio_actual)}"
        )
    else:
        label.config(text="")


def reproducir(
    lista_archivos,
    lista_archivos_paths,
    boton_pausar_reanudar,
    label_reproduccion,
    label_tiempo,
    boton_adelantar,
    boton_retroceder,
):
    if transcripcion_en_curso:
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
    except pygame.error:
        try:
            wav_path = convertir_a_wav(ruta_archivo)
            reproductor.iniciar(wav_path)
            messagebox.showinfo(
                "Conversión",
                f"El archivo se ha convertido a WAV para su reproducción: {wav_path}",
            )
        except Exception as e:
            messagebox.showerror(
                "Error de conversión",
                f"No se pudo convertir ni reproducir el archivo: {str(e)}",
            )
            return

    boton_pausar_reanudar.config(text="Pausar", state="active")
    boton_retroceder.config(state="active")
    boton_adelantar.config(state="active")
    actualizar_tiempo(label_tiempo)
    actualizar_label_reproduccion(label_reproduccion)


def pausar_reanudar(boton_pausar_reanudar, label_reproduccion, label_tiempo):
    if reproductor.reproduciendo:
        reproductor.pausar()
        boton_pausar_reanudar.config(text="Reanudar")
    else:
        reproductor.reanudar()
        boton_pausar_reanudar.config(text="Pausar")
    actualizar_tiempo(label_tiempo)


def detener_reproduccion(
    boton_pausar_reanudar,
    label_reproduccion,
    label_tiempo,
    boton_adelantar,
    boton_retroceder,
):
    reproductor.detener()
    boton_pausar_reanudar.config(text="Pausar", state="disabled")
    boton_adelantar.config(state="disabled")
    boton_retroceder.config(state="disabled")
    actualizar_label_reproduccion(label_reproduccion)
    label_tiempo.config(text="00:00 / 00:00")
    pygame.mixer.music.unload()
    del reproductor.audio_actual
    reproductor.audio_actual = None
    gc.collect()


def retroceder(label_tiempo):
    reproductor.retroceder(5)
    actualizar_tiempo(label_tiempo)


def adelantar(label_tiempo):
    reproductor.adelantar(5)
    actualizar_tiempo(label_tiempo)
