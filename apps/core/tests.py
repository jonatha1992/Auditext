from django.test import SimpleTestCase, Client
from unittest.mock import patch
from io import BytesIO
import importlib
import os

class TranscribeAPITest(SimpleTestCase):
    def setUp(self):
        self.client = Client()

    @patch('apps.core.views.transcribe_audio')
    def test_transcribe_no_translation(self, mock_transcribe):
        mock_transcribe.return_value = []
        audio = BytesIO(b'data')
        audio.name = 'test.wav'
        response = self.client.post('/api/transcribe/', {
            'audio': audio,
            'input_lang': 'es',
            'output_lang': 'es'
        })
        self.assertEqual(response.status_code, 200)
        args, kwargs = mock_transcribe.call_args
        self.assertFalse(kwargs['translate'])
        self.assertEqual(kwargs['language'], 'es')

    @patch('apps.core.views.transcribe_audio')
    def test_transcribe_with_translation(self, mock_transcribe):
        mock_transcribe.return_value = []
        audio = BytesIO(b'data')
        audio.name = 'test.wav'
        response = self.client.post('/api/transcribe/', {
            'audio': audio,
            'input_lang': 'es',
            'output_lang': 'en'
        })
        self.assertEqual(response.status_code, 200)
        args, kwargs = mock_transcribe.call_args
        self.assertTrue(kwargs['translate'])
        self.assertEqual(kwargs['language'], 'es')

    def test_transcribe_missing_file(self):
        response = self.client.post('/api/transcribe/', {})
        self.assertEqual(response.status_code, 400)

class WhisperUtilsEnvTest(SimpleTestCase):
    def test_ffmpeg_path_env(self):
        import apps.core.whisper_utils as wu
        os.environ['FFMPEG_PATH'] = '/custom/ffmpeg'
        module = importlib.reload(wu)
        self.assertIn('/custom/ffmpeg', module.os.environ['PATH'])
