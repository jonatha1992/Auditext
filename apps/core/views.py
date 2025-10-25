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

# =============================================================================
# ⚠️  CRITICAL SECURITY WARNING - CSRF PROTECTION DISABLED ⚠️
# =============================================================================
# The @csrf_exempt decorator below DISABLES Cross-Site Request Forgery protection.
# This creates a SERIOUS SECURITY VULNERABILITY that allows malicious websites
# to make unauthorized requests to this endpoint on behalf of authenticated users.
#
# CURRENT RISK LEVEL: HIGH
# - Any website can submit requests to this endpoint
# - No authentication or authorization is implemented
# - No rate limiting to prevent abuse
# - File uploads are not properly validated beyond basic checks
#
# ❌ THIS IS ONLY ACCEPTABLE FOR:
#    - Local development and testing
#    - Proof-of-concept/demo applications
#    - Internal tools behind a secure network
#
# ✅ BEFORE PRODUCTION DEPLOYMENT, YOU MUST:
#    1. Remove the @csrf_exempt decorator completely
#    2. Implement proper authentication (Options: JWT tokens, Django Session Auth, API Keys)
#    3. Re-enable CSRF protection for all POST/PUT/DELETE endpoints
#    4. Add rate limiting (e.g., django-ratelimit, throttling middleware)
#    5. Implement file upload validation (file type, size, content verification)
#    6. Add request logging and monitoring for security events
#    7. Use HTTPS in production to encrypt data in transit
#    8. Configure CORS properly if API is accessed from web frontend
#
# REFERENCES:
#    - Django CSRF Protection: https://docs.djangoproject.com/en/stable/ref/csrf/
#    - OWASP CSRF Prevention: https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html
#    - Django REST Framework Authentication: https://www.django-rest-framework.org/api-guide/authentication/
# =============================================================================
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