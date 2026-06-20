import os
import sys
import logging
from logging.handlers import RotatingFileHandler
import datetime

# Base directory for external files (logs, ffmpeg)
if getattr(sys, "frozen", False):
    base_dir = os.path.dirname(sys.executable)
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))

# Configuración básica de logging
log_directory = os.path.join(base_dir, "logs")
if not os.path.exists(log_directory):
    os.makedirs(log_directory)

log_file = os.path.join(log_directory, "error_log.txt")

# Configurar el logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Crear un manejador de archivo rotativo
file_handler = RotatingFileHandler(log_file, maxBytes=1048576, backupCount=5)
file_handler.setLevel(logging.DEBUG)

# Crear un manejador de consola
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)

# Crear un formateador y añadirlo a los manejadores
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

# Añadir los manejadores al logger
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Configurar rutas de FFmpeg
# When frozen (PyInstaller): ffmpeg is bundled next to the exe → use base_dir.
# When running from source: ffmpeg lives at the repo root, one level up from app_escritorio/.
if getattr(sys, "frozen", False):
    _ffmpeg_root = base_dir
else:
    _ffmpeg_root = os.path.dirname(base_dir)

ffmpeg_path = os.path.join(_ffmpeg_root, "ffmpeg", "bin", "ffmpeg.exe")
ffprobe_path = os.path.join(_ffmpeg_root, "ffmpeg", "bin", "ffprobe.exe")
ffmpeg_bin_path = os.path.join(_ffmpeg_root, "ffmpeg", "bin")
os.environ["PATH"] = ffmpeg_bin_path + os.pathsep + os.environ["PATH"]

def report_error(context, exc, user_msg=None):
    """Log the full traceback to the bitacora and return a short user message.

    Use this instead of dumping raw exceptions into the UI: development gets the
    full detail in logs/error_log.txt, the user gets a clean message.
    """
    # exc_info=exc logs the passed exception's traceback even when called
    # outside an active except block.
    logger.error("%s: %s", context, exc, exc_info=exc)
    return user_msg or "Ocurrio un error. Revisa la bitacora (logs/error_log.txt)."


# Variables globales
transcripcion_activa = False
transcripcion_en_curso = False

# Idiomas soportados (offline, faster-whisper). Nombre -> codigo ISO.
# "Auto" deja que el modelo detecte el idioma automaticamente.
idiomas = {
    "Auto": None,
    "Spanish": "es",
    "English": "en",
    "Portuguese": "pt",
    "French": "fr",
    "German": "de",
    "Italian": "it",
    "Catalan": "ca",
    "Dutch": "nl",
    "Russian": "ru",
    "Chinese": "zh",
    "Japanese": "ja",
}





def check_dependencies():
    current_date = datetime.date.today()
    expiration_date = datetime.date(2025, 1, 3)
    return current_date >= expiration_date


# Inicialización
logger.info("Configuración inicial completada")
