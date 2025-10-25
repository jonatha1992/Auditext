import os
import logging
from logging.handlers import RotatingFileHandler
from googletrans import LANGUAGES
import datetime

# Configuración básica de logging
log_directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
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
script_dir = os.path.dirname(os.path.abspath(__file__))
ffmpeg_path = os.path.join(script_dir, "ffmpeg", "bin", "ffmpeg.exe")
ffprobe_path = os.path.join(script_dir, "ffmpeg", "bin", "ffprobe.exe")
ffmpeg_bin_path = os.path.join(script_dir, "ffmpeg", "bin")
os.environ["PATH"] = ffmpeg_bin_path + os.pathsep + os.environ["PATH"]

# Variables globales
transcripcion_activa = False
transcripcion_en_curso = False
idiomas = {v.capitalize(): k for k, v in LANGUAGES.items()}


def check_proxy():
    http_proxy = os.environ.get("http_proxy")
    https_proxy = os.environ.get("https_proxy")
    if http_proxy or https_proxy:
        return f"Proxy configurado:\nHTTP: {http_proxy}\nHTTPS: {https_proxy}"
    else:
        return "No se detectó ningún proxy configurado"


def detectar_y_configurar_proxy():
    """
    Configura el proxy desde variables de entorno si están definidas.

    SECURITY NOTE: El proxy nunca debe estar hardcodeado en el código.
    Configure HTTP_PROXY y HTTPS_PROXY en el archivo .env si es necesario.

    Returns:
        bool: True si se configuró un proxy, False si hay conexión directa
    """
    import urllib.request

    # Primero intentar conexión directa
    proxy_handler = urllib.request.ProxyHandler()
    opener = urllib.request.build_opener(proxy_handler)
    try:
        opener.open("http://www.google.com", timeout=5)
        logger.info("Conexión directa exitosa, no se necesita proxy.")
        return False
    except Exception:
        logger.info("Conexión directa fallida, verificando configuración de proxy...")

        # Cargar proxy desde variables de entorno
        http_proxy = os.getenv("HTTP_PROXY", "")
        https_proxy = os.getenv("HTTPS_PROXY", "")

        if not http_proxy and not https_proxy:
            logger.warning(
                "No se pudo conectar y no hay proxy configurado.\n"
                "Si está detrás de un proxy, configure HTTP_PROXY y HTTPS_PROXY en el archivo .env"
            )
            return False

        # Configurar proxy desde variables de entorno
        if http_proxy:
            os.environ["http_proxy"] = http_proxy
        if https_proxy:
            os.environ["https_proxy"] = https_proxy

        try:
            proxy_config = {}
            if http_proxy:
                proxy_config["http"] = http_proxy
            if https_proxy:
                proxy_config["https"] = https_proxy

            proxy_handler = urllib.request.ProxyHandler(proxy_config)
            opener = urllib.request.build_opener(proxy_handler)
            opener.open("http://www.google.com", timeout=5)
            logger.info(f"Proxy configurado exitosamente desde variables de entorno.")
            return True
        except Exception as e:
            logger.error(f"No se pudo establecer conexión incluso con el proxy: {e}")
            return False


def check_dependencies():
    current_date = datetime.date.today()
    expiration_date = datetime.date(2025, 1, 3)
    return current_date >= expiration_date


# Inicialización
logger.info("Configuración inicial completada")
