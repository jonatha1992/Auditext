# app/utils/file_operations.py

import os
from mutagen import File as MutagenFile

def obtener_duracion_audio(file_path):
    """
    Obtiene la duración de un archivo de audio en segundos.
    """
    try:
        audio = MutagenFile(file_path)
        return audio.info.length
    except Exception as e:
        print(f"Error al obtener la duración del archivo {file_path}: {e}")
        return 0

def formatear_duracion(segundos):
    """
    Convierte una duración en segundos a un formato MM:SS.
    """
    minutos, segundos = divmod(int(segundos), 60)
    return f"{minutos:02}:{segundos:02}"
