"""Text-to-speech provider selection and fallback tests."""

from __future__ import annotations

import unittest
from unittest import mock

from infrastructure.services import tts


class TextToSpeechTests(unittest.TestCase):
    def test_spanish_uses_a_calm_argentinian_neural_voice(self):
        self.assertEqual(
            tts.neural_profile("es"),
            ("es-AR-ElenaNeural", "-8%", "-2Hz"),
        )

    def test_english_uses_a_natural_neural_voice(self):
        self.assertEqual(
            tts.neural_profile("en"),
            ("en-US-AvaNeural", "-6%", "-1Hz"),
        )

    def test_neural_failure_falls_back_to_offline_sapi(self):
        done = mock.Mock()
        with (
            mock.patch.object(
                tts, "_speak_neural", side_effect=RuntimeError("offline")
            ) as neural,
            mock.patch.object(tts, "_speak_sapi") as sapi,
        ):
            tts._speak("Explicación clara.", "es", done)

        neural.assert_called_once_with("Explicación clara.", "es")
        sapi.assert_called_once_with("Explicación clara.", "es")
        done.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
