import os
from celery import shared_task
from django.conf import settings
from django.utils import timezone
import logging

from .models import Transcription, Translation, UserProfile
from .audio_processing import transcribe_audio, translate_text, extract_audio_metadata

# Configurar logger
logger = logging.getLogger(__name__)

@shared_task
def process_transcription(transcription_id):
    """
    Tarea Celery para procesar una transcripción de manera asíncrona
    """
    transcription = None
    try:
        # Obtener la transcripción
        transcription = Transcription.objects.get(id=transcription_id)
        audio_file = transcription.audio_file
        
        # Actualizar estado
        transcription.status = 'processing'
        transcription.save(update_fields=['status'])
        
        # Obtener la ruta del archivo
        file_path = os.path.join(settings.MEDIA_ROOT, audio_file.file.name)
        
        # Si no se han extraído los metadatos del audio, hacerlo ahora
        if audio_file.duration == 0:
            metadata = extract_audio_metadata(file_path)
            for key, value in metadata.items():
                setattr(audio_file, key, value)
            audio_file.save()
        
        # Realizar transcripción
        result = transcribe_audio(
            file_path,
            language=transcription.language,
            use_vad=transcription.vad_enabled,
            reduce_noise=transcription.noise_reduction
        )
        
        # Actualizar la transcripción con el resultado
        if result['success']:
            transcription.text = result['text']
            transcription.word_count = result['word_count']
            transcription.duration = result['duration_processed']
            transcription.status = 'completed'
        else:
            transcription.status = 'failed'
            transcription.error_message = result.get('error', 'Error desconocido en la transcripción')
        
        transcription.save()
        
        # Actualizar estadísticas del perfil de usuario
        if result['success']:
            profile, created = UserProfile.objects.get_or_create(user=transcription.user)
            profile.total_words_transcribed += result['word_count']
            profile.total_audio_duration += audio_file.duration
            profile.api_usage_count += 1
            profile.save()
        
        return True
    
    except Transcription.DoesNotExist:
        logger.error(f"No se encontró la transcripción con ID {transcription_id}")
        return False
    
    except Exception as e:
        logger.error(f"Error al procesar transcripción {transcription_id}: {str(e)}")
        if transcription:
            transcription.status = 'failed'
            transcription.error_message = str(e)[:500]  # Limitar longitud del mensaje de error
            transcription.save(update_fields=['status', 'error_message'])
        return False


@shared_task
def process_translation(translation_id):
    """
    Tarea Celery para procesar una traducción de manera asíncrona
    """
    translation = None
    try:
        # Obtener la traducción
        translation = Translation.objects.get(id=translation_id)
        transcription = translation.transcription
        
        # Verificar que la transcripción está completa
        if transcription.status != 'completed' or not transcription.text:
            translation.text = "Error: La transcripción no está completa"
            translation.save()
            return False
        
        # Realizar la traducción
        result = translate_text(
            transcription.text,
            source_language=translation.source_language,
            target_language=translation.target_language
        )
        
        # Actualizar la traducción con el resultado
        if result['success']:
            translation.text = result['text']
        else:
            translation.text = f"Error en la traducción: {result.get('error', 'Error desconocido')}"
        
        translation.save()
        
        # Actualizar estadísticas del perfil de usuario
        profile, created = UserProfile.objects.get_or_create(user=translation.user)
        profile.api_usage_count += 1
        profile.save()
        
        return True
    
    except Translation.DoesNotExist:
        logger.error(f"No se encontró la traducción con ID {translation_id}")
        return False
    
    except Exception as e:
        logger.error(f"Error al procesar traducción {translation_id}: {str(e)}")
        if translation:
            translation.text = f"Error: {str(e)[:500]}"
            translation.save()
        return False
