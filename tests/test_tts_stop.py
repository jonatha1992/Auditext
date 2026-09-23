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


class CancelledUtteranceIsolationTests(unittest.TestCase):
    """Un hilo cancelado no puede tocar el audio de la locución siguiente.

    El solapamiento no es hipotético: ``next_question_button`` se habilita
    apenas ARRANCA la síntesis de la devolución, así que el barge-in del alumno
    encima de una locución neural larga deja dos jobs vivos por diseño.
    """

    def test_a_cancelled_thread_does_not_claim_the_active_generation(self):
        """El hilo viejo no puede reclamar el token del nuevo.

        ``_speak`` asignaba ``_active_generation`` sin mirar si su propia
        generación seguía vigente, así que un hilo ya superado se anotaba como
        el dueño del audio y hacía sonar su texto bajo el token del otro.
        """
        tts.speak_async  # noqa: B018 - documenta de dónde sale el token
        with tts._state_lock:
            tts._generation += 1
            stale = tts._generation
            tts._generation += 1
            current = tts._generation
        tts._active_generation = current

        with mock.patch.object(tts, "_speak_neural", return_value=None):
            # El hilo de `stale` arranca tarde: ya fue superado por `current`.
            tts._speak("texto viejo", "es", None, stale)

        self.assertEqual(tts._active_generation, current)

    def test_a_cancelled_utterance_does_not_unload_the_new_audio(self):
        """El ``unload()`` del hilo viejo no puede descargar la pregunta nueva.

        ``_speak_neural`` llamaba a ``pygame.mixer.music.unload()`` siempre al
        salir del loop de espera, incluso cuando la generación que lo trajo ya
        estaba cancelada: eso descarga el mp3 que la locución siguiente recién
        cargó, y después su ``on_done`` dispara como si se hubiera leído entera.
        """
        pygame = mock.MagicMock()
        pygame.mixer.get_init.return_value = True
        # Cortada apenas arranca a sonar: get_busy sigue en True porque el
        # mixer tarda un tick en reflejar el corte.
        pygame.mixer.music.get_busy.return_value = True

        edge_tts = mock.MagicMock()
        modules = {"pygame": pygame, "edge_tts": edge_tts}

        with tts._state_lock:
            tts._generation += 1
            stale = tts._generation

        def cancel_then_play(*args, **kwargs):
            # Simula el barge-in: stop() invalida `stale` mientras este hilo
            # todavía está dentro de su reproducción.
            tts.stop()

        pygame.mixer.music.play.side_effect = cancel_then_play

        with (
            mock.patch.dict("sys.modules", modules),
            mock.patch.object(tts.asyncio, "run", return_value=None),
        ):
            tts._speak_neural("pregunta vieja", "es", stale)

        pygame.mixer.music.unload.assert_not_called()

    def test_a_current_utterance_still_unloads_its_own_audio(self):
        """La locución vigente sí tiene que liberar su archivo al terminar."""
        pygame = mock.MagicMock()
        pygame.mixer.get_init.return_value = True
        pygame.mixer.music.get_busy.return_value = False
        edge_tts = mock.MagicMock()

        with (
            mock.patch.dict("sys.modules", {"pygame": pygame, "edge_tts": edge_tts}),
            mock.patch.object(tts.asyncio, "run", return_value=None),
        ):
            tts._speak_neural("pregunta vigente", "es", tts._ALWAYS_CURRENT)

        pygame.mixer.music.unload.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
