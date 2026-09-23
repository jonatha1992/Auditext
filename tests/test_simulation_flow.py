"""Flujo del práctico oral académico sin los dos modales bloqueantes.

Sin Tk a propósito, mismo patrón que ``test_simulation_retry_buttons.py``: lo
que se prueba es la decisión (no bloquear, no disparar la pregunta dos veces),
no el toolkit. Nunca se llama a un proveedor real ni se reproduce audio real:
``tts`` va siempre mockeado.
"""

from __future__ import annotations

import queue
import unittest
from unittest import mock

from presentation.views.interview_frame import InterviewFrame


class _FakeButton:
    def __init__(self):
        self.state = "disabled"
        self.packed = False

    def configure(self, **kwargs):
        if "state" in kwargs:
            self.state = kwargs["state"]

    def pack(self, **kwargs):
        self.packed = True

    def pack_forget(self):
        self.packed = False

    def winfo_manager(self):
        return "pack" if self.packed else ""


class _FakeFrame:
    """Presta los métodos reales del flujo sin construir la ventana entera."""

    _show_knowledge_check = InterviewFrame._show_knowledge_check
    _cancel_knowledge_check = InterviewFrame._cancel_knowledge_check
    _dismiss_knowledge_dialog = InterviewFrame._dismiss_knowledge_dialog
    _show_next_question_dialog = InterviewFrame._show_next_question_dialog
    _cancel_next_question_timer = InterviewFrame._cancel_next_question_timer
    _continue_to_next_question = InterviewFrame._continue_to_next_question
    _start_simulation_answer = InterviewFrame._start_simulation_answer
    _resume_simulation_answer_capture = InterviewFrame._resume_simulation_answer_capture
    # Sigue siendo estática del otro lado: sin volver a envolverla, el fake se
    # pasaría a sí mismo como primer argumento.
    _widget_is_packed = staticmethod(InterviewFrame._widget_is_packed)

    def __init__(self):
        self._session_ended = False
        self._simulation_busy = False
        self._simulation_session = mock.Mock(answer_lang="es")
        self._simulation_answer_parts = []
        self._simulation_answering = False
        self._knowledge_check_after_id = None
        self._knowledge_dialog = None
        self._pending_simulation_turn = None
        self._next_question_dialog = None
        self._next_question_after_id = None
        self._choice_dialog = mock.Mock(name="_choice_dialog")
        self.next_question_button = _FakeButton()
        self.start_answer_button = _FakeButton()
        self.complete_answer_button = _FakeButton()
        self.read_question_button = _FakeButton()
        self.candidate_box = mock.Mock()
        self.candidate_queue: "queue.Queue[str]" = queue.Queue()
        self.candidate_listener = mock.Mock()
        self.candidate_listener.is_running.return_value = False
        self._microphone_map = {}
        self.mic_var = mock.Mock()
        self.mic_var.get.return_value = ""
        self.status = None
        self.status_color = None
        self._after_calls: list[dict] = []
        self._after_id_counter = 0

    def _set_status(self, text, color=None):
        self.status = text
        self.status_color = color

    def after(self, delay, callback):
        self._after_id_counter += 1
        after_id = self._after_id_counter
        self._after_calls.append(
            {"id": after_id, "delay": delay, "callback": callback}
        )
        return after_id

    def after_cancel(self, after_id):
        self._after_calls = [c for c in self._after_calls if c["id"] != after_id]


class KnowledgeCheckTests(unittest.TestCase):
    """El modal "¿La sabés?" robaba el foco por dos botones ya visibles."""

    def test_show_knowledge_check_creates_no_dialog(self):
        frame = _FakeFrame()

        frame._show_knowledge_check()

        frame._choice_dialog.assert_not_called()
        self.assertIsNone(frame._knowledge_dialog)
        self.assertIsNotNone(frame.status)

    def test_show_knowledge_check_does_not_overwrite_an_active_answer(self):
        frame = _FakeFrame()
        frame._simulation_answering = True

        frame._show_knowledge_check()

        self.assertIsNone(frame.status)


class NextQuestionInlineControlTests(unittest.TestCase):
    """El modal "¿Listo para continuar?" pasa a ser un botón + auto-avance."""

    def test_packs_and_enables_button_and_schedules_auto_advance(self):
        frame = _FakeFrame()
        frame._pending_simulation_turn = object()

        with mock.patch(
            "presentation.views.interview_frame._NEXT_QUESTION_GRACE_MS", 4000
        ):
            frame._show_next_question_dialog()

        self.assertTrue(frame.next_question_button.packed)
        self.assertEqual(frame.next_question_button.state, "normal")
        self.assertEqual(len(frame._after_calls), 1)
        self.assertEqual(frame._after_calls[0]["delay"], 4000)

    def test_manual_mode_schedules_nothing(self):
        frame = _FakeFrame()
        frame._pending_simulation_turn = object()

        with mock.patch(
            "presentation.views.interview_frame._NEXT_QUESTION_GRACE_MS", None
        ):
            frame._show_next_question_dialog()

        self.assertTrue(frame.next_question_button.packed)
        self.assertEqual(frame._after_calls, [])

    def test_timer_and_click_advance_the_question_exactly_once(self):
        frame = _FakeFrame()
        turn = object()
        frame._pending_simulation_turn = turn
        # ``_present_simulation_question`` real vacía el turno pendiente como
        # primer paso; ese es el guardia que hace idempotente el avance.
        frame._present_simulation_question = mock.Mock(
            side_effect=lambda _turn: setattr(frame, "_pending_simulation_turn", None)
        )

        with mock.patch("presentation.views.interview_frame.tts") as tts_mock:
            with mock.patch(
                "presentation.views.interview_frame._NEXT_QUESTION_GRACE_MS", 4000
            ):
                frame._show_next_question_dialog()
            timer_callback = frame._after_calls[0]["callback"]

            # El click llega primero...
            frame._continue_to_next_question()
            # ...y el timer, que en la app real habría sido cancelado, dispara
            # igual acá porque el fake no lo hace desaparecer por sí solo.
            timer_callback()

        self.assertEqual(tts_mock.stop.call_count, 2)
        frame._present_simulation_question.assert_called_once_with(turn)
        self.assertFalse(frame.next_question_button.packed)


class BargeInTests(unittest.TestCase):
    """Arrancar a responder corta la lectura de la pregunta en el acto."""

    def test_start_simulation_answer_stops_tts(self):
        frame = _FakeFrame()

        with mock.patch("presentation.views.interview_frame.tts") as tts_mock:
            frame._start_simulation_answer()

        tts_mock.stop.assert_called_once_with()
        self.assertTrue(frame._simulation_answering)
        self.assertEqual(frame.read_question_button.state, "normal")


if __name__ == "__main__":
    unittest.main()
