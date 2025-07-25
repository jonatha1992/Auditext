import os
import sys

# Permitir configurar la ruta de FFmpeg mediante la variable de entorno
ffmpeg_path = os.environ.get("FFMPEG_PATH")
if not ffmpeg_path:
    if sys.platform == "win32":
        ffmpeg_path = r"D:\Repositorio\jonatha1992\Auditext\ffmpeg\bin"
    else:
        ffmpeg_path = "/usr/bin"

# Asegurar que FFmpeg esté en el PATH
os.environ["PATH"] = ffmpeg_path + os.pathsep + os.environ.get("PATH", "")

import whisper
from pydub.silence import detect_nonsilent
from pydub import AudioSegment

_model = None

def get_model():
    global _model
    if _model is None:
        print('Cargando modelo Whisper (medium)...')
        _model = whisper.load_model("medium")
        print('Modelo Whisper cargado.')
    return _model

def vad_segmentacion(audio, min_silence_len=1000, silence_thresh=-40, keep_silence=300):
    """
    Segmentación usando pydub.silence como la app de escritorio
    """
    not_silence_ranges = detect_nonsilent(audio, min_silence_len=min_silence_len, silence_thresh=silence_thresh)
    
    chunks = []
    for start_i, end_i in not_silence_ranges:
        start_i = max(0, start_i - keep_silence)
        end_i = min(len(audio), end_i + keep_silence)
        chunks.append(audio[start_i:end_i])
    
    return chunks

def transcribe_audio(file_path, pause_threshold=1.0, translate=False, language=None):
    """
    Transcribe el audio usando VAD de pydub para separar turnos por silencios.

    Si ``translate`` es ``True`` se realiza la traducción a inglés utilizando
    la propia capacidad del modelo Whisper.
    """
    model = get_model()
    
    # Cargar audio con pydub para VAD
    audio_pydub = AudioSegment.from_file(file_path)
    
    # Segmentar usando VAD
    chunks = vad_segmentacion(audio_pydub)
    
    # Transcribir cada chunk con Whisper
    turns = []
    current_turn_start = 0
    
    for i, chunk in enumerate(chunks):
        if len(chunk) < 1000:  # Ignorar chunks menores a 1 segundo
            continue
            
        # Guardar chunk temporalmente
        temp_chunk_path = f"temp_chunk_{i}.wav"
        chunk.export(temp_chunk_path, format="wav")
        
        try:
            # Transcribir chunk con Whisper
            task = 'translate' if translate else 'transcribe'
            result = model.transcribe(temp_chunk_path, task=task, language=language)
            text = result['text'].strip()
            
            if text and text != "[inaudible]" and text != "[chunk vacío]":
                # Calcular tiempo relativo al audio original
                chunk_start = current_turn_start
                chunk_end = chunk_start + (len(chunk) / 1000)  # Convertir a segundos
                
                turns.append({
                    'turn': len(turns) + 1,
                    'start': chunk_start,
                    'end': chunk_end,
                    'text': text
                })
                
                current_turn_start = chunk_end
                
        except Exception as e:
            print(f"Error transcribiendo chunk {i}: {e}")
        finally:
            # Limpiar archivo temporal
            if os.path.exists(temp_chunk_path):
                os.remove(temp_chunk_path)
    
    return turns 