"""UI and routing regression tests for the standalone question resolver."""

from __future__ import annotations

import tkinter as tk
import unittest
from unittest import mock

import customtkinter as ctk

import config
from infrastructure.services import notebooklm_service
from presentation.views.interview_frame import InterviewFrame
from presentation.views.practice_frame import PracticeFrame


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
            ],
        )
        self.assertEqual(self.frame.lang_var.get(), "Español")
        self.assertIn("TU RESPUESTA", self.frame.mic_label.cget("text"))
        self.assertIn("MICRÓFONO", self.frame.mic_label.cget("text"))
        self.assertEqual(
            self.frame.question_label.cget("text"),
            "Esperando una pregunta...",
        )
        self.assertTrue(bool(self.frame.transcripts_frame.pack_info()))
        self.assertTrue(bool(self.frame.interviewer_transcript_label.grid_info()))
        self.assertTrue(bool(self.frame.interviewer_box.grid_info()))
        self.assertFalse(bool(self.frame.candidate_transcript_label.grid_info()))
        self.assertFalse(bool(self.frame.candidate_box.grid_info()))
        active_order = self.frame.active.pack_slaves()
        self.assertLess(
            active_order.index(self.frame.transcripts_frame),
            active_order.index(self.frame.question_label.master),
        )
        self.assertTrue(self.frame.source_combo.winfo_ismapped())
        # La ampliación sigue a la respuesta, en el mismo flujo de lectura.
        first = self.frame.reply_cards[0].grid_info()
        second = self.frame.reply_cards[1].grid_info()
        self.assertEqual(int(first["column"]), 0)
        self.assertEqual(int(second["column"]), 0)
        self.assertGreater(int(second["row"]), int(first["row"]))
        self.assertGreaterEqual(int(self.frame.reply_boxes[0].cget("height")), 150)
        self.assertGreaterEqual(int(self.frame.reply_boxes[1].cget("height")), 180)
        self.assertTrue(hasattr(self.frame, "notebook_combo"))
        self.assertEqual(
            self.frame.notebook_var.get(), "Seleccioná una materia…"
        )
        self.assertTrue(self.frame.use_written_context_var.get())
        self.assertFalse(self.frame.use_notebook_var.get())
        self.assertEqual(self.frame.notebook_combo.cget("state"), "disabled")

    def test_academic_practice_is_a_separate_professor_module(self):
        practice = PracticeFrame(self.root)
        try:
            practice.pack(fill="both", expand=True)
            self.root.update_idletasks()
            self.assertEqual(practice._current_mode, "practica_oral")
            self.assertEqual(
                list(practice.mode_combo.cget("values")),
                ["Práctica oral con profesor IA"],
            )
            self.assertIn("Práctica oral", practice.header_title.cget("text"))
            self.assertFalse(bool(practice.source_combo.grid_info()))
            self.assertTrue(bool(practice.candidate_box.grid_info()))
            self.assertTrue(hasattr(practice, "hint_button"))
            self.assertTrue(hasattr(practice, "read_question_button"))
            self.assertTrue(hasattr(practice, "notebook_login_button"))
            self.assertTrue(practice.use_notebook_var.get())
            self.assertFalse(practice.use_written_context_var.get())
            self.assertEqual(practice.notebook_login_button.cget("state"), "normal")
            self.assertIn("DEVOLUCIÓN", practice.reply_titles[0].cget("text"))
            self.assertIn(
                "después de responder",
                practice.reply_boxes[0].get("1.0", "end").lower(),
            )
        finally:
            practice.destroy()

    def test_practice_questions_use_the_synced_notebook_material(self):
        practice = PracticeFrame(self.root)
        try:
            practice._active_notebook_id = "uml"
            practice._active_notebook_title = "UML"
            practice._active_notebook_context = "[NotebookLM · UML]\nClases y objetos"
            practice.use_notebook_var.set(True)
            practice.use_written_context_var.set(False)
            previous_repository = config.repository
            config.repository = None
            try:
                with (
                    mock.patch(
                        "presentation.views.interview_frame.interview_simulation.is_configured",
                        return_value=True,
                    ),
                    mock.patch.object(practice, "_start_simulation") as start,
                ):
                    practice.start_session()
            finally:
                config.repository = previous_repository

            self.assertIn("Clases y objetos", start.call_args.args[0])
            self.assertEqual(start.call_args.args[2], "academic")
        finally:
            practice.destroy()

    def test_next_simulation_question_resumes_the_existing_microphone_listener(self):
        self.frame._simulation_session = mock.Mock(answer_lang="es", state="waiting_answer")
        self.frame.candidate_listener.is_running = mock.Mock(return_value=True)
        self.frame.candidate_listener.resume = mock.Mock()
        turn = mock.Mock(
            action="follow_up",
            question="¿Por qué una clase no es una instancia?",
            feedback="Bien encaminado.",
            improvements=(),
            strengths=(),
        )

        self.frame._apply_simulation_turn(turn)

        self.frame.candidate_listener.resume.assert_called_once_with()

    def test_academic_practice_export_keeps_questions_answers_and_feedback(self):
        self.frame._simulation_session = mock.Mock()
        self.frame._simulation_session.report.return_value = "Fortalezas: definición clara."
        self.frame.interviewer_box.insert("1.0", "¿Qué es una clase?")
        self.frame.candidate_box.insert("1.0", "Es una plantilla para crear objetos.")

        combined = self.frame._combined_transcript()

        self.assertIn("PROFESOR IA\n¿Qué es una clase?", combined)
        self.assertIn("ESTUDIANTE\nEs una plantilla", combined)
        self.assertIn("DEVOLUCIÓN\nFortalezas", combined)

    def test_interview_offers_ai_simulation_with_explicit_answer_boundary(self):
        interview = InterviewFrame(self.root)
        try:
            self.assertIn(
                "Simulacro con IA",
                list(interview.mode_combo.cget("values")),
            )
            interview.mode_var.set("Simulacro con IA")
            interview._on_mode_change("Simulacro con IA")

            self.assertEqual(interview._current_mode, "simulacro")
            self.assertEqual(interview.start_button.cget("text"), "▶  Iniciar simulacro")
            self.assertTrue(hasattr(interview, "complete_answer_button"))
        finally:
            interview.destroy()

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

    def test_audio_apps_can_refresh_after_chrome_starts(self):
        with mock.patch(
            "infrastructure.audio.process_loopback.list_audio_apps",
            return_value=[("chrome.exe", 4242)],
        ):
            self.frame._refresh_audio_sources()

        values = list(self.frame.source_combo.cget("values"))
        self.assertTrue(any("chrome.exe" in value for value in values))
        self.assertTrue(hasattr(self.frame, "refresh_sources_button"))

    def test_audio_source_refresh_preserves_current_selection(self):
        label = "💻  App: chrome.exe (PID 4242)"
        self.frame._source_map[label] = ("app", 4242)
        self.frame.source_var.set(label)

        with mock.patch(
            "infrastructure.audio.process_loopback.list_audio_apps",
            return_value=[("chrome.exe", 4242), ("vlc.exe", 99)],
        ):
            self.frame._refresh_audio_sources()

        self.assertEqual(self.frame.source_var.get(), label)
        self.assertEqual(
            self.frame.context_box._textbox.cget("state"), "normal"
        )
        self.assertEqual(self.frame.notebook_combo.cget("state"), "disabled")
        # Turning NotebookLM off does not make an empty form startable: the
        # resolver needs material from some source.
        self.assertEqual(self.frame.start_button.cget("state"), "disabled")

        self.frame.context_box.delete("1.0", "end")
        self.frame.context_box.insert("1.0", "Unidad 1: derivadas")
        self.frame._apply_context_source_state()

        self.assertEqual(self.frame.start_button.cget("state"), "normal")

    def test_start_is_locked_until_some_material_is_loaded(self):
        self.frame.context_box.delete("1.0", "end")
        self.frame._apply_context_source_state()

        self.assertEqual(self.frame.start_button.cget("state"), "disabled")
        self.assertIn("Cargá el material", self.frame.start_ready_label.cget("text"))

        self.frame.context_box.insert("1.0", "Temario de Álgebra")
        self.frame._apply_context_source_state()

        self.assertEqual(self.frame.start_button.cget("state"), "normal")
        self.assertIn("Contexto escrito", self.frame.start_ready_label.cget("text"))

    def test_synced_subject_stays_ready_after_typing(self):
        self.frame.use_notebook_var.set(True)
        self.frame._active_notebook_id = "nb-1"
        self.frame._active_notebook_title = "Álgebra"
        self.frame._active_notebook_context = "[NotebookLM · Álgebra]\nUnidad 1"
        self.frame.context_box.delete("1.0", "end")
        self.frame._apply_context_source_state()

        self.assertIn("lista para usar", self.frame.notebook_status.cget("text"))

        # A keystroke in the written box used to wipe the ready message even
        # though the material was still loaded.
        self.frame.context_box.insert("1.0", "a")
        self.frame._apply_context_source_state()

        self.assertIn("Álgebra", self.frame.notebook_status.cget("text"))
        self.assertIn("lista", self.frame.notebook_status.cget("text"))
        self.assertEqual(self.frame.start_button.cget("state"), "normal")

    def test_last_subject_is_restored_from_the_local_cache(self):
        notebook = notebooklm_service.NotebookRef(
            id="nb-1", title="Álgebra", source_count=3
        )
        self.frame._notebook_map = {notebook.label: notebook}
        settings = {
            "notebooklm_selected_id": "nb-1",
            "notebooklm_context_nb-1": "[NotebookLM · Álgebra]\nUnidad 1",
        }

        with mock.patch.object(
            self.frame, "_load_setting", side_effect=settings.get
        ):
            label = self.frame._restore_saved_notebook()

        self.assertEqual(label, notebook.label)
        self.assertEqual(self.frame._active_notebook_id, "nb-1")
        self.assertEqual(self.frame._active_notebook_title, "Álgebra")
        self.assertIn("Unidad 1", self.frame._active_notebook_context)

    def test_resolver_refuses_to_start_without_material(self):
        self.frame.context_box.delete("1.0", "end")
        with (
            mock.patch(
                "presentation.views.interview_frame.interview_live.is_configured",
                return_value=True,
            ),
            mock.patch(
                "presentation.views.interview_frame.show_warning"
            ) as warning,
            mock.patch.object(self.frame.worker, "start") as worker_start,
        ):
            self.frame.start_session()

        warning.assert_called_once()
        worker_start.assert_not_called()

    def test_resolver_routes_system_as_question_and_mic_as_user(self):
        self.frame.context_box.delete("1.0", "end")
        self.frame.context_box.insert("1.0", "Temario de la materia")
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

    def test_answer_loading_indicator_marks_a_new_response(self):
        self.frame._set_answer_loading(True)

        self.assertTrue(self.frame._answer_loading)
        self.assertTrue(bool(self.frame.answer_loading_label.grid_info()))
        self.assertIn("respuesta nueva", self.frame.answer_loading_label.cget("text").lower())
        self.assertEqual(self.frame.reply_boxes[0]._reply_text, "")

        assist = mock.Mock(
            pregunta_es="Pregunta",
            respuestas=["Respuesta nueva", "Detalle nuevo"],
            ideas_clave=[],
            partial=False,
        )
        self.frame._apply_assist(assist)
        self.assertFalse(self.frame._answer_loading)
        self.assertFalse(bool(self.frame.answer_loading_label.grid_info()))

    def test_oral_generation_status_also_replaces_the_previous_answer(self):
        self.frame.status_queue.put("Generando sugerencias...")

        self.frame._drain_queues()

        self.assertTrue(self.frame._answer_loading)
        self.assertEqual(self.frame.reply_boxes[0]._reply_text, "")

    def test_interview_module_does_not_offer_resolver_or_oral_modes(self):
        interview = InterviewFrame(self.root)
        try:
            self.assertEqual(
                list(interview.mode_combo.cget("values")),
                [
                    "Entrevista laboral",
                    "Práctica de idioma",
                    "Conversación general",
                    "Simulacro con IA",
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
