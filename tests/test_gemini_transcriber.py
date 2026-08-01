import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from infrastructure.services import gemini_transcriber
from infrastructure.services.gemini_transcriber import GeminiTranscriptionError


class FakeTypes:
    """Stand-in for google.genai.types — records how the audio was attached."""

    class Part:
        @staticmethod
        def from_bytes(*, data, mime_type):
            return {"inline": True, "bytes": len(data), "mime": mime_type}

    @staticmethod
    def HttpOptions(**kwargs):
        return kwargs

    @staticmethod
    def HttpRetryOptions(**kwargs):
        return kwargs


def install_fake_sdk(client):
    """Patch `from google import genai` / `from google.genai import types`."""
    genai = SimpleNamespace(Client=MagicMock(return_value=client))
    return patch.dict(
        sys.modules,
        {
            "google": SimpleNamespace(genai=genai),
            "google.genai": SimpleNamespace(types=FakeTypes, Client=genai.Client),
        },
    )


def make_client(text="hola mundo", error=None):
    client = MagicMock()
    if error is not None:
        client.models.generate_content.side_effect = error
    else:
        client.models.generate_content.return_value = SimpleNamespace(text=text)
    client.files.upload.return_value = {"uploaded": True}
    return client


class TransportSelectionTests(unittest.TestCase):
    def setUp(self):
        self.audio = MagicMock()
        self.audio.is_file.return_value = True
        self.audio.read_bytes.return_value = b"\x00" * 32

    def _transcribe(self, size_bytes, client):
        self.audio.stat.return_value = SimpleNamespace(st_size=size_bytes)
        with install_fake_sdk(client), patch.object(
            gemini_transcriber, "Path", return_value=self.audio
        ), patch.object(gemini_transcriber.gemini_keys, "pool") as pool:
            pool.is_configured.return_value = True
            pool.current.side_effect = ["key-1"]
            return gemini_transcriber.transcribe_file("audio.wav")

    def test_small_file_goes_inline(self):
        client = make_client()
        _text, meta = self._transcribe(1024, client)
        self.assertEqual(meta["transport"], "inline")
        client.files.upload.assert_not_called()

    def test_large_file_uses_files_api(self):
        # Inline bytes would blow past the request size cap.
        client = make_client()
        _text, meta = self._transcribe(
            gemini_transcriber.INLINE_LIMIT_BYTES + 1, client
        )
        self.assertEqual(meta["transport"], "files_api")
        client.files.upload.assert_called_once()


class FailureTests(unittest.TestCase):
    def setUp(self):
        self.audio = MagicMock()
        self.audio.is_file.return_value = True
        self.audio.read_bytes.return_value = b"\x00" * 32
        self.audio.stat.return_value = SimpleNamespace(st_size=1024)

    def test_empty_text_is_not_reported_as_success(self):
        client = make_client(text="   ")
        with install_fake_sdk(client), patch.object(
            gemini_transcriber, "Path", return_value=self.audio
        ), patch.object(gemini_transcriber.gemini_keys, "pool") as pool:
            pool.is_configured.return_value = True
            pool.current.side_effect = ["key-1"]
            with self.assertRaises(GeminiTranscriptionError):
                gemini_transcriber.transcribe_file("audio.wav")

    def test_quota_error_rotates_to_the_next_key(self):
        client = make_client(error=RuntimeError("429 RESOURCE_EXHAUSTED"))
        with install_fake_sdk(client), patch.object(
            gemini_transcriber, "Path", return_value=self.audio
        ), patch.object(gemini_transcriber.gemini_keys, "pool") as pool:
            pool.is_configured.return_value = True
            pool.current.side_effect = ["key-1", "key-2"]
            pool.is_quota_error.return_value = True
            pool.mark_exhausted.side_effect = ["key-2", None]
            with self.assertRaises(GeminiTranscriptionError):
                gemini_transcriber.transcribe_file("audio.wav")

        # Both keys were attempted before giving up.
        self.assertEqual(client.models.generate_content.call_count, 2)

    def test_missing_file_fails_fast(self):
        missing = MagicMock()
        missing.is_file.return_value = False
        with patch.object(gemini_transcriber, "Path", return_value=missing):
            with self.assertRaises(GeminiTranscriptionError):
                gemini_transcriber.transcribe_file("no-existe.wav")


if __name__ == "__main__":
    unittest.main()
