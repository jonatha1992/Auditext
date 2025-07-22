from django.db import models
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _


class BaseModel(models.Model):
    """Modelo base con campos comunes"""
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Fecha de creación'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Fecha de actualización'))
    
    class Meta:
        abstract = True


class AudioFile(BaseModel):
    """Modelo para almacenar archivos de audio subidos"""
    title = models.CharField(max_length=255, verbose_name=_('Título'))
    file = models.FileField(upload_to='uploads/', verbose_name=_('Archivo de audio'))
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='audio_files', verbose_name=_('Usuario'))
    duration = models.FloatField(default=0.0, verbose_name=_('Duración (segundos)'))
    file_size = models.PositiveIntegerField(default=0, verbose_name=_('Tamaño de archivo (bytes)'))
    
    # Metadatos del archivo
    file_format = models.CharField(max_length=10, blank=True, verbose_name=_('Formato'))
    sample_rate = models.PositiveIntegerField(default=0, verbose_name=_('Tasa de muestreo (Hz)'))
    channels = models.PositiveSmallIntegerField(default=0, verbose_name=_('Canales'))
    
    class Meta:
        verbose_name = _('Archivo de audio')
        verbose_name_plural = _('Archivos de audio')
        ordering = ['-created_at']
    
    def __str__(self):
        return self.title


class Transcription(BaseModel):
    """Modelo para almacenar transcripciones de audio"""
    LANGUAGE_CHOICES = [
        ('es-ES', _('Español')),
        ('en-US', _('Inglés')),
        ('fr-FR', _('Francés')),
        ('de-DE', _('Alemán')),
        ('it-IT', _('Italiano')),
    ]
    
    STATUS_CHOICES = [
        ('pending', _('Pendiente')),
        ('processing', _('Procesando')),
        ('completed', _('Completado')),
        ('failed', _('Fallido')),
    ]
    
    audio_file = models.ForeignKey(AudioFile, on_delete=models.CASCADE, related_name='transcriptions', verbose_name=_('Archivo de audio'))
    text = models.TextField(blank=True, verbose_name=_('Texto transcrito'))
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='transcriptions', verbose_name=_('Usuario'))
    language = models.CharField(max_length=5, choices=LANGUAGE_CHOICES, default='es-ES', verbose_name=_('Idioma'))
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', verbose_name=_('Estado'))
    word_count = models.PositiveIntegerField(default=0, verbose_name=_('Conteo de palabras'))
    duration = models.FloatField(default=0.0, verbose_name=_('Duración procesada (segundos)'))
    error_message = models.TextField(blank=True, verbose_name=_('Mensaje de error'))
    
    # Configuración usada
    vad_enabled = models.BooleanField(default=True, verbose_name=_('Detección de actividad de voz (VAD)'))
    noise_reduction = models.BooleanField(default=True, verbose_name=_('Reducción de ruido'))
    
    class Meta:
        verbose_name = _('Transcripción')
        verbose_name_plural = _('Transcripciones')
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Transcripción de {self.audio_file.title}"


class Translation(BaseModel):
    """Modelo para almacenar traducciones de transcripciones"""
    LANGUAGE_CHOICES = [
        ('es', _('Español')),
        ('en', _('Inglés')),
        ('fr', _('Francés')),
        ('de', _('Alemán')),
        ('it', _('Italiano')),
    ]
    
    transcription = models.ForeignKey(Transcription, on_delete=models.CASCADE, related_name='translations', verbose_name=_('Transcripción'))
    text = models.TextField(blank=True, verbose_name=_('Texto traducido'))
    source_language = models.CharField(max_length=5, choices=LANGUAGE_CHOICES, verbose_name=_('Idioma origen'))
    target_language = models.CharField(max_length=5, choices=LANGUAGE_CHOICES, verbose_name=_('Idioma destino'))
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='translations', verbose_name=_('Usuario'))
    
    class Meta:
        verbose_name = _('Traducción')
        verbose_name_plural = _('Traducciones')
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Traducción de {self.transcription.audio_file.title} ({self.source_language} → {self.target_language})"


class UserProfile(models.Model):
    """Modelo para perfiles de usuario extendidos"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile', verbose_name=_('Usuario'))
    profile_picture = models.ImageField(upload_to='profile_pictures/', blank=True, null=True, verbose_name=_('Foto de perfil'))
    api_usage_count = models.PositiveIntegerField(default=0, verbose_name=_('Conteo de uso de API'))
    total_audio_duration = models.FloatField(default=0.0, verbose_name=_('Duración total de audio (segundos)'))
    total_words_transcribed = models.PositiveIntegerField(default=0, verbose_name=_('Total de palabras transcritas'))
    
    class Meta:
        verbose_name = _('Perfil de usuario')
        verbose_name_plural = _('Perfiles de usuario')
    
    def __str__(self):
        return f"Perfil de {self.user.username}"


# Modelo para configuración de mensaje de contacto
class Contacto(BaseModel):
    """Modelo para mensajes de contacto"""
    nombre = models.CharField(max_length=100, verbose_name=_('Nombre'))
    email = models.EmailField(verbose_name=_('Email'))
    asunto = models.CharField(max_length=200, verbose_name=_('Asunto'))
    mensaje = models.TextField(verbose_name=_('Mensaje'))
    leido = models.BooleanField(default=False, verbose_name=_('Leído'))
    
    class Meta:
        verbose_name = _('Contacto')
        verbose_name_plural = _('Contactos')
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.nombre} - {self.asunto}"