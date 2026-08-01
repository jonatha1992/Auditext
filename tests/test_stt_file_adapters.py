import contextlib
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from infrastructure.services import stt_file_adapters
from infrastructure.services.stt_file_adapters import SttEngineUnavailable

SAMPLE_RATE = 8000


def write_silent_wav(path: Path, seconds: float) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b"\x00\x00" * int(SAMPLE_RATE * seconds))


@contextlib.contextmanager
def temp_wav(seconds: float):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "audio.wav"
        write_silent_wav(path, seconds)
        yield path


class GoogleChunkingTests(unittest.TestCase):
    """Long audio must be split: the free endpoint truncates a single request."""

    def test_splits_long_audio_into_expected_chunks(self):
        # 110 s at a 50 s chunk size -> 50 + 50 + 10.
        with temp_wav(110) as path, patch(
            "speech_recognition.Recognizer.recognize_google", return_value="texto"
        ):
            text, meta = stt_file_adapters.transcribe_google(path)

        self.assertEqual(meta["chunks"], 3)
        self.assertEqual(meta["chunk_seconds"], stt_file_adapters.GOOGLE_CHUNK_SECONDS)
        self.assertEqual(text, "texto texto texto")

    def test_short_audio_is_a_single_chunk(self):
        with temp_wav(5) as path, patch(
            "speech_recognition.Recognizer.recognize_google", return_value="hola"
        ):
            _text, meta = stt_file_adapters.transcribe_google(path)
        self.assertEqual(meta["chunks"], 1)

    def test_unintelligible_chunks_are_skipped_not_counted_as_failures(self):
        import speech_recognition as sr

        with temp_wav(60) as path, patch(
            "speech_recognition.Recognizer.recognize_google",
            side_effect=sr.UnknownValueError(),
        ):
            text, meta = stt_file_adapters.transcribe_google(path)

        self.assertEqual(text, "")
        self.assertEqual(meta["failed_chunks"], 0)
        self.assertEqual(meta["chunks"], 2)

    def test_rejected_chunks_are_reported(self):
        import speech_recognition as sr

        with temp_wav(60) as path, patch(
            "speech_recognition.Recognizer.recognize_google",
            side_effect=sr.RequestError("cuota"),
        ):
            _text, meta = stt_file_adapters.transcribe_google(path)

        self.assertEqual(meta["failed_chunks"], 2)

    def test_missing_file_raises_typed_error(self):
        with self.assertRaises(SttEngineUnavailable):
            stt_file_adapters.transcribe_google("no-existe.wav")


@contextlib.contextmanager
def fake_windows_process(first_line, stdout_lines=()):
    yield SimpleNamespace(stdout=iter(stdout_lines)), first_line


class WindowsAdapterTests(unittest.TestCase):
    def test_missing_language_pack_raises_instead_of_returning_empty(self):
        # A silent "" would read as a fast, empty transcription in the benchmark.
        with temp_wav(1) as path, patch.object(
            stt_file_adapters,
            "windows_stt_process",
            lambda *a, **k: fake_windows_process("ERROR_NO_RECOGNIZER"),
        ):
            with self.assertRaises(SttEngineUnavailable):
                stt_file_adapters.transcribe_windows(path)

    def test_engine_error_raises(self):
        with temp_wav(1) as path, patch.object(
            stt_file_adapters,
            "windows_stt_process",
            lambda *a, **k: fake_windows_process("ERROR:algo falló"),
        ):
            with self.assertRaises(SttEngineUnavailable):
                stt_file_adapters.transcribe_windows(path)

    def test_joins_recognized_phrases(self):
        with temp_wav(1) as path, patch.object(
            stt_file_adapters,
            "windows_stt_process",
            lambda *a, **k: fake_windows_process(
                "READY:es-ES", ["hola\n", "  \n", "mundo\n"]
            ),
        ):
            text, meta = stt_file_adapters.transcribe_windows(path)

        self.assertEqual(text, "hola mundo")
        self.assertEqual(meta["phrases"], 2)
        self.assertEqual(meta["language"], "es-ES")


if __name__ == "__main__":
    unittest.main()
