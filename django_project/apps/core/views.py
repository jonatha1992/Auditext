import os
from django.shortcuts import render, redirect, get_object_or_404
from django.views.generic import TemplateView, ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
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
from .tasks import process_transcription, process_translation


class HomeView(TemplateView):
    """Vista para la página principal"""
    template_name = 'core/index.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo'] = 'AudioText'
        context['mensaje'] = 'Transcribe y traduce audio de manera inteligente y eficiente'
        return context


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


class DashboardView(LoginRequiredMixin, TemplateView):
    """Vista del panel de control del usuario"""
    template_name = 'core/dashboard.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo'] = 'Panel de Control'
        
        # Obtener estadísticas del usuario
        user = self.request.user
        profile, created = UserProfile.objects.get_or_create(user=user)
        
        context['audio_files_count'] = AudioFile.objects.filter(user=user).count()
        context['transcriptions_count'] = Transcription.objects.filter(user=user).count()
        context['translations_count'] = Translation.objects.filter(user=user).count()
        
        # Últimos archivos procesados
        context['recent_audio_files'] = AudioFile.objects.filter(user=user).order_by('-created_at')[:5]
        context['recent_transcriptions'] = Transcription.objects.filter(user=user).order_by('-created_at')[:5]
        
        # Estadísticas generales
        context['total_duration'] = profile.total_audio_duration
        context['total_words'] = profile.total_words_transcribed
        
        return context


class AudioFileListView(LoginRequiredMixin, ListView):
    """Vista para listar los archivos de audio del usuario"""
    model = AudioFile
    template_name = 'core/audio_list.html'
    context_object_name = 'audio_files'
    paginate_by = 10
    
    def get_queryset(self):
        return AudioFile.objects.filter(user=self.request.user).order_by('-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo'] = 'Mis Archivos de Audio'
        return context


class AudioFileDetailView(LoginRequiredMixin, DetailView):
    """Vista de detalle para un archivo de audio"""
    model = AudioFile
    template_name = 'core/audio_detail.html'
    context_object_name = 'audio'
    
    def get_queryset(self):
        return AudioFile.objects.filter(user=self.request.user)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        audio = self.get_object()
        context['titulo'] = f'Detalles: {audio.title}'
        context['transcriptions'] = audio.transcriptions.all().order_by('-created_at')
        return context


class AudioFileUploadView(LoginRequiredMixin, CreateView):
    """Vista para subir un nuevo archivo de audio"""
    model = AudioFile
    form_class = AudioFileUploadForm
    template_name = 'core/audio_upload.html'
    success_url = reverse_lazy('core:audio_list')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo'] = 'Subir Audio'
        return context
    
    def form_valid(self, form):
        form.instance.user = self.request.user
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


class AudioFileDeleteView(LoginRequiredMixin, DeleteView):
    """Vista para eliminar un archivo de audio"""
    model = AudioFile
    template_name = 'core/audio_confirm_delete.html'
    success_url = reverse_lazy('core:audio_list')
    
    def get_queryset(self):
        return AudioFile.objects.filter(user=self.request.user)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        audio = self.get_object()
        context['titulo'] = f'Eliminar: {audio.title}'
        return context
    
    def delete(self, request, *args, **kwargs):
        messages.success(request, _('Archivo de audio eliminado correctamente.'))
        return super().delete(request, *args, **kwargs)


class TranscriptionCreateView(LoginRequiredMixin, CreateView):
    """Vista para crear una nueva transcripción"""
    model = Transcription
    form_class = TranscriptionForm
    template_name = 'core/transcription_create.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        audio_id = self.kwargs.get('audio_id')
        audio = get_object_or_404(AudioFile, id=audio_id, user=self.request.user)
        context['audio'] = audio
        context['titulo'] = f'Transcribir: {audio.title}'
        return context
    
    def form_valid(self, form):
        audio_id = self.kwargs.get('audio_id')
        audio = get_object_or_404(AudioFile, id=audio_id, user=self.request.user)
        
        form.instance.audio_file = audio
        form.instance.user = self.request.user
        form.instance.status = 'pending'
        
        response = super().form_valid(form)
        
        # Iniciar tarea de transcripción
        process_transcription.delay(self.object.id)
        
        messages.success(self.request, _('Proceso de transcripción iniciado.'))
        return response
    
    def get_success_url(self):
        return reverse_lazy('core:audio_detail', kwargs={'pk': self.object.audio_file.id})


class TranscriptionDetailView(LoginRequiredMixin, DetailView):
    """Vista de detalle para una transcripción"""
    model = Transcription
    template_name = 'core/transcription_detail.html'
    context_object_name = 'transcription'
    
    def get_queryset(self):
        return Transcription.objects.filter(user=self.request.user)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        transcription = self.get_object()
        context['titulo'] = f'Transcripción: {transcription.audio_file.title}'
        context['translations'] = transcription.translations.all().order_by('-created_at')
        return context


class TranslationCreateView(LoginRequiredMixin, CreateView):
    """Vista para crear una nueva traducción"""
    model = Translation
    form_class = TranslationForm
    template_name = 'core/translation_create.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        transcription_id = self.kwargs.get('transcription_id')
        transcription = get_object_or_404(Transcription, id=transcription_id, user=self.request.user)
        context['transcription'] = transcription
        context['titulo'] = f'Traducir: {transcription.audio_file.title}'
        return context
    
    def form_valid(self, form):
        transcription_id = self.kwargs.get('transcription_id')
        transcription = get_object_or_404(Transcription, id=transcription_id, user=self.request.user)
        
        form.instance.transcription = transcription
        form.instance.user = self.request.user
        form.instance.source_language = transcription.language[:2]  # Extract 'es' from 'es-ES'
        
        response = super().form_valid(form)
        
        # Iniciar tarea de traducción
        process_translation.delay(self.object.id)
        
        messages.success(self.request, _('Proceso de traducción iniciado.'))
        return response
    
    def get_success_url(self):
        return reverse_lazy('core:transcription_detail', kwargs={'pk': self.object.transcription.id})


class TranslationDetailView(LoginRequiredMixin, DetailView):
    """Vista de detalle para una traducción"""
    model = Translation
    template_name = 'core/translation_detail.html'
    context_object_name = 'translation'
    
    def get_queryset(self):
        return Translation.objects.filter(user=self.request.user)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        translation = self.get_object()
        context['titulo'] = f'Traducción: {translation.transcription.audio_file.title}'
        return context


class UserProfileView(LoginRequiredMixin, UpdateView):
    """Vista para ver y editar el perfil de usuario"""
    model = UserProfile
    form_class = UserProfileForm
    template_name = 'core/user_profile.html'
    success_url = reverse_lazy('core:profile')
    
    def get_object(self):
        profile, created = UserProfile.objects.get_or_create(user=self.request.user)
        return profile
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo'] = 'Mi Perfil'
        return context
    
    def form_valid(self, form):
        messages.success(self.request, _('Perfil actualizado correctamente.'))
        return super().form_valid(form)


# Vistas de función para API y operaciones AJAX
@login_required
def check_transcription_status(request, pk):
    """Verificar el estado de una transcripción"""
    transcription = get_object_or_404(Transcription, pk=pk, user=request.user)
    return JsonResponse({
        'status': transcription.status,
        'word_count': transcription.word_count,
        'completed': transcription.status == 'completed',
        'failed': transcription.status == 'failed',
        'error_message': transcription.error_message
    })


@login_required
def check_translation_status(request, pk):
    """Verificar el estado de una traducción"""
    translation = get_object_or_404(Translation, pk=pk, user=request.user)
    return JsonResponse({
        'completed': translation.text != '',
        'source_language': translation.source_language,
        'target_language': translation.target_language
    })


@login_required
def download_transcription(request, pk):
    """Descargar texto de transcripción como archivo"""
    transcription = get_object_or_404(Transcription, pk=pk, user=request.user)
    
    response = HttpResponse(transcription.text, content_type='text/plain')
    filename = f"transcripcion_{transcription.audio_file.title}_{timezone.now().strftime('%Y%m%d')}.txt"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    return response


@login_required
def download_translation(request, pk):
    """Descargar texto de traducción como archivo"""
    translation = get_object_or_404(Translation, pk=pk, user=request.user)
    
    response = HttpResponse(translation.text, content_type='text/plain')
    filename = f"traduccion_{translation.transcription.audio_file.title}_{translation.target_language}_{timezone.now().strftime('%Y%m%d')}.txt"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    return response