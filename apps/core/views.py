import os
from django.shortcuts import render, redirect, get_object_or_404
from django.views.generic import TemplateView, ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.utils import timezone

from .models import AudioFile, Transcription, Translation, UserProfile, Contacto
from .forms import (
    AudioFileUploadForm, 
    TranscriptionForm, 
    TranslationForm, 
    ContactForm, 
    UserProfileForm
)
# Temporary comment out for testing without Celery
# from .tasks import process_transcription, process_translation


class HomeView(TemplateView):
    """Vista para la página principal que redirige directamente a subir audio"""
    
    def get(self, request, *args, **kwargs):
        # Redirigir directamente a la página de subir audio para pruebas rápidas
        return redirect('core:audio_upload')


class AboutView(TemplateView):
    """Vista para la página Sobre Nosotros"""
    template_name = 'core/sobre_nosotros.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo'] = 'Sobre AudioText'
        return context


class ContactView(CreateView):
    """Vista para la página de contacto"""
    template_name = 'core/contacto.html'
    model = Contacto
    form_class = ContactForm
    success_url = reverse_lazy('core:contacto_gracias')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo'] = 'Contacto'
        return context
    
    def form_valid(self, form):
        messages.success(self.request, _('Tu mensaje ha sido enviado. ¡Gracias por contactarnos!'))
        return super().form_valid(form)


class ContactThanksView(TemplateView):
    """Vista de agradecimiento tras enviar contacto"""
    template_name = 'core/contacto_gracias.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo'] = 'Mensaje Enviado'
        return context


class DashboardView(TemplateView):
    """Vista del panel de control - acceso libre para pruebas"""
    template_name = 'core/dashboard.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo'] = 'Panel de Control'
        
        # Obtener estadísticas generales sin filtrar por usuario
        context['audio_files_count'] = AudioFile.objects.count()
        context['transcriptions_count'] = Transcription.objects.count()
        context['translations_count'] = Translation.objects.count()
        
        # Últimos archivos procesados (sin filtrar por usuario)
        context['recent_audio_files'] = AudioFile.objects.order_by('-created_at')[:5]
        context['recent_transcriptions'] = Transcription.objects.order_by('-created_at')[:5]
        
        # Estadísticas generales
        context['total_duration'] = sum(audio.duration for audio in AudioFile.objects.all())
        context['total_words'] = sum(trans.word_count for trans in Transcription.objects.all())
        
        return context


class AudioFileListView(ListView):
    """Vista para listar todos los archivos de audio - acceso libre para pruebas"""
    model = AudioFile
    template_name = 'core/audio_list.html'
    context_object_name = 'audio_files'
    paginate_by = 10
    
    def get_queryset(self):
        return AudioFile.objects.order_by('-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo'] = 'Archivos de Audio'
        return context


class AudioFileDetailView(DetailView):
    """Vista de detalle para un archivo de audio - acceso libre para pruebas"""
    model = AudioFile
    template_name = 'core/audio_detail.html'
    context_object_name = 'audio'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        audio = self.get_object()
        context['titulo'] = f'Detalles: {audio.title}'
        context['transcriptions'] = audio.transcriptions.all().order_by('-created_at')
        return context


class AudioFileUploadView(CreateView):
    """Vista para subir un nuevo archivo de audio - acceso libre para pruebas"""
    model = AudioFile
    form_class = AudioFileUploadForm
    template_name = 'core/audio_upload.html'
    success_url = reverse_lazy('core:audio_list')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo'] = 'Subir Audio'
        return context
    
    def form_valid(self, form):
        # Crear un usuario temporal o usar uno por defecto para las pruebas
        from django.contrib.auth.models import User
        try:
            # Intentar obtener un usuario existente o crear uno temporal
            test_user, created = User.objects.get_or_create(
                username='test_user',
                defaults={
                    'email': 'test@audiotext.com',
                    'first_name': 'Usuario',
                    'last_name': 'de Prueba'
                }
            )
            form.instance.user = test_user
        except:
            # Si hay problemas, usar el primer usuario disponible
            first_user = User.objects.first()
            if first_user:
                form.instance.user = first_user
            
        # Extract and save audio metadata after saving the file
        audio_file = form.save()
        
        try:
            from .audio_processing import extract_audio_metadata
            file_path = audio_file.file.path
            metadata = extract_audio_metadata(file_path)
            
            # Update the audio file with metadata
            audio_file.duration = metadata.get('duration', 0)
            audio_file.file_size = metadata.get('file_size', 0)
            audio_file.file_format = metadata.get('file_format', '')
            audio_file.sample_rate = metadata.get('sample_rate', 0)
            audio_file.channels = metadata.get('channels', 0)
            audio_file.save()
            
            messages.success(self.request, _('Archivo de audio subido y procesado correctamente.'))
        except Exception as e:
            messages.warning(self.request, _('Archivo subido, pero no se pudieron extraer todos los metadatos.'))
            
        return super().form_valid(form)


class AudioFileDeleteView(DeleteView):
    """Vista para eliminar un archivo de audio - acceso libre para pruebas"""
    model = AudioFile
    template_name = 'core/audio_confirm_delete.html'
    success_url = reverse_lazy('core:audio_list')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        audio = self.get_object()
        context['titulo'] = f'Eliminar: {audio.title}'
        return context
    
    def delete(self, request, *args, **kwargs):
        messages.success(request, _('Archivo de audio eliminado correctamente.'))
        return super().delete(request, *args, **kwargs)


class TranscriptionCreateView(CreateView):
    """Vista para crear una nueva transcripción - acceso libre para pruebas"""
    model = Transcription
    form_class = TranscriptionForm
    template_name = 'core/transcription_create.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        audio_id = self.kwargs.get('audio_id')
        audio = get_object_or_404(AudioFile, id=audio_id)
        context['audio'] = audio
        context['titulo'] = f'Transcribir: {audio.title}'
        return context
    
    def form_valid(self, form):
        audio_id = self.kwargs.get('audio_id')
        audio = get_object_or_404(AudioFile, id=audio_id)
        
        form.instance.audio_file = audio
        # Usar el mismo usuario que el archivo de audio
        form.instance.user = audio.user
        form.instance.status = 'pending'
        
        response = super().form_valid(form)
        
        # Temporary comment out Celery task for testing
        # process_transcription.delay(self.object.id)
        
        messages.success(self.request, _('Proceso de transcripción iniciado.'))
        return response
    
    def get_success_url(self):
        return reverse_lazy('core:audio_detail', kwargs={'pk': self.object.audio_file.id})


class TranscriptionDetailView(DetailView):
    """Vista de detalle para una transcripción - acceso libre para pruebas"""
    model = Transcription
    template_name = 'core/transcription_detail.html'
    context_object_name = 'transcription'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        transcription = self.get_object()
        context['titulo'] = f'Transcripción: {transcription.audio_file.title}'
        context['translations'] = transcription.translations.all().order_by('-created_at')
        return context


class TranslationCreateView(CreateView):
    """Vista para crear una nueva traducción - acceso libre para pruebas"""
    model = Translation
    form_class = TranslationForm
    template_name = 'core/translation_create.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        transcription_id = self.kwargs.get('transcription_id')
        transcription = get_object_or_404(Transcription, id=transcription_id)
        context['transcription'] = transcription
        context['titulo'] = f'Traducir: {transcription.audio_file.title}'
        return context
    
    def form_valid(self, form):
        transcription_id = self.kwargs.get('transcription_id')
        transcription = get_object_or_404(Transcription, id=transcription_id)
        
        form.instance.transcription = transcription
        form.instance.user = transcription.user  # Usar el mismo usuario que la transcripción
        form.instance.source_language = transcription.language[:2]  # Extract 'es' from 'es-ES'
        
        response = super().form_valid(form)
        
        # Temporary comment out Celery task for testing
        # process_translation.delay(self.object.id)
        
        messages.success(self.request, _('Proceso de traducción iniciado.'))
        return response
    
    def get_success_url(self):
        return reverse_lazy('core:transcription_detail', kwargs={'pk': self.object.transcription.id})


class TranslationDetailView(DetailView):
    """Vista de detalle para una traducción - acceso libre para pruebas"""
    model = Translation
    template_name = 'core/translation_detail.html'
    context_object_name = 'translation'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        translation = self.get_object()
        context['titulo'] = f'Traducción: {translation.transcription.audio_file.title}'
        return context


class UserProfileView(UpdateView):
    """Vista para ver y editar el perfil de usuario - acceso libre para pruebas"""
    model = UserProfile
    form_class = UserProfileForm
    template_name = 'core/user_profile.html'
    success_url = reverse_lazy('core:profile')
    
    def get_object(self):
        # Obtener o crear un perfil para el usuario de prueba
        from django.contrib.auth.models import User
        try:
            test_user, created = User.objects.get_or_create(
                username='test_user',
                defaults={
                    'email': 'test@audiotext.com',
                    'first_name': 'Usuario',
                    'last_name': 'de Prueba'
                }
            )
            profile, created = UserProfile.objects.get_or_create(user=test_user)
            return profile
        except:
            # Si hay problemas, usar el primer perfil disponible o crear uno nuevo
            first_user = User.objects.first()
            if first_user:
                profile, created = UserProfile.objects.get_or_create(user=first_user)
                return profile
            return None
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo'] = 'Perfil de Usuario'
        return context
    
    def form_valid(self, form):
        messages.success(self.request, _('Perfil actualizado correctamente.'))
        return super().form_valid(form)


# Vistas de función para API y operaciones AJAX - acceso libre para pruebas
def check_transcription_status(request, pk):
    """Verificar el estado de una transcripción"""
    transcription = get_object_or_404(Transcription, pk=pk)
    return JsonResponse({
        'status': transcription.status,
        'word_count': transcription.word_count,
        'completed': transcription.status == 'completed',
        'failed': transcription.status == 'failed',
        'error_message': transcription.error_message
    })


def check_translation_status(request, pk):
    """Verificar el estado de una traducción"""
    translation = get_object_or_404(Translation, pk=pk)
    return JsonResponse({
        'completed': translation.text != '',
        'source_language': translation.source_language,
        'target_language': translation.target_language
    })


def download_transcription(request, pk):
    """Descargar texto de transcripción como archivo"""
    transcription = get_object_or_404(Transcription, pk=pk)
    
    response = HttpResponse(transcription.text, content_type='text/plain')
    filename = f"transcripcion_{transcription.audio_file.title}_{timezone.now().strftime('%Y%m%d')}.txt"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    return response


def download_translation(request, pk):
    """Descargar texto de traducción como archivo"""
    translation = get_object_or_404(Translation, pk=pk)
    
    response = HttpResponse(translation.text, content_type='text/plain')
    filename = f"traduccion_{translation.transcription.audio_file.title}_{translation.target_language}_{timezone.now().strftime('%Y%m%d')}.txt"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    return response