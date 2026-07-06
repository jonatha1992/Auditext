import os
import sys
import unittest
from core.interfaces.interfaces import TranscriptionService
from infrastructure.services.onnx_transcriber import OfflineTranscriptionService

class TestOfflineTranscriptionService(unittest.TestCase):
    def setUp(self):
        # We search for the test audio relative to the test file location
        self.audio_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "Audios", "PRUEBA (2).wav")
        )
        # Use a tiny model for fast testing
        self.service = OfflineTranscriptionService(model_size="tiny")

    def test_transcribe_local_file_without_torch(self):
        # Double check that the test file exists
        self.assertTrue(os.path.exists(self.audio_path), f"Audio file not found: {self.audio_path}")
        
        # Ensure 'torch' is mocked as None in sys.modules to simulate its absence
        sys.modules["torch"] = None

        # Run transcription
        progress_calls = []
        def progress_cb(fraction):
            progress_calls.append(fraction)

        text = self.service.transcribe_file(
            file_path=self.audio_path,
            language="es",
            translate=False,
            progress_cb=progress_cb
        )

        # Check results
        self.assertIsNotNone(text)
        self.assertGreater(len(text), 0)
        self.assertIn("Hola", text)  # The audio says "Hola amigos"
        
        # Verify progress callback was triggered
        self.assertGreater(len(progress_calls), 0)
        self.assertEqual(progress_calls[-1], 1.0)

    def test_transcribe_file_segments_without_torch(self):
        self.assertTrue(os.path.exists(self.audio_path), f"Audio file not found: {self.audio_path}")
        sys.modules["torch"] = None

        progress_calls = []
        def progress_cb(fraction):
            progress_calls.append(fraction)

        segments = self.service.transcribe_file_segments(
            file_path=self.audio_path,
            language="es",
            translate=False,
            progress_cb=progress_cb
        )

        self.assertIsNotNone(segments)
        self.assertGreater(len(segments), 0)
        start, end, text = segments[0]
        self.assertIsInstance(start, float)
        self.assertIsInstance(end, float)
        self.assertIsInstance(text, str)
        self.assertTrue(any("Hola" in t for _, _, t in segments))
        self.assertGreater(len(progress_calls), 0)
        self.assertEqual(progress_calls[-1], 1.0)

if __name__ == "__main__":
    unittest.main()
