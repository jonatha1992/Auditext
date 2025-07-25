import os
import sys

if sys.platform == "win32":
    ffmpeg_path = r"D:\Repositorio\jonatha1992\Auditext\ffmpeg\bin"
else:
    ffmpeg_path = "/usr/bin"

os.environ["PATH"] = ffmpeg_path + os.pathsep + os.environ.get("PATH", "")

import whisper
from pydub.silence import detect_nonsilent
from pydub import AudioSegment

# Importar pyannote.audio para diarización
try:
    from pyannote.audio import Pipeline
    from pyannote.audio.pipelines.utils.hook import ProgressHook
    DIARIZATION_AVAILABLE = True
except ImportError:
    print("pyannote.audio no está disponible. La diarización automática no funcionará.")
    DIARIZATION_AVAILABLE = False

_model_cache = {}

def get_model(model_name="medium"):
    global _model_cache
    if model_name not in _model_cache:
        print(f'Cargando modelo Whisper ({model_name})...')
        _model_cache[model_name] = whisper.load_model(model_name)
        print(f'Modelo Whisper {model_name} cargado.')
    return _model_cache[model_name]

def get_diarization_pipeline():
    """
    Obtiene el pipeline de diarización de pyannote.audio
    Requiere token de HuggingFace para descargar el modelo
    """
    global _diarization_pipeline
    
    if not DIARIZATION_AVAILABLE:
        return None
        
    if _diarization_pipeline is None:
        try:
            token = "os.getenv("HUGGINGFACE_TOKEN")"  # Token de HuggingFace del usuario
            _diarization_pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization@2.1",
                use_auth_token=token
            )
            print("Modelo de diarización cargado.")
        except Exception as e:
            print(f"Error cargando modelo de diarización: {e}")
            print("Para usar diarización automática, necesitas:")
            print("1. Crear cuenta en https://huggingface.co/join")
            print("2. Ir a https://huggingface.co/settings/tokens y crear un token")
            print("3. Usar el token en la función get_diarization_pipeline()")
            return None
    
    return _diarization_pipeline

def diarize_speakers(audio_path):
    """
    Detecta automáticamente los hablantes en el audio
    Retorna una lista de intervalos con el hablante asignado
    """
    pipeline = get_diarization_pipeline()
    if pipeline is None:
        return []
    
    try:
        print("Analizando hablantes...")
        diarization = pipeline(audio_path)
        
        # Extraer los intervalos de cada hablante
        speaker_segments = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            speaker_segments.append({
                'start': turn.start,
                'end': turn.end,
                'speaker': speaker,
                'duration': turn.end - turn.start
            })
        
        print(f"Diarización completada: {len(speaker_segments)} segmentos de hablantes detectados")
        return speaker_segments
        
    except Exception as e:
        print(f"Error en diarización: {e}")
        return []

def vad_segmentacion(audio, min_silence_len=1000, silence_thresh=-40, keep_silence=300):
    """
    Segmentación usando pydub.silence como la app de escritorio
    min_silence_len=1000 ms, silence_thresh=-40 dB, keep_silence=300 ms
    """
    not_silence_ranges = detect_nonsilent(audio, min_silence_len=min_silence_len, silence_thresh=silence_thresh)
    chunks = []
    for start_i, end_i in not_silence_ranges:
        start_i = max(0, start_i - keep_silence)
        end_i = min(len(audio), end_i + keep_silence)
        chunks.append(audio[start_i:end_i])
    return chunks

def transcribe_audio(file_path, pause_threshold=1.0, whisper_model="medium"):
    model = get_model(whisper_model)
    audio_pydub = AudioSegment.from_file(file_path)
    # Usar exactamente los mismos parámetros que la app de escritorio
    chunks = vad_segmentacion(audio_pydub, min_silence_len=1000, silence_thresh=-40, keep_silence=300)
    turns = []
    current_turn_start = 0
    for i, chunk in enumerate(chunks):
        if len(chunk) < 1000:  # Ignorar chunks menores a 1 segundo
            continue
        temp_chunk_path = f"temp_chunk_{i}.wav"
        chunk.export(temp_chunk_path, format="wav")
        try:
            result = model.transcribe(temp_chunk_path)
            text = result['text'].strip()
            if text and text != "[inaudible]" and text != "[chunk vacío]":
                chunk_start = current_turn_start
                chunk_end = chunk_start + (len(chunk) / 1000)
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
            if os.path.exists(temp_chunk_path):
                os.remove(temp_chunk_path)
    return turns 