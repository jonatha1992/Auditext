import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk

# Monkey-patch config to configure on all customtkinter widgets for backwards compatibility
ctk.CTkBaseClass.config = lambda self, **kwargs: self.configure(**kwargs)

from interfaz import crear_interfaz, centrar_ventana
from config import check_dependencies, logger
from reproductor import pygame
import os
import sys
import db


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS  # type: ignore
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


def main():
    try:
        # Inicializar la base de datos SQLite
        db.init_db()
        logger.info("[DIAG] db.init_db OK")

        # Inicializar pygame mixer
        logger.info("[DIAG] Iniciando pygame.mixer.init...")
        pygame.mixer.init()
        logger.info("[DIAG] pygame.mixer.init OK")

        # Configurar CustomTkinter
        logger.info("[DIAG] Configurando CustomTkinter...")
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        logger.info("[DIAG] CustomTkinter OK")

        logger.info("[DIAG] Creando ventana CTk...")
        ventana = ctk.CTk()
        logger.info("[DIAG] Ventana CTk creada")
        ventana.title("AudioText")
        # Optional window icon. Bundled only in the PyInstaller build, so a
        # missing file when running from source must not crash the app.
        try:
            ventana.iconbitmap(resource_path("icons/icono.ico"))
        except Exception:
            logger.warning("Icono no encontrado, se usa el icono por defecto.")
        widgets = crear_interfaz(ventana)

        # Centrar la ventana en la pantalla
        centrar_ventana(ventana)

        # Protocolo para cierre limpio de la ventana
        def on_closing():
            try:
                from reproductor import reproductor
                reproductor.detener()
            except Exception:
                pass
            try:
                if widgets and "live_frame" in widgets:
                    widgets["live_frame"].stop_worker()
            except Exception:
                pass
            ventana.destroy()

        ventana.protocol("WM_DELETE_WINDOW", on_closing)

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
