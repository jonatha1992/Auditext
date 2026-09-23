"""Shared Groq -> NVIDIA fallback chain, walked without touching the network."""

from __future__ import annotations

import time
import unittest
from unittest import mock

from infrastructure.services import provider_chain
from infrastructure.services.provider_chain import ProviderChainError


class _FakeProvider:
    """Stands in for groq_provider / nvidia_provider at the call boundary."""

    def __init__(self, *, answer: str = "", error: BaseException | None = None):
        self.answer = answer
        self.error = error
        self.calls: list[dict] = []

    def generate(self, prompt, *, max_tokens=480, system=None, budget_seconds=None):
        self.calls.append(
            {
                "prompt": prompt,
                "max_tokens": max_tokens,
                "system": system,
                "budget_seconds": budget_seconds,
            }
        )
        if self.error is not None:
            raise self.error
        return self.answer


class FallbackOrderTests(unittest.TestCase):
    def test_groq_leads_nvidia_follows(self):
        with mock.patch(
            "infrastructure.services.groq_provider.is_configured", return_value=True
        ), mock.patch(
            "infrastructure.services.nvidia_provider.is_configured", return_value=True
        ):
            self.assertEqual(
                [name for name, _ in provider_chain.fallback_providers()],
                ["Groq", "NVIDIA"],
            )

    def test_unconfigured_provider_is_left_out(self):
        with mock.patch(
            "infrastructure.services.groq_provider.is_configured", return_value=False
        ), mock.patch(
            "infrastructure.services.nvidia_provider.is_configured", return_value=True
        ):
            self.assertEqual(
                [name for name, _ in provider_chain.fallback_providers()], ["NVIDIA"]
            )
            self.assertTrue(provider_chain.has_fallback_provider())

    def test_no_provider_configured_reports_empty_chain(self):
        with mock.patch(
            "infrastructure.services.groq_provider.is_configured", return_value=False
        ), mock.patch(
            "infrastructure.services.nvidia_provider.is_configured", return_value=False
        ):
            self.assertEqual(provider_chain.fallback_providers(), [])
            self.assertFalse(provider_chain.has_fallback_provider())


class GenerateWithFallbackTests(unittest.TestCase):
    def _run(self, chain, **kwargs):
        with mock.patch.object(
            provider_chain, "fallback_providers", return_value=chain
        ):
            return provider_chain.generate_with_fallback("prompt", **kwargs)

    def test_first_healthy_provider_answers_and_the_rest_stay_untouched(self):
        groq = _FakeProvider(answer="respuesta")
        nvidia = _FakeProvider(answer="no deberia usarse")

        text = self._run([("Groq", groq), ("NVIDIA", nvidia)])

        self.assertEqual(text, "respuesta")
        self.assertEqual(len(groq.calls), 1)
        self.assertEqual(nvidia.calls, [])

    def test_a_failing_provider_hands_over_to_the_next_one(self):
        # Este es el caso que dejaba mudo al simulacro: el modelo NVIDIA por
        # defecto llegó a su end of life y devolvía 410 con cualquier key.
        groq = _FakeProvider(error=RuntimeError("Groq HTTP 503 overloaded"))
        nvidia = _FakeProvider(answer="respuesta de respaldo")

        text = self._run([("Groq", groq), ("NVIDIA", nvidia)])

        self.assertEqual(text, "respuesta de respaldo")
        self.assertEqual(len(nvidia.calls), 1)

    def test_an_empty_answer_counts_as_a_failure(self):
        groq = _FakeProvider(answer="   ")
        nvidia = _FakeProvider(answer="respuesta real")

        self.assertEqual(
            self._run([("Groq", groq), ("NVIDIA", nvidia)]), "respuesta real"
        )

    def test_every_provider_failing_raises_with_each_reason(self):
        groq = _FakeProvider(error=RuntimeError("Groq HTTP 429"))
        nvidia = _FakeProvider(error=RuntimeError("NVIDIA HTTP 410 Gone"))

        with self.assertRaises(ProviderChainError) as ctx:
            self._run([("Groq", groq), ("NVIDIA", nvidia)])

        message = str(ctx.exception)
        self.assertIn("Groq", message)
        self.assertIn("410", message)

    def test_empty_chain_raises_instead_of_returning_nothing(self):
        with self.assertRaises(ProviderChainError):
            self._run([])

    def test_max_tokens_and_system_reach_the_provider(self):
        groq = _FakeProvider(answer="ok")

        self._run([("Groq", groq)], max_tokens=700, system="sos un profesor")

        self.assertEqual(groq.calls[0]["max_tokens"], 700)
        self.assertEqual(groq.calls[0]["system"], "sos un profesor")


class DeadlineTests(unittest.TestCase):
    """Un simulacro congelado es peor que un error reintentable."""

    def test_the_remaining_deadline_caps_the_per_provider_budget(self):
        groq = _FakeProvider(answer="ok")

        with mock.patch.object(
            provider_chain, "fallback_providers", return_value=[("Groq", groq)]
        ):
            provider_chain.generate_with_fallback(
                "prompt",
                provider_budget_seconds=12.0,
                deadline=time.monotonic() + 6.0,
            )

        self.assertLessEqual(groq.calls[0]["budget_seconds"], 6.0)

    def test_no_provider_is_started_without_time_left_to_answer(self):
        # Arrancar una llamada que el propio timeout va a cortar a mitad gasta
        # la espera del usuario para nada: mejor fallar ya y que reintente.
        groq = _FakeProvider(answer="nunca llega a correr")
        nvidia = _FakeProvider(answer="tampoco")

        with mock.patch.object(
            provider_chain,
            "fallback_providers",
            return_value=[("Groq", groq), ("NVIDIA", nvidia)],
        ), self.assertRaises(ProviderChainError) as ctx:
            provider_chain.generate_with_fallback(
                "prompt", deadline=time.monotonic() + 0.01
            )

        self.assertEqual(groq.calls, [])
        self.assertEqual(nvidia.calls, [])
        self.assertIn("sin tiempo restante", str(ctx.exception))

    def test_a_slow_first_provider_leaves_no_room_for_the_second(self):
        # Reloj falso: el reparto del presupuesto se prueba por su lógica, no
        # haciendo esperar al test.
        clock = iter([0.0, 30.0, 30.0])
        slow = _FakeProvider(error=RuntimeError("Groq HTTP 503"))
        nvidia = _FakeProvider(answer="no llega")

        with mock.patch.object(
            provider_chain,
            "fallback_providers",
            return_value=[("Groq", slow), ("NVIDIA", nvidia)],
        ), mock.patch.object(
            provider_chain.time, "monotonic", side_effect=lambda: next(clock)
        ), self.assertRaises(ProviderChainError) as ctx:
            provider_chain.generate_with_fallback("prompt", deadline=32.0)

        self.assertEqual(len(slow.calls), 1)
        self.assertEqual(nvidia.calls, [])
        self.assertIn("sin tiempo restante", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
