from django.contrib import admin
from .models import AudioFile, Transcription, Translation, UserProfile, Contacto


@admin.register(AudioFile)
class AudioFileAdmin(admin.ModelAdmin):
    """Administración de archivos de audio"""
    list_display = ('title', 'user', 'file_format', 'duration', 'file_size', 'created_at')
    list_filter = ('file_format', 'created_at')
    search_fields = ('title', 'user__username')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    
    fieldsets = (
        ('Información básica', {
            'fields': ('title', 'user', 'file')
        }),
        ('Metadatos del archivo', {
            'fields': ('duration', 'file_size', 'file_format', 'sample_rate', 'channels')
        }),
    )


@admin.register(Transcription)
class TranscriptionAdmin(admin.ModelAdmin):
    """Administración de transcripciones"""
    list_display = ('audio_file', 'user', 'language', 'status', 'word_count', 'created_at')
    list_filter = ('language', 'status', 'vad_enabled', 'noise_reduction', 'created_at')
    search_fields = ('audio_file__title', 'user__username', 'text')
    list_editable = ('status',)
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    
    fieldsets = (
        ('Información básica', {
            'fields': ('audio_file', 'user', 'language', 'status')
        }),
        ('Resultado', {
            'fields': ('text', 'word_count', 'duration')
        }),
        ('Configuración', {
            'fields': ('vad_enabled', 'noise_reduction')
        }),
        ('Errores', {
            'fields': ('error_message',),
            'classes': ('collapse',)
        }),
    )


@admin.register(Translation)
class TranslationAdmin(admin.ModelAdmin):
    """Administración de traducciones"""
    list_display = ('transcription', 'source_language', 'target_language', 'user', 'created_at')
    list_filter = ('source_language', 'target_language', 'created_at')
    search_fields = ('transcription__audio_file__title', 'user__username', 'text')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    
    fieldsets = (
        ('Información básica', {
            'fields': ('transcription', 'user', 'source_language', 'target_language')
        }),
        ('Resultado', {
            'fields': ('text',)
        }),
    )


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    """Administración de perfiles de usuario"""
    list_display = ('user', 'api_usage_count', 'total_audio_duration', 'total_words_transcribed')
    search_fields = ('user__username', 'user__email')
    ordering = ('user__username',)
    
    fieldsets = (
        ('Usuario', {
            'fields': ('user', 'profile_picture')
        }),
        ('Estadísticas', {
            'fields': ('api_usage_count', 'total_audio_duration', 'total_words_transcribed')
        }),
    )


@admin.register(Contacto)
class ContactoAdmin(admin.ModelAdmin):
    """Administración de contactos"""
    list_display = ('nombre', 'email', 'asunto', 'leido', 'created_at')
    list_filter = ('leido', 'created_at')
    search_fields = ('nombre', 'email', 'asunto', 'mensaje')
    list_editable = ('leido',)
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('-created_at',)
    
    fieldsets = (
        ('Información del contacto', {
            'fields': ('nombre', 'email', 'asunto')
        }),
        ('Mensaje', {
            'fields': ('mensaje',)
        }),
        ('Estado', {
            'fields': ('leido',)
        }),
        ('Fechas', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )