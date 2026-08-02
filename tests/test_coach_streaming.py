"""Adaptive flush + streaming line format — the two levers that cut Live latency."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from infrastructure.services import interview_live
from infrastructure.services.interview_live import (
    InterviewAssist,
    InterviewLiveSession,
    _stream_coach,
    looks_complete_question,
    parse_assist,
    parse_assist_lines,
)


class LooksCompleteQuestionTests(unittest.TestCase):
    """Gate for the fast flush. A false positive re-introduces the cut-sentence bug."""

    def test_closed_question_is_complete(self):
        self.assertTrue(
            looks_complete_question("¿Cuáles son los elementos de un diagrama de secuencia?")
        )

    def test_question_cut_mid_sentence_is_not_complete(self):
        # extract_question_candidate alone matches this on the opening "Qué".
        self.assertFalse(looks_complete_question("¿Qué es un diagrama de"))
        self.assertFalse(looks_complete_question("Explicá brevemente cómo funciona el"))

    def test_unpunctuated_speech_falls_back_to_the_slow_path(self):
        # If the STT emits no closing punctuation the fast path must not fire.
        self.assertFalse(
            looks_complete_question("cuáles son los elementos de un diagrama de secuencia")
        )

    def test_statement_without_a_question_is_not_complete(self):
        self.assertFalse(looks_complete_question("Perfecto, esa respuesta está muy bien."))

    def test_too_short_is_not_complete(self):
        self.assertFalse(looks_complete_question("¿Qué?"))


class IdleThresholdTests(unittest.TestCase):
    def _session(self) -> InterviewLiveSession:
        return InterviewLiveSession(
            context="",
            on_transcript=lambda _t: None,
            on_assist=lambda _a: None,
            on_status=lambda _s: None,
            mode="examen_oral",
        )

    def test_complete_question_uses_the_fast_threshold(self):
        session = self._session()
        session._utterance_buf = "¿Qué diagramas representan una vista estática?"
        self.assertEqual(session._idle_threshold(), InterviewLiveSession._FLUSH_FAST_SECONDS)

    def test_incomplete_buffer_keeps_the_slow_threshold(self):
        session = self._session()
        session._utterance_buf = "¿Qué diagramas representan una vista"
        self.assertEqual(session._idle_threshold(), InterviewLiveSession._FLUSH_IDLE_SECONDS)

    def test_slow_threshold_still_tolerates_natural_pauses(self):
        # Same guarantee the old fixed timer gave; see test_interview_answer_lang.
        self.assertGreaterEqual(InterviewLiveSession._FLUSH_IDLE_SECONDS, 2.5)
        self.assertLess(
            InterviewLiveSession._FLUSH_FAST_SECONDS,
            InterviewLiveSession._FLUSH_IDLE_SECONDS,
        )


class ParseAssistLinesTests(unittest.TestCase):
    def test_parses_the_four_lines(self):
        assist = parse_assist_lines(
            "R: El diagrama de clases es una vista estática.\n"
            "A: Muestra las clases y cómo se relacionan, sin el paso del tiempo.\n"
            "P: ¿Qué vista representa el diagrama de clases?\n"
            "I: vista estática; estructura; UML\n"
        )
        # Izquierda = corta, derecha = ampliada.
        self.assertEqual(
            assist.respuestas,
            [
                "El diagrama de clases es una vista estática.",
                "Muestra las clases y cómo se relacionan, sin el paso del tiempo.",
            ],
        )
        self.assertEqual(assist.pregunta_es, "¿Qué vista representa el diagrama de clases?")
        self.assertEqual(assist.ideas_clave, ["vista estática", "estructura", "UML"])

    def test_answer_alone_is_enough(self):
        # The whole point: render as soon as the R line closes, before A exists.
        assist = parse_assist_lines("R: Es una vista estática.", partial=True)
        self.assertEqual(assist.respuestas, ["Es una vista estática."])
        self.assertTrue(assist.partial)
        self.assertEqual(assist.pregunta_es, "")

    def test_expansion_without_a_short_answer_is_dropped(self):
        # Would otherwise land in the left box, which is the one the user reads first.
        self.assertIsNone(parse_assist_lines("A: Una explicación larga sin respuesta corta."))

    def test_empty_answer_means_no_clear_question(self):
        self.assertIsNone(parse_assist_lines("R:"))
        self.assertIsNone(parse_assist_lines(""))
        self.assertIsNone(parse_assist_lines("cualquier cosa sin prefijo"))

    def test_caps_ideas_at_three(self):
        assist = parse_assist_lines("R: x\nI: a; b; c; d; e")
        self.assertEqual(assist.ideas_clave, ["a", "b", "c"])

    def test_parse_assist_still_accepts_json_from_nvidia(self):
        assist = parse_assist('{"pregunta_es":"Hola","respuestas":["Hi there"]}')
        self.assertEqual(assist.respuestas, ["Hi there"])


class FakeChunk(SimpleNamespace):
    pass


class StreamCoachTests(unittest.TestCase):
    def _client(self, pieces):
        client = mock.MagicMock()
        client.models.generate_content_stream.return_value = [
            FakeChunk(text=p) for p in pieces
        ]
        return client

    def test_paints_the_answer_while_it_is_still_being_written(self):
        # The whole point: the answer is one line, so waiting for its newline
        # would mean waiting for the entire generation.
        partials: list[InterviewAssist] = []
        client = self._client(["R: Es una ", "vista ", "estática.\n"])
        _stream_coach(client, "m", "prompt", None, partials.append)

        self.assertEqual(
            [p.respuestas[0] for p in partials],
            ["Es una", "Es una vista", "Es una vista estática."],
        )
        self.assertTrue(all(p.partial for p in partials))

    def test_never_paints_half_a_word(self):
        partials: list[InterviewAssist] = []
        client = self._client(["R: Es una vis", "ta estática.\n"])
        _stream_coach(client, "m", "prompt", None, partials.append)
        # "vis" must never reach the screen.
        self.assertNotIn("vis", [p.respuestas[0] for p in partials])

    def test_short_answer_paints_before_the_expansion_closes(self):
        partials: list[InterviewAssist] = []
        client = self._client(
            ["R: Es una ", "vista estática.\n", "A: Muestra las clases y ", "sus relaciones.\n"]
        )
        _stream_coach(client, "m", "prompt", None, partials.append)

        # La corta ya estaba en pantalla antes de que llegara la ampliada.
        self.assertEqual([len(p.respuestas) for p in partials], [1, 1, 2])
        self.assertEqual(
            partials[-1].respuestas,
            ["Es una vista estática.", "Muestra las clases y sus relaciones."],
        )

    def test_later_lines_are_only_emitted_once_closed(self):
        partials: list[InterviewAssist] = []
        client = self._client(["R: Hola\n", "P: ¿Qué ", "tal?\n", "I: a; b"])
        _stream_coach(client, "m", "prompt", None, partials.append)

        glosses = [p.pregunta_es for p in partials if p.pregunta_es]
        self.assertEqual(glosses, ["¿Qué tal?"])

    def test_keeps_every_line_in_the_returned_text(self):
        client = self._client(["R: Hola\n", "P: ¿Qué tal?\n", "I: UML; estructura"])
        text = _stream_coach(client, "m", "prompt", None, None)
        self.assertIn("I: UML; estructura", text)

    def test_does_not_repeat_the_same_partial(self):
        partials: list[InterviewAssist] = []
        client = self._client(["R: Hola\n", "", "P: ¿Qué tal?\n"])
        _stream_coach(client, "m", "prompt", None, partials.append)
        self.assertEqual(len(partials), 2)

    def test_logs_time_to_first_token_once(self):
        client = self._client(["", "R: Hola\n", "P: x\n"])
        with mock.patch.object(interview_live.latency_log, "log_stage") as log_stage:
            _stream_coach(client, "modelo-x", "prompt", None, None)

        ttft_calls = [c for c in log_stage.call_args_list if c[0][1] == "coach_ttft"]
        self.assertEqual(len(ttft_calls), 1)
        self.assertEqual(ttft_calls[0][1]["model"], "modelo-x")

    def test_empty_chunks_do_not_count_as_first_token(self):
        client = self._client(["", "", "R: Hola\n"])
        with mock.patch.object(interview_live.latency_log, "log_stage") as log_stage:
            _stream_coach(client, "m", "prompt", None, None)
        self.assertEqual(
            len([c for c in log_stage.call_args_list if c[0][1] == "coach_ttft"]), 1
        )


class BrokenModelTests(unittest.TestCase):
    """A 400 is deterministic: retrying it every turn only costs latency."""

    def setUp(self):
        interview_live._broken_models.clear()
        self.addCleanup(interview_live._broken_models.clear)

    def test_recognises_permanent_errors(self):
        self.assertTrue(
            interview_live._is_permanent_model_error(
                RuntimeError("400 INVALID_ARGUMENT. Request contains an invalid argument.")
            )
        )
        self.assertTrue(
            interview_live._is_permanent_model_error(RuntimeError("404 model not found"))
        )

    def test_transient_errors_are_not_permanent(self):
        # Blacklisting on a network blip would disable a working model.
        self.assertFalse(
            interview_live._is_permanent_model_error(RuntimeError("connection reset"))
        )
        self.assertFalse(
            interview_live._is_permanent_model_error(RuntimeError("503 service unavailable"))
        )

    def test_broken_model_is_skipped_on_the_next_call(self):
        client = mock.MagicMock()
        client.models.generate_content_stream.side_effect = RuntimeError(
            "400 INVALID_ARGUMENT"
        )
        with mock.patch.object(interview_live, "_get_client", return_value=client), \
             mock.patch.object(
                 interview_live.gemini_keys.pool, "available", return_value=["k"]
             ):
            interview_live.coach_assist("ctx", "¿Qué es UML?", api_key="k")
            first_round = client.models.generate_content_stream.call_count
            client.models.generate_content_stream.reset_mock()
            interview_live.coach_assist("ctx", "¿Qué es UML?", api_key="k")

        # Every model 400s on the first call, so the second must retry them all
        # (the blacklist self-clears) instead of going silent.
        self.assertGreater(first_round, 1)
        self.assertEqual(client.models.generate_content_stream.call_count, first_round)

    def test_only_the_failing_model_is_disabled(self):
        interview_live._broken_models.clear()
        interview_live._broken_models.add(interview_live.COACH_MODELS[1])
        remaining = [
            m for m in dict.fromkeys(interview_live.COACH_MODELS)
            if m and m not in interview_live._broken_models
        ]
        self.assertNotIn(interview_live.COACH_MODELS[1], remaining)
        self.assertIn(interview_live.COACH_MODELS[0], remaining)


class CoachConfigTests(unittest.TestCase):
    def test_no_longer_forces_json_output(self):
        # JSON cannot be parsed mid-stream; the line format replaced it.
        config = interview_live._coach_config("gemini-2.5-flash", "examen_oral")
        self.assertIsNone(getattr(config, "response_mime_type", None))


if __name__ == "__main__":
    unittest.main()
