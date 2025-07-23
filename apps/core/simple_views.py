import os
import tempfile
from django.shortcuts import render, redirect
from django.views.generic import TemplateView
from django.urls import reverse_lazy
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.conf import settings  
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile

from .simple_forms import SimpleAudioUploadForm
from .audio_processing import transcribe_audio, extract_audio_metadata


class SimpleHomeView(TemplateView):
    """Vista para la página principal que redirige directamente a subir audio"""
    
    def get(self, request, *args, **kwargs):
        # Redirigir directamente a la página de transcripción simple
        return redirect('core:simple_transcribe')


class SimpleTranscribeView(TemplateView):
    """Vista simple para transcribir audio sin base de datos"""
    template_name = 'core/simple_transcribe.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo'] = 'Transcribir Audio'
        context['form'] = SimpleAudioUploadForm()
        return context
    
    def post(self, request, *args, **kwargs):
        form = SimpleAudioUploadForm(request.POST, request.FILES)
        
        if not form.is_valid():
            context = self.get_context_data(**kwargs)
            context['form'] = form
            return render(request, self.template_name, context)
        
        # Obtener datos del formulario
        title = form.cleaned_data['title']
        audio_file = form.cleaned_data['file']
        language = form.cleaned_data['language']
        vad_enabled = form.cleaned_data['vad_enabled']
        noise_reduction = form.cleaned_data['noise_reduction']
        
        try:
            # Guardar archivo temporalmente
            temp_file_path = None
            with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(audio_file.name)[1]) as temp_file:
                for chunk in audio_file.chunks():
                    temp_file.write(chunk)
                temp_file_path = temp_file.name
            
            # Extraer metadatos del audio
            metadata = extract_audio_metadata(temp_file_path)
            
            # Realizar transcripción
            transcription_result = transcribe_audio(
                temp_file_path,
                language=language[:2],  # Usar solo el código de idioma sin país
                use_vad=vad_enabled,
                reduce_noise=noise_reduction
            )
            
            # Limpiar archivo temporal
            if temp_file_path and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            
            if transcription_result['success']:
                # Mostrar resultados
                context = {
                    'titulo': 'Resultado de Transcripción',
                    'result': {
                        'title': title,
                        'filename': audio_file.name,
                        'transcription': transcription_result['text'],
                        'word_count': transcription_result['word_count'],
                        'duration_processed': transcription_result['duration_processed'],
                        'language': language,
                        'metadata': metadata,
                        'vad_enabled': vad_enabled,
                        'noise_reduction': noise_reduction,
                    }
                }
                return render(request, 'core/transcription_result.html', context)
            else:
                messages.error(request, f"Error en la transcripción: {transcription_result.get('error', 'Error desconocido')}")
                
        except Exception as e:
            # Limpiar archivo temporal en caso de error
            if temp_file_path and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            messages.error(request, f"Error procesando el archivo: {str(e)}")
        
        # Si hay error, mostrar el formulario nuevamente
        context = self.get_context_data(**kwargs)
        context['form'] = form
        return render(request, self.template_name, context)


class SimpleAboutView(TemplateView):
    """Vista para la página Sobre Nosotros"""
    template_name = 'core/sobre_nosotros.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo'] = 'Sobre AudioText'
        return context


# Vista de función para descargar transcripción
def download_transcription_text(request):
    """Descargar texto de transcripción desde la sesión"""
    transcription_text = request.session.get('last_transcription_text', '')
    filename = request.session.get('last_transcription_filename', 'transcripcion')
    
    if not transcription_text:
        messages.error(request, 'No hay transcripción disponible para descargar.')
        return redirect('core:simple_transcribe')
    
    response = HttpResponse(transcription_text, content_type='text/plain; charset=utf-8')
    safe_filename = f"transcripcion_{filename}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.txt"
    response['Content-Disposition'] = f'attachment; filename="{safe_filename}"'
    
    return response