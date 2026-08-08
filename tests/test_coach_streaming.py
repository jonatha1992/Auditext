"""Adaptive flush + streaming line format — the two levers that cut Live latency."""

from __future__ import annotations

import contextlib
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


class OralCommandDetectionTests(unittest.TestCase):
    """Un examinador manda tanto preguntando como dando una consigna."""

    def test_detects_commands_without_a_question_mark(self):
        from infrastructure.services.interview_live import extract_question_candidate

        for text in (
            "Defina bucle, aristas múltiples, multigrafo y vértice aislado.",
            "Definime esto también.",
            "Enumerá los tipos de grafos.",
            "Nombrá tres ejemplos.",
            "Contame qué es un multigrafo.",
            "Resolvé el ejercicio 3.",
            "Diferenciá bucle de arista múltiple.",
            "Hablame de los multigrafos.",
            "Explicanos la diferencia.",
            "Señale los vértices aislados.",
        ):
            with self.subTest(text=text):
                self.assertTrue(extract_question_candidate(text))

    def test_keeps_a_question_the_stt_left_unclosed(self):
        # Caso real del log: el examinador aclaró "la repito una vez más", la
        # STT no cerró con "?" y el turno se descartó entero.
        from infrastructure.services.interview_live import extract_question_candidate

        self.assertEqual(
            extract_question_candidate(
                "Por supuesto. La repito una vez más. "
                "En el simulador del trabajo, ¿cuál es la diferencia"
            ),
            "¿cuál es la diferencia",
        )

    def test_prefers_the_latest_question_in_the_buffer(self):
        from infrastructure.services.interview_live import extract_question_candidate

        self.assertEqual(
            extract_question_candidate(
                "¿Qué es un grafo? Perfecto. ¿Cuál es la diferencia entre dirigido y no dirigido?"
            ),
            "¿Cuál es la diferencia entre dirigido y no dirigido?",
        )

    def test_ignores_conjugations_that_are_not_commands(self):
        # Un falso positivo dispara una llamada al coach por pura charla.
        from infrastructure.services.interview_live import extract_question_candidate

        for text in (
            "Hablamos de grafos la clase pasada.",
            "Estuve analizando el trabajo práctico.",
            "Definitivamente eso no es correcto.",
            "Vamos a comparar los resultados después.",
            "Bueno, contando desde el primero.",
        ):
            with self.subTest(text=text):
                self.assertFalse(extract_question_candidate(text))


class TurnSeparatorTests(unittest.TestCase):
    """Sin marca de fin de turno, el siguiente arranca pegado al anterior."""

    def test_closing_a_turn_emits_a_line_break(self):
        import asyncio

        emitted: list[str] = []

        async def run():
            session = InterviewLiveSession(
                context="",
                on_transcript=emitted.append,
                on_assist=lambda _a: None,
                on_status=lambda _s: None,
                mode="examen_oral",
            )
            # Turno sin consigna: se descarta, pero la línea igual se cierra.
            session._utterance_buf = "Perfecto, esa respuesta está muy bien."
            session._flush_utterance()

        asyncio.run(run())
        self.assertEqual(emitted, ["\n"])

    def test_empty_buffer_emits_nothing(self):
        import asyncio

        emitted: list[str] = []

        async def run():
            session = InterviewLiveSession(
                context="",
                on_transcript=emitted.append,
                on_assist=lambda _a: None,
                on_status=lambda _s: None,
                mode="examen_oral",
            )
            session._utterance_buf = "   "
            session._flush_utterance()

        asyncio.run(run())
        self.assertEqual(emitted, [])


class IdleThresholdTests(unittest.TestCase):
    def test_detected_question_is_shown_before_the_answer_arrives(self):
        import asyncio

        assists: list[InterviewAssist] = []

        async def run():
            session = InterviewLiveSession(
                context="",
                on_transcript=lambda _t: None,
                on_assist=assists.append,
                on_status=lambda _s: None,
                mode="examen_oral",
            )
            session._utterance_buf = "¿Qué diferencia fundamental entre clase y objeto?"
            session._flush_utterance()

        asyncio.run(run())
        self.assertEqual(len(assists), 1)
        self.assertIn("clase y objeto", assists[0].pregunta_es)
        self.assertEqual(assists[0].respuestas, [])

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

    def test_actionable_question_without_punctuation_uses_intermediate_threshold(self):
        session = self._session()
        session._utterance_buf = "Qué diferencia hay entre un caso de uso y un diagrama de clases"
        self.assertEqual(
            session._idle_threshold(),
            InterviewLiveSession._FLUSH_ACTIONABLE_SECONDS,
        )

    def test_real_fragmented_question_is_reconstructed(self):
        session = self._session()
        self.assertIsNone(
            session._interpret_or_hold("Vale. En un día rama de clases", now=100.0)
        )
        result = session._interpret_or_hold(
            "eh representa una asociación con multiplicidad.", now=104.5
        )
        self.assertIsNotNone(result)
        self.assertEqual(
            result.question,
            "¿En un diagrama de clases qué representa una asociación con multiplicidad?",
        )

    def test_expired_fragment_is_not_joined_to_a_new_one(self):
        session = self._session()
        session._interpret_or_hold("En un día rama de clases", now=100.0)
        result = session._interpret_or_hold(
            "eh representa una asociación con multiplicidad.", now=108.0
        )
        self.assertIsNotNone(result)
        self.assertEqual(
            result.question,
            "¿qué representa una asociación con multiplicidad?",
        )

    def test_slow_threshold_still_tolerates_natural_pauses(self):
        # Same guarantee the old fixed timer gave; see test_interview_answer_lang.
        self.assertGreaterEqual(InterviewLiveSession._FLUSH_IDLE_SECONDS, 2.5)
        self.assertLess(
            InterviewLiveSession._FLUSH_FAST_SECONDS,
            InterviewLiveSession._FLUSH_IDLE_SECONDS,
        )
        self.assertLessEqual(InterviewLiveSession._FLUSH_FAST_SECONDS, 0.7)


class DeliveryRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_once_when_question_was_not_delivered(self):
        session = InterviewLiveSession(
            context="",
            on_transcript=lambda _t: None,
            on_assist=lambda _a: None,
            on_status=lambda _s: None,
            mode="examen_oral",
        )
        session._api_key = "test-key"
        session._coach_once = mock.AsyncMock(return_value=False)

        await session._run_coach("¿Qué diferencia hay entre clase y objeto?")

        self.assertEqual(session._coach_once.await_count, 2)


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

    def test_expansion_is_split_into_bullets(self):
        # El modelo debe mandar A en UNA línea (el formato se parsea línea a
        # línea), así que las viñetas las pone la app al mostrar.
        assist = parse_assist_lines(
            "R: Un bucle es una arista que une un vértice consigo mismo.\n"
            "A: Por ejemplo, en un grafo de 3 vértices, una arista de A hacia A. "
            "Otro caso: en un multigrafo dirigido, dos bucles sobre el mismo vértice.\n"
        )
        self.assertEqual(
            assist.respuestas[1],
            "• Por ejemplo, en un grafo de 3 vértices, una arista de A hacia A.\n"
            "• Otro caso: en un multigrafo dirigido, dos bucles sobre el mismo vértice.",
        )
        # La corta se muestra tal cual, sin viñetas.
        self.assertNotIn("•", assist.respuestas[0])

    def test_strips_latex_from_a_spoken_answer(self):
        # Caso real: el TTS leía "$a$" como "dólar a dólar".
        assist = parse_assist_lines(
            "R: El máximo común divisor de $a$ y $b$ debe dividir a $c$.\n"
            "A: Si no se cumple, no existen enteros $x$ e $y$ que satisfagan la ecuación.\n"
        )
        self.assertEqual(
            assist.respuestas[0],
            "El máximo común divisor de a y b debe dividir a c.",
        )
        self.assertEqual(
            assist.respuestas[1],
            "Si no se cumple, no existen enteros x e y que satisfagan la ecuación.",
        )

    def test_translates_math_instead_of_deleting_it(self):
        # Borrar la notación dejaría la respuesta incompleta: hay que decirla.
        assist = parse_assist_lines(
            "R: La probabilidad es \\frac{3}{4} del total.\n"
            "A: Como \\sqrt{16} es 4 y a \\cdot b \\neq 0, queda \\frac{a}{b}.\n"
        )
        self.assertEqual(assist.respuestas[0], "La probabilidad es 3 sobre 4 del total.")
        self.assertEqual(
            assist.respuestas[1],
            "Como raíz de 16 es 4 y a por b distinto de 0, queda a sobre b.",
        )

    def test_unknown_command_keeps_its_name(self):
        # "\\gcd" sin el nombre dejaría "(a,b)", que no se entiende al leerlo.
        assist = parse_assist_lines("R: El \\gcd(a,b) tiene que dividir a c.")
        self.assertEqual(assist.respuestas[0], "El gcd(a,b) tiene que dividir a c.")

    def test_plain_arithmetic_is_left_alone(self):
        assist = parse_assist_lines("R: La mitad de 8 es 4 y 3/4 es mayor que 1/2.")
        self.assertEqual(
            assist.respuestas[0], "La mitad de 8 es 4 y 3/4 es mayor que 1/2."
        )

    def test_strips_markdown_and_latex_wrappers(self):
        assist = parse_assist_lines(
            "R: Es la **vista estática** del sistema.\n"
            "A: Se escribe \\text{gcd} y se lee en voz alta.\n"
            "I: `UML`; \\(x\\) mayor a cero; diseño\n"
        )
        self.assertEqual(assist.respuestas[0], "Es la vista estática del sistema.")
        self.assertEqual(assist.respuestas[1], "Se escribe gcd y se lee en voz alta.")
        self.assertEqual(assist.ideas_clave, ["UML", "x mayor a cero", "diseño"])

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
             ), \
             mock.patch.object(
                 interview_live.nvidia_provider, "is_configured", return_value=False
             ):
            with contextlib.suppress(interview_live.CoachUnavailable):
                interview_live.coach_assist("ctx", "¿Qué es UML?", api_key="k")
            first_round = client.models.generate_content_stream.call_count
            client.models.generate_content_stream.reset_mock()
            with contextlib.suppress(interview_live.CoachUnavailable):
                interview_live.coach_assist("ctx", "¿Qué es UML?", api_key="k")

        # Permanent 400s stay blacklisted. Retrying all of them before the
        # fallback on every turn made live assistance appear frozen.
        self.assertGreater(first_round, 1)
        self.assertEqual(client.models.generate_content_stream.call_count, 0)

    def test_a_400_over_thinking_budget_retries_without_it_instead_of_blacklisting(self):
        """The 93 logged 400s were a bad parameter, not a dead model.

        Gemini 3.x rejects thinking_budget=0, and because a 400 also reads as a
        permanent model error, a perfectly healthy model was being disabled for
        the whole session over a field we can simply drop.
        """
        interview_live._broken_models.clear()
        interview_live._no_thinking_models.clear()
        model = interview_live.COACH_MODELS[0]
        answered = "R: " + " ".join(["respuesta"] * 120) + "\nP: Pregunta\nI: idea"

        calls: list[bool] = []

        def stream(*, model, contents, config):
            has_thinking = getattr(config, "thinking_config", None) is not None
            calls.append(has_thinking)
            if has_thinking:
                raise RuntimeError("400 INVALID_ARGUMENT: thinking_budget")
            return iter([SimpleNamespace(text=answered)])

        client = mock.MagicMock()
        client.models.generate_content_stream.side_effect = stream
        with mock.patch.object(interview_live, "_get_client", return_value=client), \
             mock.patch.object(
                 interview_live.gemini_keys.pool, "available", return_value=["k"]
             ), \
             mock.patch.object(interview_live, "COACH_MODELS", [model]):
            assist = interview_live.coach_assist("ctx", "¿Qué es UML?", api_key="k")

        self.assertIsNotNone(assist)
        self.assertEqual(calls, [True, False])
        self.assertIn(model, interview_live._no_thinking_models)
        self.assertNotIn(model, interview_live._broken_models)

    def test_dropped_models_are_gone_from_the_coach_list(self):
        """2.5-flash-lite and 2.5-flash answer 404 on every key now."""
        self.assertNotIn("gemini-2.5-flash-lite", interview_live.COACH_MODELS)
        self.assertNotIn("gemini-2.5-flash", interview_live.COACH_MODELS)

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
