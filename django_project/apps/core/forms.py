from django import forms
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError

from .models import AudioFile, Transcription, Translation, UserProfile, Contacto


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


class AudioFileUploadForm(forms.ModelForm):
    """Formulario para subir archivos de audio"""
    
    class Meta:
        model = AudioFile
        fields = ['title', 'file']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'form-control', 
                'placeholder': _('Título del archivo (ej: Reunión del 15 de enero)')
            }),
            'file': forms.FileInput(attrs={
                'class': 'form-control', 
                'accept': 'audio/*,.mp3,.wav,.flac,.ogg,.m4a,.mp4,.aac,.opus'
            }),
        }
        help_texts = {
            'file': _('Formatos soportados: MP3, WAV, OGG, FLAC, M4A, MP4, AAC, OPUS (máx. 100MB)')
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['file'].validators.append(validate_audio_file)
        
        # Si no se proporciona título, usar el nombre del archivo
        if 'file' in self.data and not self.data.get('title'):
            file = self.files.get('file')
            if file:
                import os
                name = os.path.splitext(file.name)[0]
                self.initial['title'] = name


class TranscriptionForm(forms.ModelForm):
    """Formulario para iniciar una transcripción"""
    
    class Meta:
        model = Transcription
        fields = ['language', 'vad_enabled', 'noise_reduction']
        widgets = {
            'language': forms.Select(attrs={'class': 'form-select'}),
            'vad_enabled': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'noise_reduction': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        help_texts = {
            'language': _('Selecciona el idioma del audio'),
            'vad_enabled': _('Activa la detección automática de voz para mejorar la precisión'),
            'noise_reduction': _('Reduce el ruido de fondo'),
        }


class TranslationForm(forms.ModelForm):
    """Formulario para iniciar una traducción"""
    
    class Meta:
        model = Translation
        fields = ['target_language']
        widgets = {
            'target_language': forms.Select(attrs={'class': 'form-select'}),
        }
        help_texts = {
            'target_language': _('Selecciona el idioma al que deseas traducir'),
        }


class ContactForm(forms.ModelForm):
    """Formulario de contacto"""
    
    class Meta:
        model = Contacto
        fields = ['nombre', 'email', 'asunto', 'mensaje']
        widgets = {
            'nombre': forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('Tu nombre')}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': _('tu.email@ejemplo.com')}),
            'asunto': forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('Asunto del mensaje')}),
            'mensaje': forms.Textarea(attrs={'class': 'form-control', 'rows': 6, 'placeholder': _('Escribe tu mensaje aquí...')}),
        }


class UserProfileForm(forms.ModelForm):
    """Formulario para editar el perfil de usuario"""
    
    first_name = forms.CharField(
        max_length=30, 
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        label=_('Nombre')
    )
    
    last_name = forms.CharField(
        max_length=30, 
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        label=_('Apellido')
    )
    
    email = forms.EmailField(
        max_length=254,
        required=True,
        widget=forms.EmailInput(attrs={'class': 'form-control'}),
        label=_('Email')
    )
    
    class Meta:
        model = UserProfile
        fields = ['profile_picture']
        widgets = {
            'profile_picture': forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            user = self.instance.user
            self.fields['first_name'].initial = user.first_name
            self.fields['last_name'].initial = user.last_name
            self.fields['email'].initial = user.email
    
    def save(self, commit=True):
        profile = super().save(commit=False)
        user = profile.user
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        user.email = self.cleaned_data['email']
        user.save()
        if commit:
            profile.save()
        return profile
