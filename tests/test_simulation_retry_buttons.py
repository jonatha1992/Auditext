"""Qué botones vuelve a habilitar un fallo del simulacro.

Sin Tk a propósito: lo que se prueba es la decisión, no el toolkit. Un root de
Tcl por caso agota el intérprete alrededor de los 50 tests (ver la nota en
``test_resolver_ui.py``), y acá alcanza con widgets falsos.
"""

from __future__ import annotations

import unittest
from unittest import mock

from presentation.views.interview_frame import InterviewFrame

_ACADEMIC_BUTTONS = (
    "start_answer_button",
    "complete_answer_button",
    "hint_button",
    "mastered_button",
    "dont_know_button",
    "read_question_button",
)


class _FakeButton:
    def __init__(self, name: str, *, packed: bool = True):
        self.name = name
        self.packed = packed
        self.state = "disabled"

    def winfo_exists(self):
        return True

    def winfo_manager(self):
        return "pack" if self.packed else ""

    def configure(self, **kwargs):
        if "state" in kwargs:
            self.state = kwargs["state"]


class _FakeSession:
    def __init__(self, simulation_type: str):
        self.simulation_type = simulation_type


class _FakeFrame:
    """Presta los dos métodos reales sin construir la ventana entera."""

    _simulation_retry_buttons = InterviewFrame._simulation_retry_buttons
    # Sigue siendo estática del otro lado: sin volver a envolverla, el fake se
    # pasaría a sí mismo como primer argumento.
    _widget_is_packed = staticmethod(InterviewFrame._widget_is_packed)
    _simulation_failed = InterviewFrame._simulation_failed

    def __init__(self, simulation_type: str, *, packed: tuple[str, ...], session=True):
        self._simulation_type = simulation_type
        self._simulation_session = _FakeSession(simulation_type) if session else None
        self._simulation_busy = True
        self.buttons = {
            name: _FakeButton(name, packed=name in packed)
            for name in _ACADEMIC_BUTTONS
        }
        for name, button in self.buttons.items():
            setattr(self, name, button)
        self.candidate_listener = mock.Mock()
        self.status = None

    def _set_status(self, text, color=None):
        self.status = text

    def enabled(self) -> set[str]:
        return {n for n, b in self.buttons.items() if b.state == "normal"}


class AcademicRetryButtonsTests(unittest.TestCase):
    """El práctico oral quedaba inerte: cinco botones visibles, todos muertos."""

    def test_every_packed_button_comes_back(self):
        frame = _FakeFrame(
            "academic",
            packed=(
                "start_answer_button",
                "hint_button",
                "mastered_button",
                "dont_know_button",
                "read_question_button",
            ),
        )

        with mock.patch(
            "presentation.views.interview_frame.show_error"
        ) as show_error:
            frame._simulation_failed("Gemini no pudo responder")

        self.assertEqual(
            frame.enabled(),
            {
                "start_answer_button",
                "hint_button",
                "mastered_button",
                "dont_know_button",
                "read_question_button",
            },
        )
        # El que no está en pantalla no se toca: reactivarlo no daba camino de
        # reintento y era justamente lo único que hacía el código viejo.
        self.assertEqual(frame.buttons["complete_answer_button"].state, "disabled")
        show_error.assert_called_once()
        frame.candidate_listener.resume.assert_called_once()
        self.assertFalse(frame._simulation_busy)

    def test_mid_answer_the_complete_button_is_the_one_restored(self):
        # Respondiendo, ``start`` se despacka y ``complete`` ocupa su lugar.
        frame = _FakeFrame(
            "academic",
            packed=(
                "complete_answer_button",
                "hint_button",
                "mastered_button",
                "dont_know_button",
                "read_question_button",
            ),
        )

        with mock.patch("presentation.views.interview_frame.show_error"):
            frame._simulation_failed("timeout")

        self.assertIn("complete_answer_button", frame.enabled())
        self.assertNotIn("start_answer_button", frame.enabled())

    def test_a_failure_before_the_session_exists_still_uses_academic_mode(self):
        # ``_start_simulation`` puede fallar antes de que la sesión se arme;
        # por eso el modo vive también en el frame.
        frame = _FakeFrame(
            "academic", packed=("start_answer_button", "hint_button"), session=False
        )

        with mock.patch("presentation.views.interview_frame.show_error"):
            frame._simulation_failed("sin proveedor")

        self.assertEqual(
            frame.enabled(), {"start_answer_button", "hint_button"}
        )

    def test_nothing_packed_yet_falls_back_to_the_academic_set(self):
        frame = _FakeFrame("academic", packed=())

        buttons = frame._simulation_retry_buttons()

        self.assertEqual(len(buttons), len(_ACADEMIC_BUTTONS))


class InterviewRetryButtonsTests(unittest.TestCase):
    def test_the_interview_mode_only_gets_its_single_button_back(self):
        frame = _FakeFrame("interview", packed=("complete_answer_button",))

        with mock.patch("presentation.views.interview_frame.show_error"):
            frame._simulation_failed("503")

        self.assertEqual(frame.enabled(), {"complete_answer_button"})


if __name__ == "__main__":
    unittest.main()
