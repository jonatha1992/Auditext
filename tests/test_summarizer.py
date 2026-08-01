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


if __name__ == "__main__":
    unittest.main()
