"""Fast-provider selection tests for transcript summaries."""

from __future__ import annotations

import unittest
from unittest import mock

from infrastructure.services import summarizer


class SummarizerTests(unittest.TestCase):
    def test_prefers_fast_nvidia_provider_when_available(self):
        with (
            mock.patch.object(summarizer.gemini_keys.pool, "reload"),
            mock.patch.object(summarizer.nvidia_provider.pool, "reload"),
            mock.patch.object(
                summarizer.nvidia_provider,
                "is_configured",
                return_value=True,
            ),
            mock.patch.object(
                summarizer.gemini_keys,
                "is_configured",
                return_value=True,
            ),
            mock.patch.object(
                summarizer.nvidia_provider,
                "generate",
                return_value="- **Punto clave:** resumen rápido.",
            ) as generate,
        ):
            result = summarizer.summarize("Una transcripción suficientemente larga.")

        self.assertIn("resumen rápido", result)
        generate.assert_called_once()
        self.assertEqual(generate.call_args.kwargs["max_tokens"], 700)


class SummaryOutageFallbackTests(unittest.TestCase):
    """The summary died whenever NVIDIA timed out and Gemini was saturated.

    Measured 2026-09-23 16:32: NVIDIA timed out after 45 s, Gemini
    `gemini-flash-latest` answered 503, and `summarize` raised "Error al
    contactar la API" on the FIRST 503 - it never tried the other Gemini
    models nor Groq, which was answering in 0.6 s. Same bug class as the coach
    (AUD-2): only quota moved on; saturation was treated as fatal.
    """

    def _gemini_client(self, answers: dict[str, object]):
        calls: list[str] = []

        class _Models:
            def generate_content(self, model, contents):
                calls.append(model)
                outcome = answers.get(model, RuntimeError("503 UNAVAILABLE high demand"))
                if isinstance(outcome, BaseException):
                    raise outcome
                return mock.Mock(text=outcome)

        client = mock.Mock()
        client.models = _Models()
        return client, calls

    def _run(self, gemini_answers, groq=None, nvidia_error=None):
        client, calls = self._gemini_client(gemini_answers)
        genai = mock.Mock()
        genai.Client.return_value = client
        pool = mock.Mock()
        pool.current.side_effect = ["k1", None]
        pool.is_quota_error.side_effect = lambda exc: "429" in str(exc)
        groq_fallback = (
            [("Groq", mock.Mock(generate=mock.Mock(return_value=groq)))]
            if groq is not None
            else []
        )
        with (
            mock.patch.dict("sys.modules", {"google.genai": mock.Mock(types=mock.Mock())}),
            mock.patch("google.genai", genai, create=True),
            mock.patch.object(summarizer.gemini_keys, "pool", pool),
            mock.patch.object(summarizer.gemini_keys, "is_configured", return_value=True),
            mock.patch.object(summarizer.gemini_keys, "usable_models", side_effect=lambda m: list(m)),
            mock.patch.object(summarizer.nvidia_provider.pool, "reload"),
            mock.patch.object(summarizer.nvidia_provider, "is_configured", return_value=True),
            mock.patch.object(
                summarizer.nvidia_provider,
                "generate",
                side_effect=nvidia_error or RuntimeError("NVIDIA conexión: timed out"),
            ),
            mock.patch(
                "infrastructure.services.provider_chain.fallback_providers",
                return_value=groq_fallback,
            ),
        ):
            result = summarizer.summarize("Una transcripción suficientemente larga.")
        return result, calls

    def test_a_saturated_primary_model_moves_on_to_the_next_gemini_model(self):
        answers = {summarizer._FALLBACK_MODELS[0]: "- resumen del segundo modelo"}
        result, calls = self._run(answers)
        self.assertIn("segundo modelo", result)
        self.assertGreaterEqual(len(calls), 2)

    def test_every_gemini_model_saturated_falls_back_to_groq(self):
        result, _calls = self._run({}, groq="- resumen de Groq")
        self.assertIn("Groq", result)


if __name__ == "__main__":
    unittest.main()
