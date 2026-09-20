"""Contrato de cancelación de tts: stop() corta la locución sin disparar on_done tardío."""

from __future__ import annotations

import unittest
from unittest import mock

from infrastructure.services import tts


class _ImmediateThread:
    """Reemplazo de threading.Thread que corre el target en el mismo hilo.

    Así el test controla con exactitud cuándo se llama tts.stop() respecto de
    la "locución" simulada, sin depender de sleeps ni de hilos reales -
    tts.stop() no debe reproducir audio real ni la app debe esperar timing.
    """

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self) -> None:
        self._target(*self._args, **self._kwargs)


class TtsStopTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(tts.threading, "Thread", _ImmediateThread)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_normal_utterance_fires_on_done(self):
        done = mock.Mock()
        with mock.patch.object(tts, "_speak_neural", return_value=None):
            tts.speak_async("hola", "es", on_done=done)
        done.assert_called_once_with()

    def test_stop_during_playback_swallows_on_done(self):
        done = mock.Mock()

        def fake_speak_neural(text, language):
            # Simula que el alumno corta la locución mientras "suena": el
            # hilo de fondo sigue vivo y va a intentar cerrar igual.
            tts.stop()

        with mock.patch.object(tts, "_speak_neural", side_effect=fake_speak_neural):
            tts.speak_async("hola", "es", on_done=done)

        done.assert_not_called()

    def test_stop_with_nothing_playing_does_not_raise(self):
        tts.stop()

    def test_new_utterance_after_stop_fires_normally(self):
        done = mock.Mock()
        tts.stop()
        with mock.patch.object(tts, "_speak_neural", return_value=None):
            tts.speak_async("hola de nuevo", "es", on_done=done)
        done.assert_called_once_with()

    def test_is_speaking_reflects_active_utterance(self):
        states = []

        def fake_speak_neural(text, language):
            states.append(tts.is_speaking())

        self.assertFalse(tts.is_speaking())
        with mock.patch.object(tts, "_speak_neural", side_effect=fake_speak_neural):
            tts.speak_async("hola", "es", on_done=None)
        self.assertEqual(states, [True])
        self.assertFalse(tts.is_speaking())


if __name__ == "__main__":
    unittest.main()
