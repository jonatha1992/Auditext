"""NVIDIA provider rotation and fallback tests (no network)."""

from __future__ import annotations

import os
import time
import unittest
from unittest import mock

from infrastructure.services import groq_provider, interview_live, nvidia_provider


_SATURATION_503 = (
    'NVIDIA HTTP 503: {"error":{"message":"ResourceExhausted: Worker local '
    'total request limit reached (33/32)","code":503}}'
)


class NvidiaProviderTests(unittest.TestCase):
    def test_request_timeout_is_bounded_for_live_assistance(self):
        self.assertLessEqual(nvidia_provider.NVIDIA_REQUEST_TIMEOUT_SECONDS, 20)
        self.assertLessEqual(nvidia_provider.NVIDIA_MAX_REQUEST_TIMEOUT_SECONDS, 45)

    def test_long_answers_get_a_longer_socket_timeout(self):
        short = nvidia_provider.request_timeout_for(270)
        long = nvidia_provider.request_timeout_for(1800)
        self.assertEqual(short, nvidia_provider.NVIDIA_REQUEST_TIMEOUT_SECONDS)
        self.assertGreater(long, short)
        self.assertLessEqual(long, nvidia_provider.NVIDIA_MAX_REQUEST_TIMEOUT_SECONDS)

    def test_saturation_is_classified_apart_from_quota_and_auth(self):
        self.assertEqual(
            nvidia_provider.classify_error(RuntimeError(_SATURATION_503)),
            nvidia_provider.SATURATION,
        )
        self.assertEqual(
            nvidia_provider.classify_error(RuntimeError("NVIDIA HTTP 429 rate limit")),
            nvidia_provider.RATE_LIMIT,
        )
        self.assertEqual(
            nvidia_provider.classify_error(RuntimeError("NVIDIA HTTP 403 forbidden")),
            nvidia_provider.AUTH,
        )

    def test_saturated_key_cools_down_for_seconds_not_half_a_minute(self):
        """A 503 clears upstream in seconds; a 30 s park killed whole turns."""
        saturation = nvidia_provider.cooldown_for(nvidia_provider.SATURATION)
        self.assertLessEqual(saturation, 10.0)
        self.assertLess(saturation, nvidia_provider.cooldown_for(nvidia_provider.RATE_LIMIT))

    def test_saturation_is_retried_within_the_budget_instead_of_failing_the_turn(self):
        """The logged bug: one 503 with a single key returned no answer at all."""
        pool = nvidia_provider.NvidiaKeyPool(_keys=["nv-one"])
        with (
            mock.patch.object(nvidia_provider, "pool", pool),
            mock.patch.object(nvidia_provider.time, "sleep"),
            # Cooldown 0: con sleep mockeado el reloj no avanza, asi que un
            # cooldown real convertiria el reintento en un busy-loop de 6 s.
            mock.patch.object(nvidia_provider, "cooldown_for", return_value=0.0),
            mock.patch.object(
                nvidia_provider,
                "_request",
                side_effect=[
                    nvidia_provider.NvidiaError(_SATURATION_503),
                    "respuesta completa",
                ],
            ) as request,
        ):
            self.assertEqual(
                nvidia_provider.generate("pregunta"), "respuesta completa"
            )
        self.assertEqual(request.call_count, 2)

    def test_generate_stops_at_the_budget_instead_of_hanging(self):
        pool = nvidia_provider.NvidiaKeyPool(_keys=["nv-one", "nv-two"])
        with (
            mock.patch.object(nvidia_provider, "pool", pool),
            mock.patch.object(nvidia_provider.time, "sleep"),
            mock.patch.object(
                nvidia_provider,
                "_request",
                side_effect=nvidia_provider.NvidiaError("NVIDIA HTTP 429 rate limit"),
            ),
        ):
            started = time.monotonic()
            with self.assertRaises(nvidia_provider.NvidiaError):
                nvidia_provider.generate("pregunta", budget_seconds=5.0)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 5.0)

    def test_auth_failure_is_not_swept_again(self):
        """Quota/auth fail identically on a second pass; only saturation retries."""
        pool = nvidia_provider.NvidiaKeyPool(_keys=["nv-one"])
        with (
            mock.patch.object(nvidia_provider, "pool", pool),
            mock.patch.object(nvidia_provider.time, "sleep"),
            mock.patch.object(
                nvidia_provider,
                "_request",
                side_effect=nvidia_provider.NvidiaError("NVIDIA HTTP 403 forbidden"),
            ) as request,
        ):
            with self.assertRaises(nvidia_provider.NvidiaError):
                nvidia_provider.generate("pregunta")
        self.assertEqual(request.call_count, 1)

    def test_temporarily_unavailable_key_recovers_after_cooldown(self):
        pool = nvidia_provider.NvidiaKeyPool(_keys=["nv-one"])
        with mock.patch.object(nvidia_provider.time, "monotonic", return_value=100.0):
            pool.mark_unavailable("nv-one", cooldown_seconds=30.0)
            self.assertEqual(pool.available(), [])
        with mock.patch.object(nvidia_provider.time, "monotonic", return_value=131.0):
            self.assertEqual(pool.available(), ["nv-one"])

    def test_collects_both_numbered_keys(self):
        env = {
            "NVIDIA_API_KEYS": "",
            "NVIDIA_API_KEY": "",
            "NVIDIA_API_KEY1": "nv-one",
            "NVIDIA_API_KEY2": "nv-two",
            "NVIDIA_API_KEY_1": "",
            "NVIDIA_API_KEY_2": "",
            "NVIDIA_API_KEY3": "",
            "NVIDIA_API_KEY4": "",
            "NVIDIA_API_KEY5": "",
            "NVIDIA_API_KEY_3": "",
            "NVIDIA_API_KEY_4": "",
            "NVIDIA_API_KEY_5": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            pool = nvidia_provider.NvidiaKeyPool()
            pool.reload()
        self.assertEqual(pool.available(), ["nv-one", "nv-two"])

    def test_generate_rotates_to_second_key(self):
        pool = nvidia_provider.NvidiaKeyPool(
            _keys=["nv-one", "nv-two"]
        )
        with (
            mock.patch.object(nvidia_provider, "pool", pool),
            mock.patch.object(
                nvidia_provider,
                "_request",
                side_effect=[
                    nvidia_provider.NvidiaError("NVIDIA HTTP 429"),
                    "respuesta",
                ],
            ) as request,
        ):
            self.assertEqual(nvidia_provider.generate("pregunta"), "respuesta")
        self.assertEqual(request.call_count, 2)
        self.assertNotIn("nv-one", pool.available())

    def test_coach_uses_nvidia_when_gemini_has_no_keys(self):
        raw = (
            '{"pregunta_es":"¿Qué es HTTP?",'
            '"respuestas":["HTTP es un protocolo de comunicación."],'
            '"ideas_clave":["protocolo"]}'
        )
        with (
            # Groq encabeza la cadena de respaldo, asi que hay que apagarlo o
            # estos tests pegan a la API real si el .env del dev tiene su key.
            mock.patch.object(groq_provider, "is_configured", return_value=False),
            mock.patch.object(interview_live.gemini_keys.pool, "available", return_value=[]),
            mock.patch.object(interview_live.nvidia_provider.pool, "reload"),
            mock.patch.object(interview_live.nvidia_provider, "is_configured", return_value=True),
            mock.patch.object(interview_live.nvidia_provider, "generate", return_value=raw),
        ):
            assist = interview_live.coach_assist(
                "Arquitectura web", "¿Qué es HTTP?", mode="resolver"
            )
        self.assertIsNotNone(assist)
        self.assertIn("protocolo", assist.respuestas[0])

    def test_coach_repairs_nvidia_answer_when_both_cards_are_too_short(self):
        short = "R: Respuesta mínima.\nA: Ejemplo mínimo.\nP: Pregunta\nI: idea"
        repaired = (
            f"R: {'respuesta ' * 70}\n"
            f"A: {'ampliación ' * 100}\n"
            "P: Pregunta\nI: concepto; ejemplo"
        )
        with (
            # Groq encabeza la cadena de respaldo, asi que hay que apagarlo o
            # estos tests pegan a la API real si el .env del dev tiene su key.
            mock.patch.object(groq_provider, "is_configured", return_value=False),
            mock.patch.object(interview_live.gemini_keys.pool, "available", return_value=[]),
            mock.patch.object(interview_live.nvidia_provider, "is_configured", return_value=True),
            mock.patch.object(
                interview_live.nvidia_provider,
                "generate",
                side_effect=[short, repaired],
            ) as generate,
        ):
            assist = interview_live.coach_assist(
                "Scrum", "Explicá el primer rol", mode="examen_oral"
            )

        self.assertEqual(generate.call_count, 2)
        self.assertIsNotNone(assist)
        self.assertGreaterEqual(len(assist.respuestas[0].split()), 70)
        self.assertGreaterEqual(len(assist.respuestas[1].split()), 100)

    def test_exhausted_live_key_is_not_retried_before_nvidia(self):
        raw = (
            '{"pregunta_es":"¿Qué es HTTP?",'
            '"respuestas":["HTTP es un protocolo."],"ideas_clave":[]}'
        )
        with (
            # Groq encabeza la cadena de respaldo, asi que hay que apagarlo o
            # estos tests pegan a la API real si el .env del dev tiene su key.
            mock.patch.object(groq_provider, "is_configured", return_value=False),
            mock.patch.object(interview_live.gemini_keys.pool, "available", return_value=[]),
            mock.patch.object(interview_live.nvidia_provider, "is_configured", return_value=True),
            mock.patch.object(interview_live.nvidia_provider, "generate", return_value=raw),
            mock.patch.object(interview_live, "_get_client") as get_client,
        ):
            assist = interview_live.coach_assist(
                "Arquitectura web sobre HTTP", "¿Qué es HTTP?",
                api_key="gemini-agotada", mode="resolver",
            )
        self.assertIsNotNone(assist)
        get_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
