import os
import sys
from pathlib import Path

# =============================================================================
# FFmpeg Path Configuration
# =============================================================================
# SECURITY NOTE: Never hardcode absolute paths like "D:\user\project\ffmpeg"
# Use relative paths from project root or environment variables
# =============================================================================

# Get project base directory (3 levels up from this file)
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load FFmpeg path from environment variable or use relative path
if sys.platform == "win32":
    # Windows: Allow override from environment, otherwise use relative path
    ffmpeg_path = os.getenv("FFMPEG_PATH", str(BASE_DIR / "ffmpeg" / "bin"))
else:
    # Linux/Mac: Allow override from environment, otherwise use system path
    ffmpeg_path = os.getenv("FFMPEG_PATH", "/usr/bin")

# Add FFmpeg to PATH
os.environ["PATH"] = ffmpeg_path + os.pathsep + os.environ.get("PATH", "")

import whisper
from pydub.silence import detect_nonsilent
from pydub import AudioSegment

_model_cache = {}

def get_model(model_name="medium"):
    global _model_cache
    if model_name not in _model_cache:
        print(f'Cargando modelo Whisper ({model_name})...')
        _model_cache[model_name] = whisper.load_model(model_name)
        print(f'Modelo Whisper {model_name} cargado.')
    return _model_cache[model_name]

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