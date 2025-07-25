from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
import os
import tempfile
import shutil
import json
import time
from django.shortcuts import render
from .forms import AudioUploadForm
from .whisper_utils import transcribe_audio
from django.views.decorators.csrf import csrf_exempt


def test_view(request):
    return HttpResponse("¡Django funciona!")

@csrf_exempt
def transcribe_api(request):
    if request.method == 'POST' and request.FILES.get('audio'):
        audio_file = request.FILES['audio']
        # Obtener modelo de Whisper desde el frontend
        whisper_model = request.POST.get('whisper_model', 'medium')
        with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as temp_file:
            for chunk in audio_file.chunks():
                temp_file.write(chunk)
            temp_file_path = temp_file.name
        try:
            # Pasar el modelo seleccionado al backend
            transcription_turns = transcribe_audio(temp_file_path, whisper_model=whisper_model)
            os.remove(temp_file_path)
            method_used = "VAD (silencios)"
            return JsonResponse({
                'turns': transcription_turns,
                'status': 'completed',
                'method': method_used,
                'message': f'Transcripción completada usando {method_used}: {len(transcription_turns)} turnos detectados'
            })
        except Exception as e:
            os.remove(temp_file_path)
            return JsonResponse({
                'error': f'Error en la transcripción: {str(e)}',
                'status': 'error'
            }, status=500)
    return JsonResponse({'error': 'Invalid request'}, status=400)

def transcribe_view(request):
    transcription = None
    audio_url = None

    if request.method == 'POST':
        form = AudioUploadForm(request.POST, request.FILES)
        if form.is_valid():
            audio_file = form.cleaned_data['audio_file']
            # Guardar archivo temporalmente
            with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(audio_file.name)[1]) as temp_file:
                for chunk in audio_file.chunks():
                    temp_file.write(chunk)
                temp_file_path = temp_file.name

            # Transcribir usando Whisper
            transcription = transcribe_audio(temp_file_path)

            # Guardar el archivo en static temporalmente para reproducirlo
            static_dir = os.path.join(os.path.dirname(__file__), '../../static')
            static_dir = os.path.abspath(static_dir)
            if not os.path.exists(static_dir):
                os.makedirs(static_dir)
            audio_filename = os.path.basename(temp_file_path)
            static_path = os.path.join(static_dir, audio_filename)
            shutil.copyfile(temp_file_path, static_path)
            audio_url = '/static/' + audio_filename
            os.remove(temp_file_path)
    else:
        form = AudioUploadForm()

    return render(request, 'core/transcribe.html', {
        'form': form,
        'transcription': transcription,
        'audio_url': audio_url,
    }) 