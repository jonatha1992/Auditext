"""Groq provider rotation and its place in the fallback chain (no network)."""

from __future__ import annotations

import os
import time
import unittest
from unittest import mock

from infrastructure.services import groq_provider, interview_live, nvidia_provider

_SATURATION_503 = 'Groq HTTP 503: {"error":{"message":"over capacity"}}'


class GroqProviderTests(unittest.TestCase):
    def setUp(self):
        groq_provider.reset_socket_block()
        self.addCleanup(groq_provider.reset_socket_block)

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


class GroqDefaultModelTests(unittest.TestCase):
    """El default de GROQ_MODEL, que es lo que rompio el simulacro.

    `llama-3.3-70b-versatile` fue el default hasta el 2026-09-20 y para
    entonces ya no existia: Groq retiro toda la familia Llama y contesta
    `HTTP 404 model_not_found` con cualquier key. Ese 404 clasifica PERMANENT,
    asi que abortaba la cadena en el primer intento y el profesor IA quedaba
    mudo apenas Gemini se saturaba, con Groq "configurado".
    """

    # Ninguno de estos esta en el catalogo de Groq desde el 2026-09-20. El test
    # es un tope: si alguien vuelve a poner un Llama de default, falla aca y no
    # en la cara del usuario a mitad de un examen.
    _RETIRED_MARKERS = ("llama-3", "llama3", "llama-2", "llama-4", "mixtral", "gemma")

    def test_the_default_model_is_not_from_a_retired_family(self):
        model = groq_provider.GROQ_MODEL.casefold()
        for marker in self._RETIRED_MARKERS:
            with self.subTest(marker=marker):
                self.assertNotIn(
                    marker,
                    model,
                    f"{groq_provider.GROQ_MODEL!r} es de una familia retirada de "
                    "Groq: devuelve HTTP 404 model_not_found con cualquier key. "
                    "Validar el reemplazo con una llamada real antes de ponerlo.",
                )

    def test_the_default_model_is_one_verified_against_the_live_catalog(self):
        # Catalogo real de Groq leido el 2026-09-20 (13 modelos). Los de
        # transcripcion y los guard/prompt-guard no generan texto, asi que no
        # son candidatos a default de este proveedor.
        verified_chat_models = {
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "openai/gpt-oss-safeguard-20b",
            "groq/compound",
            "groq/compound-mini",
            "qwen/qwen3.8-27b",
            "allam-2-7b",
        }
        self.assertIn(groq_provider.GROQ_MODEL, verified_chat_models)


class GroqReasoningTokenFloorTests(unittest.TestCase):
    """La trampa del `content` vacio en los modelos de razonamiento.

    Medido el 2026-09-20 contra `openai/gpt-oss-120b`: el reasoning se cobra
    del MISMO `max_tokens` que la respuesta, asi que con un techo bajo la API
    devuelve HTTP 200 con `content` vacio. `_request` lo convierte en "respuesta
    vacia" y `provider_chain` lo cuenta como falla: el proveedor parece caido
    estando sano. El coach pide 270 tokens en los modos cortos
    (`interview_live._nvidia_token_budget`), justo en la zona de riesgo.
    """

    def test_a_reasoning_model_gets_room_for_the_reasoning(self):
        self.assertEqual(
            groq_provider.effective_max_tokens(270, "openai/gpt-oss-120b"),
            groq_provider._MIN_REASONING_TOKENS,
        )

    def test_a_ceiling_already_above_the_floor_is_left_alone(self):
        # Es un techo, no un objetivo: no se recorta lo que pide quien llama.
        self.assertEqual(
            groq_provider.effective_max_tokens(1800, "openai/gpt-oss-120b"), 1800
        )

    def test_a_plain_model_is_not_inflated(self):
        self.assertEqual(groq_provider.effective_max_tokens(270, "allam-2-7b"), 270)

    def test_generate_applies_the_floor_to_the_real_request(self):
        pool = groq_provider.GroqKeyPool(_keys=["gsk-one"])
        with (
            mock.patch.object(groq_provider, "pool", pool),
            mock.patch.object(groq_provider, "GROQ_MODEL", "openai/gpt-oss-120b"),
            mock.patch.object(
                groq_provider, "_request", return_value="respuesta"
            ) as request,
        ):
            groq_provider.generate("pregunta", max_tokens=270)

        # Tercer posicional de _request(key, prompt, max_tokens, ...).
        self.assertEqual(request.call_args.args[2], groq_provider._MIN_REASONING_TOKENS)

    def test_the_socket_timeout_follows_the_inflated_ceiling(self):
        """Mas tokens es mas tiempo de escritura: el timeout tiene que seguirlo."""
        pool = groq_provider.GroqKeyPool(_keys=["gsk-one"])
        with (
            mock.patch.object(groq_provider, "pool", pool),
            mock.patch.object(groq_provider, "GROQ_MODEL", "openai/gpt-oss-120b"),
            mock.patch.object(
                groq_provider, "_request", return_value="respuesta"
            ) as request,
        ):
            groq_provider.generate("pregunta", max_tokens=270, budget_seconds=120.0)

        self.assertAlmostEqual(
            request.call_args.kwargs["timeout"],
            groq_provider.request_timeout_for(groq_provider._MIN_REASONING_TOKENS),
        )


class GroqReasoningEffortTests(unittest.TestCase):
    """The 700-token floor alone is not enough on the real coach prompt.

    Measured 2026-09-23 with the 3867-char coach prompt on `openai/gpt-oss-120b`:
    max_tokens=700 with the default effort spent 698 tokens reasoning and
    returned `finish_reason=length` with empty content. The same ceiling with
    `reasoning_effort=low` used 191-295 reasoning tokens and answered in
    1.2-1.3 s. Without it Groq looks dead on every coach turn while healthy.
    """

    @staticmethod
    def _sent_body(model: str) -> dict:
        import io
        import json

        captured: dict = {}

        class _Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout=None):
            captured.update(json.loads(request.data.decode("utf-8")))
            payload = {"choices": [{"message": {"content": "respuesta"}}]}
            return _Response(json.dumps(payload).encode("utf-8"))

        with (
            mock.patch.object(groq_provider, "GROQ_MODEL", model),
            mock.patch.object(
                groq_provider.urllib.request, "urlopen", side_effect=fake_urlopen
            ),
        ):
            groq_provider._request("gsk-one", "pregunta", 700)
        return captured

    def test_a_reasoning_model_is_asked_to_reason_briefly(self):
        body = self._sent_body("openai/gpt-oss-120b")
        self.assertEqual(body.get("reasoning_effort"), "low")

    def test_a_plain_model_gets_no_reasoning_field(self):
        body = self._sent_body("allam-2-7b")
        self.assertNotIn("reasoning_effort", body)


if __name__ == "__main__":
    unittest.main()
