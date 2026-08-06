import sys

# Patch sys.stdout and sys.stderr when running in windowed mode (console=False)
# to prevent AttributeError when third-party libraries (like torch.hub) try to write to them.
class _DummyWriter:
    def write(self, data):
        pass
    def flush(self):
        pass

if sys.stdout is None:
    sys.stdout = _DummyWriter()
if sys.stderr is None:
    sys.stderr = _DummyWriter()
if sys.__stdout__ is None:
    sys.__stdout__ = sys.stdout
if sys.__stderr__ is None:
    sys.__stderr__ = sys.stderr

import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk

# Monkey-patch config to configure on all customtkinter widgets for backwards compatibility
ctk.CTkBaseClass.config = lambda self, **kwargs: self.configure(**kwargs)

from presentation.views.interfaz import crear_interfaz, centrar_ventana
from config import logger
import pygame
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
        # La bitacora se instala antes que nada: a partir de aca cualquier fallo
        # no capturado (hilo, callback de Tk, tarea asyncio) queda registrado en
        # logs/bitacora.jsonl en vez de perderse en un stderr inexistente.
        from core.errors import install as install_error_journal

        install_error_journal()

        # Inicializar e inyectar dependencias (SOLID / Inyección de dependencias)
        from infrastructure.repositories.sqlite_repository import SQLiteTranscriptionRepository
        from infrastructure.services.onnx_transcriber import OfflineTranscriptionService
        import config
        
        config.repository = SQLiteTranscriptionRepository(config.DB_PATH)
        config.repository.init_db()
        
        config.transcription_service = OfflineTranscriptionService(model_size=config.MODEL_SIZE)
        logger.info("[DIAG] Dependencias inyectadas exitosamente en config.")

        # Inicializar pygame mixer de forma segura
        logger.info("[DIAG] Iniciando pygame.mixer.init...")
        try:
            pygame.mixer.init()
            logger.info("[DIAG] pygame.mixer.init OK")
        except Exception as e:
            logger.warning("[DIAG] pygame.mixer.init fallo (se desactivara la reproduccion): %s", e)

        # Configurar CustomTkinter
        logger.info("[DIAG] Configurando CustomTkinter...")
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        logger.info("[DIAG] CustomTkinter OK")

        logger.info("[DIAG] Creando ventana CTk...")
        ventana = ctk.CTk()
        logger.info("[DIAG] Ventana CTk creada")
        install_error_journal(ventana)
        ventana.title("AudioText")
        # Optional window icon. Bundled only in the PyInstaller build, so a
        # missing file when running from source must not crash the app.
        try:
            ventana.iconbitmap(resource_path("icons/icono.ico"))
        except Exception:
            logger.warning("Icono no encontrado, se usa el icono por defecto.")
        widgets = crear_interfaz(ventana)

        # Iniciar maximizada por defecto (pantalla completa con barra de tareas)
        try:
            ventana.state("zoomed")
        except Exception:
            centrar_ventana(ventana)

        # Protocolo para cierre limpio de la ventana
        def on_closing():
            try:
                from infrastructure.audio.reproductor import reproductor
                reproductor.detener()
            except Exception:
                pass
            try:
                if widgets and "live_frame" in widgets:
                    widgets["live_frame"].stop_worker()
            except Exception:
                pass
            try:
                if widgets and "interview_frame" in widgets:
                    widgets["interview_frame"].stop_session()
            except Exception:
                pass
            ventana.destroy()

        ventana.protocol("WM_DELETE_WINDOW", on_closing)

        # Ejecutar la aplicación
        ventana.mainloop()

    except Exception as e:
        # Last-resort net: feature modules handle their own errors now, so this
        # only fires on a hard startup failure. Log the full traceback.
        try:
            from core.errors import record

            record("arranque", e, severity="critical")
        except Exception:
            logger.exception("Fallo critico en el arranque: %s", e)
        messagebox.showerror(
            "Error", f"Se produjo un error al iniciar. Revisa logs/bitacora.jsonl:\n{e}"
        )


if __name__ == "__main__":

    main()
