"""Tests de la pestaña Probar micrófono.

Los defectos del watchdog y del botón "Usar este micrófono" sobrevivieron a la
primera pasada porque el frame no tenía ni un test. Igual que en
``test_resolver_ui.py``, el root de Tk es uno solo para toda la clase: uno por
test agota el intérprete de Tcl.
"""

import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import tkinter as tk

import customtkinter as ctk

import config
from infrastructure.services.microphone_test import MicrophoneTestResult


def _result(mean_level, transcription="", captured=5.0, requested=5.0):
    return MicrophoneTestResult(
        microphone_name="Micrófono (PD200X Podcast Microphone)",
        endpoint_name="Micrófono (PD200X Podcast Microphone)",
        sample_rate=16000,
        audio=np.zeros(int(16000 * captured), dtype=np.float32),
        mean_level=mean_level,
        peak_level=mean_level * 2,
        transcription=transcription,
        playback_path=Path("auditext_microphone_test.wav"),
        requested_seconds=requested,
    )


class MicrophoneTestFrameTests(unittest.TestCase):
    # Mismo trato que en ``test_resolver_ui.py``: un root por test agota el
    # intérprete de Tcl. Crearlo a nivel de módulo es peor todavía, porque se
    # instancia durante la colección y le come el intérprete a la otra clase
    # de Tk aunque esta ni corra.
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = ctk.CTk()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Tk is not available: {exc}")
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "root", None) is not None:
            cls.root.destroy()
            cls.root = None

    def setUp(self):
        patcher = mock.patch(
            "presentation.views.microphone_test_frame.MicrophoneTestService"
        )
        self.service_cls = patcher.start()
        self.addCleanup(patcher.stop)
        self.service_cls.return_value.list_microphones.return_value = [
            "Micrófono (PD200X Podcast Microphone)"
        ]
        repository = mock.patch.object(config, "repository", None)
        repository.start()
        self.addCleanup(repository.stop)

        from presentation.views.microphone_test_frame import MicrophoneTestFrame

        self.frame = MicrophoneTestFrame(self.root)
        self.addCleanup(self.frame.destroy)

    def test_silent_take_cannot_be_approved_as_the_preferred_microphone(self):
        self.frame._show_result(_result(mean_level=0.0))

        self.assertEqual(str(self.frame.use_button.cget("state")), "disabled")
        self.assertEqual(str(self.frame.play_button.cget("state")), "normal")

    def test_partial_take_cannot_be_approved_even_when_it_is_loud(self):
        """Un chasquido de 0,2 s promedia muy por encima del umbral de nivel."""
        self.frame._show_result(
            _result(mean_level=0.6, captured=0.2, requested=5.0)
        )

        self.assertEqual(str(self.frame.use_button.cget("state")), "disabled")
        self.assertIn("0.2 s de 5 s", str(self.frame.status_label.cget("text")))

    def test_audible_take_can_be_approved(self):
        self.frame._show_result(_result(mean_level=0.02, transcription="Hola"))

        self.assertEqual(str(self.frame.use_button.cget("state")), "normal")

    def test_stale_failure_does_not_disarm_the_running_test(self):
        """Un intento viejo que falla tarde cancelaba el watchdog del actual y
        dejaba largar una tercera prueba sobre el mismo micrófono."""
        self.frame._testing = True
        self.frame._test_token = 7
        self.frame._watchdog = "watchdog-del-intento-en-curso"

        self.frame._show_error("fallo viejo", token=3)

        self.assertTrue(self.frame._testing)
        self.assertEqual(self.frame._watchdog, "watchdog-del-intento-en-curso")

    def test_current_failure_is_shown(self):
        self.frame._testing = True
        self.frame._test_token = 7
        self.frame._watchdog = None

        self.frame._show_error("no se pudo abrir el endpoint", token=7)

        self.assertFalse(self.frame._testing)
        self.assertIn(
            "no se pudo abrir el endpoint",
            str(self.frame.status_label.cget("text")),
        )

    def test_start_test_puts_the_real_failure_on_screen(self):
        """Sin user_msg, record() devuelve el texto genérico de la bitácora y
        el diagnóstico del servicio no llega nunca al usuario."""
        detalle = (
            "Micrófono (Maono AI Microphone) aceptó la conexión pero no "
            "entregó audio en ningún endpoint."
        )
        self.frame.service.probe.side_effect = RuntimeError(detalle)

        # `after(0, ...)` se encola y se drena en el hilo principal, que es lo
        # que hace Tk de verdad. El watchdog, agendado a 90 s, no se ejecuta:
        # correrlo acá dispararía el timeout antes que llegue el resultado.
        pending = []

        def fake_after(delay, cb=None, *_args):
            if delay == 0 and cb is not None:
                pending.append(cb)
                return None
            return "watchdog-id"

        self.frame._watchdog = None
        with mock.patch.object(self.frame, "after", fake_after):
            self.frame.start_test()
            for _ in range(100):
                if pending:
                    break
                time.sleep(0.02)
            self.assertTrue(pending, "el hilo nunca entregó el resultado")
            for cb in pending:
                cb()

        self.assertFalse(self.frame._testing)
        self.assertIn(detalle, str(self.frame.status_label.cget("text")))

    def test_timeout_invalidates_the_attempt_so_a_late_result_cannot_repaint(self):
        self.frame._testing = True
        self.frame._test_token = 4
        self.frame._watchdog = None

        with mock.patch(
            "presentation.views.microphone_test_frame.record",
            return_value="no respondió",
        ):
            self.frame._on_timeout(4, "Micrófono (Maono AI Microphone)")

        self.assertFalse(self.frame._testing)
        self.assertNotEqual(self.frame._test_token, 4)

        self.frame._show_result(_result(mean_level=0.5), token=4)

        self.assertIn("no respondió", str(self.frame.status_label.cget("text")))


if __name__ == "__main__":
    unittest.main()
