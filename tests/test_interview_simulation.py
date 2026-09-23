"""Adaptive interview simulation behavior without network access."""

from __future__ import annotations

import json
import time
import unittest
from unittest import mock

from infrastructure.services import interview_simulation as sim, provider_chain
from infrastructure.services.interview_simulation import (
    InterviewSimulationSession,
    SimulationOutputError,
    parse_simulation_turn,
)


class InterviewSimulationParsingTests(unittest.TestCase):
    def test_parses_question_and_feedback_contract(self):
        turn = parse_simulation_turn(
            '{"action":"follow_up","question":"What did you learn?",'
            '"feedback":"Clear example.","strengths":["specific"],'
            '"improvements":["quantify the result"],'
            '"example_answer":"I reduced processing time by 30 percent."}'
        )

        self.assertEqual(turn.action, "follow_up")
        self.assertEqual(turn.question, "What did you learn?")
        self.assertEqual(turn.strengths, ("specific",))
        self.assertEqual(
            turn.example_answer,
            "I reduced processing time by 30 percent.",
        )

    def test_rejects_invalid_action(self):
        with self.assertRaises(SimulationOutputError):
            parse_simulation_turn(
                '{"action":"continue_forever","question":"Next?",'
                '"feedback":"","strengths":[],"improvements":[]}'
            )


class InterviewSimulationSessionTests(unittest.TestCase):
    def test_mastered_question_advances_without_faking_an_answer(self):
        outputs = iter([
            '{"action":"next_topic","question":"¿Qué es homeostasis?",'
            '"feedback":"","strengths":[],"improvements":[]}',
            '{"action":"next_topic","question":"¿Qué es equifinalidad?",'
            '"feedback":"","strengths":[],"improvements":[]}',
        ])
        session = InterviewSimulationSession(
            context="TGS", generate=lambda _prompt: next(outputs),
            simulation_type="academic",
        )
        session.start()

        turn = session.skip_mastered_question()

        self.assertEqual(turn.question, "¿Qué es equifinalidad?")
        self.assertEqual(session.question_count, 2)
        self.assertFalse(any(role == "CANDIDATE" for role, _ in session._history))

    def test_academic_unknown_answer_requests_teaching_feedback(self):
        prompts = []
        outputs = iter([
            '{"action":"next_topic","question":"¿Qué es equifinalidad?",'
            '"feedback":"","strengths":[],"improvements":[],"example_answer":""}',
            '{"action":"follow_up","question":"¿Podés decirlo con tus palabras?",'
            '"feedback":"Es la posibilidad de alcanzar un mismo resultado desde condiciones distintas.",'
            '"strengths":[],"improvements":["Distinguir estado inicial y resultado"],'
            '"example_answer":"Un sistema equifinal llega al mismo resultado desde estados iniciales diferentes."}',
        ])

        def generate(prompt):
            prompts.append(prompt)
            return next(outputs)

        session = InterviewSimulationSession(
            context="Teoría general de sistemas",
            generate=generate,
            simulation_type="academic",
        )
        session.start()
        session.submit_answer(
            "No lo sé. Explicame el concepto y mostrame un ejemplo."
        )

        self.assertIn("teach the concept", prompts[-1].lower())
        self.assertIn("easier follow-up", prompts[-1].lower())

    def test_hint_does_not_advance_the_session_or_reveal_the_answer(self):
        outputs = iter([
            '{"action":"next_topic","question":"What is a class?","feedback":"","strengths":[],"improvements":[]}',
            '{"hint":"Think about the template used to create objects."}',
        ])
        session = InterviewSimulationSession(
            context="UML", generate=lambda _prompt: next(outputs), simulation_type="academic"
        )
        session.start()

        hint = session.hint()

        self.assertIn("template", hint)
        self.assertEqual(session.question_count, 1)
        self.assertEqual(session.state, "waiting_answer")

    def test_duplicate_follow_up_is_regenerated(self):
        outputs = iter([
            '{"action":"next_topic","question":"What is a class?","feedback":"","strengths":[],"improvements":[]}',
            '{"action":"follow_up","question":"What is a class?","feedback":"Good.","strengths":[],"improvements":[]}',
            '{"action":"next_topic","question":"How do classes and objects differ?","feedback":"Good.","strengths":[],"improvements":[]}',
        ])
        session = InterviewSimulationSession(
            context="UML", generate=lambda _prompt: next(outputs), simulation_type="academic"
        )
        session.start()

        turn = session.submit_answer("A class is a template.")

        self.assertIn("differ", turn.question)
        self.assertEqual(session.question_count, 2)

    def test_academic_practice_prompts_as_an_oral_exam_professor(self):
        prompts: list[str] = []

        def generate(prompt: str) -> str:
            prompts.append(prompt)
            return (
                '{"action":"next_topic","question":"¿Qué diferencia hay entre clase y objeto?",'
                '"feedback":"","strengths":[],"improvements":[]}'
            )

        session = InterviewSimulationSession(
            context="UML: clases, objetos y diagramas",
            generate=generate,
            simulation_type="academic",
        )

        session.start()

        self.assertIn("oral exam professor", prompts[0])
        self.assertIn("UML: clases, objetos y diagramas", prompts[0])
        self.assertIn("correctness", prompts[0])

    def test_starts_proactively_with_a_question(self):
        prompts: list[str] = []

        def generate(prompt: str) -> str:
            prompts.append(prompt)
            return (
                '{"action":"next_topic","question":"Tell me about yourself.",'
                '"feedback":"","strengths":[],"improvements":[]}'
            )

        session = InterviewSimulationSession(
            context="Backend developer role", generate=generate, max_questions=4
        )

        turn = session.start()

        self.assertEqual(turn.question, "Tell me about yourself.")
        self.assertEqual(session.state, "waiting_answer")
        self.assertIn("Backend developer role", prompts[0])

    def test_reacts_to_answer_with_a_follow_up(self):
        outputs = iter(
            [
                '{"action":"next_topic","question":"Describe a challenge.",'
                '"feedback":"","strengths":[],"improvements":[]}',
                '{"action":"follow_up","question":"What was the measurable result?",'
                '"feedback":"Good structure.","strengths":["clear context"],'
                '"improvements":["add metrics"]}',
            ]
        )
        session = InterviewSimulationSession(
            context="Engineering", generate=lambda _prompt: next(outputs)
        )
        session.start()

        turn = session.submit_answer("I reduced the processing time.")

        self.assertEqual(turn.action, "follow_up")
        self.assertIn("measurable result", turn.question)
        self.assertEqual(session.question_count, 2)
        self.assertIn("Recomendación:", session.report())

    def test_finishes_at_question_limit_even_if_model_continues(self):
        outputs = iter(
            [
                '{"action":"next_topic","question":"Question one?",'
                '"feedback":"","strengths":[],"improvements":[]}',
                '{"action":"next_topic","question":"Question two?",'
                '"feedback":"Good.","strengths":["clear"],'
                '"improvements":["detail"]}',
            ]
        )
        session = InterviewSimulationSession(
            context="Role", generate=lambda _prompt: next(outputs), max_questions=1
        )
        session.start()

        turn = session.submit_answer("My answer")

        self.assertEqual(turn.action, "finish")
        self.assertEqual(session.state, "completed")
        self.assertFalse(turn.question)

    def test_manual_finish_builds_bounded_feedback_report(self):
        session = InterviewSimulationSession(
            context="Role",
            generate=lambda _prompt: (
                '{"action":"next_topic","question":"Why this role?",'
                '"feedback":"","strengths":[],"improvements":[]}'
            ),
        )
        session.start()

        report = session.finish()

        self.assertEqual(session.state, "completed")
        self.assertIn("Simulacro finalizado", report)

    def test_reading_report_does_not_finish_an_active_session(self):
        session = InterviewSimulationSession(
            context="Role",
            generate=lambda _prompt: (
                '{"action":"next_topic","question":"Why this role?",'
                '"feedback":"","strengths":[],"improvements":[]}'
            ),
        )
        session.start()

        session.report()

        self.assertEqual(session.state, "waiting_answer")


class _Pool:
    """Stand-in for ``gemini_keys.pool`` with no keys on disk involved."""

    def __init__(self, keys):
        self._keys = list(keys)
        self.exhausted: list[str] = []

    def available(self):
        return [k for k in self._keys if k not in self.exhausted]

    def mark_exhausted(self, key=None):
        self.exhausted.append(key)
        return None

    def is_quota_error(self, exc):
        detail = str(exc).lower()
        return "429" in detail or "quota" in detail

    def current_label(self):
        return "API 1/2"


class GeminiKeyRotationTests(unittest.TestCase):
    """El turno se pedía con ``pool.available()[0]`` y un solo intento.

    Un 503 transitorio en la primera key mataba el turno entero aunque hubiera
    otra key sana detrás, y con ella la devolución del profesor.
    """

    def setUp(self):
        self.pool = _Pool(["key-1", "key-2"])
        patcher = mock.patch.object(sim.gemini_keys, "pool", self.pool)
        patcher.start()
        self.addCleanup(patcher.stop)
        # El backoff se prueba por su lógica; nadie espera 1,5 s por test.
        sleep_patcher = mock.patch.object(sim.time, "sleep")
        self.sleep = sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    def _deadline(self):
        return time.monotonic() + 60.0

    def test_a_503_retries_the_same_key_before_moving_on(self):
        calls = []

        def request(key, _prompt, _timeout):
            calls.append(key)
            if len(calls) == 1:
                raise RuntimeError("503 UNAVAILABLE. This model is overloaded")
            return '{"action":"finish","question":""}'

        with mock.patch.object(sim, "_gemini_request", side_effect=request):
            text = sim._generate_with_gemini("prompt", self._deadline())

        self.assertEqual(calls, ["key-1", "key-1"])
        self.assertIn("finish", text)
        self.assertEqual(self.pool.exhausted, [])
        self.sleep.assert_called_once()

    def test_a_saturated_key_falls_through_to_the_next_one(self):
        calls = []

        def request(key, _prompt, _timeout):
            calls.append(key)
            if key == "key-1":
                raise RuntimeError("503 UNAVAILABLE overloaded")
            return '{"action":"finish","question":""}'

        with mock.patch.object(sim, "_gemini_request", side_effect=request):
            text = sim._generate_with_gemini("prompt", self._deadline())

        self.assertEqual(calls, ["key-1", "key-1", "key-2"])
        self.assertIn("finish", text)

    def test_a_quota_error_burns_the_key_without_retrying_it(self):
        calls = []

        def request(key, _prompt, _timeout):
            calls.append(key)
            if key == "key-1":
                raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")
            return '{"action":"finish","question":""}'

        with mock.patch.object(sim, "_gemini_request", side_effect=request):
            sim._generate_with_gemini("prompt", self._deadline())

        # Reintentar una key sin cuota es gastar la espera del usuario al pedo.
        self.assertEqual(calls, ["key-1", "key-2"])
        self.assertEqual(self.pool.exhausted, ["key-1"])
        self.sleep.assert_not_called()

    def test_an_expired_deadline_stops_the_rotation(self):
        with mock.patch.object(sim, "_gemini_request") as request:
            result = sim._generate_with_gemini("prompt", time.monotonic() - 1.0)

        self.assertIsNone(result)
        request.assert_not_called()

    def test_every_key_failing_returns_none_instead_of_raising(self):
        with mock.patch.object(
            sim, "_gemini_request", side_effect=RuntimeError("503 overloaded")
        ):
            self.assertIsNone(sim._generate_with_gemini("prompt", self._deadline()))


class GeminiRequestTimeoutTests(unittest.TestCase):
    """Gemini tiene que estar acotado DOS veces, como Groq y NVIDIA.

    Lo estaba una sola: el presupuesto se miraba *entre* intentos, nunca
    durante uno, asi que un request colgado corria sin techo. Medido en
    `logs/error_log.txt`: el control volvia a los 15,9 s con los 40 s de
    TOTAL_BUDGET_SECONDS ya gastados y la cadena de respaldo arrancaba muerta
    ("Groq: sin tiempo restante"). Eso es el "responde la primera pregunta y
    despues se queda" que reporto el usuario.
    """

    def test_the_timeout_is_derived_from_the_budget_and_is_not_a_constant(self):
        """El punto entero: dos presupuestos distintos, dos timeouts distintos."""
        amplio = sim.gemini_request_timeout(30.0)
        angosto = sim.gemini_request_timeout(10.0)
        self.assertNotEqual(amplio, angosto)
        self.assertGreater(amplio, angosto)
        # Y escala con el presupuesto, no es un escalon arbitrario. Con 30 s
        # toca la mitad (15 s); con 10 s la mitad seria ilegal, asi que se le da
        # el piso exacto de la API (10 s). Razon 1,5 -> derivado por arriba de
        # un piso duro, que es el contrato entero.
        self.assertAlmostEqual(amplio / angosto, 1.5, places=5)

    def test_a_single_attempt_never_outlives_the_budget(self):
        """La regla que faltaba: el request no puede durar mas de lo que queda."""
        for remaining in (4.0, 8.0, 16.0, 40.0):
            with self.subTest(remaining=remaining):
                timeout = sim.gemini_request_timeout(remaining)
                # `None` = no entra un deadline legal y la llamada se saltea,
                # que tambien respeta la regla: no dura mas de lo que queda.
                if timeout is not None:
                    self.assertLessEqual(timeout, remaining)

    def test_one_attempt_does_not_eat_the_whole_budget(self):
        """Si un request se lleva todo, las otras nueve claves no existen."""
        self.assertLess(sim.gemini_request_timeout(30.0), 30.0)

    def test_a_thin_budget_skips_the_call_instead_of_faking_a_socket(self):
        """Darle "el piso" a un presupuesto flaco ERA el bug.

        La version anterior devolvia 4 s creyendo que un socket corto es mejor
        que ninguno. No lo es: 4 s esta por debajo del minimo que acepta Gemini,
        asi que ese request no era corto, era un 400 garantizado que ademas
        gastaba el turno de una key. Con menos de 10 s la respuesta correcta es
        no llamar.
        """
        self.assertIsNone(sim.gemini_request_timeout(6.0))

    def test_the_rotation_passes_the_derived_timeout_to_the_request(self):
        pool = _Pool(["key-1"])
        with (
            mock.patch.object(sim.gemini_keys, "pool", pool),
            mock.patch.object(
                sim, "_gemini_request", return_value='{"action":"finish","question":""}'
            ) as request,
        ):
            sim._generate_with_gemini("prompt", time.monotonic() + 30.0)

        timeout = request.call_args.args[2]
        self.assertGreater(timeout, sim._MIN_GEMINI_REQUEST_SECONDS)
        self.assertLessEqual(timeout, 30.0)

    def test_a_budget_below_the_floor_skips_gemini_instead_of_starting(self):
        """Arrancar con dos segundos de aire solo se los roba al respaldo."""
        pool = _Pool(["key-1"])
        with (
            mock.patch.object(sim.gemini_keys, "pool", pool),
            mock.patch.object(sim, "_gemini_request") as request,
        ):
            result = sim._generate_with_gemini(
                "prompt", time.monotonic() + sim._MIN_GEMINI_REQUEST_SECONDS - 1.0
            )

        self.assertIsNone(result)
        request.assert_not_called()

    def test_http_options_carry_milliseconds_not_seconds(self):
        """`HttpOptions.timeout` va en ms; mandarle segundos da un techo de 12 ms.

        Se usa el `HttpOptions` real de google-genai a proposito: un fake que
        acepte cualquier cosa no probaria nada sobre la unidad, que es lo unico
        que este test cuida.
        """
        from google import genai

        captured = {}

        class _FakeClient:
            def __init__(self, api_key, http_options):
                captured["http_options"] = http_options
                self.models = self

            def generate_content(self, **_kwargs):
                return mock.Mock(text='{"action":"finish","question":""}')

        with mock.patch.object(genai, "Client", _FakeClient):
            sim._gemini_request("key-1", "prompt", 12.0)

        # 12 s -> 12000 ms. Si alguien pasa segundos, esto da 12 y el request
        # muere antes de salir de la maquina.
        self.assertEqual(captured["http_options"].timeout, 12000)


class SimulationProviderChainTests(unittest.TestCase):
    """El simulacro encadenaba Gemini -> NVIDIA y se salteaba Groq."""

    def setUp(self):
        patcher = mock.patch.object(sim.gemini_keys, "pool", _Pool(["key-1"]))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_gemini_answers_and_the_fallback_chain_stays_untouched(self):
        with mock.patch.object(
            sim, "_generate_with_gemini", return_value="respuesta de gemini"
        ), mock.patch.object(sim.provider_chain, "generate_with_fallback") as chain:
            self.assertEqual(
                sim.generate_simulation_text("prompt"), "respuesta de gemini"
            )

        chain.assert_not_called()

    def test_gemini_failing_walks_the_same_chain_as_the_coach(self):
        # Con Gemini en 503 y el modelo NVIDIA por defecto en end of life, esto
        # era un RuntimeError y el profesor IA quedaba mudo con Groq disponible.
        with mock.patch.object(
            sim, "_generate_with_gemini", return_value=None
        ), mock.patch.object(
            sim.provider_chain, "has_fallback_provider", return_value=True
        ), mock.patch.object(
            sim.provider_chain, "generate_with_fallback", return_value="respaldo"
        ) as chain:
            self.assertEqual(sim.generate_simulation_text("prompt"), "respaldo")

        self.assertEqual(chain.call_args.kwargs["max_tokens"], sim._MAX_OUTPUT_TOKENS)
        self.assertIn("deadline", chain.call_args.kwargs)

    def test_no_provider_at_all_names_the_env_vars_to_set(self):
        with mock.patch.object(
            sim, "_generate_with_gemini", return_value=None
        ), mock.patch.object(
            sim.provider_chain, "has_fallback_provider", return_value=False
        ), self.assertRaises(RuntimeError) as ctx:
            sim.generate_simulation_text("prompt")

        message = str(ctx.exception)
        self.assertIn("GROQ_API_KEY", message)
        self.assertIn("NVIDIA_API_KEY", message)

    def test_is_configured_counts_the_fallback_chain_too(self):
        with mock.patch.object(
            sim.gemini_keys, "is_configured", return_value=False
        ), mock.patch.object(
            sim.provider_chain, "has_fallback_provider", return_value=True
        ):
            self.assertTrue(sim.is_configured())


# Corte real, reproducido el 2026-09-20 en el tercer turno de un práctico oral
# con proveedores de verdad: el modelo se quedó sin techo de salida, la API
# cerró con ``finish_reason=length`` y el JSON llegó partido a mitad de un
# string — "Unterminated string starting at: line 7 column 21 (char 578)".
# La forma es lo que importa: campos completos arriba, un string abierto abajo.
_TRUNCATED_TURN = """{
  "action": "follow_up",
  "question": "¿Qué diferencia hay entre una clave primaria y una clave foránea?",
  "feedback": "Explicaste bien la unicidad, pero no mencionaste la integridad referencial.",
  "strengths": ["Definió la unicidad", "Usó un ejemplo concreto"],
  "improvements": ["Faltó la integridad referencial", "No mencionó los índices"],
  "example_answer": "La clave primaria identifica de forma única cada fila y no admite nulos; la clave forá"""

_OPENING_TURN = (
    '{"action":"next_topic","question":"¿Qué es una clave primaria?",'
    '"feedback":"","strengths":[],"improvements":[],"example_answer":""}'
)
_NEXT_TURN = (
    '{"action":"next_topic","question":"¿Y un índice para qué sirve?",'
    '"feedback":"Bien.","strengths":["claridad"],"improvements":[],'
    '"example_answer":"Un índice acelera la búsqueda."}'
)


class TruncatedOutputRecoveryTests(unittest.TestCase):
    """Una respuesta cortada no puede costar el turno ni la sesión."""

    def test_the_fixture_is_a_genuinely_broken_json(self):
        # Si esto dejara de romper, los tests de abajo probarían nada.
        with self.assertRaises(json.JSONDecodeError) as ctx:
            json.loads(_TRUNCATED_TURN)
        self.assertIn("Unterminated string", str(ctx.exception))

    def test_a_truncated_turn_keeps_the_question_and_the_feedback(self):
        turn = parse_simulation_turn(_TRUNCATED_TURN)

        self.assertEqual(turn.action, "follow_up")
        self.assertIn("clave foránea", turn.question)
        self.assertIn("integridad referencial", turn.feedback)
        self.assertEqual(len(turn.strengths), 2)
        self.assertEqual(len(turn.improvements), 2)

    def test_the_half_written_field_is_dropped_not_closed(self):
        # Media respuesta modelo presentada como completa es peor que ninguna.
        turn = parse_simulation_turn(_TRUNCATED_TURN)

        self.assertEqual(turn.example_answer, "")

    def test_a_cut_before_the_question_is_not_invented(self):
        with self.assertRaises(SimulationOutputError):
            parse_simulation_turn('{"action":"follow_up","question":"¿Qué es un ín')

    def test_a_cut_inside_a_list_closes_the_list(self):
        turn = parse_simulation_turn(
            '{"action":"next_topic","question":"¿Y bien?","feedback":"ok",'
            '"strengths":["uno","do'
        )

        self.assertEqual(turn.question, "¿Y bien?")
        self.assertEqual(turn.strengths, ("uno",))

    def test_balanced_but_invalid_json_is_still_rejected(self):
        # El rescate es para cortes, no una tapa para un contrato roto.
        self.assertIsNone(sim._close_truncated_json('{"action": nope}'))
        with self.assertRaises(SimulationOutputError):
            parse_simulation_turn("no soy json, soy prosa")

    def test_valid_json_never_goes_through_the_repair_path(self):
        with mock.patch.object(sim, "_close_truncated_json") as repair:
            parse_simulation_turn(_OPENING_TURN)
        repair.assert_not_called()


class TruncatedOutputSessionSurvivalTests(unittest.TestCase):
    """El test que define si el fix sirve: la sesión sigue viva."""

    def test_a_truncated_answer_does_not_kill_the_session(self):
        outputs = iter([_OPENING_TURN, _TRUNCATED_TURN, _NEXT_TURN])
        session = InterviewSimulationSession(
            "Bases de datos",
            generate=lambda prompt: next(outputs),
            simulation_type="academic",
        )
        session.start()

        turn = session.submit_answer("Identifica una fila de forma única.")

        self.assertIn("clave foránea", turn.question)
        self.assertEqual(session.state, "waiting_answer")
        # Y sigue andando: el turno siguiente no hereda nada roto.
        self.assertEqual(
            session.submit_answer("Apunta a la clave primaria de otra tabla.").action,
            "next_topic",
        )
        self.assertEqual(session.question_count, 3)

    def test_an_unrecoverable_turn_is_retried_once_with_a_repair_hint(self):
        prompts: list[str] = []
        outputs = iter([_OPENING_TURN, "perdón, no puedo responder eso", _NEXT_TURN])

        def generate(prompt: str) -> str:
            prompts.append(prompt)
            return next(outputs)

        session = InterviewSimulationSession("Bases de datos", generate=generate)
        session.start()

        turn = session.submit_answer("Mi respuesta.")

        self.assertEqual(turn.question, "¿Y un índice para qué sirve?")
        self.assertEqual(len(prompts), 3)
        self.assertTrue(prompts[2].startswith(prompts[1]))
        self.assertIn(sim._RETRY_INSTRUCTION, prompts[2])

    def test_the_retry_is_bounded_and_does_not_beg_for_quota(self):
        calls = []

        def generate(prompt: str) -> str:
            calls.append(prompt)
            return _OPENING_TURN if len(calls) == 1 else "basura"

        session = InterviewSimulationSession("Bases de datos", generate=generate)
        session.start()

        with self.assertRaises(SimulationOutputError):
            session.submit_answer("Mi respuesta.")

        self.assertEqual(len(calls), 1 + sim._TURN_ATTEMPTS)

    def test_the_retry_spends_what_is_left_not_a_brand_new_budget(self):
        """El reintento va acotado por lo que queda, no por 40 s nuevos.

        ``self.generate`` es ``generate_simulation_text``, que sin
        ``budget_seconds`` abre una ventana NUEVA de Gemini+respaldo. Una
        respuesta inválida lenta encadenaba dos ventanas completas y congelaba
        el simulacro bastante más allá del techo documentado. Es la misma regla
        de CLAUDE.md: todo proveedor va acotado dos veces.
        """
        budgets: list[float | None] = []
        calls: list[str] = []

        def generate(prompt: str, *, budget_seconds: float | None = None) -> str:
            calls.append(prompt)
            budgets.append(budget_seconds)
            return _OPENING_TURN if len(calls) == 1 else "basura"

        session = InterviewSimulationSession("Bases de datos", generate=generate)
        # start() consume un monotonic(); submit_answer consume el suyo y uno
        # por cada intento fallido.
        with mock.patch.object(
            sim.time, "monotonic", side_effect=[0.0, 0.0, 12.0, 13.0]
        ):
            session.start()
            with self.assertRaises(SimulationOutputError):
                session.submit_answer("Mi respuesta.")

        self.assertEqual(budgets[-1], sim.TOTAL_BUDGET_SECONDS - 12.0)

    def test_a_generate_that_cannot_take_a_budget_is_still_called(self):
        """Los callables inyectados de un solo argumento siguen funcionando."""
        calls: list[str] = []

        def generate(prompt: str) -> str:
            calls.append(prompt)
            return _OPENING_TURN if len(calls) == 1 else "basura"

        session = InterviewSimulationSession("Bases de datos", generate=generate)
        session.start()
        with self.assertRaises(SimulationOutputError):
            session.submit_answer("Mi respuesta.")

        self.assertEqual(len(calls), 1 + sim._TURN_ATTEMPTS)

    def test_an_exhausted_budget_skips_the_retry_instead_of_freezing(self):
        session = InterviewSimulationSession(
            "Bases de datos", generate=lambda prompt: "basura"
        )
        with mock.patch.object(
            sim.time, "monotonic", side_effect=[0.0, sim.TOTAL_BUDGET_SECONDS + 1.0]
        ), self.assertRaises(SimulationOutputError):
            session.start()

    def test_a_dead_turn_leaves_the_session_answerable(self):
        outputs = iter([_OPENING_TURN, "basura", "basura", _NEXT_TURN])
        session = InterviewSimulationSession(
            "Bases de datos", generate=lambda prompt: next(outputs)
        )
        session.start()

        with self.assertRaises(SimulationOutputError):
            session.submit_answer("Identifica una fila.")

        # Sin esto la sesión quedaba en "evaluating" y el reintento moría con
        # "The simulation is not waiting for an answer".
        self.assertEqual(session.state, "waiting_answer")
        turn = session.submit_answer("Identifica una fila.")
        self.assertEqual(turn.question, "¿Y un índice para qué sirve?")

    def test_a_failed_turn_does_not_leave_the_answer_twice_in_the_history(self):
        outputs = iter([_OPENING_TURN, "basura", "basura", _NEXT_TURN])
        session = InterviewSimulationSession(
            "Bases de datos", generate=lambda prompt: next(outputs)
        )
        session.start()
        with self.assertRaises(SimulationOutputError):
            session.submit_answer("Identifica una fila.")
        session.submit_answer("Identifica una fila.")

        answers = [text for role, text in session._history if role == "CANDIDATE"]
        self.assertEqual(answers, ["Identifica una fila."])

    def test_skipping_a_question_also_survives_a_dead_turn(self):
        outputs = iter([_OPENING_TURN, "basura", "basura", _NEXT_TURN])
        session = InterviewSimulationSession(
            "Bases de datos", generate=lambda prompt: next(outputs)
        )
        session.start()

        with self.assertRaises(SimulationOutputError):
            session.skip_mastered_question()

        self.assertEqual(session.state, "waiting_answer")
        self.assertEqual(
            session.skip_mastered_question().question, "¿Y un índice para qué sirve?"
        )


class OutputBudgetTests(unittest.TestCase):
    """El techo de salida tiene aire y no estrangula la cadena de respaldo."""

    def test_the_ceiling_clears_the_measured_output_size(self):
        # Medido sobre el prompt real: 312-544 tokens típicos, con cola sobre
        # los 700 que truncaba. 700 era el borde, no un techo.
        self.assertGreaterEqual(sim._MAX_OUTPUT_TOKENS, 1200)

    def test_groq_gets_real_headroom_over_its_reasoning_floor(self):
        from infrastructure.services import groq_provider

        # `gpt-oss-120b` cobra el reasoning del mismo `max_tokens`, y
        # `effective_max_tokens` pisaba el piso en 700: el simulacro corría
        # exactamente ahí, sin margen.
        self.assertGreater(
            groq_provider.effective_max_tokens(sim._MAX_OUTPUT_TOKENS),
            groq_provider.effective_max_tokens(700),
        )

    def test_raising_the_ceiling_does_not_shrink_the_provider_attempt(self):
        from infrastructure.services import groq_provider, nvidia_provider

        budget = provider_chain._DEFAULT_PROVIDER_BUDGET_SECONDS
        for provider in (groq_provider, nvidia_provider):
            with self.subTest(provider=provider.__name__):
                before = min(provider.request_timeout_for(700), budget)
                after = min(
                    provider.request_timeout_for(sim._MAX_OUTPUT_TOKENS), budget
                )
                # El techo efectivo del intento lo fija el presupuesto del
                # proveedor (12 s), no el timeout derivado de los tokens: subir
                # tokens sube el timeout y no cambia nada acá.
                self.assertEqual(after, before)
                self.assertEqual(after, budget)


if __name__ == "__main__":
    unittest.main()


# El número sale del propio error de Gemini, no de nuestra implementación: por
# eso está escrito acá como literal en vez de leerse del módulo. Un test que
# importa la constante que quiere verificar no puede detectar que la constante
# esté mal.
GEMINI_API_MIN_DEADLINE = 10.0

_DEADLINE_400 = (
    "400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': 'Manually set "
    "deadline 8s is too short. Minimum allowed deadline is 10s.', 'status': "
    "'INVALID_ARGUMENT'}}"
)


class GeminiMinimumDeadlineTests(unittest.TestCase):
    """El piso de 10 s es contrato del servidor, no una preferencia nuestra.

    Gemini rechaza cualquier deadline menor con
    ``400 INVALID_ARGUMENT: 'Manually set deadline Ns is too short. Minimum
    allowed deadline is 10s.'``. El fix anterior inventó un piso de 4 s, así que
    con los defaults NINGÚN intento era legal: 109 de esos 400 en
    ``logs/errores.log`` y el camino de Gemini muerto al 100%.
    """

    def test_the_named_floor_matches_the_api_contract(self):
        self.assertEqual(sim._GEMINI_MIN_DEADLINE_SECONDS, GEMINI_API_MIN_DEADLINE)
        # El nombre viejo sigue existiendo porque lo usan los llamadores, pero
        # ya no puede divergir del piso real.
        self.assertEqual(sim._MIN_GEMINI_REQUEST_SECONDS, GEMINI_API_MIN_DEADLINE)

    def test_every_budget_either_yields_a_legal_deadline_or_skips_the_call(self):
        """La regla completa, barrida sobre todo el rango de presupuestos.

        Dos salidas válidas y ninguna más: un timeout que Gemini acepta
        (>= 10 s y que no sobrevive al presupuesto), o ``None`` para saltear la
        llamada. Cualquier float por debajo de 10 s es un 400 garantizado que
        encima gasta el turno de una key.
        """
        remaining = 0.5
        while remaining <= 60.0:
            with self.subTest(remaining=remaining):
                timeout = sim.gemini_request_timeout(remaining)
                if timeout is not None:
                    self.assertGreaterEqual(timeout, GEMINI_API_MIN_DEADLINE)
                    self.assertLessEqual(timeout, remaining)
            remaining += 0.5

    def test_the_default_budget_funds_a_real_first_attempt(self):
        """El defecto tiene que servir para algo: hoy el PRIMER intento ya era 400."""
        timeout = sim.gemini_request_timeout(sim.GEMINI_BUDGET_SECONDS)
        self.assertIsNotNone(timeout)
        self.assertGreaterEqual(timeout, GEMINI_API_MIN_DEADLINE)

    def test_a_budget_under_the_floor_returns_none_instead_of_an_illegal_socket(self):
        for remaining in (1.0, 4.0, 6.0, 9.9):
            with self.subTest(remaining=remaining):
                self.assertIsNone(sim.gemini_request_timeout(remaining))


class GeminiClientErrorTests(unittest.TestCase):
    """Un 400 de argumento es culpa del código, no de la clave."""

    def test_a_deadline_400_does_not_burn_a_key_nor_walk_the_pool(self):
        pool = _Pool(["key-1", "key-2", "key-3"])
        with (
            mock.patch.object(sim.gemini_keys, "pool", pool),
            mock.patch.object(
                sim, "_gemini_request", side_effect=RuntimeError(_DEADLINE_400)
            ) as request,
        ):
            result = sim._generate_with_gemini("prompt", time.monotonic() + 40.0)

        self.assertIsNone(result)
        # Ni cuota ni auth: la key sigue sana para el próximo turno.
        self.assertEqual(pool.exhausted, [])
        # Y las otras dos claves fallarían idéntico, así que ni se intentan:
        # diez llamadas condenadas le robaban el presupuesto a Groq/NVIDIA.
        self.assertEqual(request.call_count, 1)

    def test_the_classifier_still_burns_quota_and_auth_keys(self):
        """El fix no puede aflojar el caso que sí justifica quemar la clave."""
        for detail in ("429 RESOURCE_EXHAUSTED quota", "403 API key not valid"):
            with self.subTest(detail=detail):
                pool = _Pool(["key-1"])
                with (
                    mock.patch.object(sim.gemini_keys, "pool", pool),
                    mock.patch.object(
                        sim, "_gemini_request", side_effect=RuntimeError(detail)
                    ),
                ):
                    sim._generate_with_gemini("prompt", time.monotonic() + 40.0)
                self.assertEqual(pool.exhausted, ["key-1"])


class SimulationBudgetSplitTests(unittest.TestCase):
    """Peor caso: Gemini agota lo suyo y la cadena de respaldo TIENE que arrancar."""

    def _gemini_schedule(self) -> list[float]:
        """Los intentos reales que entran en el presupuesto de Gemini."""
        attempts: list[float] = []
        remaining = min(sim.GEMINI_BUDGET_SECONDS, sim.TOTAL_BUDGET_SECONDS)
        for _ in range(20):  # guarda anti-cuelgue
            timeout = sim.gemini_request_timeout(remaining)
            if timeout is None or timeout <= 0.0:
                break
            attempts.append(timeout)
            remaining -= timeout
        return attempts

    def test_every_scheduled_attempt_is_legal(self):
        for index, timeout in enumerate(self._gemini_schedule()):
            with self.subTest(attempt=index):
                self.assertGreaterEqual(timeout, GEMINI_API_MIN_DEADLINE)

    def test_the_budget_funds_at_least_two_real_attempts(self):
        """Una sola key con un 503 transitorio no puede matar el turno."""
        self.assertGreaterEqual(len(self._gemini_schedule()), 2)

    def test_both_fallbacks_still_start_after_gemini_burns_everything(self):
        consumed = sum(self._gemini_schedule())
        self.assertLessEqual(consumed, sim.GEMINI_BUDGET_SECONDS)

        left = sim.TOTAL_BUDGET_SECONDS - consumed
        groq = min(provider_chain._DEFAULT_PROVIDER_BUDGET_SECONDS, left)
        self.assertGreaterEqual(groq, provider_chain._MIN_PROVIDER_SECONDS)

        after_groq = left - groq
        nvidia = min(provider_chain._DEFAULT_PROVIDER_BUDGET_SECONDS, after_groq)
        self.assertGreaterEqual(nvidia, provider_chain._MIN_PROVIDER_SECONDS)
