import os
import sys
import logging

# Limit ctranslate2 / OpenMP threads so the Tkinter event loop keeps
# enough CPU headroom to stay responsive during heavy transcription.
_cpu_count = os.cpu_count() or 4
_model_threads = max(2, int(_cpu_count * 0.75))
os.environ.setdefault("OMP_NUM_THREADS", str(_model_threads))
os.environ.setdefault("CT2_INTER_THREADS", "1")
os.environ.setdefault("CT2_INTRA_THREADS", str(_model_threads))
from logging.handlers import RotatingFileHandler

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
file_handler = RotatingFileHandler(
    log_file, maxBytes=1048576, backupCount=5, encoding="utf-8"
)
file_handler.setLevel(logging.DEBUG)

# Crear un manejador de consola
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)

# Crear un formateador y añadirlo a los manejadores
# El modulo y la linea son la diferencia entre "algo fallo" y saber donde:
# todos los modulos comparten este logger, asi que sin ellos cada linea decia
# "config" y no habia forma de ubicar el origen.
formatter = logging.Formatter(
    "%(asctime)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s"
)
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

# Añadir los manejadores al logger
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Archivo aparte solo con fallas. error_log.txt mezcla el INFO de cada turno con
# los errores reales, asi que encontrar la falla ahi es imposible; este queda
# corto y legible.
error_only_file = os.path.join(log_directory, "errores.log")
error_handler = RotatingFileHandler(
    error_only_file, maxBytes=524288, backupCount=2, encoding="utf-8"
)
error_handler.setLevel(logging.WARNING)
error_handler.setFormatter(formatter)
logger.addHandler(error_handler)

# Las librerias (google-genai, urllib3, asyncio) loguean en el root logger, que
# no tenia handlers: sus errores se descartaban en silencio. Se enganchan solo
# desde WARNING para no inundar el archivo con su ruido de debug.
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.WARNING)
if not _root_logger.handlers:
    _root_logger.addHandler(file_handler)
    _root_logger.addHandler(error_handler)

# Configurar rutas de FFmpeg
# When frozen (PyInstaller 6.x+): bundled files live in sys._MEIPASS (_internal/),
# not next to the exe. Use _MEIPASS for bundled binaries, base_dir for user data.
# Running from source: config.py is at repo root, ffmpeg/ sits right next to it.
# When frozen (PyInstaller 6.x+): bundled files live in sys._MEIPASS (_internal/).
if getattr(sys, "frozen", False):
    _ffmpeg_root = getattr(sys, "_MEIPASS", base_dir)
else:
    _ffmpeg_root = base_dir

ffmpeg_path = os.path.join(_ffmpeg_root, "ffmpeg", "bin", "ffmpeg.exe")
ffprobe_path = os.path.join(_ffmpeg_root, "ffmpeg", "bin", "ffprobe.exe")
ffmpeg_bin_path = os.path.join(_ffmpeg_root, "ffmpeg", "bin")
os.environ["PATH"] = ffmpeg_bin_path + os.pathsep + os.environ["PATH"]

def report_error(context, exc, user_msg=None):
    """Log the full traceback to the bitacora and return a short user message.

    Use this instead of dumping raw exceptions into the UI: development gets the
    full detail in logs/, the user gets a clean message. Delegates to
    core.errors so every caller lands in the structured journal; the import is
    lazy because core.errors imports this module.
    """
    try:
        from core.errors import record

        return record(context, exc, user_msg=user_msg)
    except Exception:
        # exc_info=exc logs the passed exception's traceback even when called
        # outside an active except block.
        logger.error("%s: %s", context, exc, exc_info=exc)
        return user_msg or "Ocurrio un error. Revisa la bitacora (logs/bitacora.jsonl)."


# Global dependency container (SOLID)
repository = None
transcription_service = None
DB_PATH = os.path.join(base_dir, "auditext.db")
MODEL_SIZE = "small"
SAMPLE_RATE = 16000

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







# Inicialización
logger.info("Configuración inicial completada")
