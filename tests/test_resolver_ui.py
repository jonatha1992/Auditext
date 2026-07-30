"""UI and routing regression tests for the standalone question resolver."""

from __future__ import annotations

import tkinter as tk
import unittest
from unittest import mock

import customtkinter as ctk

import config
from presentation.views.interview_frame import InterviewFrame


class ResolverFrameTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = ctk.CTk()
        except tk.TclError as exc:
            self.skipTest(f"Tk is not available: {exc}")
        self.root.withdraw()
        self.frame = InterviewFrame(self.root, fixed_mode="resolver")
        self.frame.pack(fill="both", expand=True)
        self.root.update_idletasks()

    def tearDown(self):
        if hasattr(self, "frame"):
            self.frame.destroy()
        if hasattr(self, "root"):
            self.root.destroy()

    def test_resolver_interface_is_spanish_and_question_focused(self):
        self.assertEqual(self.frame.mode_var.get(), "Resolver preguntas")
        self.assertEqual(
            list(self.frame.mode_combo.cget("values")),
            [
                "Resolver preguntas",
                "Examen oral (respuestas completas)",
                "Práctica oral (solo ideas)",
            ],
        )
        self.assertEqual(self.frame.lang_var.get(), "Español")
        self.assertIn("TU RESPUESTA", self.frame.mic_label.cget("text"))
        self.assertIn("MICRÓFONO", self.frame.mic_label.cget("text"))
        self.assertEqual(
            self.frame.question_label.cget("text"),
            "Esperando una pregunta...",
        )
        self.assertTrue(bool(self.frame.candidate_box.grid_info()))
        self.assertTrue(self.frame.source_combo.winfo_ismapped())
        self.assertEqual(int(self.frame.reply_boxes[0].cget("height")), 150)
        self.assertTrue(hasattr(self.frame, "notebook_combo"))
        self.assertIn("NotebookLM", self.frame.notebook_var.get())

    def test_resolver_routes_system_as_question_and_mic_as_user(self):
        selected_source = self.frame._source_map.get(
            self.frame.source_var.get()
        )
        selected_mic = self.frame._microphone_map.get(self.frame.mic_var.get())
        previous_repository = config.repository
        config.repository = None
        try:
            with (
                mock.patch(
                    "presentation.views.interview_frame.interview_live.is_configured",
                    return_value=True,
                ),
                mock.patch.object(self.frame.worker, "start") as worker_start,
                mock.patch.object(
                    self.frame.candidate_listener, "start"
                ) as candidate_start,
            ):
                self.frame.start_session()
        finally:
            config.repository = previous_repository

        worker_start.assert_called_once()
        kwargs = worker_start.call_args.kwargs
        self.assertEqual(
            (kwargs["source_type"], kwargs["source_val"]),
            selected_source,
        )
        self.assertEqual(kwargs["interview_assist_mode"], "resolver")
        self.assertEqual(kwargs["interview_answer_lang"], "es")
        candidate_start.assert_called_once_with(
            selected_mic, language="es"
        )

    def test_oral_exam_accepts_a_course_program_or_schedule(self):
        self.frame.mode_combo.set("Examen oral (respuestas completas)")
        self.frame._on_mode_change("Examen oral (respuestas completas)")

        self.assertEqual(
            self.frame.context_label.cget("text"),
            "📚  PROGRAMA / TEMARIO / CRONOGRAMA",
        )
        self.assertIn("programa", self.frame.context_box.get("1.0", "end").lower())
        self.assertIn("cronograma", self.frame.context_box.get("1.0", "end").lower())

    def test_saved_resolution_includes_question_suggestions_and_user_answer(self):
        self.frame.interviewer_box.insert("1.0", "¿Qué es la fotosíntesis?")
        self.frame.candidate_box.insert("1.0", "Es el proceso que transforma la luz.")
        self.frame.reply_boxes[0]._reply_text = "La fotosíntesis convierte energía lumínica en química."

        combined = self.frame._combined_transcript()

        self.assertIn("PREGUNTA\n¿Qué es la fotosíntesis?", combined)
        self.assertIn("RESPUESTA 1\nLa fotosíntesis", combined)
        self.assertIn("TU RESPUESTA\nEs el proceso", combined)

    def test_interview_module_does_not_offer_resolver_or_oral_modes(self):
        interview = InterviewFrame(self.root)
        try:
            self.assertEqual(
                list(interview.mode_combo.cget("values")),
                [
                    "Entrevista laboral",
                    "Práctica de idioma",
                    "Conversación general",
                ],
            )
        finally:
            interview.destroy()


if __name__ == "__main__":
    unittest.main()
