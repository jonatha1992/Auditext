import tkinter as tk
from tkinter import messagebox
from interfaz import crear_interfaz, centrar_ventana
from config import detectar_y_configurar_proxy, check_dependencies, logger
from reproductor import pygame
import os
import sys


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS  # type: ignore
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


def main():
    try:
        # Configurar el proxy automáticamente al inicio
        detectar_y_configurar_proxy()

        # Inicializar pygame mixer
        pygame.mixer.init()

        ventana = tk.Tk()
        ventana.title("AudioText")
        # Optional window icon. Bundled only in the PyInstaller build, so a
        # missing file when running from source must not crash the app.
        try:
            ventana.iconbitmap(resource_path("icons/icono.ico"))
        except Exception:
            logger.warning("Icono no encontrado, se usa el icono por defecto.")
        crear_interfaz(ventana)

        # Centrar la ventana en la pantalla
        centrar_ventana(ventana)

        # Ejecutar la aplicación
        ventana.mainloop()

    except Exception as e:
        # Last-resort net: feature modules handle their own errors now, so this
        # only fires on a hard startup failure. Log the full traceback.
        logger.exception("Fallo critico en el arranque: %s", e)
        messagebox.showerror(
            "Error", f"Se produjo un error al iniciar. Revisa logs/error_log.txt:\n{e}"
        )


if __name__ == "__main__":

    main()
