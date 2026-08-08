"""Groq provider rotation and its place in the fallback chain (no network)."""

from __future__ import annotations

import os
import time
import unittest
from unittest import mock

from infrastructure.services import groq_provider, interview_live, nvidia_provider

_SATURATION_503 = 'Groq HTTP 503: {"error":{"message":"over capacity"}}'


class GroqProviderTests(unittest.TestCase):
    def test_collects_keys_with_the_canonical_collector(self):
        # clear=True: el colector lee hasta la key 20, y una GROQ_API_KEY real
        # en el .env del desarrollador haria fallar el conteo.
        env = {"GROQ_API_KEY": "gsk-one", "GROQ_API_KEY1": "gsk-two"}
        with mock.patch.dict(os.environ, env, clear=True):
            pool = groq_provider.GroqKeyPool()
            pool.reload()
        self.assertEqual(pool.available(), ["gsk-one", "gsk-two"])

    def test_placeholder_keys_are_ignored(self):
        env = {"GROQ_API_KEY": "your-api-key-here"}
        with mock.patch.dict(os.environ, env, clear=True):
            pool = groq_provider.GroqKeyPool()
            pool.reload()
        self.assertFalse(pool.is_configured())

    def test_long_answers_get_a_longer_socket_timeout(self):
        self.assertGreater(
            groq_provider.request_timeout_for(1800),
            groq_provider.request_timeout_for(270),
        )
        self.assertLessEqual(
            groq_provider.request_timeout_for(1800),
            groq_provider.GROQ_MAX_REQUEST_TIMEOUT_SECONDS,
        )

    def test_generate_rotates_to_the_second_key(self):
        pool = groq_provider.GroqKeyPool(_keys=["gsk-one", "gsk-two"])
        with (
            mock.patch.object(groq_provider, "pool", pool),
            mock.patch.object(groq_provider.time, "sleep"),
            mock.patch.object(
                groq_provider,
                "_request",
                side_effect=[
                    groq_provider.GroqError("Groq HTTP 429 rate limit"),
                    "respuesta",
                ],
            ) as request,
        ):
            self.assertEqual(groq_provider.generate("pregunta"), "respuesta")
        self.assertEqual(request.call_count, 2)

    def test_saturation_is_retried_within_the_budget(self):
        pool = groq_provider.GroqKeyPool(_keys=["gsk-one"])
        with (
            mock.patch.object(groq_provider, "pool", pool),
            mock.patch.object(groq_provider.time, "sleep"),
            # Cooldown 0: con sleep mockeado el reloj no avanza, asi que un
            # cooldown real convertiria el reintento en un busy-loop de 6 s.
            mock.patch.object(groq_provider, "cooldown_for", return_value=0.0),
            mock.patch.object(
                groq_provider,
                "_request",
                side_effect=[groq_provider.GroqError(_SATURATION_503), "respuesta"],
            ) as request,
        ):
            self.assertEqual(groq_provider.generate("pregunta"), "respuesta")
        self.assertEqual(request.call_count, 2)

    def test_generate_stops_at_the_budget_instead_of_hanging(self):
        pool = groq_provider.GroqKeyPool(_keys=["gsk-one", "gsk-two"])
        with (
            mock.patch.object(groq_provider, "pool", pool),
            mock.patch.object(groq_provider.time, "sleep"),
            mock.patch.object(
                groq_provider,
                "_request",
                side_effect=groq_provider.GroqError("Groq HTTP 429 rate limit"),
            ),
        ):
            started = time.monotonic()
            with self.assertRaises(groq_provider.GroqError):
                groq_provider.generate("pregunta", budget_seconds=5.0)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 5.0)


class BlockedSocketTests(unittest.TestCase):
    """WinError 10013 is the OS refusing the socket, not a provider failure.

    The journal collected 64 identical entries: no key fixes it and no retry
    cures it, so it must cost one attempt per process, not two per coach turn.
    """

    _BLOCKED = groq_provider.GroqError(
        "Groq conexión: <urlopen error [WinError 10013] Intento de acceso a un "
        "socket no permitido por sus permisos de acceso>"
    )

    def setUp(self):
        groq_provider.reset_socket_block()
        self.addCleanup(groq_provider.reset_socket_block)

    def test_first_block_stops_the_rotation_immediately(self):
        pool = groq_provider.GroqKeyPool(_keys=["gsk-one", "gsk-two"])
        with (
            mock.patch.object(groq_provider, "pool", pool),
            mock.patch.object(groq_provider.time, "sleep"),
            mock.patch.object(
                groq_provider, "_request", side_effect=self._BLOCKED
            ) as request,
        ):
            with self.assertRaises(groq_provider.GroqError):
                groq_provider.generate("pregunta")
        # One attempt, not one per key: the second key faces the same firewall.
        self.assertEqual(request.call_count, 1)
        self.assertTrue(groq_provider.socket_is_blocked())

    def test_later_calls_do_not_touch_the_network_at_all(self):
        pool = groq_provider.GroqKeyPool(_keys=["gsk-one"])
        with (
            mock.patch.object(groq_provider, "pool", pool),
            mock.patch.object(groq_provider.time, "sleep"),
            mock.patch.object(
                groq_provider, "_request", side_effect=self._BLOCKED
            ) as request,
        ):
            with self.assertRaises(groq_provider.GroqError):
                groq_provider.generate("pregunta")
            with self.assertRaises(groq_provider.GroqError):
                groq_provider.generate("otra pregunta")
        self.assertEqual(request.call_count, 1)

    def test_blocked_provider_leaves_the_fallback_chain(self):
        pool = groq_provider.GroqKeyPool(_keys=["gsk-one"])
        with (
            mock.patch.object(groq_provider, "pool", pool),
            mock.patch.object(groq_provider.time, "sleep"),
            mock.patch.object(groq_provider, "_request", side_effect=self._BLOCKED),
        ):
            self.assertTrue(groq_provider.is_configured())
            with self.assertRaises(groq_provider.GroqError):
                groq_provider.generate("pregunta")
            # NVIDIA must be reached directly from now on.
            self.assertFalse(groq_provider.is_configured())

    def test_ordinary_failures_still_rotate(self):
        pool = groq_provider.GroqKeyPool(_keys=["gsk-one", "gsk-two"])
        with (
            mock.patch.object(groq_provider, "pool", pool),
            mock.patch.object(groq_provider.time, "sleep"),
            mock.patch.object(
                groq_provider,
                "_request",
                side_effect=[
                    groq_provider.GroqError("Groq HTTP 429 rate limit"),
                    "respuesta",
                ],
            ),
        ):
            self.assertEqual(groq_provider.generate("pregunta"), "respuesta")
        self.assertFalse(groq_provider.socket_is_blocked())


class FallbackChainTests(unittest.TestCase):
    """Groq leads the chain: it fails for reasons unrelated to NVIDIA's 503s."""

    def test_groq_is_tried_before_nvidia(self):
        with (
            mock.patch.object(groq_provider, "is_configured", return_value=True),
            mock.patch.object(nvidia_provider, "is_configured", return_value=True),
        ):
            chain = [name for name, _module in interview_live._fallback_providers()]
        self.assertEqual(chain, ["Groq", "NVIDIA"])

    def test_nvidia_answers_when_groq_is_saturated(self):
        raw = (
            "R: " + " ".join(["respuesta"] * 120) + "\n"
            "A: " + " ".join(["ampliación"] * 160) + "\n"
            "P: Pregunta\nI: idea"
        )
        with (
            mock.patch.object(interview_live.gemini_keys.pool, "available", return_value=[]),
            mock.patch.object(groq_provider, "is_configured", return_value=True),
            mock.patch.object(
                groq_provider,
                "generate",
                side_effect=groq_provider.GroqError(_SATURATION_503),
            ),
            mock.patch.object(nvidia_provider, "is_configured", return_value=True),
            mock.patch.object(nvidia_provider, "generate", return_value=raw),
        ):
            assist = interview_live.coach_assist(
                "Kanban", "¿Cómo detecto exceso de WIP?", mode="resolver"
            )
        self.assertIsNotNone(assist)
        self.assertEqual(assist.provider, "NVIDIA")

    def test_every_provider_down_reports_the_reason_instead_of_silence(self):
        """The logged bug: assist=none with nothing on screen and no reason."""
        with (
            mock.patch.object(interview_live.gemini_keys.pool, "available", return_value=[]),
            mock.patch.object(groq_provider, "is_configured", return_value=True),
            mock.patch.object(
                groq_provider,
                "generate",
                side_effect=groq_provider.GroqError(_SATURATION_503),
            ),
            mock.patch.object(nvidia_provider, "is_configured", return_value=True),
            mock.patch.object(
                nvidia_provider,
                "generate",
                side_effect=nvidia_provider.NvidiaError(
                    "NVIDIA HTTP 503: Worker local total request limit reached (33/32)"
                ),
            ),
        ):
            with self.assertRaises(interview_live.CoachUnavailable) as ctx:
                interview_live.coach_assist("Kanban", "¿Qué es el WIP?", mode="resolver")
        self.assertIn("saturado", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
