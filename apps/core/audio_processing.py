import os
import time
import whisper
from pydub import AudioSegment
import subprocess
import mutagen
import tempfile
import logging
import torch
from django.conf import settings

# Configurar logger
logger = logging.getLogger(__name__)

# Cargar el modelo de Whisper (se cargará una sola vez al importar el módulo)
try:
    # Usar el modelo base para balance entre velocidad y precisión
    # Cambiar a "small", "medium", "large" según necesidades
    WHISPER_MODEL = whisper.load_model("base")
    logger.info("Modelo Whisper cargado correctamente")
except Exception as e:
    logger.warning(f"Error cargando modelo Whisper: {str(e)}")
    logger.info("Modo de desarrollo: se usará transcripción simulada")
    WHISPER_MODEL = None

def extract_audio_metadata(file_path):
    """Extrae metadatos del archivo de audio"""
    try:
        audio_info = mutagen.File(file_path)
        metadata = {
            'duration': audio_info.info.length,
            'sample_rate': getattr(audio_info.info, 'sample_rate', 0),
            'channels': getattr(audio_info.info, 'channels', 0),
            'file_size': os.path.getsize(file_path),
            'file_format': file_path.split('.')[-1].lower()
        }
        return metadata
    except Exception as e:
        logger.error(f"Error al extraer metadatos: {str(e)}")
        return {
            'duration': 0,
            'sample_rate': 0,
            'channels': 0,
            'file_size': os.path.getsize(file_path) if os.path.exists(file_path) else 0,
            'file_format': file_path.split('.')[-1].lower() if '.' in file_path else ''
        }


def convert_audio_to_wav(file_path, output_path=None):
    """Convierte un archivo de audio a formato WAV para su procesamiento"""
    if output_path is None:
        output_path = tempfile.mktemp(suffix='.wav')
    
    try:
        audio = AudioSegment.from_file(file_path)
        audio = audio.set_channels(1)  # Convertir a mono
        audio = audio.set_frame_rate(16000)  # Establecer tasa de muestreo a 16kHz
        audio.export(output_path, format="wav")
        return output_path
    except Exception as e:
        logger.error(f"Error al convertir audio: {str(e)}")
        raise


def apply_noise_reduction(input_path, output_path=None):
    """Aplica reducción de ruido al archivo de audio"""
    if output_path is None:
        output_path = tempfile.mktemp(suffix='.wav')
    
    try:
        # Usar FFmpeg para aplicar un filtro de reducción de ruido
        cmd = [
            'ffmpeg', '-y', '-i', input_path, 
            '-af', 'afftdn=nf=-25', 
            output_path
        ]
        subprocess.run(cmd, check=True, stderr=subprocess.PIPE)
        return output_path
    except Exception as e:
        logger.error(f"Error al aplicar reducción de ruido: {str(e)}")
        return input_path  # Devolver el archivo original si falla la reducción


def apply_vad_segmentation(audio_path):
    """Segmenta el audio utilizando detección de actividad de voz (VAD)"""
    try:
        # Importar aquí para evitar error de importación circular
        from webrtcvad import Vad
        import wave
        import contextlib
        
        vad = Vad(3)  # Nivel de agresividad del VAD (0-3)
        
        # Obtener la duración del audio
        with contextlib.closing(wave.open(audio_path, 'r')) as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            duration = frames / float(rate)
            
            # Determinar si los segmentos contienen voz
            chunk_duration = 0.03  # 30 ms chunks
            chunk_size = int(rate * chunk_duration)
            num_chunks = int(duration / chunk_duration)
            
            # Leer el archivo de audio
            wf.rewind()
            pcm_data = wf.readframes(frames)
            
            # Procesar los chunks
            voice_segments = []
            is_speech = False
            start_time = 0
            
            for i in range(num_chunks):
                start_byte = i * chunk_size * 2  # 16 bit = 2 bytes por muestra
                end_byte = min((i + 1) * chunk_size * 2, len(pcm_data))
                chunk = pcm_data[start_byte:end_byte]
                
                if len(chunk) < chunk_size:
                    break
                
                is_voice = vad.is_speech(chunk, rate)
                
                # Inicio de un segmento de voz
                if is_voice and not is_speech:
                    is_speech = True
                    start_time = i * chunk_duration
                
                # Fin de un segmento de voz
                elif not is_voice and is_speech:
                    is_speech = False
                    end_time = i * chunk_duration
                    voice_segments.append((start_time, end_time))
            
            # Si termina en voz, cerrar el último segmento
            if is_speech:
                voice_segments.append((start_time, duration))
                
        return voice_segments
    except ImportError:
        logger.warning("No se pudo importar webrtcvad, procesando el audio completo")
        return [(0, get_audio_duration(audio_path))]
    except Exception as e:
        logger.error(f"Error en segmentación VAD: {str(e)}")
        return [(0, get_audio_duration(audio_path))]


def get_audio_duration(file_path):
    """Obtiene la duración de un archivo de audio"""
    try:
        audio_info = mutagen.File(file_path)
        return audio_info.info.length
    except:
        return 0


def transcribe_audio(file_path, language='es', use_vad=True, reduce_noise=True):
    """Transcribe un archivo de audio a texto usando OpenAI Whisper"""
    start_time = time.time()
    word_count = 0
    transcribed_text = ""
    
    try:
        # Verificar que el modelo Whisper esté cargado
        if WHISPER_MODEL is None:
            # En modo desarrollo, simular transcripción
            logger.info("Simulando transcripción (Whisper no disponible)")
            audio_name = os.path.basename(file_path)
            transcribed_text = f"Transcripción simulada del archivo: {audio_name}. Este texto sería reemplazado por la transcripción real de Whisper cuando esté disponible."
            word_count = len(transcribed_text.split())
            elapsed_time = time.time() - start_time
            
            return {
                'text': transcribed_text,
                'word_count': word_count,
                'duration_processed': elapsed_time,
                'success': True
            }
        
        # Convertir a formato WAV si es necesario
        wav_path = file_path
        if not file_path.lower().endswith('.wav'):
            wav_path = convert_audio_to_wav(file_path)
        
        # Aplicar reducción de ruido si está habilitada
        processed_path = wav_path
        if reduce_noise:
            processed_path = apply_noise_reduction(wav_path)
        
        # Configurar opciones de Whisper
        whisper_options = {
            'language': map_language_code(language),  # Usar función para mapear códigos de idioma
            'task': 'transcribe',
            'fp16': torch.cuda.is_available(),  # Usar FP16 si hay GPU disponible
        }
        
        # Realizar la transcripción con Whisper
        logger.info(f"Iniciando transcripción con Whisper para: {file_path}")
        result = WHISPER_MODEL.transcribe(processed_path, **whisper_options)
        
        # Extraer el texto transcrito
        transcribed_text = result.get('text', '').strip()
        
        # Contar palabras
        word_count = len(transcribed_text.split()) if transcribed_text else 0
        
        # Limpiar archivos temporales
        if wav_path != file_path and os.path.exists(wav_path):
            os.unlink(wav_path)
        if processed_path != wav_path and processed_path != file_path and os.path.exists(processed_path):
            os.unlink(processed_path)
        
        # Calcular tiempo total de procesamiento
        elapsed_time = time.time() - start_time
        
        logger.info(f"Transcripción completada. Palabras: {word_count}, Tiempo: {elapsed_time:.2f}s")
        
        return {
            'text': transcribed_text,
            'word_count': word_count,
            'duration_processed': elapsed_time,
            'success': True
        }
        
    except Exception as e:
        logger.error(f"Error en la transcripción con Whisper: {str(e)}")
        return {
            'text': "",
            'word_count': 0,
            'duration_processed': 0,
            'success': False,
            'error': str(e)
        }


def map_language_code(language_code):
    """Mapea códigos de idioma de Django a códigos de Whisper"""
    language_mapping = {
        'es-ES': 'es',  # Español
        'en-US': 'en',  # Inglés
        'fr-FR': 'fr',  # Francés
        'de-DE': 'de',  # Alemán
        'it-IT': 'it',  # Italiano
        'es': 'es',
        'en': 'en', 
        'fr': 'fr',
        'de': 'de',
        'it': 'it',
    }
    return language_mapping.get(language_code, 'es')  # Por defecto español


def translate_text(text, source_language='es', target_language='en'):
    """Traduce un texto de un idioma a otro (implementación simple)"""
    try:
        # Por ahora implementamos una traducción básica o retornamos el texto original
        # TODO: Implementar con una API de traducción como Google Translate o similar
        logger.info(f"Traducción de {source_language} a {target_language}: {len(text)} caracteres")
        
        return {
            'text': text,  # Por ahora retornamos el texto original
            'success': True
        }
    except Exception as e:
        logger.error(f"Error en la traducción: {str(e)}")
        return {
            'text': "",
            'success': False,
            'error': str(e)
        }
