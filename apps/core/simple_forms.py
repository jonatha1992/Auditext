from django import forms
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError


def validate_audio_file(value):
    """Validador personalizado para archivos de audio"""
    import os
    
    # Validar extensión
    valid_extensions = ['.mp3', '.wav', '.flac', '.ogg', '.m4a', '.mp4', '.aac', '.opus']
    ext = os.path.splitext(value.name)[1].lower()
    
    if ext not in valid_extensions:
        raise ValidationError(
            _('Formato de archivo no soportado. Use: %(extensions)s'),
            params={'extensions': ', '.join(valid_extensions)},
        )
    
    # Validar tamaño (100MB max)
    if value.size > 100 * 1024 * 1024:
        raise ValidationError(
            _('El archivo es demasiado grande. Tamaño máximo: 100MB')
        )


class SimpleAudioUploadForm(forms.Form):
    """Formulario simple para subir y transcribir audio sin base de datos"""
    
    LANGUAGE_CHOICES = [
        ('es-ES', _('Español')),
        ('en-US', _('Inglés')),
        ('fr-FR', _('Francés')),
        ('de-DE', _('Alemán')),
        ('it-IT', _('Italiano')),
    ]
    
    title = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control', 
            'placeholder': _('Título del archivo (ej: Reunión del 15 de enero)')
        }),
        label=_('Título')
    )
    
    file = forms.FileField(
        validators=[validate_audio_file],
        widget=forms.FileInput(attrs={
            'class': 'form-control', 
            'accept': 'audio/*,.mp3,.wav,.flac,.ogg,.m4a,.mp4,.aac,.opus'
        }),
        label=_('Archivo de audio'),
        help_text=_('Formatos soportados: MP3, WAV, OGG, FLAC, M4A, MP4, AAC, OPUS (máx. 100MB)')
    )
    
    language = forms.ChoiceField(
        choices=LANGUAGE_CHOICES,
        initial='es-ES',
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_('Idioma del audio'),
        help_text=_('Selecciona el idioma del audio para mejorar la precisión')
    )
    
    vad_enabled = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        label=_('Detección de actividad de voz'),
        help_text=_('Activa la detección automática de voz para mejorar la precisión')
    )
    
    noise_reduction = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        label=_('Reducción de ruido'),
        help_text=_('Reduce el ruido de fondo del audio')
    )

    def clean(self):
        cleaned_data = super().clean()
        title = cleaned_data.get('title')
        file = cleaned_data.get('file')
        
        # Si no se proporciona título, usar el nombre del archivo
        if not title and file:
            import os
            name = os.path.splitext(file.name)[0]
            cleaned_data['title'] = name
        
        return cleaned_data