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
        # La corta ocupa menos que la ampliada: llevan cantidades de texto distintas.
        self.assertEqual(int(self.frame.reply_boxes[0].cget("height")), 110)
        self.assertEqual(int(self.frame.reply_boxes[1].cget("height")), 190)
        self.assertTrue(hasattr(self.frame, "notebook_combo"))
        self.assertEqual(
            self.frame.notebook_var.get(), "Seleccioná una materia…"
        )
        self.assertTrue(self.frame.use_written_context_var.get())
        self.assertFalse(self.frame.use_notebook_var.get())
        self.assertEqual(self.frame.notebook_combo.cget("state"), "disabled")

    def test_context_sources_can_be_enabled_together(self):
        self.frame._select_notebook_context()

        # Calling the handler does not alter either checkbox; the user may use
        # written material and NotebookLM together.
        self.frame.use_notebook_var.set(True)
        self.frame._select_notebook_context()
        self.assertTrue(self.frame.use_notebook_var.get())
        self.assertTrue(self.frame.use_written_context_var.get())
        self.assertEqual(self.frame.context_box._textbox.cget("state"), "normal")
        self.assertEqual(self.frame.notebook_combo.cget("state"), "readonly")
        self.assertEqual(self.frame.start_button.cget("state"), "disabled")

        self.frame.use_notebook_var.set(False)
        self.frame._select_notebook_context()

        self.assertTrue(self.frame.use_written_context_var.get())
        self.assertFalse(self.frame.use_notebook_var.get())
        self.assertEqual(
            self.frame.context_box._textbox.cget("state"), "normal"
        )
        self.assertEqual(self.frame.notebook_combo.cget("state"), "disabled")
        self.assertEqual(self.frame.start_button.cget("state"), "normal")

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

    def test_written_context_can_be_cleared(self):
        self.frame.context_box.delete("1.0", "end")
        self.frame.context_box.insert("1.0", "Contenido para eliminar")

        self.frame.clear_context_button.invoke()

        self.assertEqual(self.frame.context_box.get("1.0", "end").strip(), "")

    def test_saved_resolution_includes_question_suggestions_and_user_answer(self):
        self.frame.interviewer_box.insert("1.0", "¿Qué es la fotosíntesis?")
        self.frame.candidate_box.insert("1.0", "Es el proceso que transforma la luz.")
        self.frame.reply_boxes[0]._reply_text = "La fotosíntesis convierte energía lumínica en química."
        self.frame.reply_boxes[1]._reply_text = "La planta usa la luz del sol para fabricar su alimento."

        combined = self.frame._combined_transcript()

        self.assertIn("PREGUNTA\n¿Qué es la fotosíntesis?", combined)
        self.assertIn("RESPUESTA CORTA\nLa fotosíntesis", combined)
        self.assertIn("RESPUESTA AMPLIADA\nLa planta usa", combined)
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

    def test_active_session_can_pause_and_resume_without_stopping(self):
        self.frame.worker.is_running = mock.Mock(return_value=True)
        self.frame.candidate_listener.is_running = mock.Mock(return_value=True)
        self.frame.worker.pause = mock.Mock()
        self.frame.worker.resume = mock.Mock()
        self.frame.candidate_listener.pause = mock.Mock()
        self.frame.candidate_listener.resume = mock.Mock()

        self.frame.toggle_pause()

        self.assertTrue(self.frame._session_paused)
        self.frame.worker.pause.assert_called_once_with()
        self.frame.candidate_listener.pause.assert_called_once_with()
        self.assertIn("Reanudar", self.frame.pause_button.cget("text"))

        self.frame.toggle_pause()

        self.assertFalse(self.frame._session_paused)
        self.frame.worker.resume.assert_called_once_with()
        self.frame.candidate_listener.resume.assert_called_once_with()
        self.assertIn("Pausar", self.frame.pause_button.cget("text"))

    def test_notebook_material_is_sent_as_session_context(self):
        written = "Contexto escrito por el usuario."
        material = "[NotebookLM · Física]\nLeyes de Newton y energía."
        self.frame.context_box.delete("1.0", "end")
        self.frame.context_box.insert("1.0", written)
        self.frame._active_notebook_id = "physics"
        self.frame._active_notebook_context = material
        self.frame.use_notebook_var.set(True)
        self.frame.use_written_context_var.set(False)
        previous_repository = config.repository
        config.repository = None
        try:
            with (
                mock.patch(
                    "presentation.views.interview_frame.interview_live.is_configured",
                    return_value=True,
                ),
                mock.patch.object(self.frame.worker, "start") as worker_start,
                mock.patch.object(self.frame.candidate_listener, "start"),
            ):
                self.frame.start_session()
        finally:
            config.repository = previous_repository

        self.assertEqual(
            worker_start.call_args.kwargs["interview_context"], material
        )

    def test_written_context_is_used_when_notebook_is_not_active(self):
        written = "Este es el contexto ingresado manualmente."
        self.frame.context_box.delete("1.0", "end")
        self.frame.context_box.insert("1.0", written)
        self.frame._active_notebook_id = "physics"
        self.frame._active_notebook_context = (
            "[NotebookLM · Física]\nMaterial que no debe usarse."
        )
        self.frame.use_notebook_var.set(False)
        previous_repository = config.repository
        config.repository = None
        try:
            with (
                mock.patch(
                    "presentation.views.interview_frame.interview_live.is_configured",
                    return_value=True,
                ),
                mock.patch.object(self.frame.worker, "start") as worker_start,
                mock.patch.object(self.frame.candidate_listener, "start"),
            ):
                self.frame.start_session()
        finally:
            config.repository = previous_repository

        self.assertEqual(
            worker_start.call_args.kwargs["interview_context"], written
        )

    def test_written_and_notebook_contexts_are_combined(self):
        written = "Modelo ferroviario preparado por el estudiante."
        material = "[NotebookLM · ADS III]\nConceptos de UML."
        self.frame.context_box.delete("1.0", "end")
        self.frame.context_box.insert("1.0", written)
        self.frame._active_notebook_id = "ads"
        self.frame._active_notebook_context = material
        self.frame.use_written_context_var.set(True)
        self.frame.use_notebook_var.set(True)
        previous_repository = config.repository
        config.repository = None
        try:
            with (
                mock.patch(
                    "presentation.views.interview_frame.interview_live.is_configured",
                    return_value=True,
                ),
                mock.patch.object(self.frame.worker, "start") as worker_start,
                mock.patch.object(self.frame.candidate_listener, "start"),
            ):
                self.frame.start_session()
        finally:
            config.repository = previous_repository

        sent = worker_start.call_args.kwargs["interview_context"]
        self.assertIn("[Contexto escrito]", sent)
        self.assertIn(written, sent)
        self.assertIn(material, sent)

    def test_selecting_a_subject_starts_sync_automatically(self):
        notebook = mock.Mock(id="ads", title="ADS III")
        self.frame._notebook_map = {"ADS III": notebook}
        self.frame.use_notebook_var.set(True)

        with (
            mock.patch.object(self.frame, "_save_notebook_selection"),
            mock.patch.object(self.frame, "_sync_notebook") as sync,
        ):
            self.frame._on_notebook_change("ADS III")

        sync.assert_called_once_with()

    def test_cached_subject_enables_start_immediately(self):
        notebook = mock.Mock(id="ads", title="ADS III")
        self.frame._notebook_map = {"ADS III": notebook}
        self.frame.notebook_var.set("ADS III")
        self.frame.use_notebook_var.set(True)
        self.frame._apply_context_source_state()
        self.assertEqual(self.frame.start_button.cget("state"), "disabled")

        with (
            mock.patch.object(
                self.frame,
                "_load_setting",
                return_value="[NotebookLM · ADS III]\nMaterial guardado.",
            ),
            mock.patch.object(self.frame, "_save_notebook_selection"),
            mock.patch.object(self.frame, "_sync_notebook"),
        ):
            self.frame._on_notebook_change("ADS III")

        self.assertEqual(self.frame.start_button.cget("state"), "normal")
        self.assertIn("caché", self.frame.notebook_status.cget("text"))

    def test_written_material_allows_start_while_notebook_syncs(self):
        self.frame.context_box.delete("1.0", "end")
        self.frame.context_box.insert("1.0", "Programa completo de ADS III")
        self.frame.use_notebook_var.set(True)
        self.frame._active_notebook_context = None

        self.frame._apply_context_source_state()

        self.assertEqual(self.frame.start_button.cget("state"), "normal")


if __name__ == "__main__":
    unittest.main()
