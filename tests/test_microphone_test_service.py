import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

from infrastructure.services.microphone_test import (
    MicrophoneTestService,
    choose_preferred_microphone_label,
    load_preferred_microphone,
    save_preferred_microphone,
)


class _Stream:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, frames):
        return np.full((frames, 1), 0.2, dtype=np.float32), False


class MicrophoneTestServiceTests(unittest.TestCase):
    def test_lists_physical_microphones_without_host_api_duplicates(self):
        soundcard_module = mock.Mock()
        soundcard_module.all_microphones.return_value = [
            SimpleNamespace(name="Micrófono (PD200X Podcast Microphone)"),
            SimpleNamespace(name="Micrófono (C505e HD Webcam)"),
        ]
        sounddevice_module = mock.Mock()

        service = MicrophoneTestService(
            soundcard_module=soundcard_module,
            sounddevice_module=sounddevice_module,
        )

        self.assertEqual(
            service.list_microphones(),
            [
                "Micrófono (PD200X Podcast Microphone)",
                "Micrófono (C505e HD Webcam)",
            ],
        )

    def test_probe_records_and_transcribes_selected_microphone(self):
        soundcard_module = mock.Mock()
        sounddevice_module = mock.Mock()
        sounddevice_module.query_devices.return_value = [
            {
                "name": "Micrófono (PD200X Podcast Microphone)",
                "max_input_channels": 1,
                "default_samplerate": 16000,
            }
        ]
        sounddevice_module.default.device = (0, 0)
        sounddevice_module.InputStream.return_value = _Stream()
        transcriber = mock.Mock()
        transcriber.transcribe_array.return_value = (["Probando uno dos tres."], "es")
        service = MicrophoneTestService(
            transcriber=transcriber,
            soundcard_module=soundcard_module,
            sounddevice_module=sounddevice_module,
        )

        result = service.probe(
            "Micrófono (PD200X Podcast Microphone)", duration_seconds=1
        )

        self.assertEqual(result.transcription, "Probando uno dos tres.")
        self.assertGreater(result.mean_level, 0.1)
        self.assertEqual(result.sample_rate, 16000)
        self.assertTrue(result.audio.size)

    def test_preferred_microphone_round_trip(self):
        repository = mock.Mock()
        repository.get_setting.return_value = "Micrófono (PD200X Podcast Microphone)"

        save_preferred_microphone(repository, "Micrófono (PD200X Podcast Microphone)")
        loaded = load_preferred_microphone(repository)

        repository.set_setting.assert_called_once_with(
            "preferred_microphone", "Micrófono (PD200X Podcast Microphone)"
        )
        self.assertEqual(loaded, "Micrófono (PD200X Podcast Microphone)")

    def test_resolves_saved_physical_microphone_to_ui_label(self):
        mapping = {
            "🎤  Webcam": "Micrófono (C505e HD Webcam)",
            "🎤  Podcast": "Micrófono (PD200X Podcast Microphone)",
        }

        selected = choose_preferred_microphone_label(
            mapping, "Micrófono (PD200X Podcast Microphone)"
        )

        self.assertEqual(selected, "🎤  Podcast")


if __name__ == "__main__":
    unittest.main()
